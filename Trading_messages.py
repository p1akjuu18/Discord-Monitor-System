import json
import os
from datetime import datetime
import requests
from typing import List, Dict, Optional, Tuple, Any, Union
from pathlib import Path
import pandas as pd
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import traceback
import re
import gc
import shutil
import subprocess
import sys
import uuid
import logging
import threading
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
import hashlib
import tempfile
from io import StringIO
import numpy as np
import glob

# 配置日志系统
def setup_logging():
    """配置日志系统"""
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    
    # 清理旧日志文件，防止磁盘空间不足
    try:
        # 保留最近7天的日志
        log_files = [f for f in os.listdir(log_dir) if f.endswith('.log')]
        if len(log_files) > 10:  # 如果日志文件超过10个
            log_files.sort()  # 按文件名排序（通常包含日期）
            for old_file in log_files[:-7]:  # 删除最旧的，只保留最新的7个
                try:
                    os.remove(os.path.join(log_dir, old_file))
                    print(f"已删除旧日志文件: {old_file}")
                except Exception as e:
                    print(f"删除日志文件失败: {e}")
    except Exception as e:
        print(f"清理日志文件时出错: {e}")
    
    # 检查磁盘空间
    try:
        import shutil
        total, used, free = shutil.disk_usage("/")
        free_gb = free / (1024**3)
        if free_gb < 1.0:  # 如果剩余空间小于1GB
            print(f"警告: 磁盘空间不足! 剩余: {free_gb:.2f}GB")
            # 创建紧急备用日志目录在内存中（使用临时目录）
            log_dir = tempfile.gettempdir()
            print(f"切换到临时日志目录: {log_dir}")
    except Exception as e:
        print(f"检查磁盘空间时出错: {e}")
    
    # 创建安全的日志处理器工厂
    def create_file_handler():
        try:
            return logging.FileHandler(
                os.path.join(log_dir, f"trading_messages_{datetime.now().strftime('%Y%m%d')}.log"), 
                encoding='utf-8'
            )
        except Exception as e:
            print(f"创建文件日志处理器失败: {e}")
            # 返回一个内存中的StringIO日志处理器
            class StringIOHandler(logging.StreamHandler):
                def __init__(self):
                    self.strio = StringIO()
                    super().__init__(self.strio)
                def flush(self):
                    pass  # 防止内存字符串IO发生异常
            return StringIOHandler()
    
    # 创建日志格式
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    # 配置根日志记录器
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[
            create_file_handler(),
            logging.StreamHandler()
        ]
    )
    
    # 获取当前模块的日志记录器
    logger = logging.getLogger(__name__)
    return logger

# 初始化日志记录器
logger = setup_logging()

# 添加关闭Excel连接的函数
def close_excel_connections():
    """尝试关闭所有Excel连接并释放文件锁"""
    try:
        if sys.platform == 'win32':
            # 尝试强制结束Excel进程
            subprocess.run(['taskkill', '/F', '/IM', 'EXCEL.EXE'], 
                           stdout=subprocess.DEVNULL, 
                           stderr=subprocess.DEVNULL, 
                           check=False)
            # 强制垃圾回收
            gc.collect()
            # 等待系统释放文件
            time.sleep(1)
            logger.info("已关闭所有Excel连接")
    except Exception as e:
        logger.error(f"关闭Excel连接时出错: {e}")

def get_output_dir():
    """获取统一的输出目录"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(current_dir, "data", "analysis_results")
    os.makedirs(output_dir, exist_ok=True)
    return output_dir

class MessageFileHandler(FileSystemEventHandler):
    def __init__(self, analyzer):
        self.analyzer = analyzer
        self.processed_files = set()  # 保存已处理过的文件路径
        self.processed_message_ids = set()  # 保存已处理过的消息ID
        self.processed_content_hashes = set()  # 保存已处理过的消息内容哈希
        self.last_processed_time = {}  # 记录每个文件最后处理的时间
        self.processing_lock = threading.Lock()  # 添加线程锁防止并发处理同一文件
        self.current_processing_file = None  # 当前正在处理的文件
        # 防止集合过大，限制大小
        self.max_processed_items = 1000
        # 添加文件过滤规则
        self.skip_files = ["1283359910788202499-土狗博主群ca.json"]
        logger.info("消息处理器已初始化")
        
    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith('.json'):
            logger.info(f"检测到新文件: {event.src_path}")
            self._safe_process_file(event.src_path, "created")
            
    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith('.json'):
            logger.info(f"检测到文件修改: {event.src_path}")
            self._safe_process_file(event.src_path, "modified")
    
    def _safe_process_file(self, file_path, event_type):
        """使用线程锁安全处理文件"""
        # 检查是否需要跳过的文件
        file_name = os.path.basename(file_path)
        if any(skip_file in file_path for skip_file in self.skip_files):
            logger.info(f"跳过处理文件: {file_path}")
            return

        # 检查磁盘空间
        try:
            total, used, free = shutil.disk_usage("/")
            free_gb = free / (1024**3)
            if free_gb < 0.5:  # 如果剩余空间小于500MB
                logger.warning(f"磁盘空间不足! 剩余: {free_gb:.2f}GB，暂停文件处理")
                # 自动清理一些日志文件以释放空间
                self._cleanup_old_logs()
                self._cleanup_old_analysis_files()
                # 重新检查空间
                total, used, free = shutil.disk_usage("/")
                free_gb = free / (1024**3)
                if free_gb < 0.2:  # 如果仍然小于200MB
                    logger.error("磁盘空间危急，无法继续处理文件!")
                    return
        except Exception as e:
            logger.error(f"检查磁盘空间时出错: {str(e)}")

        # 检查是否最近处理过（5秒内）
        current_time = time.time()
        if file_path in self.last_processed_time:
            time_diff = current_time - self.last_processed_time[file_path]
            if time_diff < 5:  # 5秒内的重复处理会被忽略
                logger.info(f"文件 {file_path} 在 {time_diff:.2f} 秒前刚处理过，跳过此次 {event_type} 事件")
                return
        
        # 检查文件是否已在处理中
        if not self.processing_lock.acquire(blocking=False):
            logger.warning(f"文件 {file_path} 正在被处理中，跳过")
            return
            
        try:
            # 记录当前处理的文件
            self.current_processing_file = file_path
            
            # 确保文件写入完成
            time.sleep(2)  # 增加等待时间，确保文件完全写入
            
            # 记录最后处理时间
            self.last_processed_time[file_path] = current_time
            
            # 处理文件
            self.process_file(file_path)
            
        except OSError as ose:
            if "No space left on device" in str(ose):
                logger.critical("磁盘空间已用尽! 尝试紧急清理...")
                self._cleanup_old_logs(emergency=True)
                self._cleanup_old_analysis_files(emergency=True)
                logger.warning("紧急清理完成，请检查磁盘空间")
            else:
                logger.error(f"处理文件时发生OS错误: {str(ose)}")
                traceback.print_exc()
        except Exception as e:
            logger.error(f"处理文件时发生未捕获异常: {str(e)}")
            traceback.print_exc()
        finally:
            self.current_processing_file = None
            self.processing_lock.release()
    
    def _cleanup_old_logs(self, emergency=False):
        """清理旧日志文件"""
        try:
            log_dir = "logs"
            if os.path.exists(log_dir):
                log_files = [os.path.join(log_dir, f) for f in os.listdir(log_dir) if f.endswith('.log')]
                log_files.sort(key=os.path.getmtime)  # 按修改时间排序
                
                # 正常清理保留最近7天，紧急清理只保留最近2天
                keep_count = 2 if emergency else 7
                if len(log_files) > keep_count:
                    for old_file in log_files[:-keep_count]:
                        try:
                            os.remove(old_file)
                            logger.info(f"已删除旧日志文件: {old_file}")
                        except Exception as e:
                            logger.error(f"删除日志文件失败: {e}")
        except Exception as e:
            logger.error(f"清理日志文件时出错: {e}")
    
    def _cleanup_old_analysis_files(self, emergency=False):
        """清理旧分析结果文件"""
        try:
            analysis_dir = "data/analysis_logs"
            if os.path.exists(analysis_dir):
                for root, dirs, files in os.walk(analysis_dir):
                    # 跳过最近的文件夹
                    if any(recent in root for recent in ['recent', 'latest']):
                        continue
                        
                    log_files = [os.path.join(root, f) for f in files if f.endswith('.json') or f.endswith('.txt')]
                    log_files.sort(key=os.path.getmtime)  # 按修改时间排序
                    
                    # 正常清理保留最近50个，紧急清理只保留最近10个
                    keep_count = 10 if emergency else 50
                    if len(log_files) > keep_count:
                        for old_file in log_files[:-keep_count]:
                            try:
                                os.remove(old_file)
                                if emergency:
                                    logger.info(f"紧急清理: 已删除旧分析文件: {old_file}")
                            except Exception as e:
                                logger.error(f"删除分析文件失败: {e}")
        except Exception as e:
            logger.error(f"清理分析文件时出错: {e}")
    
    def process_file(self, file_path):
        try:
            # 跳过土狗博主群ca.json文件
            if any(skip_file in file_path for skip_file in self.skip_files):
                logger.info(f"跳过处理文件: {file_path}")
                return
                
            logger.info(f"开始处理文件: {file_path}")
            
            # 文件完整性检查
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                logger.warning(f"文件 {file_path} 为空，跳过处理")
                return
            
            # 降低旧文件的时间限制，允许处理更多文件    
            file_mtime = os.path.getmtime(file_path)
            current_time = time.time()
            file_age = current_time - file_mtime
            
            if file_age > 600:  # 增加到10分钟，确保能处理大部分文件
                logger.info(f"文件 {file_path} 最后修改时间是 {file_age:.1f}秒前，可能是旧文件，跳过处理")
                return
                
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    logger.info("成功读取JSON文件")
            except json.JSONDecodeError as je:
                logger.error(f"JSON解析错误，可能文件未完全写入: {str(je)}")
                return
            
            # 从文件名中提取频道名称
            channel_name = os.path.basename(file_path).split('-')[1].replace('.json', '')
            logger.info(f"处理频道: {channel_name}")
            
            # 判断数据结构类型并获取消息
            messages = data if isinstance(data, list) else data.get('messages', [])
            
            if not messages:
                logger.warning(f"文件 {file_path} 中没有找到消息数据")
                return
                
            # 获取最后更新的消息
            latest_message = messages[-1]
            message_id = latest_message.get('id')
            
            # 检查是否已处理过该消息ID
            if message_id and message_id in self.processed_message_ids:
                logger.info(f"消息ID {message_id} 已处理过，跳过")
                return
                
            content = latest_message.get('content', '')
            
            if not content:
                logger.warning("最新消息内容为空，跳过处理")
                return
                
            # 计算内容哈希，检查是否处理过相同内容
            content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()
            if content_hash in self.processed_content_hashes:
                logger.info(f"相同内容已处理过，跳过 (哈希值: {content_hash[:8]}...)")
                
                # 添加消息ID到已处理集合，防止重复处理
                if message_id:
                    self.processed_message_ids.add(message_id)
                return
                
            # 使用预处理函数检查消息是否需要分析
            try:
                should_analyze = self.analyzer.should_analyze_message({'content': content}, channel_name)
            except Exception as e:
                logger.error(f"检查消息是否需要分析时出错: {str(e)}")
                # 出错时默认为不需要分析
                should_analyze = False
                
            if not should_analyze:
                logger.info(f"消息内容不符合分析条件，跳过")
                
                # 即使跳过也记录已处理
                if message_id:
                    self.processed_message_ids.add(message_id)
                self.processed_content_hashes.add(content_hash)
                return
                
            logger.info(f"最新消息内容长度: {len(content)}")
            logger.debug(f"消息内容预览: {content[:100]}...")
            logger.info("开始调用 DeepSeek API 进行分析...")
            
            # 调用 DeepSeek API 分析消息，使用线程池
            max_retries = 3
            retry_count = 0
            result = None
            
            while retry_count < max_retries and result is None:
                try:
                    result = self.analyzer.analyze_message(content, channel_name)
                except Exception as e:
                    retry_count += 1
                    logger.error(f"API分析失败 (尝试 {retry_count}/{max_retries}): {str(e)}")
                    if retry_count < max_retries:
                        time.sleep(2 ** retry_count)  # 指数退避
                    else:
                        logger.error("达到最大重试次数，放弃处理")
                        # 即使分析失败也记录ID和哈希，避免重复处理出错
                        if message_id:
                            self.processed_message_ids.add(message_id)
                        self.processed_content_hashes.add(content_hash)
            
            # 处理API分析结果
            if result:
                logger.info("DeepSeek API 分析成功!")
                
                # 添加元数据
                enriched_result = {
                    'channel': channel_name,
                    'timestamp': latest_message.get('timestamp'),
                    'message_id': latest_message.get('id'),
                    'author': latest_message.get('author'),
                    'author_id': latest_message.get('author_id'),
                    'attachments': latest_message.get('attachments', []),
                    'analysis': result
                }
                
                try:
                    # 保存结果
                    output_dir = get_output_dir()
                    
                    # 保存到频道特定的JSON文件
                    self._save_json_result(enriched_result, output_dir, channel_name)
                    
                    # 更新Excel文件
                    self._update_excel_files(enriched_result, output_dir)
                except Exception as save_err:
                    logger.error(f"保存分析结果时出错: {str(save_err)}")
                    traceback.print_exc()
                
                # 记录已处理的消息ID和内容哈希
                if message_id:
                    self.processed_message_ids.add(message_id)
                self.processed_content_hashes.add(content_hash)
                
            else:
                logger.error("DeepSeek API 分析失败或返回空结果")
                
                # 即使分析失败也记录ID和哈希，避免重复处理
                if message_id:
                    self.processed_message_ids.add(message_id)
                self.processed_content_hashes.add(content_hash)
            
            logger.info(f"文件处理完成: {file_path}")
            
            # 定期打印统计信息
            if len(self.processed_message_ids) % 10 == 0:
                logger.info(f"处理统计: 已处理 {len(self.processed_message_ids)} 条消息, {len(self.processed_content_hashes)} 种内容")
            
            # 限制集合大小，防止内存泄漏
            if len(self.processed_message_ids) > self.max_processed_items:
                logger.info(f"清理处理记录缓存，当前大小: {len(self.processed_message_ids)}")
                # 只保留最新的一半项目
                self.processed_message_ids = set(list(self.processed_message_ids)[-self.max_processed_items//2:])
                self.processed_content_hashes = set(list(self.processed_content_hashes)[-self.max_processed_items//2:])
            
        except Exception as e:
            logger.error(f"处理文件时出错 {file_path}: {str(e)}")
            traceback.print_exc()
            # 记录已处理的消息ID和内容哈希，防止重复失败
            if 'message_id' in locals() and message_id:
                self.processed_message_ids.add(message_id)
            if 'content_hash' in locals() and content_hash:
                self.processed_content_hashes.add(content_hash)
    
    def _save_json_result(self, enriched_result, output_dir, channel_name):
        """保存JSON结果到文件"""
        try:
            # 保存到频道特定的JSON文件
            channel_file = os.path.join(output_dir, f"{channel_name}_results.json")
            channel_results = []
            
            # 如果文件已存在，读取现有结果
            if os.path.exists(channel_file):
                try:
                    with open(channel_file, 'r', encoding='utf-8') as f:
                        channel_results = json.load(f)
                except json.JSONDecodeError:
                    logger.error(f"读取{channel_file}时JSON解析错误，创建新文件")
                    channel_results = []
            
            # 添加新结果
            channel_results.append(enriched_result)
            
            # 使用临时文件机制保存结果，避免写入时文件损坏
            temp_file = channel_file + '.tmp'
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(channel_results, f, ensure_ascii=False, indent=2)
            
            # 替换原文件
            shutil.move(temp_file, channel_file)
            logger.info(f"结果已保存到: {channel_file}")
            return True
        except Exception as e:
            logger.error(f"保存JSON结果时出错: {str(e)}")
            return False
    
    def _update_excel_files(self, enriched_result, output_dir):
        """更新Excel文件"""
        if not enriched_result:
            logger.warning("没有有效的分析结果，跳过Excel更新")
            return False
        
        # 使用process_single_message处理单条消息
        try:
            # 这里的excel_path可能返回DataFrame而不是文件路径
            excel_path = process_single_message(enriched_result, output_dir)
            
            # 检查返回的是DataFrame还是路径字符串
            if isinstance(excel_path, pd.DataFrame):
                logger.warning("process_single_message返回了DataFrame而不是文件路径")
                logger.info("已成功处理消息，但跳过Excel文件路径检查")
                return True
            
            if excel_path and os.path.exists(excel_path):
                logger.info(f"Excel数据已更新: {excel_path}")
                return excel_path
            else:
                logger.warning(f"Excel更新失败，未找到文件: {excel_path}")
                return False
        except Exception as e:
            logger.error(f"更新Excel时出错: {str(e)}")
            traceback.print_exc()
            return False
    
    def _update_complete_excel(self, output_dir):
        """更新完整的Excel文件，合并所有已处理的数据"""
        try:
            # 找到所有处理过的JSON文件
            json_files = glob.glob(os.path.join(output_dir, "*.json"))
            if not json_files:
                logger.warning("没有找到JSON文件，无法更新主Excel文件")
                return False
            
            # 读取所有有效的JSON文件内容
            all_records = []
            for json_file in json_files:
                try:
                    with open(json_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        if data:  # 确保数据有效
                            all_records.append(data)
                except Exception as e:
                    logger.error(f"读取JSON文件 {json_file} 时出错: {str(e)}")
            
            if not all_records:
                logger.warning("没有有效的JSON记录，跳过Excel更新")
                return False
            
            # 创建一个DataFrame
            df = pd.DataFrame(all_records)
            
            # 定义输出路径
            complete_excel_path = os.path.join(output_dir, "trading_messages_complete.xlsx")
            
            # 检查是否有现有文件并尝试合并
            if os.path.exists(complete_excel_path):
                try:
                    # 读取现有Excel文件
                    existing_df = pd.read_excel(complete_excel_path)
                    
                    # 确保两个DataFrame有相同的列
                    if not existing_df.empty and set(df.columns).issubset(set(existing_df.columns)):
                        # 根据消息ID检查重复项
                        if 'message_id' in df.columns and 'message_id' in existing_df.columns:
                            # 获取现有的消息ID
                            existing_ids = set(existing_df['message_id'].dropna().astype(str).tolist())
                            
                            # 过滤掉新数据中已存在的ID
                            if not df.empty:
                                new_df = df[~df['message_id'].astype(str).isin(existing_ids)]
                                
                                # 合并数据
                                if not new_df.empty:
                                    combined_df = pd.concat([existing_df, new_df], ignore_index=True)
                                    df = combined_df
                                else:
                                    df = existing_df
                                    logger.info("没有新的消息ID需要添加")
                        else:
                            # 如果没有message_id列，直接合并并删除重复项
                            combined_df = pd.concat([existing_df, df], ignore_index=True)
                            df = combined_df.drop_duplicates()
                except Exception as e:
                    logger.error(f"合并Excel数据时出错: {str(e)}")
                    # 继续使用新数据
            
            # 保存到Excel
            df.to_excel(complete_excel_path, index=False)
            logger.info(f"已成功更新主Excel文件: {complete_excel_path}")
            return True
            
        except Exception as e:
            logger.error(f"更新主Excel文件时出错: {str(e)}")
            traceback.print_exc()
            return False

    def merge_similar_messages(self, df):
        """合并内容相似的消息，处理两种主要场景：
        1. 短时间内的重复消息（相同币种、方向、相近点位）
        2. 后续添加止盈/止损点的更新消息（如TP1, STOP TO BE等）
        """
        logger.info("\n=== 数据合并过程 ===")
        logger.info(f"初始数据行数: {len(df)}")
        
        # 确保时间戳列是datetime类型
        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
        
        # 处理可能是列表的数据列，将它们转换为字符串
        list_columns = []
        for col in df.columns:
            if df[col].apply(lambda x: isinstance(x, list)).any():
                logger.info(f"检测到列表类型数据列: {col}，将转换为字符串")
                df[col] = df[col].apply(lambda x: str(x) if isinstance(x, list) else x)
                list_columns.append(col)
        
        try:
            # 按channel、币种和交易方向分组
            grouped = df.groupby(['channel', 'analysis.交易币种', 'analysis.方向'])
            
            # 统计分组情况
            logger.info("\n分组统计:")
            for (channel, currency, direction), group in grouped:
                logger.info(f"频道: {channel}, 币种: {currency}, 方向: {direction}, 记录数: {len(group)}")
        except Exception as e:
            logger.error(f"分组统计时出错: {str(e)}")
            # 如果分组失败，返回原始DataFrame
            return df
        
        final_records = []  # 最终保留的记录
        
        # 合并参数配置
        config = {
            "time_window_hours": 24,          # 时间窗口（小时）
            "entry_tolerance": 0.015,         # 入场点位容差（1.5%）
            "update_keywords": [              # 更新关键词（用于识别补充消息）
                "tp", "tp1", "tp2", "tp3", 
                "止盈", "止损", "移动止损", "止损移动", 
                "移动止盈", "止盈点", "止损点", "移动", 
                "stop to be", "move sl", "move tp"
            ]
        }
        
        try:
            # 逐组处理
            for (channel, currency, direction), group in grouped:
                if pd.isna(currency) or pd.isna(direction):
                    # 跳过币种或方向为空的组
                    final_records.extend(group.to_dict('records'))
                    continue
                    
                # 按时间排序
                group = group.sort_values('timestamp')
                group_records = group.to_dict('records')
                
                if len(group_records) == 1:
                    # 只有一条记录，不需要合并
                    final_records.extend(group_records)
                    continue
                
                # 合并逻辑...
                processed_ids = set()  # 跟踪已处理的消息ID
                
                for i, record in enumerate(group_records):
                    if record.get('message_id') in processed_ids:
                        continue
                        
                    processed_ids.add(record.get('message_id'))
                    
                    merged_record = record.copy()
                    merged_with_any = False
                    
                    # 查找后续可合并的记录
                    current_time = pd.to_datetime(record.get('timestamp'))
                    
                    for j in range(i+1, len(group_records)):
                        next_record = group_records[j]
                        
                        if next_record.get('message_id') in processed_ids:
                            continue
                            
                        next_time = pd.to_datetime(next_record.get('timestamp'))
                        time_diff = (next_time - current_time).total_seconds() / 3600  # 小时
                        
                        # 检查时间窗口
                        if time_diff > config["time_window_hours"]:
                            break
                            
                        # 检查是否是更新消息（包含特定关键词）
                        message_content = str(next_record.get('content', '')).lower()
                        is_update = any(keyword in message_content for keyword in config["update_keywords"])
                        
                        # 更新或者近时间段内的相似入场点位，合并记录
                        should_merge = False
                        
                        if is_update:
                            should_merge = True
                        else:
                            # 检查入场点位是否相近
                            entry1 = record.get('analysis.入场点位1')
                            next_entry1 = next_record.get('analysis.入场点位1')
                            
                            if entry1 and next_entry1 and not pd.isna(entry1) and not pd.isna(next_entry1):
                                try:
                                    entry1 = float(entry1)
                                    next_entry1 = float(next_entry1)
                                    diff_ratio = abs(entry1 - next_entry1) / entry1
                                    should_merge = diff_ratio <= config["entry_tolerance"]
                                except (ValueError, TypeError):
                                    # 转换为浮点数失败，跳过比较
                                    pass
                        
                        if should_merge:
                            processed_ids.add(next_record.get('message_id'))
                            merged_with_any = True
                            
                            # 合并记录，优先保留非空值
                            for key, value in next_record.items():
                                if key.startswith('analysis.'):
                                    current_value = merged_record.get(key)
                                    
                                    # 如果当前值为空且新值不为空，则更新
                                    if (pd.isna(current_value) or current_value is None or current_value == '') and not (pd.isna(value) or value is None or value == ''):
                                        merged_record[key] = value
                    
                    if merged_with_any:
                        merged_record['merged'] = True
                        logger.info(f"合并了币种 {currency} {direction} 的记录")
                    
                    final_records.append(merged_record)
                
            logger.info(f"合并后数据行数: {len(final_records)}")
            
            # 将合并后的记录转换回DataFrame
            result_df = pd.DataFrame(final_records)
            
            # 如果有列表类型列被转换为字符串，尝试转换回原始类型
            for col in list_columns:
                if col in result_df.columns:
                    try:
                        result_df[col] = result_df[col].apply(
                            lambda x: eval(x) if isinstance(x, str) and x.startswith('[') and x.endswith(']') else x
                        )
                    except Exception as e:
                        logger.warning(f"转换列 {col} 回列表类型时出错: {str(e)}")
            
            return result_df
        except Exception as e:
            logger.error(f"合并消息时出错: {str(e)}")
            traceback.print_exc()
            # 如果合并过程出错，返回原始DataFrame
            return df

class HistoricalMessageAnalyzer:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.siliconflow.cn/v1"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        # 配置具有重试机制的HTTP会话
        self.session = self._create_retry_session()
        
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

1. **交易币种**：提取博主提到的币种名称，最开始的几个字母一般为币种名称。其中，如果没有指代任何货币，并且描述会是以70k-100k为单位和价格，那么，这个币种是BTC。需要把k转换成80000-100000这样的描述。

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
你是一个专业的加密货币交易分析师。请仔细分析以下交易员给出的信息，给出其中的关键数据，提取并整理出以下信息：

1. **交易币种**：提取博主提到的币种名称，最开始的几个字母一般为币种名称。或者原文：\r\n以后的内容为币种名称，或者以**开头，以**结尾的内容为币种名称。或者是原文：**《Traeep》** \n以后的内容为币种名称。

2. **方向**：提取博主的交易方向（例如：多单、空单）。

3. **杠杆**：如果博主提到杠杆，请标明杠杆倍数。

4. **入场点位**：如果博主提到入场价格，请列出。一般以long或者short后面的数字作为入场点位。如果提到order Filled，则在入场点位1标注入场二字，进行保存。
   - 入场点位1：第一个入场价格
   - 入场点位2：第二个入场价格（如果有）
   - 入场点位3：第三个入场价格（如果有）

5. **止损点位**：如果博主提到止损价格，请列出。一般以stop作为止损的的数字作为止损点位。如果提到sl，但没有具体数字，则止损点位1标注止损二字，进行保存。
   - 止损点位1：第一个止损价格
   - 止损点位2：第二个止损价格（如果有）
   - 止损点位3：第三个止损价格（如果有）

6. **止盈点位**：如果博主提到止盈价格，请列出。TP1、TP2作为止盈的点，如果没有提到具体的价格，则在止盈点位1标注止盈二字，进行保存。如果提到Stopped BE，则标注为保本。
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
你是一个专业的加密货币交易分析师。请仔细分析以下交易员给出的信息，给出其中的关键数据，提取并整理出以下信息：

1. **交易币种**：提取博主提到的币种名称，最开始的几个字母一般为币种名称。或者原文：\r\n以后的内容为币种名称，或者以**开头，以**结尾的内容为币种名称。或者是原文：**《Traeep》** \n以后的内容为币种名称。

2. **方向**：提取博主的交易方向（例如：多单、空单）。

3. **杠杆**：如果博主提到杠杆，请标明杠杆倍数。

4. **入场点位**：如果博主提到入场价格，请列出。一般以long或者short后面的数字作为入场点位。如果提到order Filled，则在入场点位1标注入场二字，进行保存。
   - 入场点位1：第一个入场价格
   - 入场点位2：第二个入场价格（如果有）
   - 入场点位3：第三个入场价格（如果有）

5. **止损点位**：如果博主提到止损价格，请列出。一般以stop作为止损的的数字作为止损点位。如果提到sl，但没有具体数字，则止损点位1标注止损二字，进行保存。
   - 止损点位1：第一个止损价格
   - 止损点位2：第二个止损价格（如果有）
   - 止损点位3：第三个止损价格（如果有）

6. **止盈点位**：如果博主提到止盈价格，请列出。TP1、TP2作为止盈的点，如果没有提到具体的价格，则在止盈点位1标注止盈二字，进行保存。如果提到Stopped BE，则标注为保本。
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

1. **交易币种**：提取博主提到的币种名称，最开始的几个字母一般为币种名称。其中，如果没有指代任何货币，并且描述会是以70k-100k为单位和价格，那么，这个币种是BTC。需要把k转换成80000-100000这样的描述。

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
            "min_length": 5,  # 减少最小长度限制
            "price_indicators": ['$', '美元', 'k', 'K', '千', '万', '点', '位', '元'],
            "trading_keywords": [
                '多', '空', '做多', '做空', '买入', '卖出', '止损', '止盈', 
                'long', 'short', 'buy', 'sell', 'stop', 'target', 'entry', 'sl', 'tp',
                '入场', '出场', '进场', '离场', '获利', '亏损', '盈亏', '盈利',
                '破位', '支撑', '压力', '阻力', '反弹', '回调', '趋势', '走势',
                'BTC', 'ETH', 'SOL', 'DOGE', 'XRP', 'BNB', 'ADA'  # 常见币种也作为关键词
            ],
            "required_keywords": [],  # 保持为空，不要求必须包含特定关键词
            "excluded_keywords": ['广告', '招聘', '推广', '社群']  # 排除的关键词
        }
        
        # 添加这个：各频道的特定筛选规则
        self.channel_filters = {
            # 如果某个频道需要特殊的筛选规则，可以在这里添加
            # 例如：
            # "channel_name": { ... }
        }
        
        # 添加API调用统计
        self.api_stats = {
            "total_calls": 0,
            "successful_calls": 0,
            "failed_calls": 0,
            "last_call_time": None,
            "average_response_time": 0
        }
        
        # 添加线程锁用于API调用
        self.api_lock = threading.Lock()
        
        logger.info("HistoricalMessageAnalyzer 初始化完成")
    
    def _create_retry_session(self):
        """创建具有重试机制的请求会话"""
        session = requests.Session()
        
        # 定义重试策略
        retry_strategy = Retry(
            total=5,                  # 最大重试次数
            backoff_factor=0.5,       # 重试间隔系数
            status_forcelist=[429, 500, 502, 503, 504],  # 需要重试的HTTP状态码
            allowed_methods=["POST"]  # 只对POST请求进行重试
        )
        
        # 应用重试策略
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        
        return session

    def _extract_translated_content(self, content: str) -> Tuple[str, str]:
        """提取原文和翻译内容"""
        try:
            if "**原文:**" in content and "**翻译:**" in content:
                parts = content.split("**翻译:**")
                if len(parts) >= 2:
                    original = parts[0].replace("**原文:**", "").strip()
                    translated = parts[1].split("--------------")[0].strip()
                    
                    # 处理原文中可能的回复内容
                    if "**回复：" in original:
                        # 尝试提取回复目标后的实际内容
                        if "**" in original.split("**回复：", 1)[1]:
                            reply_parts = original.split("**回复：", 1)[1].split("**", 1)
                            if len(reply_parts) > 1:
                                # 保留原文，但在日志中记录提取的部分
                                extracted = reply_parts[1].strip()
                                if extracted:
                                    logger.info(f"从原文中提取回复内容: {extracted[:30]}...")
                    
                    # 处理翻译中可能的回复内容
                    if "**回复：" in translated:
                        # 尝试提取回复目标后的实际内容
                        if "**" in translated.split("**回复：", 1)[1]:
                            reply_parts = translated.split("**回复：", 1)[1].split("**", 1)
                            if len(reply_parts) > 1:
                                # 更新翻译内容为提取的部分
                                extracted = reply_parts[1].strip()
                                if extracted:
                                    logger.info(f"从翻译中提取回复内容: {extracted[:30]}...")
                                    translated = extracted
                    
                    return original, translated
            return content, content
        except Exception as e:
            logger.error(f"提取翻译内容时出错: {str(e)}")
            return content, content

    def should_analyze_message(self, msg: Dict, channel_name: str = None) -> bool:
        """判断消息是否需要分析"""
        if not msg.get('content'):
            return False
            
        content = msg['content']
        
        # 获取对应频道的筛选规则，如果没有则使用默认规则
        filter_rules = self.channel_filters.get(channel_name, self.default_filter)
        
        # 特殊处理回复消息
        is_reply = "**回复：" in content or content.strip().startswith(">")
        
        # 对回复消息进行内容提取
        if is_reply:
            # 尝试提取回复内容之后的部分作为实际内容
            extracted_content = content
            if "**回复：" in content and "**" in content.split("**回复：", 1)[1]:
                reply_parts = content.split("**回复：", 1)[1].split("**", 1)
                if len(reply_parts) > 1:
                    extracted_content = reply_parts[1].strip()
                    # 使用提取后的内容进行长度检查，降低要求
                    if len(extracted_content) >= 5:
                        # 只要有5个字符就尝试分析回复消息
                        return True
            
            # 回复消息，大幅降低长度要求
            min_length = 5  # 大幅降低长度要求
        else:
            # 非回复消息，略微降低长度要求
            min_length = max(5, filter_rules["min_length"] - 5)  # 降低5个字符，但不低于5
        
        # 检查消息长度
        if len(content.strip()) < min_length:
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
        
        # 对于回复消息，放宽要求
        if is_reply:
            return True  # 对回复消息总是尝试分析
        
        return has_price or has_trading_terms

    def preprocess_message(self, content: str) -> str:
        """消息预处理，增强可分析性"""
        if not content:
            return content
            
        # 去除多余的换行和空格
        content = re.sub(r'\n+', '\n', content)
        content = re.sub(r' +', ' ', content)
        
        # 标准化常见的价格表示方式
        content = re.sub(r'(\d+)([kK])', r'\1000', content)  # 将10k转换为10000
        content = re.sub(r'(\d+)\.(\d+)([kK])', r'\1\200', content)  # 将1.5k转换为1500
        
        # 标准化币种名称
        currency_pairs = {
            '大饼': 'BTC',
            '比特币': 'BTC',
            '以太坊': 'ETH',
            '二饼': 'ETH',
        }
        
        for original, replacement in currency_pairs.items():
            content = content.replace(original, replacement)
        
        # 标准化方向词汇
        direction_pairs = {
            '看涨': '做多',
            '看跌': '做空',
            '买入': '做多',
            '卖出': '做空',
        }
        
        for original, replacement in direction_pairs.items():
            content = content.replace(original, replacement)
        
        return content

    def _process_short_message(self, content: str) -> str:
        """处理短消息内容，尝试提取有用信息"""
        if not content:
            return content
            
        # 移除可能的引用符号
        content = re.sub(r'^>+\s*', '', content.strip())
        
        # 处理常见缩写
        abbreviations = {
            'sl': '止损',
            'tp': '止盈',
            'tp1': '止盈点位1',
            'tp2': '止盈点位2',
            'tp3': '止盈点位3',
            'long': '做多',
            'short': '做空',
            'buy': '买入',
            'sell': '卖出',
            'entry': '入场点位',
        }
        
        for abbr, full in abbreviations.items():
            # 匹配 "sl 1000" 或 "sl:1000" 或 "sl=1000" 模式
            content = re.sub(r'\b' + abbr + r'[:\s=]+(\d+\.?\d*)', r'' + full + r' \1', content, flags=re.IGNORECASE)
        
        # 处理币种标记
        # 例如: "BTC 30000" -> "BTC 价格 30000"
        currency_pattern = r'\b(BTC|ETH|SOL|DOGE|XRP|BNB|ADA|DOT|TRX|AVAX|LINK|LTC)\s+(\d+\.?\d*)'
        content = re.sub(currency_pattern, r'\1 价格 \2', content)
        
        # 重组短消息内容，使其更符合分析格式
        if len(content.strip()) < 20 and not ('做多' in content or '做空' in content or '多' in content or '空' in content):
            # 添加上下文信息，辅助分析
            content = f"关于加密货币交易的简短消息: {content}"
            
        return content

    def analyze_message(self, content: str, channel_name: str = None, retry_count: int = 3) -> Optional[Dict]:
        """分析单条消息"""
        try:
            original, translated = self._extract_translated_content(content)
            
            # 使用翻译内容进行分析
            content_to_analyze = translated or original
            
            # 预处理消息内容
            content_to_analyze = self.preprocess_message(content_to_analyze)
            
            # 生成一个唯一的消息标识（对于太短消息的日志记录）
            msg_id = hashlib.md5((original + (translated or "")).encode('utf-8')).hexdigest()[:8]
            
            # 特殊处理回复类消息（以"回复："或">"开头的消息）
            is_reply = False
            if "**回复：" in content_to_analyze or content_to_analyze.strip().startswith(">"):
                is_reply = True
                logger.info(f"检测到回复类消息 [ID:{msg_id}]")
                # 尝试提取回复内容之后的部分作为实际内容
                try:
                    if "**回复：" in content_to_analyze and "**" in content_to_analyze.split("**回复：", 1)[1]:
                        reply_parts = content_to_analyze.split("**回复：", 1)[1].split("**", 1)
                        if len(reply_parts) > 1:
                            content_to_analyze = reply_parts[1].strip()
                            logger.info(f"提取回复后内容: {content_to_analyze[:30]}...")
                except Exception as e:
                    logger.error(f"提取回复内容时出错: {str(e)}")
                    # 出错时继续使用原内容
            
            # 减少长度限制，对回复消息更宽松处理
            min_length = 5 if is_reply else 10
            
            # 对短消息进行特殊处理
            if not content_to_analyze or len(content_to_analyze.strip()) < min_length:
                # 尝试处理短消息，看是否能提取有用信息
                if content_to_analyze and len(content_to_analyze.strip()) >= 3:
                    try:
                        enhanced_short_content = self._process_short_message(content_to_analyze)
                        logger.info(f"增强短消息内容: {enhanced_short_content}")
                        
                        # 使用增强后的内容进行分析
                        content_to_analyze = enhanced_short_content
                    except Exception as e:
                        logger.error(f"处理短消息时出错: {str(e)}")
                        # 出错时仍使用原内容
                else:
                    # 消息确实太短，无法分析
                    preview = f"**原文:**{original[:15]}..." if len(original) > 15 else original
                    preview += f" **翻译:**{translated[:15]}..." if translated and len(translated) > 15 else f" **翻译:**{translated}"
                    logger.warning(f"消息内容太短或为空，跳过分析 [ID:{msg_id}]: {preview}")
                    return None
            
            # 首先尝试使用正则表达式提取基本信息
            try:
                extracted_info = self._extract_basic_trading_info(content_to_analyze)
                logger.info(f"预提取信息结果: {extracted_info}")
            except Exception as e:
                logger.error(f"提取基本交易信息时出错: {str(e)}")
                # 出错时使用空字典
                extracted_info = {
                    "交易币种": None,
                    "方向": None,
                    "杠杆": None,
                    "入场点位1": None,
                    "入场点位2": None,
                    "入场点位3": None,
                    "止损点位1": None,
                    "止损点位2": None,
                    "止损点位3": None,
                    "止盈点位1": None,
                    "止盈点位2": None,
                    "止盈点位3": None,
                    "分析内容": None
                }
            
            # 选择对应的提示词
            prompt = self.channel_prompts.get(channel_name, self.default_prompt)
            
            # 增强提示词，加入预提取信息
            try:
                enhanced_prompt = self._enhance_prompt_with_extracted_info(prompt, extracted_info)
            except Exception as e:
                logger.error(f"增强提示词时出错: {str(e)}")
                # 出错时使用原始提示词
                enhanced_prompt = prompt
            
            # 准备API请求消息
            messages = [{"role": "user", "content": enhanced_prompt.format(content=content_to_analyze)}]
            
            # API调用部分的错误处理在调用方法里处理
            
            return self._call_api_with_retry(messages, content_to_analyze, original, translated, extracted_info, channel_name, retry_count)
        
        except Exception as e:
            logger.error(f"分析消息时发生未捕获的异常: {str(e)}")
            logger.error(f"消息内容: {content[:100]}...")
            traceback.print_exc()
            return None
    
    def _call_api_with_retry(self, messages, content_to_analyze, original, translated, extracted_info, channel_name, retry_count=3):
        """对API调用进行封装，增加错误处理和重试逻辑"""
        api_result = None
        attempts = 0
        
        while attempts < retry_count and api_result is None:
            attempts += 1
            try:
                # 记录API请求开始时间
                start_time = time.time()
                
                # 创建retry会话
                session = self._create_retry_session()
                
                # 构建API请求
                url = "https://api.siliconflow.cn/v1/chat/completions"
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}"
                }
                data = {
                    "model": "deepseek-ai/DeepSeek-V3",
                    "messages": messages,
                    "temperature": 0.01,
                    "top_p": 0.95,
                    "max_tokens": 1000
                }
                
                # 发送API请求
                logger.info(f"发送API请求 (尝试 {attempts}/{retry_count})")
                response = session.post(url, json=data, headers=headers, timeout=30)
                
                # 计算请求时间
                request_time = time.time() - start_time
                
                # 记录API交互
                self._log_api_interaction(messages, response, channel_name)
                
                # 更新API统计
                self._update_api_stats(response.status_code == 200, request_time)
                
                # 检查API响应状态
                if response.status_code == 200:
                    # 解析API响应
                    result = self._parse_api_response(response, content_to_analyze, original, translated, extracted_info, channel_name)
                    if result:
                        return result
                    else:
                        logger.error(f"API响应解析失败 (尝试 {attempts}/{retry_count})")
                else:
                    logger.error(f"API请求失败，状态码: {response.status_code} (尝试 {attempts}/{retry_count})")
                    logger.error(f"错误信息: {response.text}")
                    
                    # 处理特定错误状态码
                    if response.status_code == 429:  # 速率限制
                        wait_time = min(2 ** attempts, 30)  # 指数退避，最多等待30秒
                        logger.info(f"API速率限制，等待 {wait_time} 秒后重试")
                        time.sleep(wait_time)
                    
            except Exception as e:
                logger.error(f"API请求过程中出错: {str(e)} (尝试 {attempts}/{retry_count})")
                
            # 如果没有成功获取结果且尚未达到最大重试次数，则等待后重试
            if api_result is None and attempts < retry_count:
                wait_time = 2 ** attempts  # 指数退避
                logger.info(f"等待 {wait_time} 秒后重试")
                time.sleep(wait_time)
                
        # 如果所有尝试均失败，则尝试后备解析
        if api_result is None:
            logger.warning("所有API尝试均失败，尝试后备解析")
            try:
                fallback_result = self._try_fallback_parsing(content_to_analyze, original, translated)
                if fallback_result:
                    logger.info("后备解析成功")
                    return fallback_result
            except Exception as e:
                logger.error(f"后备解析时出错: {str(e)}")
            
            # 创建空结果
            logger.warning("创建空结果")
            return self._create_empty_result(original, translated, channel_name, content_to_analyze)
        
        return api_result

    def _update_api_stats(self, success: bool, request_time: float = None):
        """更新API调用统计信息"""
        if success:
            self.api_stats["successful_calls"] += 1
            if request_time:
                # 更新平均响应时间
                total_success = self.api_stats["successful_calls"]
                avg_time = self.api_stats["average_response_time"]
                self.api_stats["average_response_time"] = (avg_time * (total_success - 1) + request_time) / total_success
        else:
            self.api_stats["failed_calls"] += 1
    
    def _parse_api_response(self, response, content_to_analyze, original, translated, extracted_info, channel_name):
        """解析API响应"""
        try:
            response.raise_for_status()
            result = response.json()
            
            if 'choices' in result and len(result['choices']) > 0:
                content = result['choices'][0]['message']['content']
                try:
                    # 清理返回的内容，移除markdown标记
                    cleaned_content = content.replace('```json', '').replace('```', '').strip()
                    parsed_result = json.loads(cleaned_content)
                    logger.info("分析成功！")
                    
                    # 合并预提取结果和API分析结果
                    merged_result = self._merge_analysis_results(extracted_info, parsed_result)
                    
                    # 添加原文和翻译到结果中
                    merged_result['原文'] = original
                    merged_result['翻译'] = translated
                    return merged_result
                except json.JSONDecodeError as e:
                    logger.error(f"JSON解析失败: {str(e)}")
                    logger.debug(f"返回内容: {content}")
                    
                    # 尝试备选解析方法
                    parsed_result = self._try_fallback_parsing(content, original, translated)
                    if parsed_result:
                        return parsed_result
                    
                    # 如果API分析失败，但预提取成功，则返回预提取结果
                    if any(extracted_info.values()):
                        logger.warning("API分析JSON解析失败，使用预提取结果作为备选")
                        extracted_info['原文'] = original
                        extracted_info['翻译'] = translated
                        extracted_info['分析内容'] = "通过规则提取的基本信息，API分析失败"
                        return extracted_info
                    
                    # 记录解析错误
                    self._log_parse_error(content_to_analyze, channel_name, str(e))
                    return None
            else:
                logger.error(f"API返回结果没有有效内容")
                # 如果API返回为空但预提取成功，则返回预提取结果
                if any(extracted_info.values()):
                    logger.warning("API分析返回空结果，使用预提取结果作为备选")
                    extracted_info['原文'] = original
                    extracted_info['翻译'] = translated
                    extracted_info['分析内容'] = "通过规则提取的基本信息，API分析未返回结果"
                    return extracted_info
                return None
                
        except Exception as e:
            logger.error(f"解析API响应时出错: {str(e)}")
            return None

    def _extract_basic_trading_info(self, content: str) -> Dict:
        """使用规则和正则表达式从消息内容中提取基本交易信息"""
        result = {
            "交易币种": None,
            "方向": None,
            "杠杆": None,
            "入场点位1": None,
            "入场点位2": None,
            "入场点位3": None,
            "止损点位1": None,
            "止损点位2": None,
            "止损点位3": None,
            "止盈点位1": None,
            "止盈点位2": None,
            "止盈点位3": None,
            "分析内容": None
        }
        
        # 尝试提取币种
        currency_pattern = r'\b(BTC|ETH|SOL|DOGE|XRP|BNB|ADA|DOT|TRX|AVAX|LINK|LTC|BCH|EOS)\b'
        currency_match = re.search(currency_pattern, content, re.IGNORECASE)
        if currency_match:
            result["交易币种"] = currency_match.group(1).upper()
        
        # 尝试提取交易方向
        if re.search(r'\b(做多|多头|多单|看涨|bull|buy|long)\b', content, re.IGNORECASE):
            result["方向"] = "做多"
        elif re.search(r'\b(做空|空头|空单|看跌|bear|sell|short)\b', content, re.IGNORECASE):
            result["方向"] = "做空"
        
        # 尝试提取杠杆
        leverage_pattern = r'(\d+)[xX倍]杠杆'
        leverage_match = re.search(leverage_pattern, content)
        if leverage_match:
            result["杠杆"] = int(leverage_match.group(1))
        
        # 尝试提取入场点位
        entry_patterns = [
            r'入场[价位点]?[：:]*\s*([\d\.]+)',
            r'进场[价位点]?[：:]*\s*([\d\.]+)',
            r'[买卖][入出]点?[：:]*\s*([\d\.]+)'
        ]
        
        entry_positions = []
        for pattern in entry_patterns:
            for match in re.finditer(pattern, content):
                entry_positions.append(float(match.group(1)))
        
        # 填充入场点位
        for i, pos in enumerate(entry_positions[:3], 1):
            result[f"入场点位{i}"] = pos
        
        # 尝试提取止损点位
        sl_patterns = [
            r'止损[价位点]?[：:]*\s*([\d\.]+)',
            r'SL[：:]*\s*([\d\.]+)',
            r'sl[：:]*\s*([\d\.]+)'
        ]
        
        sl_positions = []
        for pattern in sl_patterns:
            for match in re.finditer(pattern, content):
                sl_positions.append(float(match.group(1)))
        
        # 填充止损点位
        for i, pos in enumerate(sl_positions[:3], 1):
            result[f"止损点位{i}"] = pos
        
        # 尝试提取止盈点位
        tp_patterns = [
            r'止盈[价位点]?[：:]*\s*([\d\.]+)',
            r'目标[价位点]?[：:]*\s*([\d\.]+)',
            r'TP[：:]*\s*([\d\.]+)',
            r'tp[：:]*\s*([\d\.]+)'
        ]
        
        tp_positions = []
        for pattern in tp_patterns:
            for match in re.finditer(pattern, content):
                tp_positions.append(float(match.group(1)))
        
        # 填充止盈点位
        for i, pos in enumerate(tp_positions[:3], 1):
            result[f"止盈点位{i}"] = pos
        
        return result

    def _enhance_prompt_with_extracted_info(self, prompt: str, extracted_info: Dict) -> str:
        """根据预提取的信息增强提示词"""
        # 如果没有提取到任何信息，直接返回原提示词
        if not any(extracted_info.values()):
            return prompt
        
        # 添加一段提示，告诉API我们已经预提取了一些信息，你可以参考这些信息进行更准确的分析：
        enhancement = "\n以下是通过简单规则预先提取的信息，你可以参考这些信息进行更准确的分析：\n"
        
        for key, value in extracted_info.items():
            if value is not None:
                enhancement += f"{key}: {value}\n"
        
        # 在原提示词的适当位置插入增强内容
        enhanced_prompt = prompt.replace("内容如下：", f"{enhancement}\n内容如下：")
        
        return enhanced_prompt

    def _merge_analysis_results(self, extracted_info: Dict, api_result: Dict) -> Dict:
        """合并预提取结果和API分析结果"""
        merged_result = api_result.copy()
        
        # 对于API没有分析出来但预提取有的字段，使用预提取的结果
        for key, value in extracted_info.items():
            if value is not None and (key not in merged_result or merged_result[key] is None):
                merged_result[key] = value
        
        return merged_result

    def _log_api_interaction(self, messages, response, channel_name):
        """记录API请求和响应到日志文件"""
        try:
            log_dir = "data/analysis_logs"
            os.makedirs(log_dir, exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = os.path.join(log_dir, f"api_log_{timestamp}_{channel_name}.json")
            
            log_data = {
                "timestamp": timestamp,
                "channel": channel_name,
                "request": {
                    "messages": messages
                },
                "response": {
                    "status_code": response.status_code,
                    "content": response.text
                }
            }
            
            with open(log_file, 'w', encoding='utf-8') as f:
                json.dump(log_data, f, ensure_ascii=False, indent=2)
                
        except Exception as e:
            print(f"记录API交互时出错: {str(e)}")

    def _log_parse_error(self, content, channel_name, error_msg):
        """记录解析错误到日志文件"""
        try:
            log_dir = "data/analysis_logs/parse_errors"
            os.makedirs(log_dir, exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = os.path.join(log_dir, f"parse_error_{timestamp}_{channel_name}.txt")
            
            with open(log_file, 'w', encoding='utf-8') as f:
                f.write(f"时间: {timestamp}\n")
                f.write(f"频道: {channel_name}\n")
                f.write(f"错误: {error_msg}\n\n")
                f.write("原始内容:\n")
                f.write(content)
                
        except Exception as e:
            print(f"记录解析错误时出错: {str(e)}")

    def _try_fallback_parsing(self, content, original, translated):
        """尝试使用更宽松的方式解析JSON"""
        print("尝试备选解析方法...")
        try:
            # 尝试寻找JSON结构的开始和结束位置
            start_pos = content.find('{')
            end_pos = content.rfind('}') + 1
            
            if start_pos >= 0 and end_pos > start_pos:
                json_content = content[start_pos:end_pos]
                parsed_result = json.loads(json_content)
                print("备选解析成功！")
                parsed_result['原文'] = original
                parsed_result['翻译'] = translated
                return parsed_result
        except Exception as e:
            print(f"备选解析也失败了: {str(e)}")
        
        return None

    def _create_empty_result(self, original, translated, channel_name, content):
        """创建一个带有基本结构的空结果"""
        print("创建一个基本的空结果")
        
        # 尝试从内容中提取可能的交易币种
        possible_currency = self._extract_possible_currency(content)
        
        empty_result = {
            "交易币种": possible_currency,
            "方向": None,
            "杠杆": None,
            "入场点位1": None,
            "入场点位2": None,
            "入场点位3": None,
            "止损点位1": None,
            "止损点位2": None,
            "止损点位3": None,
            "止盈点位1": None,
            "止盈点位2": None,
            "止盈点位3": None,
            "分析内容": "分析失败，未能提取有效信息",
            "原文": original,
            "翻译": translated,
            "分析失败": True
        }
        
        # 记录失败案例以供后续改进
        self._log_analysis_failure(content, channel_name, empty_result)
        
        return empty_result

    def _extract_possible_currency(self, content):
        """从内容中尝试提取可能的交易币种"""
        # 常见币种列表
        common_currencies = ["BTC", "ETH", "SOL", "DOGE", "XRP", "BNB", "ADA", "DOT", "TRX", "AVAX"]
        
        for currency in common_currencies:
            if currency in content:
                return currency
        
        return None

    def _log_analysis_failure(self, content, channel_name, empty_result):
        """记录分析失败案例"""
        try:
            log_dir = "data/analysis_logs/failures"
            os.makedirs(log_dir, exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = os.path.join(log_dir, f"analysis_failure_{timestamp}_{channel_name}.json")
            
            log_data = {
                "timestamp": timestamp,
                "channel": channel_name,
                "content": content,
                "empty_result": empty_result
            }
            
            with open(log_file, 'w', encoding='utf-8') as f:
                json.dump(log_data, f, ensure_ascii=False, indent=2)
                
        except Exception as e:
            print(f"记录分析失败时出错: {str(e)}")

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
                
            # 保存所有结果到Excel文件 - 替换为高级数据处理
            all_results = []
            for results in channel_results.values():
                all_results.extend(results)
                
            if all_results:
                excel_path = process_analysis_data(all_results, output_dir)
                print(f"\n已更新Excel文件: {excel_path}")
                
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
        """开始监控指定路径下的消息文件"""
        logger.info(f"开始监控: {path}")
        
        try:
            # 注释掉此行，避免处理现有文件
            # self._process_existing_files(path)
            
            logger.info("跳过处理现有文件，只监控新消息")
            
            # 创建事件处理器
            event_handler = MessageFileHandler(self)
            
            # 创建观察者
            observer = Observer()
            observer.schedule(event_handler, path, recursive=False)
            observer.start()
            
            logger.info("文件监控已启动，按Ctrl+C停止...")
            
            # 添加健康检查定时器
            def health_check():
                """定期执行健康检查"""
                while True:
                    try:
                        self._perform_health_check(observer, event_handler)
                        # 每10分钟检查一次
                        time.sleep(600)
                    except Exception as e:
                        logger.error(f"健康检查时出错: {str(e)}")
                        # 错误后等待一段时间再继续
                        time.sleep(60)
            
            # 启动健康检查线程
            health_thread = threading.Thread(target=health_check, daemon=True)
            health_thread.start()
            
            # 主线程保持运行
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                logger.info("接收到中断信号，停止监控...")
                observer.stop()
                observer.join()
                logger.info("监控已停止")
            except Exception as e:
                logger.error(f"监控过程中发生错误: {str(e)}")
                logger.error(traceback.format_exc())
                # 尝试重新启动监控
                logger.info("尝试重新启动监控...")
                observer.stop()
                observer.join()
                
                # 创建新的观察者并重新启动
                new_observer = Observer()
                new_observer.schedule(event_handler, path, recursive=False)
                new_observer.start()
                
                # 继续主循环
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    logger.info("接收到中断信号，停止监控...")
                    new_observer.stop()
                    new_observer.join()
                    logger.info("监控已停止")
        except Exception as e:
            logger.critical(f"启动监控时发生严重错误: {str(e)}")
            logger.critical(traceback.format_exc())
            # 尝试再次启动
            logger.info("10秒后尝试重新启动监控...")
            time.sleep(10)
            self.start_monitoring(path)  # 递归调用，尝试重新启动

    def _perform_health_check(self, observer, event_handler):
        """执行系统健康检查"""
        logger.info("执行系统健康检查...")
        
        # 检查观察者线程是否活跃
        if not observer.is_alive():
            logger.error("监控线程已死亡，尝试重启")
            try:
                observer.start()
                logger.info("监控线程已重启")
            except Exception as e:
                logger.error(f"重启监控线程失败: {str(e)}")
        
        # 打印系统状态
        memory_usage = self._get_memory_usage()
        logger.info(f"系统状态: 内存使用: {memory_usage:.2f}MB, "
                  f"API统计: 总数:{self.api_stats['total_calls']}, "
                  f"成功:{self.api_stats['successful_calls']}, "
                  f"失败:{self.api_stats['failed_calls']}")
    
    def _get_memory_usage(self):
        """获取当前进程的内存使用情况（MB）"""
        try:
            import psutil
            process = psutil.Process(os.getpid())
            memory_info = process.memory_info()
            return memory_info.rss / 1024 / 1024  # 转换为MB
        except ImportError:
            return 0  # 如果psutil不可用
        except Exception as e:
            logger.error(f"获取内存使用信息失败: {str(e)}")
            return 0

    def _process_existing_files(self, path):
        """程序启动时处理现有文件"""
        try:
            # 获取目录下的所有json文件
            json_files = [f for f in os.listdir(path) if f.endswith('.json')]
            if json_files:
                logger.info(f"发现 {len(json_files)} 个现有JSON文件，将进行处理")
                
                # 按修改时间排序，处理最新的文件
                json_files = sorted(
                    json_files, 
                    key=lambda x: os.path.getmtime(os.path.join(path, x)),
                    reverse=True
                )
                
                # 只处理最新的10个文件
                max_files_to_process = min(10, len(json_files))
                for i in range(max_files_to_process):
                    file_path = os.path.join(path, json_files[i])
                    logger.info(f"启动时处理现有文件 {i+1}/{max_files_to_process}: {file_path}")
                    try:
                        self.event_handler._safe_process_file(file_path, "startup")
                    except Exception as e:
                        logger.error(f"处理启动时文件失败: {str(e)}")
                    # 添加短暂延迟避免API请求过于频繁
                    time.sleep(1)
            else:
                logger.info(f"目录 {path} 中没有找到现有的JSON文件")
                # 如果路径不存在文件，打印更详细的信息
                try:
                    logger.info(f"检查路径 {path} 是否存在:")
                    if os.path.exists(path):
                        logger.info(f"路径存在，列出内容:")
                        all_files = os.listdir(path)
                        if all_files:
                            logger.info(f"目录中有 {len(all_files)} 个文件/目录:")
                            for f in all_files[:10]:  # 只显示前10个
                                logger.info(f" - {f}")
                            if len(all_files) > 10:
                                logger.info(f" ... 还有 {len(all_files) - 10} 个未显示")
                        else:
                            logger.info("目录为空")
                    else:
                        logger.error(f"路径 {path} 不存在")
                except Exception as e:
                    logger.error(f"检查目录时出错: {str(e)}")
        except Exception as e:
            logger.error(f"处理现有文件时出错: {str(e)}")

    def process_short_message(self, short_message, channel_name=None):
        """测试函数：用于特别测试短消息处理功能"""
        try:
            logger.info(f"测试处理短消息: '{short_message}'")
            
            # 先检查消息是否应该被分析
            if not self.should_analyze_message({"content": short_message}, channel_name):
                logger.info("此短消息不符合分析条件，会被跳过")
                return None
                
            # 尝试分析消息
            result = self.analyze_message(short_message, channel_name)
            
            if result:
                logger.info("短消息分析成功!")
                logger.info(f"分析结果: {result}")
                return result
            else:
                logger.info("短消息分析失败或返回空结果")
                return None
        except Exception as e:
            logger.error(f"处理短消息时出错: {str(e)}")
            traceback.print_exc()
            return None

    def _process_existing_files(self, path):
        """处理监控路径中已存在的文件"""
        logger.info(f"处理目录中现有文件: {path}")
        
        if not os.path.exists(path):
            logger.error(f"路径不存在: {path}")
            return
            
        try:
            # 获取所有JSON文件
            json_files = [f for f in os.listdir(path) if f.endswith('.json')]
            logger.info(f"找到 {len(json_files)} 个JSON文件")
            
            if not json_files:
                logger.info("没有找到需要处理的JSON文件")
                return
                
            # 按修改时间排序，优先处理最新文件
            json_files.sort(key=lambda f: os.path.getmtime(os.path.join(path, f)), reverse=True)
            
            # 限制一次性处理文件数量，避免启动时负载过大
            max_files_to_process = 5
            if len(json_files) > max_files_to_process:
                logger.info(f"限制处理最新的 {max_files_to_process} 个文件，跳过剩余 {len(json_files) - max_files_to_process} 个文件")
                json_files = json_files[:max_files_to_process]
                
            # 创建MessageFileHandler实例进行文件处理
            handler = MessageFileHandler(self)
            
            # 处理每个文件
            for file_name in json_files:
                file_path = os.path.join(path, file_name)
                
                # 检查文件是否需要跳过
                if any(skip_file in file_name for skip_file in handler.skip_files):
                    logger.info(f"跳过文件: {file_name}")
                    continue
                    
                # 检查文件年龄
                file_mtime = os.path.getmtime(file_path)
                current_time = time.time()
                file_age = current_time - file_mtime
                    
                if file_age > 600:  # 超过10分钟的文件视为旧文件
                    logger.info(f"跳过旧文件: {file_name} (最后修改于 {file_age:.1f} 秒前)")
                    continue
                    
                logger.info(f"处理文件: {file_name} (最后修改于 {file_age:.1f} 秒前)")
                
                try:
                    # 使用安全处理方法
                    handler._safe_process_file(file_path, "startup")
                    # 处理文件后等待一小段时间，避免API调用过于频繁
                    time.sleep(3)
                except Exception as e:
                    logger.error(f"处理文件 {file_name} 时出错: {str(e)}")
                    # 继续处理下一个文件，不因一个文件错误而中断
                    continue
        except Exception as e:
            logger.error(f"处理现有文件时出错: {str(e)}")
            traceback.print_exc()

def standardize_direction(value):
    """标准化交易方向：统一多头和空头的表示"""
    if pd.isna(value) or value == '':
        return value
    
    value_lower = str(value).lower()  # 转为小写便于比较
    
    # 统一多头表示
    long_terms = ['做多', '多单', '多', 'long', 'buy', '买入']
    for term in long_terms:
        if term.lower() in value_lower:
            return '做多'
    
    # 统一空头表示
    short_terms = ['做空', '空单', '空', 'short', 'sell', '卖出']
    for term in short_terms:
        if term.lower() in value_lower:
            return '做空'
    
    return value  # 如果没有匹配，保持原值

def clean_currency(value):
    """清理和标准化币种名称"""
    try:
        # 处理数组/列表情况
        if isinstance(value, (list, np.ndarray)):
            # 如果是数组，我们返回第一个非空值，或第一个值
            for item in value:
                if item and not pd.isna(item):
                    return clean_currency(item)
            return None
            
        # 空值处理
        if pd.isna(value) or value is None or value == '':
            return None
            
        # 转换为字符串
        value = str(value).strip().upper()
        
        # 如果是空字符串，返回None
        if value == '' or value.lower() == 'null' or value.lower() == 'none':
            return None
            
        # 替换币种名称别名
        replacements = {
            'BITCOIN': 'BTC',
            '比特币': 'BTC',
            '以太坊': 'ETH', 
            'ETHEREUM': 'ETH',
            'DOGE COIN': 'DOGE',
            'DOGECOIN': 'DOGE',
            'SOLANA': 'SOL',
            'BINANCE': 'BNB',
            'RIPPLE': 'XRP',
            'CARDANO': 'ADA',
            'POLKADOT': 'DOT',
            'TRON': 'TRX',
            'AVALANCHE': 'AVAX',
            'CHAINLINK': 'LINK',
            'LITECOIN': 'LTC'
        }
        
        # 检查常见币种缩写
        common_currencies = ['BTC', 'ETH', 'SOL', 'DOGE', 'XRP', 'BNB', 'ADA', 'DOT', 'TRX', 'AVAX', 'LINK', 'LTC']
        for currency in common_currencies:
            if currency in value:
                return currency
                
        # 尝试应用替换规则
        for original, replacement in replacements.items():
            if original.upper() in value.upper():
                return replacement
                
        # 如果没有匹配到任何常见币种，则原样返回
        return value
    except Exception as e:
        logger.error(f"清理币种时出错: {str(e)}")
        # 出错时返回原值
        return value

def clean_position_value(value):
    """清理和标准化点位数值"""
    try:
        # 处理数组/列表情况
        if isinstance(value, (list, np.ndarray)):
            # 如果是数组，我们返回第一个非空值，或第一个值
            for item in value:
                if item and not pd.isna(item):
                    return clean_position_value(item)
            return None
            
        # 处理空值
        if pd.isna(value) or value is None or value == '':
            return None
        
        # 将字符串类型的小数点转换为数字
        if isinstance(value, str):
            # 移除所有非数字、小数点、负号的字符
            value = re.sub(r'[^\d.-]', '', value.replace(',', '.'))
            
            # 处理空字符串和特殊字符
            if not value or value in ['.', '-', '-.']:
                return None
                
            try:
                value = float(value)
            except ValueError:
                return None
        
        # 转换为浮点数，处理可能的转换错误
        try:
            value = float(value)
        except (ValueError, TypeError):
            return None
        
        return value
    except Exception as e:
        logger.error(f"清理点位值时出错: {str(e)}")
        # 任何异常情况都返回None
        return None

def process_analysis_data(data_list, output_dir="data/analysis_results"):
    """处理分析数据，更新CSV和Excel文件"""
    logger.info(f"处理{len(data_list) if data_list else 0}条分析记录...")
    
    try:
        # 检查输入数据是否为空
        if not data_list or len(data_list) == 0:
            logger.warning("输入数据列表为空，无需处理")
            return None
            
        # 确保输出目录存在
        os.makedirs(output_dir, exist_ok=True)
        
        # 将数据转换为DataFrame
        df = pd.json_normalize(data_list)
        
        # 检查数据是否为空
        if df.empty:
            logger.warning("没有数据需要处理")
            return None
            
        # 安全应用转换函数
        def safe_apply(df, column, func):
            if column in df.columns:
                try:
                    df[column] = df[column].apply(lambda x: func(x) if not pd.isna(x) else x)
                    logger.debug(f"已处理 {column} 列")
                except Exception as e:
                    logger.error(f"应用函数 {func.__name__} 到列 {column} 时出错: {str(e)}")
            return df
            
        # 标准化方向和币种
        df = safe_apply(df, 'analysis.方向', standardize_direction)
        df = safe_apply(df, 'analysis.交易币种', clean_currency)
        
        # 处理点位数据
        position_columns = [col for col in df.columns if col.startswith('analysis.') and 
                          any(keyword in col for keyword in ['入场点位', '止盈点位', '止损点位'])]
        for col in position_columns:
            df = safe_apply(df, col, clean_position_value)
        
        logger.info(f"处理点位数据完成，共处理了 {len(position_columns)} 个点位列")
        
        # 对所有列应用数据验证函数，确保可以安全保存
        logger.info("验证所有数据列以确保可以安全保存...")
        for col in df.columns:
            df = safe_apply(df, col, validate_data_before_save)
        
        # 固定的输出文件路径
        output_path = os.path.join(output_dir, "all_analysis_results.csv")
        output_excel = os.path.join(output_dir, "trading_analysis.xlsx")
        
        # 保存到CSV
        try:
            df.to_csv(output_path, index=False, encoding='utf-8-sig')
            logger.info(f"数据已保存到 {output_path}")
        except Exception as e:
            logger.error(f"保存CSV文件时出错: {str(e)}")
            # 尝试保存到备用位置
            try:
                backup_path = os.path.join(output_dir, f"all_analysis_results_{int(time.time())}.csv")
                df.to_csv(backup_path, index=False, encoding='utf-8-sig')
                logger.info(f"数据已保存到备用位置: {backup_path}")
            except Exception as backup_err:
                logger.error(f"保存到备用位置也失败: {str(backup_err)}")
        
        # 保存到Excel
        try:
            # 准备Excel工作簿
            writer = pd.ExcelWriter(output_excel, engine='openpyxl')
            
            # 主表
            df.to_excel(writer, sheet_name='所有记录', index=False)
            
            # 按交易币种分组
            if 'analysis.交易币种' in df.columns and not df['analysis.交易币种'].isna().all():
                # 过滤掉币种为空的行
                currency_df = df[df['analysis.交易币种'].notna() & (df['analysis.交易币种'] != '')]
                if not currency_df.empty:
                    logger.info(f"创建按币种分组的表...发现 {currency_df['analysis.交易币种'].nunique()} 种不同币种")
                    for currency, group in currency_df.groupby('analysis.交易币种'):
                        if isinstance(currency, str) and currency.strip() != '':
                            # 限制工作表名长度，Excel工作表名最长31字符
                            sheet_name = f"{currency}交易"
                            if len(sheet_name) > 31:
                                sheet_name = sheet_name[:28] + "..."
                            group.to_excel(writer, sheet_name=sheet_name, index=False)
            
            # 按频道分组
            if 'channel' in df.columns and not df['channel'].isna().all():
                logger.info("创建按频道分组的表...")
                for channel, group in df.groupby('channel'):
                    if isinstance(channel, str) and channel.strip() != '':
                        # 限制工作表名长度
                        sheet_name = f"{channel}"
                        if len(sheet_name) > 31:
                            sheet_name = sheet_name[:28] + "..."
                        group.to_excel(writer, sheet_name=sheet_name, index=False)
            
            # 保存Excel文件
            writer.close()
            logger.info(f"Excel数据已保存到 {output_excel}")
        except Exception as e:
            logger.error(f"保存Excel文件时出错: {str(e)}")
            traceback.print_exc()
            
            # 尝试关闭Excel连接并重试
            close_excel_connections()
            try:
                backup_excel = os.path.join(output_dir, f"trading_analysis_{int(time.time())}.xlsx")
                writer = pd.ExcelWriter(backup_excel, engine='openpyxl')
                df.to_excel(writer, sheet_name='所有记录', index=False)
                writer.close()
                logger.info(f"数据已保存到备用Excel: {backup_excel}")
            except Exception as backup_err:
                logger.error(f"保存到备用Excel也失败: {str(backup_err)}")
        
        logger.info("分析数据处理完成")
        return df
    except Exception as e:
        logger.error(f"处理分析数据时出错: {str(e)}")
        traceback.print_exc()
        return None

def process_single_message(message_data, output_dir="data/analysis_results"):
    """处理单条消息并更新CSV文件"""
    logger.info("=== 处理单条消息 ===")
    
    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)
    
    # 确保消息数据有效
    if not message_data:
        logger.error("消息数据为空，无法处理")
        return None
        
    # 记录原始消息数据的关键字段，帮助调试
    try:
        channel_name = message_data.get('channel', '未知频道')
        message_id = message_data.get('message_id', '未知ID')
        timestamp = message_data.get('timestamp', '未知时间')
        currency = message_data.get('analysis', {}).get('交易币种', '未知币种')
        direction = message_data.get('analysis', {}).get('方向', '未知方向')
        
        logger.info(f"处理消息 - 频道: {channel_name}, ID: {message_id}, 时间: {timestamp}")
        logger.info(f"消息内容 - 币种: {currency}, 方向: {direction}")
    except Exception as e:
        logger.error(f"提取消息信息时出错: {e}")
    
    # 准备新数据
    df_new = pd.json_normalize([message_data])
    
    # 定义安全应用函数
    def safe_apply(df, column, func):
        if column in df.columns:
            try:
                df[column] = df[column].apply(lambda x: func(x) if not pd.isna(x) else x)
            except Exception as e:
                logger.error(f"应用函数 {func.__name__} 到列 {column} 时出错: {str(e)}")
        return df
    
    # 标准化数据处理
    if 'analysis.方向' in df_new.columns:
        df_new = safe_apply(df_new, 'analysis.方向', standardize_direction)
        
    if 'analysis.交易币种' in df_new.columns:
        df_new = safe_apply(df_new, 'analysis.交易币种', clean_currency)
    
    # 处理点位数据
    position_columns = [col for col in df_new.columns if col.startswith('analysis.') and 
                     any(keyword in col for keyword in ['入场点位', '止盈点位', '止损点位'])]
    for col in position_columns:
        df_new = safe_apply(df_new, col, clean_position_value)
    
    # 对所有列应用数据验证，确保可以安全保存
    for col in df_new.columns:
        df_new = safe_apply(df_new, col, validate_data_before_save)
    
    # 固定的输出文件路径
    output_path = os.path.join(output_dir, "all_analysis_results.csv")
    
    try:
        logger.info(f"准备保存数据到: {output_path}")
        
        # 如果文件存在，读取并追加数据
        if os.path.exists(output_path):
            try:
                logger.info("检测到现有CSV文件，尝试读取...")
                # 读取现有CSV文件
                existing_df = pd.read_csv(output_path, encoding='utf-8-sig')
                logger.info(f"成功读取现有CSV文件，包含{len(existing_df)}条记录")
                
                # 检查现有数据的结构
                logger.debug(f"现有数据列: {existing_df.columns.tolist()}")
                logger.debug(f"新数据列: {df_new.columns.tolist()}")
                
                # 确保两个DataFrame有相同的列结构
                missing_cols = set(df_new.columns) - set(existing_df.columns)
                if missing_cols:
                    for col in missing_cols:
                        existing_df[col] = None
                    logger.info(f"为现有数据添加了缺失的列: {missing_cols}")
                    
                missing_cols = set(existing_df.columns) - set(df_new.columns)
                if missing_cols:
                    for col in missing_cols:
                        df_new[col] = None
                    logger.info(f"为新数据添加了缺失的列: {missing_cols}")
                
                # 检查是否存在重复数据
                if 'message_id' in df_new.columns and 'message_id' in existing_df.columns:
                    new_id = df_new['message_id'].iloc[0] if not df_new.empty else None
                    if new_id and not existing_df[existing_df['message_id'] == new_id].empty:
                        logger.info(f"消息ID {new_id} 已存在，跳过添加")
                        return existing_df
                
                # 合并数据
                combined_df = pd.concat([existing_df, df_new], ignore_index=True)
                logger.info(f"已合并数据，现在共有{len(combined_df)}条记录")
                
                # 保存回CSV
                combined_df.to_csv(output_path, index=False, encoding='utf-8-sig')
                logger.info(f"更新后的数据已保存到 {output_path}")
                
                # 尝试更新Excel文件
                excel_path = os.path.join(output_dir, "trading_analysis.xlsx")
                if os.path.exists(excel_path):
                    try:
                        # 关闭可能的Excel连接
                        close_excel_connections()
                        
                        # 准备Excel工作簿
                        writer = pd.ExcelWriter(excel_path, engine='openpyxl')
                        combined_df.to_excel(writer, sheet_name='所有记录', index=False)
                        writer.close()
                        logger.info(f"Excel数据已更新: {excel_path}")
                    except Exception as e:
                        logger.error(f"更新Excel文件时出错: {str(e)}")
                
                return combined_df
            except Exception as e:
                logger.error(f"处理现有CSV文件时出错: {str(e)}")
                traceback.print_exc()
        
        # 如果文件不存在或读取失败，直接创建新文件
        df_new.to_csv(output_path, index=False, encoding='utf-8-sig')
        logger.info(f"新数据已保存到 {output_path}")
        
        # 创建Excel文件
        excel_path = os.path.join(output_dir, "trading_analysis.xlsx")
        try:
            writer = pd.ExcelWriter(excel_path, engine='openpyxl')
            df_new.to_excel(writer, sheet_name='所有记录', index=False)
            writer.close()
            logger.info(f"Excel数据已创建: {excel_path}")
        except Exception as e:
            logger.error(f"创建Excel文件时出错: {str(e)}")
        
        return df_new
    except Exception as e:
        logger.error(f"保存处理结果时出错: {str(e)}")
        traceback.print_exc()
        
        # 尝试保存到备用位置
        try:
            backup_path = os.path.join(output_dir, f"single_message_{int(time.time())}.csv")
            df_new.to_csv(backup_path, index=False, encoding='utf-8-sig')
            logger.info(f"数据已保存到备用位置: {backup_path}")
            return df_new
        except Exception as backup_err:
            logger.error(f"保存到备用位置也失败: {str(backup_err)}")
            return None

def safe_save_data(df, output_dir, base_filename, max_retries=3):
    """安全地保存数据，包括重试机制和备份策略"""
    if df is None or df.empty:
        logger.warning("没有数据需要保存")
        return None
        
    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)
    
    # 验证每一列数据以确保可以安全保存
    logger.info("验证所有数据列以确保可以安全保存...")
    
    # 定义安全应用转换函数
    def safe_apply(dataframe, column, func):
        if column in dataframe.columns:
            try:
                dataframe[column] = dataframe[column].apply(lambda x: func(x) if not pd.isna(x) else x)
                logger.debug(f"已处理 {column} 列")
            except Exception as e:
                logger.error(f"应用函数 {func.__name__} 到列 {column} 时出错: {str(e)}")
        return dataframe
    
    # 对所有列应用数据验证
    for col in df.columns:
        df = safe_apply(df, col, validate_data_before_save)
    
    # 保存到CSV
    csv_path = os.path.join(output_dir, f"{base_filename}.csv")
    excel_path = os.path.join(output_dir, f"{base_filename}.xlsx")
    
    # 尝试保存CSV
    for attempt in range(max_retries):
        try:
            df.to_csv(csv_path, index=False, encoding='utf-8-sig')
            logger.info(f"成功保存CSV数据到: {csv_path}")
            break
        except PermissionError:
            if attempt < max_retries - 1:
                logger.warning(f"CSV文件被占用，等待重试 ({attempt+1}/{max_retries})...")
                # 尝试关闭可能的连接
                close_excel_connections()
                time.sleep(5 * (attempt + 1))  # 逐渐增加等待时间
            else:
                # 最后一次尝试，使用时间戳创建备用文件
                backup_path = os.path.join(output_dir, f"{base_filename}_{int(time.time())}.csv")
                try:
                    df.to_csv(backup_path, index=False, encoding='utf-8-sig')
                    logger.info(f"原CSV文件被锁定，已保存到备用位置: {backup_path}")
                    csv_path = backup_path  # 更新路径为成功的备用路径
                except Exception as backup_err:
                    logger.error(f"保存到备用CSV也失败: {str(backup_err)}")
                    csv_path = None
        except Exception as e:
            logger.error(f"保存CSV出错: {str(e)}")
            if attempt < max_retries - 1:
                logger.warning(f"正在重试 ({attempt+1}/{max_retries})...")
                time.sleep(3)
            else:
                logger.error("保存CSV文件失败，已达到最大重试次数")
                csv_path = None
    
    # 尝试保存Excel
    for attempt in range(max_retries):
        try:
            # 准备Excel工作簿
            writer = pd.ExcelWriter(excel_path, engine='openpyxl')
            
            # 主表
            df.to_excel(writer, sheet_name='所有记录', index=False)
            
            # 按交易币种分组（如果有该列）
            if 'analysis.交易币种' in df.columns and not df['analysis.交易币种'].isna().all():
                # 过滤掉币种为空的行
                currency_df = df[df['analysis.交易币种'].notna() & (df['analysis.交易币种'] != '')]
                if not currency_df.empty:
                    for currency, group in currency_df.groupby('analysis.交易币种'):
                        if isinstance(currency, str) and currency.strip() != '':
                            sheet_name = f"{currency}交易"
                            if len(sheet_name) > 31:
                                sheet_name = sheet_name[:28] + "..."
                            group.to_excel(writer, sheet_name=sheet_name, index=False)
            
            # 保存Excel文件
            writer.close()
            logger.info(f"成功保存Excel数据到: {excel_path}")
            break
        except PermissionError:
            if attempt < max_retries - 1:
                logger.warning(f"Excel文件被占用，等待重试 ({attempt+1}/{max_retries})...")
                # 尝试关闭Excel连接
                close_excel_connections()
                time.sleep(5 * (attempt + 1))  # 逐渐增加等待时间
            else:
                # 最后一次尝试，使用时间戳创建备用文件
                backup_excel = os.path.join(output_dir, f"{base_filename}_{int(time.time())}.xlsx")
                try:
                    writer = pd.ExcelWriter(backup_excel, engine='openpyxl')
                    df.to_excel(writer, sheet_name='所有记录', index=False)
                    writer.close()
                    logger.info(f"原Excel文件被锁定，已保存到备用位置: {backup_excel}")
                    excel_path = backup_excel  # 更新路径为成功的备用路径
                except Exception as backup_err:
                    logger.error(f"保存到备用Excel也失败: {str(backup_err)}")
                    excel_path = None
        except Exception as e:
            logger.error(f"保存Excel出错: {str(e)}")
            traceback.print_exc()
            if attempt < max_retries - 1:
                logger.warning(f"正在重试 ({attempt+1}/{max_retries})...")
                time.sleep(3)
            else:
                logger.error("保存Excel文件失败，已达到最大重试次数")
                excel_path = None
    
    # 返回成功保存的文件路径或None
    if csv_path or excel_path:
        return csv_path or excel_path
    else:
        return None

def validate_data_before_save(value):
    """
    验证并处理数据，确保它可以被安全地保存到CSV/Excel文件中
    """
    try:
        # 处理列表或数组
        if isinstance(value, (list, np.ndarray)):
            # 如果是空列表或空数组，返回None
            if len(value) == 0:
                return None
                
            # 提取第一个非空值
            for item in value:
                if item is not None and str(item).strip() != '':
                    # 如果这个项本身是列表或数组，递归处理
                    if isinstance(item, (list, np.ndarray)):
                        return validate_data_before_save(item)
                    return item
            return None
            
        # 处理字典
        elif isinstance(value, dict):
            # 将字典转换为JSON字符串
            return json.dumps(value, ensure_ascii=False)
            
        # 对字符串进行处理
        elif isinstance(value, str):
            # 如果字符串为空或只包含空格，返回None
            if value.strip() == '':
                return None
            return value
            
        # 对数值类型的处理
        elif isinstance(value, (int, float)):
            # 检查是否为NaN或无穷大
            if np.isnan(value) or np.isinf(value):
                return None
            return value
            
        # 对布尔值的处理
        elif isinstance(value, bool):
            return value
            
        # 处理None和NaN
        elif value is None or (isinstance(value, float) and np.isnan(value)):
            return None
            
        # 处理NumPy数组
        elif hasattr(value, 'size') and hasattr(value, 'tolist'):
            if value.size == 0:
                return None
            return validate_data_before_save(value.tolist())
            
        # 其他类型，尝试转换为字符串
        else:
            try:
                return str(value)
            except:
                return None
                
    except Exception as e:
        logger.warning(f"数据验证失败: {str(e)}, 值类型: {type(value)}")
        return None

def check_disk_space(min_space_gb=1.0, critical_space_gb=0.2):
    """检查磁盘空间并在必要时进行清理"""
    try:
        total, used, free = shutil.disk_usage("/")
        free_gb = free / (1024**3)
        
        if free_gb < critical_space_gb:
            logger.critical(f"磁盘空间危急! 剩余: {free_gb:.2f}GB")
            # 执行紧急清理
            # 1. 清理日志
            cleanup_old_logs(emergency=True)
            # 2. 清理分析文件
            cleanup_old_analysis_files(emergency=True)
            # 3. 清理临时文件
            cleanup_temp_files()
            
            # 重新检查空间
            _, _, free = shutil.disk_usage("/")
            free_gb = free / (1024**3)
            logger.info(f"紧急清理后，磁盘空间: {free_gb:.2f}GB")
            
            if free_gb < critical_space_gb:
                logger.critical("紧急清理后仍然空间不足!")
                return False
                
        elif free_gb < min_space_gb:
            logger.warning(f"磁盘空间低! 剩余: {free_gb:.2f}GB")
            # 执行常规清理
            cleanup_old_logs()
            cleanup_old_analysis_files()
            
            # 重新检查空间
            _, _, free = shutil.disk_usage("/")
            free_gb = free / (1024**3)
            logger.info(f"常规清理后，磁盘空间: {free_gb:.2f}GB")
        
        return True
    except Exception as e:
        logger.error(f"检查磁盘空间时出错: {e}")
        return True  # 出错时不中断程序

def cleanup_old_logs(emergency=False):
    """清理旧日志文件"""
    try:
        log_dir = "logs"
        if os.path.exists(log_dir):
            log_files = [os.path.join(log_dir, f) for f in os.listdir(log_dir) if f.endswith('.log')]
            log_files.sort(key=os.path.getmtime)  # 按修改时间排序
            
            # 正常清理保留最近7天，紧急清理只保留最近2天
            keep_count = 2 if emergency else 7
            if len(log_files) > keep_count:
                for old_file in log_files[:-keep_count]:
                    try:
                        os.remove(old_file)
                        logger.info(f"已删除旧日志文件: {old_file}")
                    except Exception as e:
                        logger.error(f"删除日志文件失败: {e}")
    except Exception as e:
        logger.error(f"清理日志文件时出错: {e}")

def cleanup_old_analysis_files(emergency=False):
    """清理旧分析结果文件"""
    try:
        analysis_dir = "data/analysis_logs"
        if os.path.exists(analysis_dir):
            for root, dirs, files in os.walk(analysis_dir):
                # 跳过最近的文件夹
                if any(recent in root for recent in ['recent', 'latest']):
                    continue
                    
                log_files = [os.path.join(root, f) for f in files if f.endswith('.json') or f.endswith('.txt')]
                log_files.sort(key=os.path.getmtime)  # 按修改时间排序
                
                # 正常清理保留最近50个，紧急清理只保留最近10个
                keep_count = 10 if emergency else 50
                if len(log_files) > keep_count:
                    for old_file in log_files[:-keep_count]:
                        try:
                            os.remove(old_file)
                            if emergency:
                                logger.info(f"紧急清理: 已删除旧分析文件: {old_file}")
                        except Exception as e:
                            logger.error(f"删除分析文件失败: {e}")
    except Exception as e:
        logger.error(f"清理分析文件时出错: {e}")

def cleanup_temp_files():
    """清理临时文件"""
    try:
        # 清理自定义临时目录
        temp_dirs = ["temp", "tmp", "data/temp"]
        for temp_dir in temp_dirs:
            if os.path.exists(temp_dir):
                for item in os.listdir(temp_dir):
                    item_path = os.path.join(temp_dir, item)
                    try:
                        if os.path.isfile(item_path):
                            os.remove(item_path)
                            logger.info(f"删除临时文件: {item_path}")
                        elif os.path.isdir(item_path):
                            shutil.rmtree(item_path)
                            logger.info(f"删除临时目录: {item_path}")
                    except Exception as e:
                        logger.error(f"删除临时文件失败: {item_path}, 错误: {e}")
    except Exception as e:
        logger.error(f"清理临时文件时出错: {e}")

def main():
    """主函数"""
    try:
        # 检查磁盘空间
        if not check_disk_space(min_space_gb=1.0, critical_space_gb=0.2):
            logger.critical("磁盘空间不足，程序无法安全运行!")
            return
            
        # 获取API密钥
        api_key = os.environ.get('DEEPSEEK_API_KEY', 'sk-zacrufovtechzzjashtgqewnbclgmvdbxwegjoxpqvdlfbjb')
        
        # 检查API密钥
        if not api_key or api_key.startswith("sk-"):
            logger.info(f"使用API密钥进行初始化: {api_key[:10]}...")
        else:
            logger.warning("API密钥未设置或格式不正确，可能会导致API调用失败")
        
        # 初始化并启动监控
        analyzer = HistoricalMessageAnalyzer(api_key)
        
        # 如果有命令行参数，执行历史消息处理
        if len(sys.argv) > 1 and sys.argv[1] == 'process':
            if len(sys.argv) >= 4:
                data_dir = sys.argv[2]
                output_dir = sys.argv[3]
                analyzer.process_message_files(data_dir, output_dir)
            else:
                print("用法: python Trading_messages.py process <data_dir> <output_dir>")
                sys.exit(1)
        else:
            # 优先使用环境变量，如果没有则使用默认路径
            # 1. 环境变量路径
            # 2. 绝对路径 C:\Users\wtadministrator\Desktop\discord-monitor-master0422\data\messages
            # 3. 相对路径 data/messages
            
            env_path = os.environ.get('DISCORD_MESSAGES_PATH')
            abs_path = r"C:\Users\wtadministrator\Desktop\discord-monitor-master0422\data\messages"
            rel_path = "data/messages"
            
            # 确定要使用的路径
            if env_path and os.path.exists(env_path):
                path_to_monitor = env_path
                logger.info(f"使用环境变量指定的监控路径: {path_to_monitor}")
            elif os.path.exists(abs_path):
                path_to_monitor = abs_path
                logger.info(f"使用绝对路径监控: {path_to_monitor}")
            else:
                path_to_monitor = rel_path
                logger.info(f"使用相对路径监控: {path_to_monitor}")
                # 确保目录存在
                os.makedirs(path_to_monitor, exist_ok=True)
            
            # 检查路径是否可用
            if not os.path.exists(path_to_monitor):
                logger.error(f"监控路径不存在: {path_to_monitor}")
                logger.info("尝试创建目录...")
                os.makedirs(path_to_monitor, exist_ok=True)
                
            if not os.path.isdir(path_to_monitor):
                logger.error(f"监控路径不是目录: {path_to_monitor}")
                return
            
            # 设置环境变量，确保其他部分的代码也使用这个路径
            os.environ['DISCORD_MESSAGES_PATH'] = path_to_monitor
                
            logger.info(f"确认监控路径: {path_to_monitor}")
            
            # 启动监控
            analyzer.start_monitoring(path_to_monitor)
    except Exception as e:
        logger.error(f"主程序异常: {str(e)}")
        traceback.print_exc()

if __name__ == "__main__":
    main()