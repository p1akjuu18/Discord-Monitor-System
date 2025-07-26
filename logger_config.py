# -*- coding: utf-8 -*-
"""
改进的日志配置模块
提供结构化日志、日志轮转和不同级别的日志记录
"""

import os
import sys
import logging
import logging.handlers
from datetime import datetime
from pathlib import Path
import json


class JSONFormatter(logging.Formatter):
    """JSON格式的日志格式化器"""
    
    def format(self, record):
        log_data = {
            'timestamp': datetime.fromtimestamp(record.created).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno
        }
        
        # 添加异常信息
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
        
        # 添加额外字段
        if hasattr(record, 'user_id'):
            log_data['user_id'] = record.user_id
        if hasattr(record, 'channel_id'):
            log_data['channel_id'] = record.channel_id
        if hasattr(record, 'symbol'):
            log_data['symbol'] = record.symbol
        if hasattr(record, 'operation'):
            log_data['operation'] = record.operation
            
        return json.dumps(log_data, ensure_ascii=False)


class LoggerManager:
    """日志管理器"""
    
    def __init__(self, log_dir: str = "logs", max_bytes: int = 10*1024*1024, backup_count: int = 5):
        self.log_dir = Path(log_dir)
        self.max_bytes = max_bytes  # 10MB
        self.backup_count = backup_count
        self.log_dir.mkdir(exist_ok=True)
        
        # 清理旧日志文件
        self._cleanup_old_logs()
        
        # 设置根日志记录器
        self._setup_root_logger()
        
        # 创建专用日志记录器
        self.setup_loggers()
    
    def _cleanup_old_logs(self):
        """清理过期的日志文件"""
        try:
            log_files = list(self.log_dir.glob("*.log*"))
            if len(log_files) > 20:  # 如果日志文件超过20个
                # 按修改时间排序，删除最旧的文件
                log_files.sort(key=lambda x: x.stat().st_mtime)
                for old_file in log_files[:-15]:  # 只保留最新的15个
                    try:
                        old_file.unlink()
                        print(f"已删除旧日志文件: {old_file.name}")
                    except Exception as e:
                        print(f"删除日志文件失败: {e}")
        except Exception as e:
            print(f"清理日志文件时出错: {e}")
    
    def _setup_root_logger(self):
        """设置根日志记录器"""
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        
        # 清除已有的处理器
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
        
        # 控制台处理器
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        console_handler.setFormatter(console_formatter)
        root_logger.addHandler(console_handler)
    
    def setup_loggers(self):
        """设置专用日志记录器"""
        
        # 主应用日志
        self.app_logger = self._create_logger(
            'app', 
            'app.log',
            level=logging.INFO,
            use_json=True
        )
        
        # Discord监控日志
        self.discord_logger = self._create_logger(
            'discord_monitor',
            'discord.log',
            level=logging.INFO,
            use_json=True
        )
        
        # 交易日志
        self.trading_logger = self._create_logger(
            'trading',
            'trading.log',
            level=logging.INFO,
            use_json=True
        )
        
        # 错误日志
        self.error_logger = self._create_logger(
            'errors',
            'errors.log',
            level=logging.ERROR,
            use_json=True
        )
        
        # API调用日志
        self.api_logger = self._create_logger(
            'api_calls',
            'api.log',
            level=logging.DEBUG,
            use_json=True
        )
        
        # 性能日志
        self.performance_logger = self._create_logger(
            'performance',
            'performance.log',
            level=logging.INFO,
            use_json=True
        )
    
    def _create_logger(self, name: str, filename: str, level: int = logging.INFO, use_json: bool = False):
        """创建日志记录器"""
        logger = logging.getLogger(name)
        logger.setLevel(level)
        
        # 避免重复添加处理器
        if logger.handlers:
            return logger
        
        # 文件处理器（带轮转）
        file_path = self.log_dir / filename
        file_handler = logging.handlers.RotatingFileHandler(
            file_path,
            maxBytes=self.max_bytes,
            backupCount=self.backup_count,
            encoding='utf-8'
        )
        file_handler.setLevel(level)
        
        # 设置格式化器
        if use_json:
            formatter = JSONFormatter()
        else:
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
        
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
        # 防止向上传播到根日志记录器（避免重复记录）
        logger.propagate = False
        
        return logger
    
    def get_logger(self, name: str):
        """获取指定的日志记录器"""
        loggers = {
            'app': self.app_logger,
            'discord': self.discord_logger,
            'trading': self.trading_logger,
            'error': self.error_logger,
            'api': self.api_logger,
            'performance': self.performance_logger
        }
        return loggers.get(name, self.app_logger)
    
    def log_trading_signal(self, symbol: str, side: str, price: float, operation: str, **kwargs):
        """记录交易信号"""
        extra = {
            'symbol': symbol,
            'side': side,
            'price': price,
            'operation': operation,
            **kwargs
        }
        self.trading_logger.info(f"交易信号: {operation} {symbol} {side} @ {price}", extra=extra)
    
    def log_api_call(self, method: str, endpoint: str, duration: float, success: bool, **kwargs):
        """记录API调用"""
        extra = {
            'method': method,
            'endpoint': endpoint,
            'duration': duration,
            'success': success,
            **kwargs
        }
        level = logging.INFO if success else logging.ERROR
        self.api_logger.log(level, f"API调用: {method} {endpoint} - {duration:.3f}s", extra=extra)
    
    def log_performance(self, operation: str, duration: float, memory_usage: dict = None, **kwargs):
        """记录性能信息"""
        extra = {
            'operation': operation,
            'duration': duration,
            **kwargs
        }
        if memory_usage:
            extra.update(memory_usage)
        
        self.performance_logger.info(f"性能: {operation} - {duration:.3f}s", extra=extra)
    
    def log_discord_message(self, channel_id: str, user_id: str, message_type: str, **kwargs):
        """记录Discord消息"""
        extra = {
            'channel_id': channel_id,
            'user_id': user_id,
            'message_type': message_type,
            **kwargs
        }
        self.discord_logger.info(f"Discord消息: {message_type} from {user_id} in {channel_id}", extra=extra)
    
    def log_error(self, error: Exception, context: str = "", **kwargs):
        """记录错误"""
        extra = {
            'context': context,
            'error_type': type(error).__name__,
            **kwargs
        }
        self.error_logger.error(f"错误 [{context}]: {str(error)}", exc_info=error, extra=extra)


# 全局日志管理器实例
logger_manager = LoggerManager()

# 便捷访问
app_logger = logger_manager.get_logger('app')
discord_logger = logger_manager.get_logger('discord')
trading_logger = logger_manager.get_logger('trading')
error_logger = logger_manager.get_logger('error')
api_logger = logger_manager.get_logger('api')
performance_logger = logger_manager.get_logger('performance')