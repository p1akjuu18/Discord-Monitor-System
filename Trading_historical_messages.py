import json
import os
from datetime import datetime
import requests
from typing import List, Dict, Optional, Tuple
from pathlib import Path
import pandas as pd
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class MessageFileHandler(FileSystemEventHandler):
    def __init__(self, analyzer):
        self.analyzer = analyzer
        self.processed_files = set()
        print("消息处理器已初始化")  # 添加初始化提示
        
    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith('.json'):
            print(f"\n检测到新文件: {event.src_path}")  # 添加文件检测提示
            self.process_file(event.src_path)
            
    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith('.json'):
            print(f"\n检测到文件修改: {event.src_path}")  # 添加文件修改提示
            self.process_file(event.src_path)
    
    def process_file(self, file_path):
        try:
            # 确保文件写入完成
            time.sleep(1)
            
            print(f"\n检测到新文件: {file_path}")
            print(f"开始处理文件: {file_path}")
            
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                print("成功读取JSON文件")
            
            # 从文件名中提取频道名称
            channel_name = os.path.basename(file_path).split('-')[1].replace('.json', '')
            print(f"处理频道: {channel_name}")
            
            # 判断数据结构类型并获取消息
            messages = data if isinstance(data, list) else data.get('messages', [])
            
            if messages:
                latest_message = messages[-1]  # 获取最后一条消息
                content = latest_message.get('content', '')
                print(f"\n最新消息内容: {content}")  # 完整打印消息内容
                print("\n开始调用 DeepSeek API 进行分析...")
                
                # 调用 DeepSeek API 分析消息
                result = self.analyzer.analyze_message(content, channel_name)
                if result:
                    print("\nDeepSeek API 分析成功!")
                    print("分析结果:")
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    print("\nDeepSeek API 分析失败或返回空结果")
            else:
                print("文件中没有找到消息数据")
            
            print(f"\n文件处理完成: {file_path}")
            
        except json.JSONDecodeError as e:
            print(f"JSON解析错误 {file_path}: {str(e)}")
        except Exception as e:
            print(f"处理文件时出错 {file_path}: {str(e)}")

class HistoricalMessageAnalyzer:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.siliconflow.cn/v1"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        # 默认分析提示词
        self.default_prompt = """
请根据以下博主的交易分析内容，提取并整理出以下信息：

1. **交易币种**：提取博主提到的币种名称，最开始的几个字母一般为币种名称。

2. **方向**：提取博主的交易方向（例如：多单、空单）。

3. **杠杆**：如果博主提到杠杆，请标明杠杆倍数。

4. **入场点位**：如果博主提到入场价格，请列出。
   - 入场点位1：第一个入场价格
   - 入场点位2：第二个入场价格（如果有）
   - 入场点位3：第三个入场价格（如果有）

5. **止损点位**：如果博主提到止损价格，请列出。
   - 止损点位1：第一个止损价格
   - 止损点位2：第二个止损价格（如果有）
   - 止损点位3：第三个止损价格（如果有）

6. **止盈点位**：如果博主提到止盈价格，请列出。
   - 止盈点位1：第一个止盈价格
   - 止盈点位2：第二个止盈价格（如果有）
   - 止盈点位3：第三个止盈价格（如果有）

7. **分析内容**：提取并总结博主针对该币种的市场分析和走势预测。

内容如下：
{content}

请以JSON格式返回分析结果，格式如下：
{{
    "交易币种": "币种名称",
    "方向": "交易方向",
    "杠杆": 杠杆倍数或null,
    "入场点位1": 数字或null,
    "入场点位2": 数字或null,
    "入场点位3": 数字或null,
    "止损点位1": 数字或null,
    "止损点位2": 数字或null,
    "止损点位3": 数字或null,
    "止盈点位1": 数字或null,
    "止盈点位2": 数字或null,
    "止盈点位3": 数字或null,
    "分析内容": "分析文字"
}}

价格必须是数字（不含单位），未提及的信息用null。
"""
        
        # 针对不同博主的自定义提示词
        self.channel_prompts = {
            "交易员张张子": """
请根据以下博主的交易分析内容，提取并整理出以下信息：

1. **交易币种**：提取博主提到的币种名称，一般会提到大饼=BTC=$btc，以太=ETH=$eth,SOL,BNB,DOGE。

2. **方向**：提取博主的交易方向（例如：多单、空单）。

3. **杠杆**：如果博主提到杠杆，请标明杠杆倍数。

4. **入场点位**：如果博主提到入场价格，请列出。多单多以"支撑位"为入场点位，空单多以"压力位"为入场点位。会提到"留意"的位置。
   - 入场点位1：第一个入场价格
   - 入场点位2：第二个入场价格（如果有）
   - 入场点位3：第三个入场价格（如果有）

5. **止损点位**：如果博主提到止损价格，请列出。
   - 止损点位1：第一个止损价格
   - 止损点位2：第二个止损价格（如果有）
   - 止损点位3：第三个止损价格（如果有）

6. **止盈点位**：如果博主提到止盈价格，请列出。多单多以"压力位"为止盈点位。
   - 止盈点位1：第一个止盈价格
   - 止盈点位2：第二个止盈价格（如果有）
   - 止盈点位3：第三个止盈价格（如果有）

7. **分析内容**：提取并总结博主针对各个不同币种的市场分析和走势预测，每个币种单独记录。

内容如下：
{content}

请以JSON格式返回分析结果，格式如下：
{{
    "交易币种": "币种名称",
    "方向": "交易方向",
    "杠杆": 杠杆倍数或null,
    "入场点位1": 数字或null,
    "入场点位2": 数字或null,
    "入场点位3": 数字或null,
    "止损点位1": 数字或null,
    "止损点位2": 数字或null,
    "止损点位3": 数字或null,
    "止盈点位1": 数字或null,
    "止盈点位2": 数字或null,
    "止盈点位3": 数字或null,
    "分析内容": "分析文字"
}}

价格必须是数字（不含单位），未提及的信息用null。
""",

            "打不死的交易员": """
请根据以下博主的交易分析内容，提取并整理出以下信息：

1. **交易币种**：提取博主提到的币种名称，最开始的几个字母一般为币种名称。

2. **方向**：提取博主的交易方向（例如：多单、空单）。

3. **杠杆**：如果博主提到杠杆，请标明杠杆倍数。

4. **入场点位**：如果博主提到入场价格，请列出。
   - 入场点位1：第一个入场价格
   - 入场点位2：第二个入场价格（如果有）
   - 入场点位3：第三个入场价格（如果有）

5. **止损点位**：如果博主提到止损价格，请列出。
   - 止损点位1：第一个止损价格
   - 止损点位2：第二个止损价格（如果有）
   - 止损点位3：第三个止损价格（如果有）

6. **止盈点位**：如果博主提到止盈价格，请列出。
   - 止盈点位1：第一个止盈价格
   - 止盈点位2：第二个止盈价格（如果有）
   - 止盈点位3：第三个止盈价格（如果有）

7. **分析内容**：提取并总结博主针对该币种的市场分析和走势预测。

内容如下：
{content}

请以JSON格式返回分析结果，格式如下：
{{
    "交易币种": "币种名称",
    "方向": "交易方向",
    "杠杆": 杠杆倍数或null,
    "入场点位1": 数字或null,
    "入场点位2": 数字或null,
    "入场点位3": 数字或null,
    "止损点位1": 数字或null,
    "止损点位2": 数字或null,
    "止损点位3": 数字或null,
    "止盈点位1": 数字或null,
    "止盈点位2": 数字或null,
    "止盈点位3": 数字或null,
    "分析内容": "分析文字"
}}

价格必须是数字（不含单位），未提及的信息用null。
""",


            "tia-初塔": """
请根据以下博主的交易分析内容，提取并整理出以下信息：

1. **交易币种**：提取博主提到的币种名称，最开始的几个字母一般为币种名称。

2. **方向**：提取博主的交易方向（例如：多单、空单）。

3. **杠杆**：如果博主提到杠杆，请标明杠杆倍数。

4. **入场点位**：如果博主提到入场价格，请列出。
   - 入场点位1：第一个入场价格
   - 入场点位2：第二个入场价格（如果有）
   - 入场点位3：第三个入场价格（如果有）

5. **止损点位**：如果博主提到止损价格，请列出。
   - 止损点位1：第一个止损价格
   - 止损点位2：第二个止损价格（如果有）
   - 止损点位3：第三个止损价格（如果有）

6. **止盈点位**：如果博主提到止盈价格，请列出。
   - 止盈点位1：第一个止盈价格
   - 止盈点位2：第二个止盈价格（如果有）
   - 止盈点位3：第三个止盈价格（如果有）

7. **分析内容**：提取并总结博主针对该币种的市场分析和走势预测。

内容如下：
{content}

请以JSON格式返回分析结果，格式如下：
{{
    "交易币种": "币种名称",
    "方向": "交易方向",
    "杠杆": 杠杆倍数或null,
    "入场点位1": 数字或null,
    "入场点位2": 数字或null,
    "入场点位3": 数字或null,
    "止损点位1": 数字或null,
    "止损点位2": 数字或null,
    "止损点位3": 数字或null,
    "止盈点位1": 数字或null,
    "止盈点位2": 数字或null,
    "止盈点位3": 数字或null,
    "分析内容": "分析文字"
}}

价格必须是数字（不含单位），未提及的信息用null。
""",

            "舒琴实盘": """
请根据以下博主的交易分析内容，提取并整理出以下信息：

1. **交易币种**：提取博主提到的币种名称，最开始的几个字母一般为币种名称。

2. **方向**：提取博主的交易方向（例如：多单、空单）。

3. **杠杆**：如果博主提到杠杆，请标明杠杆倍数。

4. **入场点位**：如果博主提到入场价格，请列出。
   - 入场点位1：第一个入场价格
   - 入场点位2：第二个入场价格（如果有）
   - 入场点位3：第三个入场价格（如果有）

5. **止损点位**：如果博主提到止损价格，请列出。
   - 止损点位1：第一个止损价格
   - 止损点位2：第二个止损价格（如果有）
   - 止损点位3：第三个止损价格（如果有）

6. **止盈点位**：如果博主提到止盈价格，请列出。
   - 止盈点位1：第一个止盈价格
   - 止盈点位2：第二个止盈价格（如果有）
   - 止盈点位3：第三个止盈价格（如果有）

7. **分析内容**：提取并总结博主针对该币种的市场分析和走势预测。

内容如下：
{content}

请以JSON格式返回分析结果，格式如下：
{{
    "交易币种": "币种名称",
    "方向": "交易方向",
    "杠杆": 杠杆倍数或null,
    "入场点位1": 数字或null,
    "入场点位2": 数字或null,
    "入场点位3": 数字或null,
    "止损点位1": 数字或null,
    "止损点位2": 数字或null,
    "止损点位3": 数字或null,
    "止盈点位1": 数字或null,
    "止盈点位2": 数字或null,
    "止盈点位3": 数字或null,
    "分析内容": "分析文字"
}}

价格必须是数字（不含单位），未提及的信息用null。
""",

            "三马合约": """
请根据以下博主的交易分析内容，提取并整理出以下信息：

1. **交易币种**：提取博主提到的币种名称，最开始的几个字母一般为币种名称。

2. **方向**：提取博主的交易方向（例如：多单、空单）。

3. **杠杆**：如果博主提到杠杆，请标明杠杆倍数。

4. **入场点位**：如果博主提到入场价格，请列出。
   - 入场点位1：第一个入场价格
   - 入场点位2：第二个入场价格（如果有）
   - 入场点位3：第三个入场价格（如果有）

5. **止损点位**：如果博主提到止损价格，请列出。
   - 止损点位1：第一个止损价格
   - 止损点位2：第二个止损价格（如果有）
   - 止损点位3：第三个止损价格（如果有）

6. **止盈点位**：如果博主提到止盈价格，请列出。
   - 止盈点位1：第一个止盈价格
   - 止盈点位2：第二个止盈价格（如果有）
   - 止盈点位3：第三个止盈价格（如果有）

7. **分析内容**：提取并总结博主针对该币种的市场分析和走势预测。

内容如下：
{content}

请以JSON格式返回分析结果，格式如下：
{{
    "交易币种": "币种名称",
    "方向": "交易方向",
    "杠杆": 杠杆倍数或null,
    "入场点位1": 数字或null,
    "入场点位2": 数字或null,
    "入场点位3": 数字或null,
    "止损点位1": 数字或null,
    "止损点位2": 数字或null,
    "止损点位3": 数字或null,
    "止盈点位1": 数字或null,
    "止盈点位2": 数字或null,
    "止盈点位3": 数字或null,
    "分析内容": "分析文字"
}}

价格必须是数字（不含单位），未提及的信息用null。
""",

            "三马现货": """
请根据以下博主的交易分析内容，提取并整理出以下信息：

1. **交易币种**：提取博主提到的币种名称，最开始的几个字母一般为币种名称。

2. **方向**：提取博主的交易方向（例如：多单、空单）。

3. **杠杆**：如果博主提到杠杆，请标明杠杆倍数。

4. **入场点位**：如果博主提到入场价格，请列出。
   - 入场点位1：第一个入场价格
   - 入场点位2：第二个入场价格（如果有）
   - 入场点位3：第三个入场价格（如果有）

5. **止损点位**：如果博主提到止损价格，请列出。
   - 止损点位1：第一个止损价格
   - 止损点位2：第二个止损价格（如果有）
   - 止损点位3：第三个止损价格（如果有）

6. **止盈点位**：如果博主提到止盈价格，请列出。
   - 止盈点位1：第一个止盈价格
   - 止盈点位2：第二个止盈价格（如果有）
   - 止盈点位3：第三个止盈价格（如果有）

7. **分析内容**：提取并总结博主针对该币种的市场分析和走势预测。

内容如下：
{content}

请以JSON格式返回分析结果，格式如下：
{{
    "交易币种": "币种名称",
    "方向": "交易方向",
    "杠杆": 杠杆倍数或null,
    "入场点位1": 数字或null,
    "入场点位2": 数字或null,
    "入场点位3": 数字或null,
    "止损点位1": 数字或null,
    "止损点位2": 数字或null,
    "止损点位3": 数字或null,
    "止盈点位1": 数字或null,
    "止盈点位2": 数字或null,
    "止盈点位3": 数字或null,
    "分析内容": "分析文字"
}}

价格必须是数字（不含单位），未提及的信息用null。
""",

            "btc欧阳": """
请根据以下博主的交易分析内容，提取并整理出以下信息：

1. **交易币种**：提取博主提到的币种名称，最开始的几个字母一般为币种名称。

2. **方向**：提取博主的交易方向（例如：多单、空单）。

3. **杠杆**：如果博主提到杠杆，请标明杠杆倍数。

4. **入场点位**：如果博主提到入场价格，请列出。
   - 入场点位1：第一个入场价格
   - 入场点位2：第二个入场价格（如果有）
   - 入场点位3：第三个入场价格（如果有）

5. **止损点位**：如果博主提到止损价格，请列出。
   - 止损点位1：第一个止损价格
   - 止损点位2：第二个止损价格（如果有）
   - 止损点位3：第三个止损价格（如果有）

6. **止盈点位**：如果博主提到止盈价格，请列出。
   - 止盈点位1：第一个止盈价格
   - 止盈点位2：第二个止盈价格（如果有）
   - 止盈点位3：第三个止盈价格（如果有）

7. **分析内容**：提取并总结博主针对该币种的市场分析和走势预测。

内容如下：
{content}

请以JSON格式返回分析结果，格式如下：
{{
    "交易币种": "币种名称",
    "方向": "交易方向",
    "杠杆": 杠杆倍数或null,
    "入场点位1": 数字或null,
    "入场点位2": 数字或null,
    "入场点位3": 数字或null,
    "止损点位1": 数字或null,
    "止损点位2": 数字或null,
    "止损点位3": 数字或null,
    "止盈点位1": 数字或null,
    "止盈点位2": 数字或null,
    "止盈点位3": 数字或null,
    "分析内容": "分析文字"
}}

价格必须是数字（不含单位），未提及的信息用null。
""",

            "加密大漂亮": """
请根据以下博主的交易分析内容，提取并整理出以下信息：

1. **交易币种**：提取博主提到的币种名称，最开始的几个字母一般为币种名称。

2. **方向**：提取博主的交易方向（例如：多单、空单）。

3. **杠杆**：如果博主提到杠杆，请标明杠杆倍数。

4. **入场点位**：如果博主提到入场价格，请列出。
   - 入场点位1：第一个入场价格
   - 入场点位2：第二个入场价格（如果有）
   - 入场点位3：第三个入场价格（如果有）

5. **止损点位**：如果博主提到止损价格，请列出。
   - 止损点位1：第一个止损价格
   - 止损点位2：第二个止损价格（如果有）
   - 止损点位3：第三个止损价格（如果有）

6. **止盈点位**：如果博主提到止盈价格，请列出。
   - 止盈点位1：第一个止盈价格
   - 止盈点位2：第二个止盈价格（如果有）
   - 止盈点位3：第三个止盈价格（如果有）

7. **分析内容**：提取并总结博主针对该币种的市场分析和走势预测。

内容如下：
{content}

请以JSON格式返回分析结果，格式如下：
{{
    "交易币种": "币种名称",
    "方向": "交易方向",
    "杠杆": 杠杆倍数或null,
    "入场点位1": 数字或null,
    "入场点位2": 数字或null,
    "入场点位3": 数字或null,
    "止损点位1": 数字或null,
    "止损点位2": 数字或null,
    "止损点位3": 数字或null,
    "止盈点位1": 数字或null,
    "止盈点位2": 数字或null,
    "止盈点位3": 数字或null,
    "分析内容": "分析文字"
}}

价格必须是数字（不含单位），未提及的信息用null。
""",

            "大漂亮会员策略": """
请根据以下博主的交易分析内容，提取并整理出以下信息：

1. **交易币种**：提取博主提到的币种名称，最开始的几个字母一般为币种名称。

2. **方向**：提取博主的交易方向（例如：多单、空单）。

3. **杠杆**：如果博主提到杠杆，请标明杠杆倍数。

4. **入场点位**：如果博主提到入场价格，请列出。
   - 入场点位1：第一个入场价格
   - 入场点位2：第二个入场价格（如果有）
   - 入场点位3：第三个入场价格（如果有）

5. **止损点位**：如果博主提到止损价格，请列出。
   - 止损点位1：第一个止损价格
   - 止损点位2：第二个止损价格（如果有）
   - 止损点位3：第三个止损价格（如果有）

6. **止盈点位**：如果博主提到止盈价格，请列出。
   - 止盈点位1：第一个止盈价格
   - 止盈点位2：第二个止盈价格（如果有）
   - 止盈点位3：第三个止盈价格（如果有）

7. **分析内容**：提取并总结博主针对该币种的市场分析和走势预测。

内容如下：
{content}

请以JSON格式返回分析结果，格式如下：
{{
    "交易币种": "币种名称",
    "方向": "交易方向",
    "杠杆": 杠杆倍数或null,
    "入场点位1": 数字或null,
    "入场点位2": 数字或null,
    "入场点位3": 数字或null,
    "止损点位1": 数字或null,
    "止损点位2": 数字或null,
    "止损点位3": 数字或null,
    "止盈点位1": 数字或null,
    "止盈点位2": 数字或null,
    "止盈点位3": 数字或null,
    "分析内容": "分析文字"
}}

价格必须是数字（不含单位），未提及的信息用null。
""",

            "shu-crypto": """
请根据以下博主的交易分析内容，提取并整理出以下信息：

1. **交易币种**：提取博主提到的币种名称，最开始的几个字母一般为币种名称。

2. **方向**：提取博主的交易方向（例如：多单、空单）。

3. **杠杆**：如果博主提到杠杆，请标明杠杆倍数。

4. **入场点位**：如果博主提到入场价格，请列出。
   - 入场点位1：第一个入场价格
   - 入场点位2：第二个入场价格（如果有）
   - 入场点位3：第三个入场价格（如果有）

5. **止损点位**：如果博主提到止损价格，请列出。
   - 止损点位1：第一个止损价格
   - 止损点位2：第二个止损价格（如果有）
   - 止损点位3：第三个止损价格（如果有）

6. **止盈点位**：如果博主提到止盈价格，请列出。
   - 止盈点位1：第一个止盈价格
   - 止盈点位2：第二个止盈价格（如果有）
   - 止盈点位3：第三个止盈价格（如果有）

7. **分析内容**：提取并总结博主针对该币种的市场分析和走势预测。

内容如下：
{content}

请以JSON格式返回分析结果，格式如下：
{{
    "交易币种": "币种名称",
    "方向": "交易方向",
    "杠杆": 杠杆倍数或null,
    "入场点位1": 数字或null,
    "入场点位2": 数字或null,
    "入场点位3": 数字或null,
    "止损点位1": 数字或null,
    "止损点位2": 数字或null,
    "止损点位3": 数字或null,
    "止盈点位1": 数字或null,
    "止盈点位2": 数字或null,
    "止盈点位3": 数字或null,
    "分析内容": "分析文字"
}}

价格必须是数字（不含单位），未提及的信息用null。
""",

            "三木的交易日记": """
请根据以下博主的交易分析内容，提取并整理出以下信息：

1. **交易币种**：提取博主提到的币种名称，最开始的几个字母一般为币种名称。

2. **方向**：提取博主的交易方向（例如：多单、空单）。

3. **杠杆**：如果博主提到杠杆，请标明杠杆倍数。

4. **入场点位**：如果博主提到入场价格，请列出。
   - 入场点位1：第一个入场价格
   - 入场点位2：第二个入场价格（如果有）
   - 入场点位3：第三个入场价格（如果有）

5. **止损点位**：如果博主提到止损价格，请列出。
   - 止损点位1：第一个止损价格
   - 止损点位2：第二个止损价格（如果有）
   - 止损点位3：第三个止损价格（如果有）

6. **止盈点位**：如果博主提到止盈价格，请列出。
   - 止盈点位1：第一个止盈价格
   - 止盈点位2：第二个止盈价格（如果有）
   - 止盈点位3：第三个止盈价格（如果有）

7. **分析内容**：提取并总结博主针对该币种的市场分析和走势预测。

内容如下：
{content}

请以JSON格式返回分析结果，格式如下：
{{
    "交易币种": "币种名称",
    "方向": "交易方向",
    "杠杆": 杠杆倍数或null,
    "入场点位1": 数字或null,
    "入场点位2": 数字或null,
    "入场点位3": 数字或null,
    "止损点位1": 数字或null,
    "止损点位2": 数字或null,
    "止损点位3": 数字或null,
    "止盈点位1": 数字或null,
    "止盈点位2": 数字或null,
    "止盈点位3": 数字或null,
    "分析内容": "分析文字"
}}

价格必须是数字（不含单位），未提及的信息用null。
""",

            "大镖客比特币行情": """
请根据以下博主的交易分析内容，提取并整理出以下信息：

1. **交易币种**：提取博主提到的币种名称，最开始的几个字母一般为币种名称。

2. **方向**：提取博主的交易方向（例如：多单、空单）。

3. **杠杆**：如果博主提到杠杆，请标明杠杆倍数。

4. **入场点位**：如果博主提到入场价格，请列出。
   - 入场点位1：第一个入场价格
   - 入场点位2：第二个入场价格（如果有）
   - 入场点位3：第三个入场价格（如果有）

5. **止损点位**：如果博主提到止损价格，请列出。
   - 止损点位1：第一个止损价格
   - 止损点位2：第二个止损价格（如果有）
   - 止损点位3：第三个止损价格（如果有）

6. **止盈点位**：如果博主提到止盈价格，请列出。
   - 止盈点位1：第一个止盈价格
   - 止盈点位2：第二个止盈价格（如果有）
   - 止盈点位3：第三个止盈价格（如果有）

7. **分析内容**：提取并总结博主针对该币种的市场分析和走势预测。

内容如下：
{content}

请以JSON格式返回分析结果，格式如下：
{{
    "交易币种": "币种名称",
    "方向": "交易方向",
    "杠杆": 杠杆倍数或null,
    "入场点位1": 数字或null,
    "入场点位2": 数字或null,
    "入场点位3": 数字或null,
    "止损点位1": 数字或null,
    "止损点位2": 数字或null,
    "止损点位3": 数字或null,
    "止盈点位1": 数字或null,
    "止盈点位2": 数字或null,
    "止盈点位3": 数字或null,
    "分析内容": "分析文字"
}}

价格必须是数字（不含单位），未提及的信息用null。
""",

            "trader-titan": """
请根据以下博主的交易分析内容，提取并整理出以下信息：

1. **交易币种**：提取博主提到的币种名称，最开始的几个字母一般为币种名称。

2. **方向**：提取博主的交易方向（例如：多单、空单）。

3. **杠杆**：如果博主提到杠杆，请标明杠杆倍数。

4. **入场点位**：如果博主提到入场价格，请列出。
   - 入场点位1：第一个入场价格
   - 入场点位2：第二个入场价格（如果有）
   - 入场点位3：第三个入场价格（如果有）

5. **止损点位**：如果博主提到止损价格，请列出。
   - 止损点位1：第一个止损价格
   - 止损点位2：第二个止损价格（如果有）
   - 止损点位3：第三个止损价格（如果有）

6. **止盈点位**：如果博主提到止盈价格，请列出。
   - 止盈点位1：第一个止盈价格
   - 止盈点位2：第二个止盈价格（如果有）
   - 止盈点位3：第三个止盈价格（如果有）

7. **分析内容**：提取并总结博主针对该币种的市场分析和走势预测。

内容如下：
{content}

请以JSON格式返回分析结果，格式如下：
{{
    "交易币种": "币种名称",
    "方向": "交易方向",
    "杠杆": 杠杆倍数或null,
    "入场点位1": 数字或null,
    "入场点位2": 数字或null,
    "入场点位3": 数字或null,
    "止损点位1": 数字或null,
    "止损点位2": 数字或null,
    "止损点位3": 数字或null,
    "止盈点位1": 数字或null,
    "止盈点位2": 数字或null,
    "止盈点位3": 数字或null,
    "分析内容": "分析文字"
}}

价格必须是数字（不含单位），未提及的信息用null。
""",

            "traeep": """
请根据以下博主的交易分析内容，提取并整理出以下信息：

1. **交易币种**：提取博主提到的币种名称，最开始的几个字母一般为币种名称。

2. **方向**：提取博主的交易方向（例如：多单、空单）。

3. **杠杆**：如果博主提到杠杆，请标明杠杆倍数。

4. **入场点位**：如果博主提到入场价格，请列出。
   - 入场点位1：第一个入场价格
   - 入场点位2：第二个入场价格（如果有）
   - 入场点位3：第三个入场价格（如果有）

5. **止损点位**：如果博主提到止损价格，请列出。
   - 止损点位1：第一个止损价格
   - 止损点位2：第二个止损价格（如果有）
   - 止损点位3：第三个止损价格（如果有）

6. **止盈点位**：如果博主提到止盈价格，请列出。
   - 止盈点位1：第一个止盈价格
   - 止盈点位2：第二个止盈价格（如果有）
   - 止盈点位3：第三个止盈价格（如果有）

7. **分析内容**：提取并总结博主针对该币种的市场分析和走势预测。

内容如下：
{content}

请以JSON格式返回分析结果，格式如下：
{{
    "交易币种": "币种名称",
    "方向": "交易方向",
    "杠杆": 杠杆倍数或null,
    "入场点位1": 数字或null,
    "入场点位2": 数字或null,
    "入场点位3": 数字或null,
    "止损点位1": 数字或null,
    "止损点位2": 数字或null,
    "止损点位3": 数字或null,
    "止盈点位1": 数字或null,
    "止盈点位2": 数字或null,
    "止盈点位3": 数字或null,
    "分析内容": "分析文字"
}}

价格必须是数字（不含单位），未提及的信息用null。
""",

            "john": """
请根据以下博主的交易分析内容，提取并整理出以下信息：

1. **交易币种**：提取博主提到的币种名称，最开始的几个字母一般为币种名称。

2. **方向**：提取博主的交易方向（例如：多单、空单）。

3. **杠杆**：如果博主提到杠杆，请标明杠杆倍数。

4. **入场点位**：如果博主提到入场价格，请列出。
   - 入场点位1：第一个入场价格
   - 入场点位2：第二个入场价格（如果有）
   - 入场点位3：第三个入场价格（如果有）

5. **止损点位**：如果博主提到止损价格，请列出。
   - 止损点位1：第一个止损价格
   - 止损点位2：第二个止损价格（如果有）
   - 止损点位3：第三个止损价格（如果有）

6. **止盈点位**：如果博主提到止盈价格，请列出。
   - 止盈点位1：第一个止盈价格
   - 止盈点位2：第二个止盈价格（如果有）
   - 止盈点位3：第三个止盈价格（如果有）

7. **分析内容**：提取并总结博主针对该币种的市场分析和走势预测。

内容如下：
{content}

请以JSON格式返回分析结果，格式如下：
{{
    "交易币种": "币种名称",
    "方向": "交易方向",
    "杠杆": 杠杆倍数或null,
    "入场点位1": 数字或null,
    "入场点位2": 数字或null,
    "入场点位3": 数字或null,
    "止损点位1": 数字或null,
    "止损点位2": 数字或null,
    "止损点位3": 数字或null,
    "止盈点位1": 数字或null,
    "止盈点位2": 数字或null,
    "止盈点位3": 数字或null,
    "分析内容": "分析文字"
}}

价格必须是数字（不含单位），未提及的信息用null。
""",

            "Michelle": """
请根据以下博主的交易分析内容，提取并整理出以下信息：

1. **交易币种**：提取博主提到的币种名称，最开始的几个字母一般为币种名称。

2. **方向**：提取博主的交易方向（例如：多单、空单）。

3. **杠杆**：如果博主提到杠杆，请标明杠杆倍数。

4. **入场点位**：如果博主提到入场价格，请列出。
   - 入场点位1：第一个入场价格
   - 入场点位2：第二个入场价格（如果有）
   - 入场点位3：第三个入场价格（如果有）

5. **止损点位**：如果博主提到止损价格，请列出。
   - 止损点位1：第一个止损价格
   - 止损点位2：第二个止损价格（如果有）
   - 止损点位3：第三个止损价格（如果有）

6. **止盈点位**：如果博主提到止盈价格，请列出。
   - 止盈点位1：第一个止盈价格
   - 止盈点位2：第二个止盈价格（如果有）
   - 止盈点位3：第三个止盈价格（如果有）

7. **分析内容**：提取并总结博主针对该币种的市场分析和走势预测。

内容如下：
{content}

请以JSON格式返回分析结果，格式如下：
{{
    "交易币种": "币种名称",
    "方向": "交易方向",
    "杠杆": 杠杆倍数或null,
    "入场点位1": 数字或null,
    "入场点位2": 数字或null,
    "入场点位3": 数字或null,
    "止损点位1": 数字或null,
    "止损点位2": 数字或null,
    "止损点位3": 数字或null,
    "止盈点位1": 数字或null,
    "止盈点位2": 数字或null,
    "止盈点位3": 数字或null,
    "分析内容": "分析文字"
}}

价格必须是数字（不含单位），未提及的信息用null。
""",

            "eliz": """
请根据以下博主的交易分析内容，提取并整理出以下信息：

1. **交易币种**：提取博主提到的币种名称，最开始的几个字母一般为币种名称。

2. **方向**：提取博主的交易方向（例如：多单、空单）。

3. **杠杆**：如果博主提到杠杆，请标明杠杆倍数。

4. **入场点位**：如果博主提到入场价格，请列出。
   - 入场点位1：第一个入场价格
   - 入场点位2：第二个入场价格（如果有）
   - 入场点位3：第三个入场价格（如果有）

5. **止损点位**：如果博主提到止损价格，请列出。
   - 止损点位1：第一个止损价格
   - 止损点位2：第二个止损价格（如果有）
   - 止损点位3：第三个止损价格（如果有）

6. **止盈点位**：如果博主提到止盈价格，请列出。
   - 止盈点位1：第一个止盈价格
   - 止盈点位2：第二个止盈价格（如果有）
   - 止盈点位3：第三个止盈价格（如果有）

7. **分析内容**：提取并总结博主针对该币种的市场分析和走势预测。

内容如下：
{content}

请以JSON格式返回分析结果，格式如下：
{{
    "交易币种": "币种名称",
    "方向": "交易方向",
    "杠杆": 杠杆倍数或null,
    "入场点位1": 数字或null,
    "入场点位2": 数字或null,
    "入场点位3": 数字或null,
    "止损点位1": 数字或null,
    "止损点位2": 数字或null,
    "止损点位3": 数字或null,
    "止盈点位1": 数字或null,
    "止盈点位2": 数字或null,
    "止盈点位3": 数字或null,
    "分析内容": "分析文字"
}}

价格必须是数字（不含单位），未提及的信息用null。
""",

            "hbj": """
请根据以下博主的交易分析内容，提取并整理出以下信息：

1. **交易币种**：提取博主提到的币种名称，最开始的几个字母一般为币种名称。

2. **方向**：提取博主的交易方向（例如：多单、空单）。

3. **杠杆**：如果博主提到杠杆，请标明杠杆倍数。

4. **入场点位**：如果博主提到入场价格，请列出。
   - 入场点位1：第一个入场价格
   - 入场点位2：第二个入场价格（如果有）
   - 入场点位3：第三个入场价格（如果有）

5. **止损点位**：如果博主提到止损价格，请列出。
   - 止损点位1：第一个止损价格
   - 止损点位2：第二个止损价格（如果有）
   - 止损点位3：第三个止损价格（如果有）

6. **止盈点位**：如果博主提到止盈价格，请列出。
   - 止盈点位1：第一个止盈价格
   - 止盈点位2：第二个止盈价格（如果有）
   - 止盈点位3：第三个止盈价格（如果有）

7. **分析内容**：提取并总结博主针对该币种的市场分析和走势预测。

内容如下：
{content}

请以JSON格式返回分析结果，格式如下：
{{
    "交易币种": "币种名称",
    "方向": "交易方向",
    "杠杆": 杠杆倍数或null,
    "入场点位1": 数字或null,
    "入场点位2": 数字或null,
    "入场点位3": 数字或null,
    "止损点位1": 数字或null,
    "止损点位2": 数字或null,
    "止损点位3": 数字或null,
    "止盈点位1": 数字或null,
    "止盈点位2": 数字或null,
    "止盈点位3": 数字或null,
    "分析内容": "分析文字"
}}

价格必须是数字（不含单位），未提及的信息用null。
""",

            "woods": """
请根据以下博主的交易分析内容，提取并整理出以下信息：

1. **交易币种**：提取博主提到的币种名称，最开始的几个字母一般为币种名称。

2. **方向**：提取博主的交易方向（例如：多单、空单）。

3. **杠杆**：如果博主提到杠杆，请标明杠杆倍数。

4. **入场点位**：如果博主提到入场价格，请列出。
   - 入场点位1：第一个入场价格
   - 入场点位2：第二个入场价格（如果有）
   - 入场点位3：第三个入场价格（如果有）

5. **止损点位**：如果博主提到止损价格，请列出。
   - 止损点位1：第一个止损价格
   - 止损点位2：第二个止损价格（如果有）
   - 止损点位3：第三个止损价格（如果有）

6. **止盈点位**：如果博主提到止盈价格，请列出。
   - 止盈点位1：第一个止盈价格
   - 止盈点位2：第二个止盈价格（如果有）
   - 止盈点位3：第三个止盈价格（如果有）

7. **分析内容**：提取并总结博主针对该币种的市场分析和走势预测。

内容如下：
{content}

请以JSON格式返回分析结果，格式如下：
{{
    "交易币种": "币种名称",
    "方向": "交易方向",
    "杠杆": 杠杆倍数或null,
    "入场点位1": 数字或null,
    "入场点位2": 数字或null,
    "入场点位3": 数字或null,
    "止损点位1": 数字或null,
    "止损点位2": 数字或null,
    "止损点位3": 数字或null,
    "止盈点位1": 数字或null,
    "止盈点位2": 数字或null,
    "止盈点位3": 数字或null,
    "分析内容": "分析文字"
}}

价格必须是数字（不含单位），未提及的信息用null。
""",

            "Dr profit": """
请根据以下博主的交易分析内容，提取并整理出以下信息：

1. **交易币种**：提取博主提到的币种名称，最开始的几个字母一般为币种名称。

2. **方向**：提取博主的交易方向（例如：多单、空单）。

3. **杠杆**：如果博主提到杠杆，请标明杠杆倍数。

4. **入场点位**：如果博主提到入场价格，请列出。
   - 入场点位1：第一个入场价格
   - 入场点位2：第二个入场价格（如果有）
   - 入场点位3：第三个入场价格（如果有）

5. **止损点位**：如果博主提到止损价格，请列出。
   - 止损点位1：第一个止损价格
   - 止损点位2：第二个止损价格（如果有）
   - 止损点位3：第三个止损价格（如果有）

6. **止盈点位**：如果博主提到止盈价格，请列出。
   - 止盈点位1：第一个止盈价格
   - 止盈点位2：第二个止盈价格（如果有）
   - 止盈点位3：第三个止盈价格（如果有）

7. **分析内容**：提取并总结博主针对该币种的市场分析和走势预测。

内容如下：
{content}

请以JSON格式返回分析结果，格式如下：
{{
    "交易币种": "币种名称",
    "方向": "交易方向",
    "杠杆": 杠杆倍数或null,
    "入场点位1": 数字或null,
    "入场点位2": 数字或null,
    "入场点位3": 数字或null,
    "止损点位1": 数字或null,
    "止损点位2": 数字或null,
    "止损点位3": 数字或null,
    "止盈点位1": 数字或null,
    "止盈点位2": 数字或null,
    "止盈点位3": 数字或null,
    "分析内容": "分析文字"
}}

价格必须是数字（不含单位），未提及的信息用null。
""",

            "Rose": """
请根据以下博主的交易分析内容，提取并整理出以下信息：

1. **交易币种**：提取博主提到的币种名称，最开始的几个字母一般为币种名称。

2. **方向**：提取博主的交易方向（例如：多单、空单）。

3. **杠杆**：如果博主提到杠杆，请标明杠杆倍数。

4. **入场点位**：如果博主提到入场价格，请列出。
   - 入场点位1：第一个入场价格
   - 入场点位2：第二个入场价格（如果有）
   - 入场点位3：第三个入场价格（如果有）

5. **止损点位**：如果博主提到止损价格，请列出。
   - 止损点位1：第一个止损价格
   - 止损点位2：第二个止损价格（如果有）
   - 止损点位3：第三个止损价格（如果有）

6. **止盈点位**：如果博主提到止盈价格，请列出。
   - 止盈点位1：第一个止盈价格
   - 止盈点位2：第二个止盈价格（如果有）
   - 止盈点位3：第三个止盈价格（如果有）

7. **分析内容**：提取并总结博主针对该币种的市场分析和走势预测。

内容如下：
{content}

请以JSON格式返回分析结果，格式如下：
{{
    "交易币种": "币种名称",
    "方向": "交易方向",
    "杠杆": 杠杆倍数或null,
    "入场点位1": 数字或null,
    "入场点位2": 数字或null,
    "入场点位3": 数字或null,
    "止损点位1": 数字或null,
    "止损点位2": 数字或null,
    "止损点位3": 数字或null,
    "止盈点位1": 数字或null,
    "止盈点位2": 数字或null,
    "止盈点位3": 数字或null,
    "分析内容": "分析文字"
}}

价格必须是数字（不含单位），未提及的信息用null。
"""
        }


        # 默认的消息筛选规则
        self.default_filter = {
            "min_length": 10,
            "price_indicators": ['$', '美元', 'k', 'K', '千', '万'],
            "trading_keywords": ['多', '空', '做多', '做空', '买入', '卖出', '止损', '止盈', 
                               'long', 'short', 'buy', 'sell', 'stop', 'target'],
            "required_keywords": [],
            "excluded_keywords": []
        }
        
        # 添加这个：各频道的特定筛选规则
        self.channel_filters = {
            # 如果某个频道需要特殊的筛选规则，可以在这里添加
            # 例如：
            # "channel_name": { ... }
        }

    def _extract_translated_content(self, content: str) -> Tuple[str, str]:
        """提取原文和翻译内容"""
        try:
            if "**原文:**" in content and "**翻译:**" in content:
                parts = content.split("**翻译:**")
                if len(parts) >= 2:
                    original = parts[0].replace("**原文:**", "").strip()
                    translated = parts[1].split("--------------")[0].strip()
                    return original, translated
            return content, content
        except Exception as e:
            print(f"提取翻译内容时出错: {str(e)}")
            return content, content

    def should_analyze_message(self, msg: Dict, channel_name: str = None) -> bool:
        """判断消息是否需要分析"""
        if not msg.get('content'):
            return False
            
        content = msg['content']
        
        # 获取对应频道的筛选规则，如果没有则使用默认规则
        filter_rules = self.channel_filters.get(channel_name, self.default_filter)
        
        # 检查消息长度
        if len(content.strip()) < filter_rules["min_length"]:
            return False
            
        # 检查是否包含需要排除的关键词
        if any(keyword in content.lower() for keyword in filter_rules["excluded_keywords"]):
            return False
            
        # 检查是否包含必需的关键词（如果有设置）
        if filter_rules["required_keywords"] and not any(keyword in content for keyword in filter_rules["required_keywords"]):
            return False
            
        # 检查是否包含价格相关信息
        has_price = any(indicator in content for indicator in filter_rules["price_indicators"])
        
        # 检查是否包含交易相关词汇
        has_trading_terms = any(keyword in content.lower() for keyword in filter_rules["trading_keywords"])
        
        return has_price or has_trading_terms

    def analyze_message(self, content: str, channel_name: str = None, retry_count: int = 3) -> Optional[Dict]:
        """分析单条消息"""
        original, translated = self._extract_translated_content(content)
        
        # 使用翻译内容进行分析
        content_to_analyze = translated or original
        
        if not content_to_analyze or len(content_to_analyze.strip()) < 10:
            return None
            
        # 选择对应的提示词
        prompt = self.channel_prompts.get(channel_name, self.default_prompt)
        messages = [{"role": "user", "content": prompt.format(content=content_to_analyze)}]
        
        for attempt in range(retry_count):
            try:
                print(f"正在使用{channel_name if channel_name else '默认'}提示词分析消息: {content_to_analyze[:100]}...")
                
                response = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers=self.headers,
                    json={
                        "model": "deepseek-ai/DeepSeek-V3",
                        "messages": messages,
                        "max_tokens": 1024,
                        "temperature": 0.7
                    },
                    timeout=30
                )
                response.raise_for_status()
                result = response.json()
                
                if 'choices' in result and len(result['choices']) > 0:
                    content = result['choices'][0]['message']['content']
                    try:
                        # 清理返回的内容，移除markdown标记
                        cleaned_content = content.replace('```json', '').replace('```', '').strip()
                        parsed_result = json.loads(cleaned_content)
                        print("分析成功！")
                        # 添加原文和翻译到结果中
                        parsed_result['原文'] = original
                        parsed_result['翻译'] = translated
                        return parsed_result
                    except json.JSONDecodeError as e:
                        print(f"JSON解析失败: {content}")
                        print(f"错误详情: {str(e)}")
                        return None
                        
            except requests.exceptions.RequestException as e:
                print(f"API请求失败 (尝试 {attempt + 1}/{retry_count}): {str(e)}")
                if attempt < retry_count - 1:
                    time.sleep(2 ** attempt)
            except Exception as e:
                print(f"未知错误 (尝试 {attempt + 1}/{retry_count}): {str(e)}")
                if attempt < retry_count - 1:
                    time.sleep(2 ** attempt)
        
        return None

    def process_message_files(self, data_dir: str, output_dir: str):
        """处理所有消息文件"""
        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)
        
        # 获取所有JSON文件
        json_files = list(Path(data_dir).glob("*.json"))
        total_files = len(json_files)
        
        if not json_files:
            print(f"警告：在目录 {data_dir} 中没有找到JSON文件")
            return
        
        all_results = []
        processed_messages = 0
        skipped_messages = 0
        
        # 创建时间戳，用于文件命名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        results_file = os.path.join(output_dir, f"analysis_results_{timestamp}.json")
        excel_file = os.path.join(output_dir, f"analysis_data_{timestamp}.xlsx")
        
        # 用于存储每个频道的结果
        channel_results = {}
        
        for i, file_path in enumerate(json_files, 1):
            print(f"\n处理文件 {i}/{total_files}: {file_path.name}")
            
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                channel_name = self._extract_channel_name(file_path.name)
                print(f"频道名称: {channel_name}")
                
                if isinstance(data, list):  # 如果文件内容直接是消息数组
                    messages = data
                elif isinstance(data, dict) and 'messages' in data:  # 如果消息在messages字段中
                    messages = data['messages']
                else:
                    print(f"警告：文件 {file_path.name} 格式不正确")
                    continue
                    
                print(f"发现 {len(messages)} 条消息")
                
                # 确保该频道在字典中有一个列表
                if channel_name not in channel_results:
                    channel_results[channel_name] = []
                
                for j, msg in enumerate(messages, 1):
                    processed_messages += 1
                    
                    if not self.should_analyze_message(msg, channel_name):
                        skipped_messages += 1
                        print(f"跳过消息 {j}: 不符合分析条件")
                        continue
                    
                    print(f"\n处理消息 {j}/{len(messages)}")
                    result = self.analyze_message(msg.get('content', ''), channel_name)
                    
                    if result:
                        # 添加元数据
                        enriched_result = {
                            'channel': channel_name,
                            'timestamp': msg.get('timestamp'),
                            'message_id': msg.get('id'),
                            'author': msg.get('author'),
                            'author_id': msg.get('author_id'),
                            'attachments': msg.get('attachments', []),
                            'analysis': result
                        }
                        channel_results[channel_name].append(enriched_result)
                        all_results.append(enriched_result)
                        
                        # 每处理完一条消息就更新该频道的文件
                        self._save_channel_results(channel_results, output_dir)
                    
                print(f"文件 {file_path.name} 分析完成，成功分析 {len(channel_results[channel_name])} 条消息")
                
            except Exception as e:
                print(f"处理文件时出错 {file_path}: {str(e)}")
            
        print(f"\n处理完成:")
        print(f"处理了 {total_files} 个文件")
        print(f"处理了 {processed_messages} 条消息")
        print(f"跳过了 {skipped_messages} 条消息")
        print(f"成功分析了 {len(all_results)} 条消息")
        
        # 最终生成统计报告
        if all_results:
            self._generate_report(all_results, output_dir)
        else:
            print("警告：没有成功分析任何消息")

    def _extract_channel_name(self, filename: str) -> str:
        """从文件名提取频道名称"""
        parts = filename.split('-')
        if len(parts) >= 2:
            return '-'.join(parts[1:]).replace('.json', '')
        return filename.replace('.json', '')

    def _save_channel_results(self, channel_results: Dict[str, List[Dict]], output_dir: str):
        """保存每个频道的分析结果"""
        try:
            # 保存每个频道的结果到对应的JSON文件
            for channel_name, results in channel_results.items():
                channel_file = os.path.join(output_dir, f"{channel_name}_results.json")
                with open(channel_file, 'w', encoding='utf-8') as f:
                    json.dump(results, f, ensure_ascii=False, indent=2)
                
            # 保存所有结果到Excel文件
            all_results = []
            for results in channel_results.values():
                all_results.extend(results)
                
            if all_results:
                excel_file = os.path.join(output_dir, "all_analysis_results.xlsx")
                df = pd.json_normalize(all_results)
                df.to_excel(excel_file, index=False)
                
        except Exception as e:
            print(f"保存频道结果时出错: {str(e)}")

    def _generate_report(self, results: List[Dict], output_dir: str):
        """生成分析报告"""
        if not results:
            print("警告：没有分析结果可供生成报告")
            report = {
                "总消息数": 0,
                "频道统计": {},
                "每日消息数": {},
                "币种统计": {},
                "交易方向统计": {}
            }
        else:
            # 转换为DataFrame
            df = pd.json_normalize(results)
            
            # 处理可能的列表类型值
            def safe_value_counts(series):
                # 将列表类型的值转换为字符串
                processed_series = series.apply(lambda x: str(x) if isinstance(x, list) else x)
                return processed_series.value_counts().to_dict()
            
            # 基础统计
            report = {
                "总消息数": len(results),
                "频道统计": safe_value_counts(df['channel']) if 'channel' in df.columns else {},
                "每日消息数": df['timestamp'].str[:10].value_counts().to_dict() if 'timestamp' in df.columns else {},
                "币种统计": safe_value_counts(df['analysis.交易币种']) if 'analysis.交易币种' in df.columns else {},
                "交易方向统计": safe_value_counts(df['analysis.方向']) if 'analysis.方向' in df.columns else {}
            }
            
            # 添加更详细的统计信息
            try:
                # 计算每个频道的消息数量趋势
                if 'timestamp' in df.columns and 'channel' in df.columns:
                    df['date'] = pd.to_datetime(df['timestamp']).dt.date
                    channel_trends = df.groupby(['channel', 'date']).size().to_dict()
                    report["频道消息趋势"] = {str(k): v for k, v in channel_trends.items()}
                
                # 计算交易方向的比例
                if 'analysis.方向' in df.columns:
                    direction_total = len(df['analysis.方向'].dropna())
                    direction_counts = safe_value_counts(df['analysis.方向'])
                    report["交易方向比例"] = {
                        k: f"{(v/direction_total*100):.2f}%" 
                        for k, v in direction_counts.items()
                    }
                
            except Exception as e:
                print(f"生成详细统计信息时出错: {str(e)}")
        
        # 保存统计报告
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = os.path.join(output_dir, f"analysis_report_{timestamp}.json")
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        
        print(f"\n分析报告已生成：{report_file}")
        
        # 打印主要统计信息
        print("\n=== 统计摘要 ===")
        print(f"总消息数: {report['总消息数']}")
        print("\n频道统计:")
        for channel, count in report['频道统计'].items():
            print(f"  {channel}: {count}条消息")
        if "交易方向比例" in report:
            print("\n交易方向比例:")
            for direction, percentage in report['交易方向比例'].items():
                print(f"  {direction}: {percentage}")

    def start_monitoring(self, path: str):
        """开始监控文件夹"""
        event_handler = MessageFileHandler(self)
        observer = Observer()
        observer.schedule(event_handler, path, recursive=False)
        observer.start()
        
        try:
            print(f"开始监控文件夹: {path}")
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
            print("监控已停止")
        
        observer.join()

def main():
    # 配置
    api_key = "sk-ztgmsyqnaahgydubxkwkdedzezbcmbndkfqqwzeclenxjbtc"
    data_dir = "data/messages"
    output_dir = "analysis_results"
    
    # 创建分析器实例
    analyzer = HistoricalMessageAnalyzer(api_key)
    
    # 创建输出目录
    os.makedirs(output_dir, exist_ok=True)
    
    # 开始监控新消息
    print(f"开始监控文件夹: {data_dir}")
    print("程序将持续运行并监控新的消息文件...")
    print("按 Ctrl+C 停止监控")
    
    try:
        analyzer.start_monitoring(data_dir)
    except KeyboardInterrupt:
        print("\n收到停止信号，程序正在退出...")
    except Exception as e:
        print(f"\n程序发生错误: {str(e)}")
    finally:
        print("监控已停止")

if __name__ == "__main__":
    main()