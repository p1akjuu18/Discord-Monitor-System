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
from aiohttp_socks import ProxyConnector
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

# 添加 CoinGecko API 相关常量
COINGECKO_API_BASE_URL = "https://pro-api.coingecko.com/api/v3"

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

# 添加自定义模块导入
from Meme_analysis import MemeAnalyzer, BacktestProcessor, process_message

# 配置管理类
class Config:
    def __init__(self, config_file='config.json'):
        self.config_file = config_file
        self._config = self.load_config(config_file)  # 使用_config存储配置
        self._use_proxy = True  # 添加代理开关
        # 添加飞书配置
        self.feishu_webhook = self._config.get("feishu_webhook", "")
        self.feishu_secret = self._config.get("feishu_secret", "")

    def load_config(self, config_file):
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"加载配置文件失败: {str(e)}")
            raise

    # 添加获取配置的方法
    def get_save_path(self):
        return self._config['monitor']['save_path']

    def get_channels(self):
        return self._config['monitor']['channels']

    def get_token(self):
        return self._config['token']

    def get_proxy(self):
        """获取代理设置"""
        if not self._use_proxy:
            return None
            
        # 从配置文件获取代理设置，如果没有则使用默认值
        proxy = self._config.get('proxy', "http://127.0.0.1:7890")
        return proxy

    def disable_proxy(self):
        self._use_proxy = False
        logger.info("已禁用代理")

    def enable_proxy(self):
        self._use_proxy = True
        logger.info("已启用代理")

    def get_channel_name(self, channel_id):
        """获取频道名称"""
        return self._config['monitor']['channel_names'].get(channel_id, channel_id)

    def get_channel_type(self, channel_id):
        """获取频道类型"""
        return self._config['monitor']['channel_types'].get(channel_id, 'general')

    def get_api_key(self):
        """获取 API key"""
        # 从 api_keys 字典中获取 twitter API key
        api_keys = self._config.get('api_keys', {})
        twitter_api_key = api_keys.get('twitter')
        if not twitter_api_key:
            logger.error("Twitter API key 未在配置文件中设置")
        return twitter_api_key

    def get_deepseek_api_key(self):
        """获取 Deepseek API key"""
        api_keys = self._config.get('api_keys', {})
        return api_keys.get('deepseek')

    def get_coingecko_api_key(self):
        """获取 CoinGecko API key"""
        api_keys = self._config.get('api_keys', {})
        return api_keys.get('coingecko')

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
            # 使用 AI 分析交易信号
            analysis = await self.analyze_trading_signal(message.content)
            
            # 提取关键信息
            trading_info = {
                'timestamp': message.created_at.isoformat(),
                'channel_id': str(message.channel.id),
                'message_id': str(message.id),
                'content': message.content,
                'analysis': analysis,
                'type': 'trading_signal'
            }
            
            # 保存到数据库或文件
            await self.save_trading_info(trading_info)
            
            # 可以添加通知逻辑
            await self.send_notification(trading_info)
            
            return trading_info
            
        except Exception as e:
            logger.error(f"处理交易消息时发生错误: {str(e)}")
            return None

    async def process_social_message(self, message):
        """处理社交媒体消息"""
        try:
            # 检查是否包含 Twitter 链接
            twitter_urls = re.findall(self.message_patterns['twitter'], message.content)
            
            if twitter_urls:
                # 处理 Twitter 链接
                for tweet_id in twitter_urls:
                    tweet_info = await self.analyze_tweet(tweet_id)
                    if tweet_info:
                        await self.save_tweet_info(tweet_info)
            
            return {
                'type': 'social',
                'platform': 'twitter' if twitter_urls else 'unknown',
                'urls': twitter_urls,
                'content': message.content
            }
            
        except Exception as e:
            logger.error(f"处理社交媒体消息时发生错误: {str(e)}")
            return None

    async def analyze_trading_signal(self, content):
        """使用 AI 分析交易信号"""
        prompt = self.config.prompts.get('trading_analysis', '')
        messages = [
            {
                "role": "user",
                "content": f"{prompt}\n\n需要分析的内容:\n{content}"
            }
        ]
        
        response = await self.silicon_client.chat_completion(
            messages=messages,
            model="deepseek-ai/DeepSeek-V3",
            max_tokens=1024,
            temperature=0.7
        )
        
        if response and 'choices' in response:
            return response['choices'][0]['message']['content']
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

    async def send_notification(self, info):
        """发送通知"""
        # 这里可以实现通知逻辑，比如：
        # - 发送到 Telegram
        # - 发送到微信
        # - 发送邮件
        # - 发送到其他 Discord 频道
        pass

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

class TwitterAPI:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.apidance.pro"
        
    async def search_tweets(self, query: str) -> dict:
        """搜索推文"""
        try:
            if not self.api_key:
                logger.error("API key 未设置")
                return {"tweets": []}
            
            headers = {
                "apikey": str(self.api_key),
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            
            # URL编码搜索词并移除特殊字符
            clean_query = re.sub(r'[^\w\s-]', '', query)
            encoded_query = quote(clean_query)
            
            params = {
                "q": encoded_query,
                "cursor": "",
                "sort_by": "Latest",
                "count": "20",
                "result_type": "recent"
            }
            
            logger.info(f"发送搜索请求，清理后的搜索词: {clean_query}")
            logger.info(f"完整请求参数: {params}")
            
            max_retries = 3
            retry_delay = 5
            
            for attempt in range(max_retries):
                try:
                    timeout = aiohttp.ClientTimeout(total=30)
                    connector = aiohttp.TCPConnector(ssl=False)
                    
                    async with aiohttp.ClientSession(connector=connector) as session:
                        url = f"{self.base_url}/sapi/Search"
                        
                        async with session.get(
                            url,
                            params=params,
                            headers=headers,
                            timeout=timeout
                        ) as response:
                            response_text = await response.text()
                            
                            if response.status == 200:
                                try:
                                    data = json.loads(response_text)
                                    
                                    # 检查返回的数据结构
                                    if 'tweets' in data:
                                        tweets = data['tweets']
                                        if tweets is None:  # tweets 字段为 null
                                            logger.info("API返回空结果")
                                            return {"tweets": []}
                                        elif isinstance(tweets, list):
                                            logger.info(f"成功获取 {len(tweets)} 条推文")
                                            return {"tweets": tweets}
                                        else:
                                            logger.error(f"tweets字段格式错误: {type(tweets)}")
                                            return {"tweets": []}
                                    else:
                                        logger.error("API响应中缺少tweets字段")
                                        logger.error(f"完整响应: {data}")
                                        return {"tweets": []}
                                    
                                except json.JSONDecodeError as e:
                                    logger.error(f"JSON解析错误: {str(e)}")
                                    continue
                            
                            elif response.status == 429:
                                wait_time = retry_delay * (2 ** attempt)
                                logger.warning(f"遇到速率限制，等待 {wait_time} 秒")
                                await asyncio.sleep(wait_time)
                                continue
                            else:
                                logger.error(f"HTTP错误: {response.status}")
                                if attempt < max_retries - 1:
                                    await asyncio.sleep(retry_delay)
                                    continue
                                return {"tweets": []}
                                
                except Exception as e:
                    logger.error(f"请求出错: {str(e)}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                        continue
                    return {"tweets": []}
                    
        except Exception as e:
            logger.error(f"搜索推文时发生错误: {str(e)}")
            return {"tweets": []}

class SimpleDiscordMonitor(discord.Client):
    async def setup_http_session(self):
        """设置HTTP会话"""
        try:
            # 从配置中获取代理
            proxy = self.config.get_proxy() or "http://127.0.0.1:7890"
            
            try:
                # 使用 ProxyConnector 设置代理
                connector = ProxyConnector.from_url(
                    proxy,
                    ssl=False,
                    force_close=True,
                    enable_cleanup_closed=True,
                    ttl_dns_cache=300,
                    limit=10,
                    family=socket.AF_INET,
                    rdns=True
                )
                logger.info(f"HTTP会话使用代理: {proxy}")
            except Exception as e:
                logger.error(f"代理设置失败，切换到直连模式: {str(e)}")
                connector = aiohttp.TCPConnector(
                    ssl=False,
                    force_close=True,
                    enable_cleanup_closed=True,
                    ttl_dns_cache=300,
                    limit=10
                )
                logger.info("已切换到直连模式")
            
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
            raise

    def __init__(self, config):
        # 保存配置
        self.config = config
        
        # 获取代理设置
        proxy = config.get_proxy() or "http://127.0.0.1:7890"
        
        # 使用 discord.py-self 的正确初始化方式
        super().__init__(
            self_bot=True,  # 必须设置为 True，表示这是一个用户账号
            chunk_guilds_at_startup=False,  # 不需要加载所有成员
            max_messages=10000,  # 消息缓存上限
            proxy=proxy  # 设置代理
        )
        
        # 初始化其他组件
        self.message_processor = MessageProcessor(config)
        self.messages = {}
        self.last_save_time = {}
        
        # 设置保存目录
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.save_dir = os.path.join(base_dir, 'data', 'messages')
        os.makedirs(self.save_dir, exist_ok=True)
        
        # 初始化API相关
        self.api_key = self.config.get_api_key()
        self.deepseek_api_key = self.config.get_deepseek_api_key()
        
        if not self.api_key:
            logger.error("Twitter API key未在配置文件中设置")
            raise ValueError("Twitter API key is required")
        
        # 初始化API客户端
        self.twitter_api = TwitterAPI(self.api_key)
        
        # 设置数据目录
        self.data_dir = Path('data')
        self.data_dir.mkdir(exist_ok=True)
        
        # 初始化分析器
        self.meme_analyzer = MemeAnalyzer(
            config_file=self.config.config_file,
            api_key=self.deepseek_api_key
        )
        self.backtest_processor = BacktestProcessor()
        
        # 初始化消息文件
        self._init_message_files()
        
        # 添加一个字典来记录已搜索的内容及其最后搜索时间
        self.searched_terms = {}
        
        # 设置清理间隔（比如1小时清理一次已搜索记录）
        self.cleanup_interval = timedelta(hours=1)
        self.last_cleanup = datetime.now()
        
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

    async def handle_meme_channel(self, message):
        """处理meme频道的消息"""
        logger.info("正在处理meme频道的消息")
        # 这里添加处理meme频道消息的逻辑

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
                        if embed.description.strip().startswith('['):
                            matches = re.findall(r'\[(.*?)\]', embed.description)
                            if matches:
                                for match in matches:
                                    meme_row = {
                                        '时间': msg_time.strftime("%Y-%m-%d %H:%M:%S"),
                                        '内容': match,
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
                
                # 先保存meme数据
                if message_data['meme_data']:
                    logger.info(f"保存meme数据: {message_data['meme_data']}")
                    await process_message(message_data)  # 这里假设process_message会保存到meme.xlsx
                    
                    # 检查每个内容的出现频率并进行Twitter搜索
                    current_time = datetime.now()
                    for meme_row in message_data['meme_data']:
                        content = meme_row['内容']
                        if await self.check_meme_frequency(content, current_time):
                            logger.info(f"内容 '{content}' 达到频率阈值，开始Twitter搜索和Deepseek分析")
                            message_data['search_terms'].append(content)
                            
                    # 只有当有搜索词时才进行Twitter和Deepseek处理
                    if message_data['search_terms']:
                        logger.info(f"开始处理搜索词: {message_data['search_terms']}")
                        tweets = await self.twitter_api.search_tweets(message_data['search_terms'][0])
                        if isinstance(tweets, dict) and 'tweets' in tweets:
                            tweet_list = tweets['tweets']
                            if tweet_list:
                                analysis = await self.meme_analyzer.analyze_tweets(
                                    message_data['search_terms'][0], 
                                    tweet_list
                                )
                                if analysis:
                                    await self.meme_analyzer.save_analysis_results([analysis])
                                    logger.info(f"已保存分析结果")
            
            # 保存原始消息
            await self.save_message(message)
            
        except Exception as e:
            logger.error(f"处理消息时发生错误: {str(e)}")
            logger.exception(e)

    # 不同频道的处理方法
    async def handle_general_channel(self, message):
        """处理general频道的消息"""
        logger.info("正在处理general频道的消息")
        # 这里添加特定的处理逻辑
        
    async def handle_other_channels(self, message):
        """处理其他频道的消息"""
        logger.info("正在处理其他频道的消息")
        # 这里添加默认的处理逻辑

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

    async def check_proxy(self):
        """检查代理是否可用"""
        try:
            proxy = self.config.get_proxy()
            async with aiohttp.ClientSession() as session:
                async with session.get('http://httpbin.org/ip', proxy=proxy) as response:
                    if response.status == 200:
                        logger.info(f"代理可用: {proxy}")
                        return True
                    else:
                        logger.error(f"代理不可用: {proxy}")
                        return False
        except Exception as e:
            logger.error(f"检查代理时发生错误: {str(e)}")
            return False

    # 添加从回测代码中复制的相关方法
    async def save_meme_data(self, meme_data: List[dict]):
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
            logger.exception(e)

    async def analyze_tweets(self, term: str, tweets: List[dict]) -> dict:
        """使用 SiliconFlow API 分析推文"""
        max_retries = 3
        retry_delay = 10
        
        for attempt in range(max_retries):
            try:
                # 提取并清理推文内容
                tweet_texts = []
                for tweet in tweets:
                    if not isinstance(tweet, dict):
                        logger.warning(f"跳过无效的推文数据: {tweet}")
                        continue
                        
                    text = tweet.get('text', '')
                    if not text or not isinstance(text, str):
                        logger.warning(f"跳过无效的推文内容: {text}")
                        continue
                        
                    text = text.strip()
                    if text:
                        # 清理合约地址和URL
                        text = re.sub(r'[A-Za-z0-9]{32,}', '', text)
                        text = re.sub(r'https?://\S+', '', text)
                        text = ' '.join(text.split())
                        if text.strip():
                            tweet_texts.append(text)
                
                if not tweet_texts:
                    logger.warning(f"没有找到有效的推文内容用于分析")
                    return self._get_default_analysis(term, len(tweets))
                
                # 去重
                tweet_texts = list(set(tweet_texts))
                
                # 调用 Deepseek API 进行分析
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                }
                
                data = {
                    "model": "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B",
                    "messages": [
                        {
                            "role": "user",
                            "content": f"""你是一个专业的加密货币分析师，Meme 币是一种基于互联网文化、表情包或热点事件的加密货币，其价值主要依赖于社区共识、名人效应和市场情绪。我希望你能帮我评估这个 Meme 币的潜力，并给出详细的分析和建议，请分析以下关于加密货币的推文内容：

{chr(10).join(tweet_texts)}

请从以下两个方面分别进行分析，分2点，并用中文回答，我需要的结果不超过100字，分以下2点明确的返回：

1. 叙事信息：用2-3句话总结这个meme币的核心和它的核心卖点。

2. 可持续性：从以下维度评估：
   - 社区热度
   - 传播潜力
   - 短期投机价值"""
                        }
                    ],
                    "stream": False,
                    "top_p": 0.8,
                    "max_length": 512
                }
                
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{self.base_url}/v1/chat/completions",
                        headers=headers,
                        json=data,
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as response:
                        if response.status != 200:
                            logger.error(f"API请求失败: {response.status}")
                            if attempt < max_retries - 1:
                                await asyncio.sleep(retry_delay)
                                continue
                            return self._get_default_analysis(term, len(tweets))
                        
                        result = await response.json()
                        
                        if not result or 'choices' not in result or not result['choices']:
                            logger.error("API返回的结果格式无效")
                            if attempt < max_retries - 1:
                                await asyncio.sleep(retry_delay)
                                continue
                            return self._get_default_analysis(term, len(tweets))
                        
                        analysis = result['choices'][0]['message']['content']
                        return self._parse_analysis_result(term, analysis, len(tweets))
                        
            except Exception as e:
                logger.error(f"分析推文时出错: {str(e)}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    continue
                return self._get_default_analysis(term, len(tweets))
        
        return self._get_default_analysis(term, len(tweets))

    def _parse_analysis_result(self, term: str, analysis: str, tweet_count: int) -> dict:
        """解析API返回的分析结果"""
        narrative = ""
        community_heat = ""
        spread_potential = ""
        investment_value = ""
        
        analysis = analysis.replace('**', '')
        parts = analysis.split('\n\n')
        
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
        
        return {
            '搜索关键词': term,
            '叙事信息': narrative,
            '可持续性_社区热度': community_heat.replace('-', '').strip(),
            '可持续性_传播潜力': spread_potential.replace('-', '').strip(),
            '可持续性_短期投机价值': investment_value.replace('-', '').strip(),
            '原始推文数量': tweet_count,
            '分析时间': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

    def _get_default_analysis(self, term: str, tweet_count: int) -> dict:
        """返回默认的分析结果"""
        return {
            '搜索关键词': term,
            '叙事信息': f'API分析失败，共有{tweet_count}条推文',
            '可持续性_社区热度': '未知',
            '可持续性_传播潜力': '未知',
            '可持续性_短期投机价值': '未知',
            '原始推文数量': tweet_count,
            '分析时间': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

    async def process_twitter_search(self, search_terms_with_time: List[tuple]):
        """处理Twitter搜索"""
        try:
            twitter_results = []
            analysis_results = []
            current_time = datetime.now(timezone.utc)
            
            # 清理过期的历史记录
            self._cleanup_term_history(current_time)
            
            for term, timestamps in search_terms_with_time:
                # 更新历史记录
                if term not in self.term_history:
                    self.term_history[term] = []
                self.term_history[term].extend([(term, ts) for ts in timestamps])
                
                # 获取最近10分钟内的所有时间戳
                recent_timestamps = [ts for _, ts in self.term_history[term] 
                                  if current_time - ts <= timedelta(minutes=10)]
                
                # 计算在最近10分钟内出现的次数
                occurrence_count = len(recent_timestamps)
                
                if occurrence_count >= self.min_occurrence_threshold:
                    logger.info(f"关键词 '{term}' 在最近10分钟内出现 {occurrence_count} 次，开始搜索")
                    
                    try:
                        results = await self.twitter_api.search_tweets(term)
                        
                        if results and isinstance(results, dict):
                            tweets = results.get('tweets', [])
                            if tweets:
                                term_results = []  # 存储当前term的所有推文结果
                                for tweet in tweets:
                                    tweet_row = {
                                        '搜索时间': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                        '搜索关键词': term,
                                        '出现次数': occurrence_count,
                                        '时间窗口': f"{recent_timestamps[0].strftime('%H:%M:%S')} - {recent_timestamps[-1].strftime('%H:%M:%S')}",
                                        '推文ID': tweet.get('tweet_id', ''),
                                        '发推时间': tweet.get('created_at', ''),
                                        '作者': tweet.get('user', {}).get('screen_name', ''),
                                        '推文内容': tweet.get('text', ''),
                                        '转推数': tweet.get('retweet_count', 0),
                                        '点赞数': tweet.get('favorite_count', 0),
                                        '回复数': tweet.get('reply_count', 0),
                                        '推文URL': f"https://twitter.com/i/web/status/{tweet.get('tweet_id', '')}"
                                    }
                                    term_results.append(tweet_row)
                                
                                # 将当前搜索词的结果添加到总结果中
                                twitter_results.extend(term_results)
                                
                                # 立即保存当前批次的Twitter结果
                                await self.save_twitter_results(term_results)
                                
                                # 立即对当前搜索词的推文进行分析
                                analysis_result = await self.analyze_tweets(term, term_results)
                                if analysis_result:
                                    analysis_results.append(analysis_result)
                                    # 每处理一个词就保存一次结果
                                    await self.save_analysis_results([analysis_result])
                                    logger.info(f"已完成对 '{term}' 的推文分析并保存结果")
                            else:
                                logger.info(f"搜索词 '{term}' 未找到任何推文")
                        else:
                            logger.warning(f"搜索词 '{term}' 返回的数据格式不正确")
                            
                    except Exception as e:
                        logger.error(f"处理搜索词 '{term}' 时出错: {str(e)}")
                        continue
                else:
                    logger.info(f"关键词 '{term}' 在最近10分钟内出现 {occurrence_count} 次，跳过搜索")
                
                await asyncio.sleep(5)  # 搜索词之间的延迟
            
            # 最后保存所有结果
            if analysis_results:
                await self.save_analysis_results(analysis_results)
                
        except Exception as e:
            logger.error(f"处理Twitter搜索时出错: {str(e)}")
            logger.exception(e)

    def _cleanup_term_history(self, current_time: datetime):
        """清理过期的历史记录"""
        for term in list(self.term_history.keys()):
            self.term_history[term] = [
                (content, ts) for content, ts in self.term_history[term]
                if current_time - ts <= self.history_cleanup_threshold
            ]
            if not self.term_history[term]:
                del self.term_history[term]

    async def save_twitter_results(self, twitter_results: List[dict]):
        """保存Twitter搜索结果到Excel"""
        try:
            if self.twitter_results_path.exists():
                df_twitter = pd.read_excel(self.twitter_results_path)
                df_twitter = pd.concat([df_twitter, pd.DataFrame(twitter_results)], ignore_index=True)
            else:
                df_twitter = pd.DataFrame(twitter_results)
            
            df_twitter.to_excel(self.twitter_results_path, index=False)
            logger.info(f"成功保存 {len(twitter_results)} 条推文到Excel")
        except Exception as e:
            logger.error(f"保存Twitter结果时出错: {e}")
            logger.exception(e)

    async def save_analysis_results(self, analysis_results: List[dict]):
        """保存分析结果到Excel"""
        try:
            if self.analysis_path.exists():
                df_analysis = pd.read_excel(self.analysis_path)
                df_analysis = pd.concat([df_analysis, pd.DataFrame(analysis_results)], ignore_index=True)
            else:
                df_analysis = pd.DataFrame(analysis_results)
            
            df_analysis.to_excel(self.analysis_path, index=False)
            logger.info(f"成功保存分析结果到Excel")
        except Exception as e:
            logger.error(f"保存分析结果时出错: {e}")
            logger.exception(e)

    async def get_token_pools(self, network: str, token_address: str) -> dict:
        """
        获取特定网络上代币的流动性池信息
        
        参数:
            network (str): 网络名称，如 'ethereum', 'binance-smart-chain' 等
            token_address (str): 代币合约地址
            
        返回:
            dict: 包含代币池信息的字典
        """
        try:
            logger.info(f"正在获取 {network} 网络上代币 {token_address} 的池信息")
            
            # 构建API URL
            url = f"{COINGECKO_API_BASE_URL}/onchain/networks/{network}/tokens/{token_address}/pools"
            
            # 设置请求头
            headers = {
                "x-cg-pro-api-key": self.coingecko_api_key,
                "Content-Type": "application/json"
            }
            
            # 发送请求
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, 
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"获取代币池信息失败: 状态码 {response.status}, 错误: {error_text}")
                        return {"error": f"API请求失败: {response.status}", "details": error_text}
                    
                    result = await response.json()
                    logger.info(f"成功获取代币池信息: {len(result.get('pools', []))} 个池")
                    
                    # 保存结果到文件
                    await self.save_token_pools_data(network, token_address, result)
                    
                    return result
                    
        except Exception as e:
            logger.error(f"获取代币池信息时出错: {str(e)}")
            logger.exception(e)
            return {"error": f"请求异常: {str(e)}"}
            
    async def save_token_pools_data(self, network: str, token_address: str, data: dict):
        """保存代币池数据到文件"""
        try:
            # 创建保存目录
            pools_dir = self.data_dir / 'token_pools'
            pools_dir.mkdir(exist_ok=True)
            
            # 创建文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{network}_{token_address.lower()}_{timestamp}.json"
            file_path = pools_dir / filename
            
            # 保存数据
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                
            logger.info(f"已保存代币池数据到: {file_path}")
            
            # 如果有池信息，也保存到Excel
            if 'pools' in data and data['pools']:
                # 提取池信息
                pools_data = []
                for pool in data['pools']:
                    pool_data = {
                        '网络': network,
                        '代币地址': token_address,
                        '池地址': pool.get('address', ''),
                        '池名称': pool.get('name', ''),
                        '池类型': pool.get('type', ''),
                        '总流动性(USD)': pool.get('total_liquidity_usd', 0),
                        '代币流动性(USD)': pool.get('token_liquidity_usd', 0),
                        '交易量24h(USD)': pool.get('volume_24h_usd', 0),
                        '获取时间': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                    pools_data.append(pool_data)
                
                # 保存到Excel
                excel_path = self.data_dir / 'token_pools.xlsx'
                if excel_path.exists():
                    df_pools = pd.read_excel(excel_path)
                    df_pools = pd.concat([df_pools, pd.DataFrame(pools_data)], ignore_index=True)
                else:
                    df_pools = pd.DataFrame(pools_data)
                
                df_pools.to_excel(excel_path, index=False)
                logger.info(f"已保存 {len(pools_data)} 条池数据到Excel")
                
        except Exception as e:
            logger.error(f"保存代币池数据时出错: {e}")
            logger.exception(e)
            
    async def process_token_address(self, message):
        """处理消息中的代币地址"""
        try:
            # 检查消息内容是否包含代币地址
            # 以太坊地址格式: 0x 后跟 40 个十六进制字符
            eth_address_pattern = r'0x[a-fA-F0-9]{40}'
            addresses = re.findall(eth_address_pattern, message.content)
            
            if addresses:
                logger.info(f"在消息中发现 {len(addresses)} 个代币地址")
                
                for address in addresses:
                    # 默认在以太坊网络上查询
                    network = "ethereum"
                    
                    # 检查消息中是否指定了网络
                    if "bsc" in message.content.lower() or "binance" in message.content.lower():
                        network = "binance-smart-chain"
                    elif "polygon" in message.content.lower():
                        network = "polygon-pos"
                    elif "arbitrum" in message.content.lower():
                        network = "arbitrum-one"
                    elif "optimism" in message.content.lower():
                        network = "optimistic-ethereum"
                    
                    # 获取代币池信息
                    pools_data = await self.get_token_pools(network, address)
                    
                    # 如果成功获取数据，可以在这里添加进一步处理逻辑
                    if pools_data and "error" not in pools_data:
                        # 例如，可以发送通知或进行分析
                        pass
                
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"处理代币地址时出错: {str(e)}")
            logger.exception(e)
            return False

    async def check_meme_frequency(self, content: str, current_time: datetime) -> bool:
        """
        检查内容在最近10分钟内是否出现3次或以上，且未被搜索过
        """
        try:
            # 清理过期的搜索记录
            self._cleanup_searched_terms(current_time)
            
            # 如果内容在最近搜索过，直接返回False
            if content in self.searched_terms:
                logger.info(f"内容 '{content}' 已经在 {self.searched_terms[content].strftime('%Y-%m-%d %H:%M:%S')} 搜索过，跳过")
                return False
            
            meme_file = os.path.join(self.data_dir, 'meme.xlsx')
            if not os.path.exists(meme_file):
                logger.info("meme.xlsx文件不存在")
                return False
            
            df = pd.read_excel(meme_file)
            df['时间'] = pd.to_datetime(df['时间'])
            
            ten_mins_ago = current_time - timedelta(minutes=10)
            recent_records = df[df['时间'] >= ten_mins_ago]
            occurrence_count = len(recent_records[recent_records['内容'] == content])
            
            logger.info(f"内容 '{content}' 在最近10分钟内出现 {occurrence_count} 次")
            
            # 如果达到阈值，记录这次搜索
            if occurrence_count >= 3:
                self.searched_terms[content] = current_time
                return True
                
            return False
            
        except Exception as e:
            logger.error(f"检查meme频率时出错: {str(e)}")
            logger.exception(e)
            return False

    def _cleanup_searched_terms(self, current_time: datetime):
        """
        清理超过1小时的搜索记录
        """
        try:
            # 每小时才进行一次清理
            if current_time - self.last_cleanup < self.cleanup_interval:
                return
                
            self.last_cleanup = current_time
            cleanup_threshold = current_time - self.cleanup_interval
            
            # 清理旧记录
            self.searched_terms = {
                term: timestamp 
                for term, timestamp in self.searched_terms.items() 
                if timestamp > cleanup_threshold
            }
            
            if len(self.searched_terms) > 0:
                logger.info(f"清理搜索记录后剩余 {len(self.searched_terms)} 个记录")
                
        except Exception as e:
            logger.error(f"清理搜索记录时出错: {str(e)}")

    def send_to_feishu(self, message):
        """发送消息到飞书群聊"""
        timestamp = str(int(time.time()))
        
        # 计算签名
        string_to_sign = f"{timestamp}\n{self.config.feishu_secret}"
        hmac_code = hmac.new(
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256
        ).digest()
        sign = base64.b64encode(hmac_code).decode('utf-8')
        
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
            self.config.feishu_webhook,
            headers=headers,
            json=payload
        )
        return response.status_code == 200

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
        client.run(config.get_token())  # 使用方法获取token
    except discord.LoginFailure:
        logger.error("登录失败！请检查token是否正确")
    except Exception as e:
        logger.error(f"运行时发生错误: {str(e)}")
        logger.exception(e)

if __name__ == '__main__':
    main()