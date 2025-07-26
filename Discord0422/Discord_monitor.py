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

# 设置日志 - 移到最前面
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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

# 配置管理类
class Config:
    def __init__(self, config_file='config.json'):
        self.config_file = config_file
        self._config = self.load_config(config_file)  # 使用_config存储配置
        # 添加飞书配置
        self.feishu_webhook = self._config.get("feishu_webhook", "")
        self.feishu_secret = self._config.get("feishu_secret", "")

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
        return self._config['token']

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

# 应用补丁
patch_discord()

class SimpleDiscordMonitor(discord.Client):
    async def setup_http_session(self):
        """设置HTTP会话"""
        try:
            # 创建TCP连接器
            connector = aiohttp.TCPConnector(
                ssl=False,
                force_close=True,
                enable_cleanup_closed=True,
                ttl_dns_cache=300,
                limit=10
            )
            
            # 设置超时
            timeout = aiohttp.ClientTimeout(
                total=60,
                connect=30,
                sock_connect=30,
                sock_read=30
            )
            
            # 创建会话
            session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept': 'application/json'
                }
            )
            
            return session
            
        except Exception as e:
            logger.error(f"设置HTTP会话时发生错误: {str(e)}")
            # 在出错时创建一个基本的会话作为后备
            try:
                logger.info("尝试创建后备HTTP会话...")
                backup_connector = aiohttp.TCPConnector(ssl=False, force_close=True)
                backup_session = aiohttp.ClientSession(
                    connector=backup_connector,
                    headers={'User-Agent': 'Mozilla/5.0'}
                )
                logger.info("成功创建后备HTTP会话")
                return backup_session
            except Exception as backup_error:
                logger.error(f"创建后备HTTP会话也失败: {str(backup_error)}")
                raise

    def __init__(self, config):
        # 保存配置
        self.config = config
        
        # 使用 discord.py-self 的正确初始化方式
        super().__init__(
            self_bot=True,  # 必须设置为 True，表示这是一个用户账号
            chunk_guilds_at_startup=False,  # 不需要加载所有成员
            max_messages=10000  # 消息缓存上限
        )
        
        # 初始化其他组件
        self.message_processor = MessageProcessor(config)
        self.messages = {}
        self.last_save_time = {}
        
        # 设置保存目录
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.save_dir = os.path.join(base_dir, 'data', 'messages')
        os.makedirs(self.save_dir, exist_ok=True)
        
        # 设置数据目录
        self.data_dir = Path('data')
        self.data_dir.mkdir(exist_ok=True)
        
        # 初始化消息文件
        self._init_message_files()
        
        logger.info("Discord客户端初始化完成")

    def _init_message_files(self):
        """初始化消息文件"""
        for channel_id in self.config.get_channels():
            channel_name = self.config.get_channel_name(channel_id)
            filename = f"{channel_id}-{channel_name}.json"
            filename = "".join(c for c in filename if c.isalnum() or c in ('-', '_', '.'))
            
            channel_file = os.path.join(self.save_dir, filename)
            try:
                if os.path.exists(channel_file):
                    with open(channel_file, 'r', encoding='utf-8') as f:
                        self.messages[channel_id] = json.load(f)
                    logger.info(f"已加载频道 {channel_name} ({channel_id}) 的消息文件")
                else:
                    self.messages[channel_id] = []
                    with open(channel_file, 'w', encoding='utf-8') as f:
                        json.dump([], f, ensure_ascii=False, indent=2)
                    logger.info(f"已创建频道 {channel_name} ({channel_id}) 的消息文件")
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
            # 获取频道名称
            channel_name = self.config.get_channel_name(channel_id)
            # 使用频道名称作为文件名
            filename = f"{channel_id}-{channel_name}.json"
            # 替换文件名中的非法字符
            filename = "".join(c for c in filename if c.isalnum() or c in ('-', '_', '.'))
            
            channel_file = os.path.join(self.save_dir, filename)
            with open(channel_file, 'w', encoding='utf-8') as f:
                json.dump(self.messages[channel_id], f, ensure_ascii=False, indent=2)
            logger.info(f"消息已保存到频道 {channel_name} ({channel_id})")
        except Exception as e:
            logger.error(f"保存频道 {channel_id} 的消息时出错: {str(e)}")
            logger.exception(e)

    async def setup_hook(self) -> None:
        """设置钩子，在客户端准备好之前调用"""
        try:
            logger.info("setup_hook 被调用")
            session = await self.setup_http_session()
            self.http.session = session
            logger.info("setup_hook 完成")
            
            # 在这里也添加一个测试日志
            logger.info("等待 on_ready 事件...")
        except Exception as e:
            logger.error(f"设置钩子时发生错误: {str(e)}")
            raise

    async def on_connect(self):
        """当客户端连接到Discord时触发"""
        logger.info("已连接到Discord服务器")

    async def on_disconnect(self):
        """当客户端断开连接时触发"""
        logger.warning("与Discord服务器的连接已断开，尝试重新连接...")

    async def on_error(self, event, *args, **kwargs):
        """当发生错误时触发"""
        logger.error(f"发生错误 - 事件: {event}")
        import traceback
        logger.error(traceback.format_exc())

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
            
            # 构建消息数据
            message_data = {
                'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
            
            logger.info(f"消息已保存到频道 {channel_id}")
            
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


def main():
    try:
        logger.info("正在启动Discord监控...")
        config = Config()  # 加载配置
        
        client = SimpleDiscordMonitor(config)
        
        # 添加信号处理
        import signal
        def signal_handler(sig, frame):
            logger.info("正在关闭客户端...")
            asyncio.create_task(client.close())
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        logger.info("开始运行客户端...")
        client.run(config.get_token())
        
    except discord.LoginFailure:
        logger.error("登录失败！请检查token是否正确")
    except Exception as e:
        logger.error(f"运行时发生错误: {str(e)}")
        logger.exception(e)

if __name__ == '__main__':
    main()