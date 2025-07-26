#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import json
import sys
import logging
import aiohttp
from datetime import datetime, timedelta, timezone
import os
import socket
from pathlib import Path
import pandas as pd
import time
from typing import Optional, Dict, List, Any
import re
from urllib.parse import quote
import requests
import hmac
import base64
import hashlib
import ssl
import certifi
import threading

# 设置日志 - 移到最前面
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 创建自定义 SSL 上下文
ssl_context = ssl.create_default_context(cafile=certifi.where())
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

# 在导入discord之前，创建并注入所有需要的假模块
class DummyAudioop:
    def __init__(self):
        pass
    
    def ratecv(self, *args, **kwargs):
        return b'', 0
    
    def tostereo(self, *args, **kwargs):
        return b''

# 创建假的模块类
class DummyModule:
    pass

# 创建假的voice相关类
class DummyVoiceClient:
    warn_nacl = False
    def __init__(self, *args, **kwargs):
        pass

class DummyVoiceProtocol:
    pass

class DummyOpusError(Exception):
    pass

# 创建假的opus模块
dummy_opus = DummyModule()
dummy_opus.is_loaded = lambda: False
dummy_opus.OpusError = DummyOpusError
dummy_opus.OpusNotLoaded = DummyOpusError

# 创建假的nacl模块
dummy_nacl = DummyModule()

# 注入所有假模块
sys.modules['audioop'] = DummyAudioop()
sys.modules['nacl'] = dummy_nacl
sys.modules['discord.voice_client'] = type('voice_client', (), {
    'VoiceClient': DummyVoiceClient,
    'VoiceProtocol': DummyVoiceProtocol
})
sys.modules['discord.opus'] = dummy_opus
sys.modules['discord.player'] = DummyModule()

# 现在导入discord相关模块
import discord
from discord.ext import commands

# 导入统一配置管理器
from config_manager import config_manager

# 配置管理类（保持向后兼容）
class Config:
    def __init__(self, config_file='config.json'):
        self.config_file = config_file
        # 使用新的配置管理器
        self._config = {
            'monitor': config_manager.get_monitor_config()
        }
        # 添加飞书配置
        try:
            self.feishu_webhook = config_manager.get_feishu_webhook_url() or ""
            self.feishu_secret = config_manager.get_feishu_app_secret() or ""
        except AttributeError:
            logger.warning("飞书Webhook配置缺失，将使用空字符串")
            self.feishu_webhook = ""
            self.feishu_secret = ""

    def load_config(self, config_file):
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"加载配置文件失败: {str(e)}")
            # 如果加载失败，创建默认配置
            default_config = {
                "token": "",
                "monitor": {
                    "save_path": "data/messages",
                    "channels": [],
                    "channel_names": {},
                    "channel_types": {}
                }
            }
            return default_config

    # 添加获取配置的方法
    def get_save_path(self):
        return self._config['monitor']['save_path']

    def get_channels(self):
        return self._config['monitor']['channels']

    def get_token(self):
        return config_manager.get_discord_token()

    def get_channel_name(self, channel_id):
        """获取频道名称"""
        return self._config['monitor']['channel_names'].get(channel_id, channel_id)

    def get_channel_type(self, channel_id):
        """获取频道类型"""
        return self._config['monitor']['channel_types'].get(channel_id, 'general')

# 消息处理类
class MessageProcessor:
    def __init__(self, config):
        self.config = config
        self.message_patterns = {
            'twitter': r'https?://(?:www\.)?twitter\.com/\w+/status/(\d+)',
            'trading_signal': r'(买入|卖出|做多|做空).*?([\d.]+)',
            # 添加更多模式匹配
        }

    async def process_message(self, message):
        """处理消息的主要方法"""
        try:
            channel_id = str(message.channel.id)
            channel_type = self.config.get_channel_type(channel_id)
            
            # 根据频道类型选择处理方法
            if channel_type == "trading":
                return await self.process_trading_message(message)
            elif channel_type == "news":
                return await self.process_news_message(message)
            elif channel_type == "social":
                return await self.process_social_message(message)
            else:
                return await self.process_general_message(message)
                
        except Exception as e:
            logger.error(f"处理消息时发生错误: {str(e)}")
            return None

    async def process_trading_message(self, message):
        """处理交易信号消息"""
        try:
            # 提取关键信息
            trading_info = {
                'timestamp': message.created_at.isoformat(),
                'channel_id': str(message.channel.id),
                'message_id': str(message.id),
                'content': message.content,
                'type': 'trading_signal'
            }
            
            # 保存到数据库或文件
            await self.save_trading_info(trading_info)
            
            return trading_info
            
        except Exception as e:
            logger.error(f"处理交易消息时发生错误: {str(e)}")
            return None

    async def process_social_message(self, message):
        """处理社交媒体消息"""
        try:
            # 检查是否包含 Twitter 链接
            twitter_urls = re.findall(self.message_patterns['twitter'], message.content)
            
            return {
                'type': 'social',
                'platform': 'twitter' if twitter_urls else 'unknown',
                'urls': twitter_urls,
                'content': message.content
            }
            
        except Exception as e:
            logger.error(f"处理社交媒体消息时发生错误: {str(e)}")
            return None

    async def save_trading_info(self, trading_info):
        """保存交易信息"""
        try:
            # 获取保存路径
            base_dir = os.path.join(os.path.dirname(__file__), 'data', 'trading')
            os.makedirs(base_dir, exist_ok=True)
            
            # 按日期保存
            date_str = datetime.now().strftime('%Y-%m-%d')
            file_path = os.path.join(base_dir, f'trading_{date_str}.json')
            
            # 读取现有数据
            existing_data = []
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
            
            # 添加新数据
            existing_data.append(trading_info)
            
            # 保存数据
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(existing_data, f, ensure_ascii=False, indent=2)
                
            logger.info(f"已保存交易信息到: {file_path}")
            
        except Exception as e:
            logger.error(f"保存交易信息时发生错误: {str(e)}")

    async def process_general_message(self, message):
        """处理一般消息"""
        try:
            # 构建消息数据
            message_data = {
                'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'channel_id': str(message.channel.id),
                'channel_name': self.config.get_channel_name(str(message.channel.id)),
                'author': str(message.author),
                'author_id': str(message.author.id),
                'content': message.content,
                'attachments': [att.url for att in message.attachments],
                'embeds': [embed.to_dict() for embed in message.embeds],
                'type': 'general'
            }
            
            logger.info(f"处理一般消息: {message_data['content'][:100]}...")
            return message_data
            
        except Exception as e:
            logger.error(f"处理一般消息时出错: {str(e)}")
            return None

# 设置事件循环
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# 修补 discord.py 的问题
def patch_discord():
    """修补 discord.py 的问题"""
    def parse_ready_supplemental(self, data):
        """处理 ready_supplemental 事件"""
        try:
            self.pending_payments = {}
            logger.debug("已处理 ready_supplemental 事件")
            return True
        except Exception as e:
            logger.error(f"处理 ready_supplemental 时发生错误: {str(e)}")
            return False

    # 替换原始方法
    discord.state.ConnectionState.parse_ready_supplemental = parse_ready_supplemental

    # 修补构建号获取方法
    async def _get_build_number(session):
        """获取构建号的补丁方法"""
        try:
            # 使用固定的构建号
            return "9999"
        except Exception as e:
            logger.error(f"获取构建号时发生错误: {str(e)}")
            return "9999"

    # 替换原始方法
    discord.utils._get_build_number = _get_build_number

    # 修补 Status 未定义问题
    if not hasattr(discord, "Status"):
        class DummyStatus:
            offline = "offline"
        discord.Status = DummyStatus
    if not hasattr(discord.gateway, "Status"):
        discord.gateway.Status = discord.Status

# 应用补丁
patch_discord()

class SimpleDiscordMonitor(discord.Client):
    def __init__(self, config):
        # 保存配置
        self.config = config
        
        # 使用 discord.py-self 的正确初始化方式
        super().__init__(
            self_bot=True,  # 必须设置为 True，表示这是一个用户账号
            chunk_guilds_at_startup=False,  # 不需要加载所有成员
            max_messages=1000  # 消息缓存上限降低到1000
        )
        
        # 初始化其他组件
        self.message_processor = MessageProcessor(config)
        self.messages = {}
        self.last_save_time = {}
        self.retry_count = 0
        self.max_retries = 3
        self.retry_delay = 10  # 增加初始重试延迟（秒）
        
        # 内存管理配置
        self.max_messages_per_channel = 1000  # 每个频道最大消息数
        self.cleanup_interval = 300  # 清理间隔（秒）
        self.last_cleanup_time = time.time()
        
        # 设置保存目录
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.save_dir = os.path.join(base_dir, 'data', 'messages')
        os.makedirs(self.save_dir, exist_ok=True)
        
        # 设置数据目录
        self.data_dir = Path('data')
        self.data_dir.mkdir(exist_ok=True)
        
        # 添加会话管理
        self._session = None
        self._session_lock = asyncio.Lock()
        
        # 添加关闭标志
        self._closing = False
        
        # 添加连接状态管理
        self._connection_lock = asyncio.Lock()
        self._is_reconnecting = False
        self._connection_state = "disconnected"
        
        # 初始化消息文件
        self._init_message_files()
        
        logger.info("Discord客户端初始化完成")
    
    def cleanup_memory(self):
        """清理内存中的旧消息"""
        try:
            current_time = time.time()
            if current_time - self.last_cleanup_time < self.cleanup_interval:
                return
            
            total_cleaned = 0
            for channel_id, messages in self.messages.items():
                if len(messages) > self.max_messages_per_channel:
                    # 保留最新的消息，删除旧的
                    old_count = len(messages)
                    self.messages[channel_id] = messages[-self.max_messages_per_channel:]
                    cleaned = old_count - len(self.messages[channel_id])
                    total_cleaned += cleaned
                    logger.info(f"频道 {channel_id} 清理了 {cleaned} 条旧消息")
            
            if total_cleaned > 0:
                logger.info(f"内存清理完成，共清理 {total_cleaned} 条消息")
                # 强制垃圾回收
                import gc
                gc.collect()
            
            self.last_cleanup_time = current_time
            
        except Exception as e:
            logger.error(f"内存清理失败: {e}")
    
    def get_memory_usage(self):
        """获取内存使用情况"""
        import psutil
        import os
        
        try:
            process = psutil.Process(os.getpid())
            memory_info = process.memory_info()
            return {
                'rss': memory_info.rss / 1024 / 1024,  # MB
                'vms': memory_info.vms / 1024 / 1024,  # MB
                'percent': process.memory_percent()
            }
        except Exception as e:
            logger.error(f"获取内存使用情况失败: {e}")
            return None

    def _init_message_files(self):
        """初始化消息文件"""
        for channel_id in self.config.get_channels():
            channel_name = self.config.get_channel_name(channel_id)
            filename = f"{channel_id}-{channel_name}.json"
            filename = "".join(c for c in filename if c.isalnum() or c in ('-', '_', '.'))
            
            channel_file = os.path.join(self.save_dir, filename)
            try:
                if os.path.exists(channel_file) and os.path.getsize(channel_file) > 0:
                    # 只有当文件存在且不为空时才尝试加载
                    try:
                        with open(channel_file, 'r', encoding='utf-8') as f:
                            content = f.read().strip()
                            if content:
                                self.messages[channel_id] = json.loads(content)
                                logger.info(f"已加载频道 {channel_name} ({channel_id}) 的 {len(self.messages[channel_id])} 条消息")
                            else:
                                self.messages[channel_id] = []
                                logger.info(f"频道 {channel_name} ({channel_id}) 的消息文件为空，初始化为空列表")
                    except json.JSONDecodeError as e:
                        logger.error(f"解析频道 {channel_name} ({channel_id}) 的JSON文件失败: {str(e)}")
                        self.messages[channel_id] = []
                else:
                    # 文件不存在或为空，初始化为空列表但不立即创建文件
                    self.messages[channel_id] = []
                    logger.info(f"频道 {channel_name} ({channel_id}) 初始化为空消息列表")
            except Exception as e:
                logger.error(f"处理频道 {channel_name} ({channel_id}) 的消息文件时出错: {str(e)}")
                self.messages[channel_id] = []

    def is_monitored_channel(self, message):
        """检查消息是否来自被监控的频道"""
        channel_id = str(message.channel.id)
        
        # 检查是否在监控列表中
        monitored_channels = self.config.get_channels()
        if channel_id in monitored_channels:
            logger.info(f"匹配到监控频道ID: {channel_id}")
            return True
        
        logger.info(f"该频道不在监控列表中: {channel_id}")
        return False

    def save_messages(self, channel_id):
        """保存指定频道的消息到文件"""
        try:
            # 确保频道ID在消息字典中
            if channel_id not in self.messages:
                logger.warning(f"频道 {channel_id} 的消息列表不存在，跳过保存")
                return
            
            # 获取频道名称
            channel_name = self.config.get_channel_name(channel_id)
            # 使用频道名称作为文件名
            filename = f"{channel_id}-{channel_name}.json"
            # 替换文件名中的非法字符
            filename = "".join(c for c in filename if c.isalnum() or c in ('-', '_', '.'))
            
            # 确保保存目录存在
            os.makedirs(self.save_dir, exist_ok=True)
            
            channel_file = os.path.join(self.save_dir, filename)
            
            # 验证数据是否可序列化
            messages_to_save = self.messages[channel_id]
            if not isinstance(messages_to_save, list):
                logger.error(f"频道 {channel_id} 的消息数据格式错误，不是列表格式")
                return
            
            # 保存文件，添加错误处理
            try:
                with open(channel_file, 'w', encoding='utf-8') as f:
                    json.dump(messages_to_save, f, ensure_ascii=False, indent=2, default=str)
                
                logger.info(f"成功保存 {len(messages_to_save)} 条消息到文件: {channel_file}")
                
                # 验证文件是否确实保存了内容
                if os.path.getsize(channel_file) == 0:
                    logger.error(f"警告：保存的文件 {channel_file} 大小为0，可能存在序列化问题")
                else:
                    logger.info(f"文件大小: {os.path.getsize(channel_file)} 字节")
                    
            except (TypeError, ValueError) as e:
                logger.error(f"JSON序列化失败: {str(e)}")
                logger.error(f"消息数据类型: {type(messages_to_save)}")
                if messages_to_save:
                    logger.error(f"第一条消息示例: {messages_to_save[0]}")
                # 尝试保存为纯文本备份
                backup_file = channel_file.replace('.json', '_backup.txt')
                with open(backup_file, 'w', encoding='utf-8') as f:
                    f.write(str(messages_to_save))
                logger.info(f"已保存纯文本备份到: {backup_file}")
            
        except Exception as e:
            logger.error(f"保存频道 {channel_id} 的消息时出错: {str(e)}")
            logger.exception(e)

    async def setup_hook(self) -> None:
        """设置钩子，在客户端准备好之前调用"""
        try:
            logger.info("setup_hook 被调用")
            async with self._session_lock:
                if self._session is None:
                    self._session = await self.setup_http_session()
                    # 只在第一次创建时设置到http
                    if hasattr(self, 'http') and self.http:
                        self.http.session = self._session
            logger.info("setup_hook 完成")
            
        except Exception as e:
            logger.error(f"设置钩子时发生错误: {str(e)}")
            raise

    async def close(self):
        """关闭客户端和清理资源"""
        try:
            logger.info("开始关闭客户端...")
            self._closing = True
            
            # 首先关闭discord客户端
            if not self.is_closed():
                await super().close()
                logger.info("Discord客户端已关闭")
            
            # 然后关闭HTTP会话 - 改进版本
            async with self._session_lock:
                if self._session and not self._session.closed:
                    try:
                        # 先关闭所有连接
                        if hasattr(self._session, 'connector') and self._session.connector:
                            await self._session.connector.close()
                        # 再关闭会话
                        await self._session.close()
                        logger.info("HTTP会话已关闭")
                    except Exception as session_error:
                        logger.error(f"关闭HTTP会话时出错: {str(session_error)}")
                    finally:
                        self._session = None
                        
            # 等待一段时间确保资源释放
            await asyncio.sleep(1)
            
            logger.info("客户端关闭完成")
            
        except Exception as e:
            logger.error(f"关闭客户端时发生错误: {str(e)}")

    async def _handle_connection_error(self):
        """处理连接错误 - 优化版本"""
        if self._closing:
            return False
        
        # 使用连接锁防止并发重连
        async with self._connection_lock:
            if self._is_reconnecting:
                logger.info("已有重连进程在运行，跳过此次重连")
                return False
            
            self._is_reconnecting = True
            
            try:
                self.retry_count += 1
                if self.retry_count <= self.max_retries:
                    wait_time = self.retry_delay * (2 ** (self.retry_count - 1))  # 指数退避
                    logger.warning(f"连接失败，{wait_time}秒后进行第{self.retry_count}次重试...")
                    await asyncio.sleep(wait_time)
                    return True
                else:
                    logger.error("达到最大重试次数，停止重试")
                    return False
            finally:
                self._is_reconnecting = False

    async def on_connect(self):
        """当客户端连接到Discord时触发 - 简化版本"""
        try:
            logger.info("已连接到Discord服务器")
            self._connection_state = "connected"
            self.retry_count = 0  # 重置重试计数
            
            # 重置重连状态
            async with self._connection_lock:
                self._is_reconnecting = False
                
        except Exception as e:
            logger.error(f"连接事件处理时发生错误: {str(e)}")
            self._connection_state = "error"

    async def on_disconnect(self):
        """当客户端断开连接时触发 - 优化版本"""
        try:
            if self._closing:
                logger.info("客户端正在关闭，跳过断开连接处理")
                return
            
            logger.warning("与Discord服务器的连接已断开")
            self._connection_state = "disconnected"
            
            # 检查是否需要重连
            if self._is_reconnecting:
                logger.info("已有重连进程在运行，跳过此次重连")
                return
            
            # 使用异步锁确保只有一个重连进程
            try:
                async with self._connection_lock:
                    if self._is_reconnecting:
                        logger.info("获取锁时发现已有重连进程在运行")
                        return
                    
                    self._is_reconnecting = True
                    
                    # 等待一段时间后再尝试重连
                    await asyncio.sleep(3)
                    
                    if self._closing:
                        logger.info("客户端正在关闭，停止重连")
                        return
                    
                    # 检查是否应该尝试重连
                    if self.retry_count < self.max_retries:
                        logger.info("准备尝试重连...")
                        wait_time = self.retry_delay * (2 ** self.retry_count)
                        logger.info(f"将在{wait_time}秒后尝试重连")
                        await asyncio.sleep(wait_time)
                        
                        if not self._closing:
                            self.retry_count += 1
                            logger.info(f"开始第{self.retry_count}次重连尝试")
                            # 让discord.py自己处理重连
                        else:
                            logger.info("客户端正在关闭，取消重连")
                    else:
                        logger.error("达到最大重试次数，不再重连")
                        # 不要立即关闭，让程序自然结束
                        
            except Exception as e:
                logger.error(f"处理断开连接时发生错误: {str(e)}")
            finally:
                self._is_reconnecting = False
                    
        except Exception as e:
            logger.error(f"断开连接事件处理时发生错误: {str(e)}")
            self._is_reconnecting = False

    async def on_error(self, event, *args, **kwargs):
        """当发生错误时触发 - 优化版本"""
        try:
            logger.error(f"发生错误 - 事件: {event}")
            import traceback
            error_msg = traceback.format_exc()
            logger.error(error_msg)
            
            # 检查是否是并发调用错误
            if "Concurrent call to receive()" in error_msg:
                logger.error("检测到并发调用错误，关闭客户端")
                await self.close()
                return
            
            # 检查是否是网关相关错误
            if "gateway" in error_msg.lower() or "websocket" in error_msg.lower():
                logger.warning("检测到网关错误，等待自动重连...")
                # 不立即重连，让discord.py自己处理
                return
            
            # 检查是否是API相关错误
            if "API" in error_msg or "connection" in error_msg.lower():
                logger.warning("检测到API连接错误，等待自动重连...")
                # 给一些时间让连接稳定
                await asyncio.sleep(5)
                return
            
            # 检查是否是认证错误
            elif "authentication" in error_msg.lower() or "token" in error_msg.lower():
                logger.error("认证失败，请检查token是否正确")
                await self.close()
                raise discord.LoginFailure("认证失败")
            
            # 其他错误
            else:
                logger.error("发生未知错误，继续运行")
                
        except Exception as e:
            logger.error(f"处理错误时发生异常: {str(e)}")
            await self.close()

    async def on_ready(self):
        """当机器人成功登录后触发"""
        try:
            logger.info("\n" + "=" * 50)
            logger.info("Discord Monitor 已就绪")
            logger.info(f"登录账号: {self.user.name}")
            logger.info(f"账号 ID: {self.user.id}")
            
            # 打印所有服务器和频道信息
            guilds = list(self.guilds)
            logger.info(f"\n已加入 {len(guilds)} 个服务器:")
            for guild in guilds:
                logger.info(f"\n服务器: {guild.name} (ID: {guild.id})")
                logger.info("频道列表:")
                for channel in guild.channels:
                    if isinstance(channel, discord.TextChannel):  # 只显示文字频道
                        channel_id = str(channel.id)
                        is_monitored = "✓" if channel_id in self.config.get_channels() else " "
                        logger.info(f"[{is_monitored}] {channel.name} (ID: {channel_id})")
            
            # 打印监控列表
            logger.info("\n监控的频道ID:")
            for channel_id in self.config.get_channels():
                logger.info(f"- {channel_id}")
            
            logger.info("=" * 50 + "\n")
            logger.info("开始监控消息...")
            
        except Exception as e:
            logger.error(f"on_ready 事件处理出错: {str(e)}")
            logger.exception(e)

    async def on_message(self, message):
        try:
            if message.author == self.user:
                return

            if not self.is_monitored_channel(message):
                return
            
            # 定期清理内存
            self.cleanup_memory()
                
            channel_id = str(message.channel.id)
            msg_time = message.created_at + timedelta(hours=8)  # UTC转北京时间
            
            # 初始化消息数据
            message_data = {
                'meme_data': [],
                'search_terms': []
            }
            
            # 处理特定频道
            if channel_id in ["1283359910788202499", "1242865180371587082"]:
                logger.info(f"检测到目标频道消息: {channel_id}")
                
                # 处理嵌入内容中的描述
                for embed in message.embeds:
                    if embed.description:
                        # 匹配两种格式：
                        # 1. [text](url) 格式
                        # 2. 直接是地址格式（包括多行文本中的地址）
                        matches = re.findall(r'\[(.*?)\]|(0x[a-fA-F0-9]{40})', embed.description)
                        if matches:
                            for match in matches:
                                # 如果是元组，取第一个非空元素
                                content = next((item for item in match if item), None)
                                if content:  # 确保内容不为空
                                    meme_row = {
                                        '时间': msg_time.strftime("%Y-%m-%d %H:%M:%S"),
                                        '内容': content,
                                        '频道ID': channel_id
                                    }
                                    message_data['meme_data'].append(meme_row)
                
                # 处理普通消息内容中的```内容
                if channel_id == "1242865180371587082" and message.content:
                    matches = re.findall(r'```(.*?)```', message.content, re.DOTALL)
                    if matches:
                        for match in matches:
                            meme_row = {
                                '时间': msg_time.strftime("%Y-%m-%d %H:%M:%S"),
                                '内容': match.strip(),
                                '频道ID': channel_id
                            }
                            message_data['meme_data'].append(meme_row)
                
                # 保存meme数据
                if message_data['meme_data']:
                    logger.info(f"保存meme数据: {message_data['meme_data']}")
                    await self.save_meme_data(message_data['meme_data'])
            
            # 保存原始消息
            await self.save_message(message)
            
        except Exception as e:
            logger.error(f"处理消息时发生错误: {str(e)}")
            logger.exception(e)

    async def save_message(self, message):
        """保存单条消息"""
        try:
            channel_id = str(message.channel.id)
            
            # 正确处理时间戳 - 使用消息的创建时间并转换为北京时间
            msg_time = message.created_at + timedelta(hours=8)
            
            # 构建消息数据
            message_data = {
                'timestamp': msg_time.strftime("%Y-%m-%d %H:%M:%S"),
                'message_id': str(message.id),
                'channel_id': channel_id,
                'channel_name': self.config.get_channel_name(channel_id),
                'author': str(message.author),
                'author_id': str(message.author.id),
                'content': message.content,
                'attachments': [att.url for att in message.attachments],
                'embeds': [embed.to_dict() for embed in message.embeds]
            }
            
            # 确保频道的消息列表已初始化
            if channel_id not in self.messages:
                self.messages[channel_id] = []
            
            # 添加到对应频道的消息列表
            self.messages[channel_id].append(message_data)
            
            # 保存到文件
            self.save_messages(channel_id)
            
            logger.info(f"消息已保存到频道 {channel_id}: {message.content[:50]}...")
            
        except Exception as e:
            logger.error(f"保存消息时出错: {str(e)}")
            logger.exception(e)

    async def save_meme_data(self, meme_data: List[dict]):
        """保存meme数据到Excel"""
        try:
            # 确保使用正确的文件扩展名
            meme_path = self.data_dir / 'meme.xlsx'
            
            # 检查目录是否存在，不存在则创建
            os.makedirs(self.data_dir, exist_ok=True)
            
            # 尝试使用openpyxl引擎
            if meme_path.exists():
                try:
                    # 明确指定引擎为openpyxl
                    df_meme = pd.read_excel(meme_path, engine='openpyxl')
                    df_meme = pd.concat([df_meme, pd.DataFrame(meme_data)], ignore_index=True)
                except Exception as excel_error:
                    logger.error(f"读取现有Excel文件失败: {excel_error}，创建新文件")
                    df_meme = pd.DataFrame(meme_data)
            else:
                df_meme = pd.DataFrame(meme_data)
            
            # 保存时也指定引擎
            df_meme.to_excel(str(meme_path), index=False, engine='openpyxl')
            logger.info(f"成功保存 {len(meme_data)} 条meme数据到Excel: {str(meme_path)}")
        except Exception as e:
            logger.error(f"保存meme数据时出错: {e}")
            logger.exception(e)

    async def setup_http_session(self):
        """设置HTTP会话"""
        try:
            # 创建TCP连接器
            connector = aiohttp.TCPConnector(
                ssl=ssl_context,  # 使用自定义SSL上下文
                force_close=True,
                enable_cleanup_closed=True,
                ttl_dns_cache=300,
                limit=10,
                family=socket.AF_INET  # 强制使用IPv4
            )
            
            # 设置超时
            timeout = aiohttp.ClientTimeout(
                total=120,  # 增加总超时时间
                connect=60,  # 增加连接超时时间
                sock_connect=60,
                sock_read=60
            )
            
            # 创建会话
            session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': 'application/json',
                    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
                }
            )
            
            logger.info("HTTP会话创建成功")
            return session
            
        except Exception as e:
            logger.error(f"设置HTTP会话时发生错误: {str(e)}")
            # 在出错时创建一个基本的会话作为后备
            try:
                logger.info("尝试创建后备HTTP会话...")
                backup_connector = aiohttp.TCPConnector(
                    ssl=ssl_context,
                    force_close=True,
                    enable_cleanup_closed=True,
                    family=socket.AF_INET
                )
                backup_session = aiohttp.ClientSession(
                    connector=backup_connector,
                    timeout=aiohttp.ClientTimeout(total=60),
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        'Accept': 'application/json'
                    }
                )
                logger.info("成功创建后备HTTP会话")
                return backup_session
            except Exception as backup_error:
                logger.error(f"创建后备HTTP会话也失败: {str(backup_error)}")
                raise

async def run_discord_monitor():
    """运行Discord监控的异步函数 - 改进版本"""
    client = None
    max_restarts = 3
    restart_count = 0
    
    while restart_count < max_restarts:
        try:
            logger.info(f"正在启动Discord监控... (第{restart_count + 1}次)")
            
            # 启动前的健康检查
            await perform_startup_checks()
            
            config = Config()  # 加载配置
            
            # 验证配置
            if not config.get_token():
                logger.error("Discord token未配置或为空")
                raise ValueError("Discord token未配置")
            
            if not config.get_channels():
                logger.warning("没有配置监控频道")
            
            client = SimpleDiscordMonitor(config)
            
            logger.info("开始运行客户端...")
            await client.start(config.get_token())
            
            # 如果正常退出，不需要重启
            break
            
        except discord.LoginFailure as e:
            logger.error(f"登录失败！请检查token是否正确: {str(e)}")
            break  # 登录失败不需要重试
        except ValueError as e:
            logger.error(f"配置错误: {str(e)}")
            break  # 配置错误不需要重试
        except RuntimeError as e:
            if "Concurrent call to receive()" in str(e):
                logger.error("检测到并发调用错误，正在关闭客户端...")
                if client:
                    try:
                        await client.close()
                    except Exception as close_error:
                        logger.error(f"关闭客户端时发生错误: {str(close_error)}")
                
                # 等待一段时间后重试
                restart_count += 1
                if restart_count < max_restarts:
                    wait_time = 30 * restart_count  # 递增等待时间
                    logger.info(f"将在 {wait_time} 秒后重新启动...")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    logger.error("达到最大重启次数，程序退出")
                    break
            else:
                logger.error(f"运行时发生错误: {str(e)}")
                break
        except Exception as e:
            logger.error(f"运行时发生错误: {str(e)}")
            logger.exception(e)
            
            # 对于其他异常，也尝试重启
            restart_count += 1
            if restart_count < max_restarts:
                wait_time = 30 * restart_count
                logger.info(f"将在 {wait_time} 秒后重新启动...")
                await asyncio.sleep(wait_time)
                continue
            else:
                logger.error("达到最大重启次数，程序退出")
                break
        finally:
            if client and not client.is_closed():
                try:
                    await client.close()
                    logger.info("客户端已安全关闭")
                except Exception as e:
                    logger.error(f"关闭客户端时发生错误: {str(e)}")
            client = None  # 清空客户端引用

async def perform_startup_checks():
    """执行启动前的健康检查"""
    try:
        logger.info("正在执行启动前检查...")
        
        # 检查网络连接
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get('https://www.google.com', timeout=10) as response:
                    if response.status == 200:
                        logger.info("网络连接正常")
                    else:
                        logger.warning(f"网络连接可能有问题，状态码: {response.status}")
        except Exception as e:
            logger.warning(f"网络连接检查失败: {str(e)}")
        
        # 检查磁盘空间
        try:
            import shutil
            total, used, free = shutil.disk_usage(".")
            free_gb = free / (1024 ** 3)
            if free_gb < 1.0:
                logger.warning(f"磁盘空间不足: {free_gb:.2f}GB")
            else:
                logger.info(f"磁盘空间充足: {free_gb:.2f}GB")
        except Exception as e:
            logger.warning(f"磁盘空间检查失败: {str(e)}")
        
        # 检查数据目录
        data_dir = Path('data')
        if not data_dir.exists():
            data_dir.mkdir(parents=True, exist_ok=True)
            logger.info("已创建数据目录")
        
        logger.info("启动前检查完成")
        
    except Exception as e:
        logger.error(f"启动前检查失败: {str(e)}")
        # 不要因为健康检查失败而终止程序

def main():
    """主函数，处理信号和运行事件循环 - 改进版本"""
    loop = None
    try:
        # 创建新的事件循环
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # 添加信号处理
        import signal
        shutdown_event = asyncio.Event()
        
        def signal_handler(sig, frame):
            logger.info("接收到停止信号，正在关闭...")
            shutdown_event.set()
            # 不要立即停止循环，让程序自然关闭
            loop.call_soon_threadsafe(shutdown_event.set)
        
        try:
            signal.signal(signal.SIGINT, signal_handler)
            signal.signal(signal.SIGTERM, signal_handler)
        except ValueError:
            # 在某些环境中可能不支持信号处理
            logger.warning("无法设置信号处理器")
        
        # 运行监控
        try:
            loop.run_until_complete(run_discord_monitor())
        except KeyboardInterrupt:
            logger.info("接收到键盘中断信号")
        except RuntimeError as e:
            if "Event loop is closed" in str(e):
                logger.info("事件循环已关闭，正常退出")
            else:
                logger.error(f"事件循环运行时发生错误: {str(e)}")
        
    except KeyboardInterrupt:
        logger.info("接收到键盘中断，正在关闭...")
    except Exception as e:
        logger.error(f"主函数发生错误: {str(e)}")
        logger.exception(e)
    finally:
        if loop and not loop.is_closed():
            try:
                # 获取所有待处理的任务
                pending = asyncio.all_tasks(loop)
                if pending:
                    logger.info(f"正在取消 {len(pending)} 个待处理任务...")
                    
                    # 取消所有任务
                    for task in pending:
                        task.cancel()
                    
                    # 等待所有任务完成或取消
                    try:
                        # 设置超时时间，避免无限等待
                        finished, unfinished = loop.run_until_complete(
                            asyncio.wait_for(
                                asyncio.gather(*pending, return_exceptions=True),
                                timeout=10.0
                            )
                        )
                        
                        if unfinished:
                            logger.warning(f"有 {len(unfinished)} 个任务未完成")
                            
                    except asyncio.TimeoutError:
                        logger.warning("任务取消超时，强制关闭")
                    except Exception as e:
                        logger.error(f"等待任务完成时发生错误: {str(e)}")
                
                # 关闭事件循环
                loop.close()
                logger.info("事件循环已关闭")
                
                # 强制垃圾回收
                import gc
                gc.collect()
                
            except Exception as e:
                logger.error(f"清理事件循环时发生错误: {str(e)}")
        
        logger.info("程序已完全退出")

if __name__ == '__main__':
    main()