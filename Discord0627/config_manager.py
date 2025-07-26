# -*- coding: utf-8 -*-
"""
统一配置管理模块
负责从环境变量和配置文件中安全地加载配置信息
"""

import os
import json
import logging
from typing import Dict, List, Optional, Any
from pathlib import Path

logger = logging.getLogger(__name__)

class ConfigManager:
    """统一配置管理类"""
    
    def __init__(self, config_file: str = 'config.json', env_file: str = '.env'):
        self.config_file = config_file
        self.env_file = env_file
        self._config = {}
        self._load_env_file()
        self._load_config_file()
        self._validate_config()
    
    def _load_env_file(self):
        """加载环境变量文件"""
        env_path = Path(self.env_file)
        if env_path.exists():
            try:
                with open(env_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, value = line.split('=', 1)
                            os.environ[key.strip()] = value.strip()
                logger.info("已加载环境变量文件")
            except Exception as e:
                logger.error(f"加载环境变量文件失败: {e}")
        else:
            logger.warning(f"环境变量文件不存在: {env_path}")
    
    def _load_config_file(self):
        """加载配置文件（不包含敏感信息）"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    file_config = json.load(f)
                    # 只加载非敏感配置
                    self._config = {
                        'monitor': file_config.get('monitor', {}),
                        'trading': {
                            'position_size': self.get_env_int('TRADING_POSITION_SIZE', 200),
                            'leverage': self.get_env_int('TRADING_LEVERAGE', 5),
                            'margin_type': self.get_env('TRADING_MARGIN_TYPE', 'CROSSED')
                        },
                        'btc': {
                            'initial_capital': self.get_env_int('BTC_INITIAL_CAPITAL', 1000),
                            'leverage': self.get_env_int('BTC_LEVERAGE', 60)
                        }
                    }
                logger.info("已加载配置文件")
            else:
                self._create_default_config()
        except Exception as e:
            logger.error(f"加载配置文件失败: {e}")
            self._create_default_config()
    
    def _create_default_config(self):
        """创建默认配置"""
        self._config = {
            'monitor': {
                'save_path': 'data/messages',
                'channels': [],
                'channel_names': {},
                'channel_types': {}
            },
            'trading': {
                'position_size': 200,
                'leverage': 5,
                'margin_type': 'CROSSED'
            },
            'btc': {
                'initial_capital': 1000,
                'leverage': 60
            }
        }
        logger.info("使用默认配置")
    
    def _validate_config(self):
        """验证关键配置项"""
        required_env_vars = [
            'DISCORD_TOKEN',
            'BINANCE_API_KEY', 
            'BINANCE_API_SECRET'
        ]
        
        missing_vars = []
        for var in required_env_vars:
            if not os.getenv(var):
                missing_vars.append(var)
        
        if missing_vars:
            logger.error(f"缺少必要的环境变量: {missing_vars}")
            raise ValueError(f"请在.env文件中设置以下环境变量: {missing_vars}")
        
        logger.info("配置验证通过")
    
    def get_env(self, key: str, default: str = None) -> Optional[str]:
        """获取环境变量"""
        return os.getenv(key, default)
    
    def get_env_int(self, key: str, default: int = None) -> Optional[int]:
        """获取整数型环境变量"""
        value = os.getenv(key)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            logger.warning(f"环境变量 {key} 的值 '{value}' 不是有效整数，使用默认值 {default}")
            return default
    
    def get_env_float(self, key: str, default: float = None) -> Optional[float]:
        """获取浮点型环境变量"""
        value = os.getenv(key)
        if value is None:
            return default
        try:
            return float(value)
        except ValueError:
            logger.warning(f"环境变量 {key} 的值 '{value}' 不是有效浮点数，使用默认值 {default}")
            return default
    
    def get_env_list(self, key: str, default: List[str] = None) -> List[str]:
        """获取列表型环境变量（逗号分隔）"""
        value = os.getenv(key)
        if value is None:
            return default or []
        return [item.strip() for item in value.split(',') if item.strip()]
    
    def get_env_bool(self, key: str, default: bool = False) -> bool:
        """获取布尔型环境变量"""
        value = os.getenv(key, '').lower()
        if value in ('true', '1', 'yes', 'on'):
            return True
        elif value in ('false', '0', 'no', 'off'):
            return False
        return default
    
    # Discord配置
    def get_discord_token(self) -> str:
        """获取Discord Token"""
        return self.get_env('DISCORD_TOKEN')
    
    # Binance配置
    def get_binance_api_key(self) -> str:
        """获取Binance API Key"""
        return self.get_env('BINANCE_API_KEY')
    
    def get_binance_api_secret(self) -> str:
        """获取Binance API Secret"""
        return self.get_env('BINANCE_API_SECRET')
    
    # 第三方API配置
    def get_deepseek_api_key(self) -> Optional[str]:
        """获取DeepSeek API Key"""
        return self.get_env('DEEPSEEK_API_KEY')
    
    def get_twitter_api_key(self) -> Optional[str]:
        """获取Twitter API Key"""
        return self.get_env('TWITTER_API_KEY')
    
    def get_coingecko_api_key(self) -> Optional[str]:
        """获取CoinGecko API Key"""
        return self.get_env('COINGECKO_API_KEY')
    
    # 飞书配置
    def get_feishu_app_id(self) -> Optional[str]:
        """获取飞书App ID"""
        return self.get_env('FEISHU_APP_ID')
    
    def get_feishu_app_secret(self) -> Optional[str]:
        """获取飞书App Secret"""
        return self.get_env('FEISHU_APP_SECRET')
    
    def get_feishu_chat_id(self) -> Optional[str]:
        """获取飞书Chat ID"""
        return self.get_env('FEISHU_CHAT_ID')
    
    def get_feishu_webhook_url(self) -> Optional[str]:
        """获取飞书Webhook URL"""
        return self.get_env('FEISHU_WEBHOOK_URL')
    
    # Telegram配置
    def get_telegram_bot_token(self) -> Optional[str]:
        """获取Telegram Bot Token"""
        return self.get_env('TELEGRAM_BOT_TOKEN')
    
    def get_telegram_chat_ids(self) -> List[str]:
        """获取Telegram Chat IDs"""
        return self.get_env_list('TELEGRAM_CHAT_IDS')
    
    # 应用配置
    def get_monitor_config(self) -> Dict:
        """获取监控配置"""
        return self._config.get('monitor', {})
    
    def get_trading_config(self) -> Dict:
        """获取交易配置"""
        return self._config.get('trading', {})
    
    def get_btc_config(self) -> Dict:
        """获取BTC配置"""
        return self._config.get('btc', {})
    
    def get_save_path(self) -> str:
        """获取保存路径"""
        return self._config['monitor'].get('save_path', 'data/messages')
    
    def get_channels(self) -> List[str]:
        """获取监控频道列表"""
        return self._config['monitor'].get('channels', [])
    
    def get_channel_names(self) -> Dict[str, str]:
        """获取频道名称映射"""
        return self._config['monitor'].get('channel_names', {})
    
    def get_channel_types(self) -> Dict[str, str]:
        """获取频道类型映射"""
        return self._config['monitor'].get('channel_types', {})
    
    def get_channel_name(self, channel_id: str) -> str:
        """获取频道名称"""
        return self.get_channel_names().get(channel_id, channel_id)
    
    def get_channel_type(self, channel_id: str) -> str:
        """获取频道类型"""
        return self.get_channel_types().get(channel_id, 'general')
    
    def update_config(self, key: str, value: Any):
        """更新配置"""
        keys = key.split('.')
        config = self._config
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]
        config[keys[-1]] = value
        logger.info(f"配置已更新: {key} = {value}")
    
    def save_config(self):
        """保存配置到文件（不包含敏感信息）"""
        try:
            safe_config = {
                'monitor': self._config.get('monitor', {}),
                # 不保存敏感的API密钥等信息
            }
            
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(safe_config, f, ensure_ascii=False, indent=2)
            logger.info("配置已保存到文件")
        except Exception as e:
            logger.error(f"保存配置失败: {e}")

# 全局配置实例
config_manager = ConfigManager()