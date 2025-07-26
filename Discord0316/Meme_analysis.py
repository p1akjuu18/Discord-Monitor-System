#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import json
import sys
import logging
import aiohttp
from datetime import datetime, timedelta
import os
from pathlib import Path
import pandas as pd
import time
from typing import Optional, Dict, List, Any
import re
import twitter_api
import hmac
import hashlib
import base64
import requests
from feishu_bot import FeishuBot
import lark_oapi as lark
from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

# 设置日志
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

__all__ = ['MemeAnalyzer', 'BacktestProcessor', 'process_message']

class MemeAnalyzer:
    def __init__(self, config_file='config.json', api_key=None):
        self.config = self.load_config(config_file)
        self.setup_directories()
        
        # API配置
        self.base_url = self.config.get("base_url", "https://api.siliconflow.cn")
        self.api_key = api_key or self.config.get("api_keys", {}).get("deepseek")
        
        # 验证 API key 格式
        if not self.api_key:
            logger.error("Deepseek API key未设置")
            raise ValueError("Deepseek API key is required")
        elif not self.api_key.startswith("sk-"):
            logger.error("Deepseek API key 格式错误，应该以 sk- 开头")
            raise ValueError("Invalid Deepseek API key format")
            
        logger.info(f"Deepseek API key 格式验证通过")
        
        self.min_occurrence_threshold = self.config.get("min_occurrence_threshold", 2)
        self.term_history = {}
        self.history_cleanup_threshold = timedelta(hours=self.config.get("history_cleanup_threshold", 24))
        
        # 添加飞书配置
        self.feishu_webhook = self.config.get("feishu_webhook", "")
        self.feishu_secret = self.config.get("feishu_secret", "")

    def load_config(self, config_file):
        """加载配置文件"""
        try:
            with open(config_file, 'r', encoding='utf-8-sig') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载配置文件失败: {str(e)}")
            raise

    def setup_directories(self):
        """设置必要的目录"""
        self.data_dir = Path('data')
        self.data_dir.mkdir(exist_ok=True)
        
        # 设置Excel文件路径
        self.twitter_results_path = self.data_dir / 'twitter_results.xlsx'
        self.meme_path = self.data_dir / 'meme.xlsx'
        self.analysis_path = self.data_dir / 'crypto_analysis_results.xlsx'

    def send_to_feishu(self, analysis_result):
        """发送分析结果到飞书"""
        try:
            timestamp = str(int(time.time()))
            
            # 计算签名
            string_to_sign = f"{timestamp}\n{self.feishu_secret}"
            hmac_code = hmac.new(
                string_to_sign.encode("utf-8"),
                digestmod=hashlib.sha256
            ).digest()
            sign = base64.b64encode(hmac_code).decode('utf-8')
            
            # 格式化消息内容
            message = f"""🔍 Meme 币分析报告
关键词: {analysis_result['搜索关键词']}

📝 叙事信息:
{analysis_result['叙事信息']}

🌡️ 可持续性分析:
• 社区热度: {analysis_result['可持续性_社区热度']}
• 传播潜力: {analysis_result['可持续性_传播潜力']}
• 短期投机价值: {analysis_result['可持续性_短期投机价值']}

📊 数据统计:
• 原始推文数量: {analysis_result['原始推文数量']}
• 分析时间: {analysis_result['分析时间']}"""

            headers = {
                "Content-Type": "application/json"
            }
            
            payload = {
                "timestamp": timestamp,
                "sign": sign,
                "msg_type": "text",
                "content": {
                    "text": message
                }
            }
            
            response = requests.post(
                self.feishu_webhook,
                headers=headers,
                json=payload
            )
            
            if response.status_code == 200:
                logger.info("成功发送分析结果到飞书")
            else:
                logger.error(f"发送到飞书失败: {response.status_code}")
                
        except Exception as e:
            logger.error(f"发送到飞书时出错: {str(e)}")

    async def analyze_tweets(self, term: str, tweets: List[dict]) -> dict:
        """使用 Deepseek API 分析推文"""
        try:
            # 提取推文内容并清理
            tweet_texts = []
            for tweet in tweets:
                text = tweet.get('text', '').strip()
                if text:
                    # 清理合约地址
                    text = re.sub(r'[A-Za-z0-9]{32,}', '', text)
                    # 清理URL
                    text = re.sub(r'https?://\S+', '', text)
                    # 清理多余空白
                    text = ' '.join(text.split())
                    if text.strip():  # 确保清理后还有内容
                        tweet_texts.append(text)
            
            if not tweet_texts:
                logger.warning(f"清理后没有找到有效的推文内容用于分析")
                return self._get_default_analysis(term, len(tweets))
            
            # 去重
            tweet_texts = list(set(tweet_texts))
            
            # 修改认证头格式
            headers = {
                "Authorization": f"Bearer {self.api_key}",  # 确保是 Bearer 认证
                "Content-Type": "application/json",
                "Accept": "application/json"  # 添加 Accept 头
            }
            
            # 检查并记录 API key（隐藏部分内容）
            masked_key = f"{self.api_key[:6]}...{self.api_key[-4:]}" if self.api_key else "None"
            logger.info(f"使用的 API key: {masked_key}")
            
            data = {
                "model": "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B",
                "messages": [
                    {
                        "role": "user",
                        "content": f"""你是一个专业的加密货币分析师，我希望你能帮我评估这个 Meme 币的潜力，并给出详细的分析和建议，请分析以下关于加密货币的推文内容：

{chr(10).join(tweet_texts)}

请从以下两个方面分别进行分析，分2点，并用中文回答，我需要的结果不超过100字，你需要分以下2点明确的返回：

1. 叙事信息：用2-3句话总结这个meme币的核心和它的核心卖点。

2. 可持续性：从以下维度评估：
   - 社区热度
   - 传播潜力
   - 短期投机价值"""
                    }
                ],
                "stream": False,
                "temperature": 0.7,
                "max_tokens": 512,
                "top_p": 0.7,
                "top_k": 50,
                "frequency_penalty": 0.5
            }
            
            logger.info(f"发送Deepseek API请求，分析 {len(tweet_texts)} 条推文")
            
            max_retries = 3
            retry_delay = 10
            
            for attempt in range(max_retries):
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(
                            f"{self.base_url}/v1/chat/completions",
                            headers=headers,
                            json=data,
                            timeout=aiohttp.ClientTimeout(total=30)
                        ) as response:
                            response_text = await response.text()
                            logger.info(f"Deepseek API响应状态码: {response.status}")
                            logger.info(f"Deepseek API响应头: {dict(response.headers)}")
                            logger.info(f"Deepseek API请求数据: {json.dumps(data, ensure_ascii=False)}")
                            logger.info(f"Deepseek API响应内容: {response_text}")
                            
                            if response.status == 200:
                                result = json.loads(response_text)
                                if 'choices' in result and len(result['choices']) > 0:
                                    analysis = result['choices'][0]['message']['content']
                                    
                                    # 解析分析结果
                                    narrative = ""
                                    community_heat = ""
                                    spread_potential = ""
                                    investment_value = ""
                                    
                                    # 移除所有 Markdown 标记
                                    analysis = analysis.replace('**', '')
                                    
                                    # 分割主要部分
                                    parts = analysis.split('\n\n')
                                    
                                    # 解析叙事信息和可持续性评估
                                    for part in parts:
                                        if '1. 叙事信息' in part:
                                            narrative = part.replace('1. 叙事信息：', '').strip()
                                        elif '2. 可持续性' in part:
                                            lines = part.split('\n')
                                            for line in lines:
                                                line = line.strip()
                                                if '社区热度' in line:
                                                    community_heat = line.split('：')[1].strip() if '：' in line else ''
                                                elif '传播潜力' in line:
                                                    spread_potential = line.split('：')[1].strip() if '：' in line else ''
                                                elif '短期投机价值' in line:
                                                    investment_value = line.split('：')[1].strip() if '：' in line else ''
                                    
                                    result_dict = {
                                        '搜索关键词': term,
                                        '叙事信息': narrative,
                                        '可持续性_社区热度': community_heat,
                                        '可持续性_传播潜力': spread_potential,
                                        '可持续性_短期投机价值': investment_value,
                                        '原始推文数量': len(tweets),
                                        '分析时间': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                    }
                                    
                                    logger.info(f"成功完成分析: {result_dict}")
                                    
                                    # 在完成分析后发送到飞书
                                    self.send_to_feishu(result_dict)
                                    
                                    return result_dict
                                    
                            elif response.status == 400:
                                logger.error(f"Deepseek API请求参数错误: {response_text}")
                                try:
                                    error_data = json.loads(response_text)
                                    logger.error(f"错误详情: {json.dumps(error_data, ensure_ascii=False, indent=2)}")
                                except:
                                    logger.error(f"无法解析错误响应: {response_text}")
                                return self._get_default_analysis(term, len(tweets))
                            elif response.status == 401:
                                logger.error("Deepseek API认证失败，请检查API key")
                                return self._get_default_analysis(term, len(tweets))
                            elif response.status == 429:
                                logger.warning("Deepseek API速率限制")
                                return self._get_default_analysis(term, len(tweets))
                            else:
                                logger.error(f"Deepseek API请求失败: {response.status}")
                                logger.error(f"错误响应: {response_text}")
                                return self._get_default_analysis(term, len(tweets))
                                
                except Exception as e:
                    logger.error(f"调用Deepseek API时出错: {str(e)}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                        continue
                    
            return self._get_default_analysis(term, len(tweets))
            
        except Exception as e:
            logger.error(f"分析推文时发生错误: {str(e)}")
            logger.exception(e)
            return self._get_default_analysis(term, len(tweets))

    def _get_default_analysis(self, term: str, tweet_count: int) -> dict:
        """返回默认的分析结果"""
        return {
            '搜索关键词': term,
            '叙事信息': f'API认证失败，无法分析。共有{tweet_count}条推文',
            '可持续性_社区热度': '未知',
            '可持续性_传播潜力': '未知',
            '可持续性_短期投机价值': '未知',
            '原始推文数量': tweet_count,
            '分析时间': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

    async def save_analysis_results(self, analysis_results: List[dict]):
        """保存分析结果到Excel"""
        try:
            # 如果文件已存在，读取现有数据并追加
            if self.analysis_path.exists():
                existing_df = pd.read_excel(self.analysis_path)
                new_df = pd.DataFrame(analysis_results)
                df = pd.concat([existing_df, new_df], ignore_index=True)
            else:
                df = pd.DataFrame(analysis_results)
            
            # 确保所有列都存在
            expected_columns = [
                '搜索关键词', 
                '叙事信息', 
                '可持续性_社区热度', 
                '可持续性_传播潜力', 
                '可持续性_短期投机价值',
                '原始推文数量',
                '分析时间'
            ]
            
            for col in expected_columns:
                if col not in df.columns:
                    df[col] = ''
            
            # 保存到Excel
            df.to_excel(self.analysis_path, index=False)
            logger.info(f"分析结果已保存至: {self.analysis_path}")
            logger.info(f"保存的数据行数: {len(df)}")
            logger.info(f"保存的数据内容: {analysis_results}")
            
        except Exception as e:
            logger.error(f"保存分析结果时出错: {e}")
            logger.exception(e)

    async def process_history_file(self):
        """处理历史数据文件"""
        try:
            logger.info("开始处理meme.xlsx历史数据")
            
            if not self.meme_path.exists():
                logger.error("meme.xlsx文件不存在")
                return
                
            df = pd.read_excel(self.meme_path)
            logger.info(f"加载了 {len(df)} 条历史记录")
            
            # 检查必要的列是否存在
            if '内容' not in df.columns:
                logger.error("meme.xlsx文件缺少'内容'列")
                return
                
            # 处理每一行数据
            for _, row in df.iterrows():
                content = row['内容']
                logger.info(f"处理关键词: {content}")
                
                # 搜索Twitter
                tweets = await twitter_api.search_tweets(content)
                logger.info(f"找到 {len(tweets)} 条相关推文")
                
                if tweets:
                    # 分析推文
                    analysis = await self.analyze_tweets(content, tweets)
                    if analysis:
                        await self.save_analysis_results([analysis])
                        logger.info(f"已保存关键词 '{content}' 的分析结果")
                    else:
                        logger.warning(f"关键词 '{content}' 的分析结果为空")
                else:
                    logger.warning(f"关键词 '{content}' 没有找到相关推文")
            
            logger.info("历史数据处理完成")
            
        except Exception as e:
            logger.error(f"处理历史数据文件时出错: {str(e)}")
            logger.exception(e)

class BacktestProcessor:
    def __init__(self):
        self.data_dir = Path('data')
        self.data_dir.mkdir(exist_ok=True)
        self.meme_path = self.data_dir / 'meme.xlsx'
        self.twitter_results_path = self.data_dir / 'twitter_results.xlsx'
        
    async def save_meme_data(self, meme_data):
        """保存meme数据到Excel"""
        try:
            if self.meme_path.exists():
                df_meme = pd.read_excel(self.meme_path)
                df_meme = pd.concat([df_meme, pd.DataFrame(meme_data)], ignore_index=True)
            else:
                df_meme = pd.DataFrame(meme_data)
            
            df_meme.to_excel(self.meme_path, index=False)
            logger.info(f"成功保存 {len(meme_data)} 条meme数据到Excel")
        except Exception as e:
            logger.error(f"保存meme数据时出错: {e}")

# 创建全局实例
processor = BacktestProcessor()

async def process_message(message_data: Dict[str, Any]) -> None:
    """处理来自Discord的消息"""
    try:
        # 提取处理好的数据
        meme_data = message_data.get('meme_data', [])
        search_terms = message_data.get('search_terms', [])
        
        # 保存meme数据
        if meme_data:
            await processor.save_meme_data(meme_data)
            
    except Exception as e:
        logger.error(f"处理消息数据时出错: {str(e)}")

async def main():
    try:
        analyzer = MemeAnalyzer()
        await analyzer.process_history_file()
        
        # 添加文件监控
        monitor = MemeAnalysisMonitor()
        # 创建新线程运行监控，这样不会阻塞主程序
        import threading
        monitor_thread = threading.Thread(
            target=monitor.monitor_analysis_file,
            args=(5,),  # 每5秒检查一次
            daemon=True
        )
        monitor_thread.start()
        
    except Exception as e:
        logger.error(f"运行时发生错误: {str(e)}")
        logger.exception(e)

# 只在直接运行时执行main函数
if __name__ == '__main__':
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())

class MemeAnalysisMonitor:
    def __init__(self):
        # 初始化飞书客户端
        self.client = lark.Client.builder() \
            .app_id("cli_a736cea2ff78100d") \
            .app_secret("C9FsC6CnJz3CLf0PEz0NQewkuH6uvCdS") \
            .log_level(lark.LogLevel.DEBUG) \
            .build()
            
        self.alert_chat_id = "oc_a2d2c5616c900bda2ab8e13a77361287"
        self.data_dir = Path('data')
        self.analysis_file = self.data_dir / 'crypto_analysis_results.xlsx'
        self.last_modified_time = None
        self.processed_rows = set()

    def send_message(self, message: str) -> bool:
        """使用API方式发送消息到飞书群"""
        try:
            # 构造消息内容
            content = json.dumps({"text": message})
            
            # 构造请求对象
            request = CreateMessageRequest.builder() \
                .receive_id_type("chat_id") \
                .request_body(CreateMessageRequestBody.builder()
                    .receive_id(self.alert_chat_id)
                    .msg_type("text")
                    .content(content)
                    .build()) \
                .build()

            # 发送消息
            response = self.client.im.v1.message.create(request)

            # 处理响应
            if not response.success():
                logging.error(
                    f"发送消息失败，错误码: {response.code}, "
                    f"错误信息: {response.msg}, "
                    f"日志ID: {response.get_log_id()}"
                )
                return False

            logging.info("消息发送成功")
            return True

        except Exception as e:
            logging.error(f"发送消息时发生错误: {str(e)}")
            logging.error(f"异常堆栈: {traceback.format_exc()}")
            return False

    def monitor_analysis_file(self, interval: int = 5):
        """
        监控 crypto_analysis_results.xlsx 文件的更新
        :param interval: 检查间隔（秒）
        """
        logging.info(f"开始监控文件: {self.analysis_file}")
        
        while True:
            try:
                if not self.analysis_file.exists():
                    logging.warning("分析结果文件不存在")
                    time.sleep(interval)
                    continue

                current_mtime = os.path.getmtime(self.analysis_file)
                
                # 检查文件是否更新
                if self.last_modified_time is None or current_mtime > self.last_modified_time:
                    logging.info("检测到文件更新，处理新数据...")
                    self._process_new_data()
                    self.last_modified_time = current_mtime
                
                time.sleep(interval)
                
            except Exception as e:
                logging.error(f"监控文件时发生错误: {str(e)}")
                time.sleep(interval)

    def _process_new_data(self):
        """处理新的分析数据"""
        try:
            df = pd.read_excel(self.analysis_file)
            
            # 处理每一行新数据
            for _, row in df.iterrows():
                # 创建行的唯一标识（使用搜索关键词和分析时间的组合）
                row_id = f"{row['搜索关键词']}_{row['分析时间']}"
                
                # 如果这行数据已经处理过，跳过
                if row_id in self.processed_rows:
                    continue
                
                # 构造预警消息
                alert_msg = (
                    f"🔍 新的Meme币分析结果\n\n"
                    f"📌 关键词: {row['搜索关键词']}\n"
                    f"📝 叙事信息: {row['叙事信息']}\n\n"
                    f"🌡️ 可持续性分析:\n"
                    f"• 社区热度: {row['可持续性_社区热度']}\n"
                    f"• 传播潜力: {row['可持续性_传播潜力']}\n"
                    f"• 短期投机价值: {row['可持续性_短期投机价值']}\n\n"
                    f"📊 数据统计:\n"
                    f"• 原始推文数量: {row['原始推文数量']}\n"
                    f"• 分析时间: {row['分析时间']}"
                )
                
                # 发送预警
                if self.send_message(alert_msg):
                    logging.info(f"已发送新分析结果预警: {row['搜索关键词']}")
                    # 标记该行数据为已处理
                    self.processed_rows.add(row_id)
                else:
                    logging.error(f"发送预警失败: {row['搜索关键词']}")
                
                # 如果已处理的行数过多，清理旧数据
                if len(self.processed_rows) > 1000:
                    self.processed_rows = set(list(self.processed_rows)[-500:])
                
        except Exception as e:
            logging.error(f"处理新数据时发生错误: {str(e)}")