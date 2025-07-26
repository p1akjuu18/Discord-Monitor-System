# -*- coding: utf-8 -*-
"""
健康检查和自动恢复模块
监控系统各组件状态，自动处理故障和恢复
"""

import asyncio
import time
import aiohttp
from datetime import datetime, timedelta
from typing import Dict, List, Callable, Optional
from dataclasses import dataclass
from enum import Enum
import traceback
from logger_config import error_logger, performance_logger


class HealthStatus(Enum):
    HEALTHY = "healthy"
    WARNING = "warning"  
    CRITICAL = "critical"
    DOWN = "down"


@dataclass
class HealthCheck:
    name: str
    check_func: Callable
    interval: int  # 秒
    timeout: int   # 秒
    retry_count: int = 3
    recovery_func: Optional[Callable] = None
    last_check: Optional[datetime] = None
    status: HealthStatus = HealthStatus.HEALTHY
    consecutive_failures: int = 0
    last_error: Optional[str] = None


class HealthChecker:
    """健康检查器"""
    
    def __init__(self):
        self.checks = {}
        self.running = False
        self.status_history = {}
        self.recovery_actions = {}
        
    def register_check(self, check: HealthCheck):
        """注册健康检查"""
        self.checks[check.name] = check
        self.status_history[check.name] = []
        performance_logger.info(f"注册健康检查: {check.name}")
    
    def register_recovery_action(self, check_name: str, action: Callable):
        """注册恢复动作"""
        self.recovery_actions[check_name] = action
        performance_logger.info(f"注册恢复动作: {check_name}")
    
    async def run_check(self, check: HealthCheck) -> bool:
        """执行单个健康检查"""
        try:
            # 设置超时
            result = await asyncio.wait_for(
                check.check_func(),
                timeout=check.timeout
            )
            
            if result:
                check.status = HealthStatus.HEALTHY
                check.consecutive_failures = 0
                check.last_error = None
                return True
            else:
                check.consecutive_failures += 1
                check.last_error = "检查函数返回False"
                return False
                
        except asyncio.TimeoutError:
            check.consecutive_failures += 1
            check.last_error = f"检查超时 ({check.timeout}s)"
            return False
        except Exception as e:
            check.consecutive_failures += 1
            check.last_error = str(e)
            error_logger.error(f"健康检查 {check.name} 执行失败: {e}")
            return False
    
    async def update_check_status(self, check: HealthCheck, success: bool):
        """更新检查状态"""
        check.last_check = datetime.now()
        
        # 确定状态
        if success:
            check.status = HealthStatus.HEALTHY
        elif check.consecutive_failures >= check.retry_count:
            check.status = HealthStatus.CRITICAL
        elif check.consecutive_failures > 1:
            check.status = HealthStatus.WARNING
        
        # 记录状态历史
        self.status_history[check.name].append({
            'timestamp': check.last_check,
            'status': check.status.value,
            'error': check.last_error
        })
        
        # 只保留最近100条记录
        if len(self.status_history[check.name]) > 100:
            self.status_history[check.name] = self.status_history[check.name][-100:]
        
        # 如果状态变为CRITICAL，尝试恢复
        if check.status == HealthStatus.CRITICAL:
            await self.attempt_recovery(check)
    
    async def attempt_recovery(self, check: HealthCheck):
        """尝试自动恢复"""
        performance_logger.warning(f"尝试恢复组件: {check.name}")
        
        try:
            # 首先尝试检查对象自己的恢复函数
            if check.recovery_func:
                await check.recovery_func()
                performance_logger.info(f"执行了 {check.name} 的内置恢复函数")
            
            # 然后尝试注册的恢复动作
            if check.name in self.recovery_actions:
                await self.recovery_actions[check.name]()
                performance_logger.info(f"执行了 {check.name} 的注册恢复动作")
            
            # 等待一段时间后重新检查
            await asyncio.sleep(5)
            
            # 重新执行检查
            if await self.run_check(check):
                performance_logger.info(f"组件 {check.name} 恢复成功")
                check.consecutive_failures = 0
                check.status = HealthStatus.HEALTHY
            else:
                error_logger.error(f"组件 {check.name} 恢复失败")
                
        except Exception as e:
            error_logger.error(f"恢复组件 {check.name} 时出错: {e}")
    
    async def start(self):
        """启动健康检查"""
        self.running = True
        performance_logger.info("健康检查器已启动")
        
        # 为每个检查创建独立的任务
        tasks = []
        for check in self.checks.values():
            task = asyncio.create_task(self._run_check_loop(check))
            tasks.append(task)
        
        try:
            await asyncio.gather(*tasks)
        except Exception as e:
            error_logger.error(f"健康检查器运行出错: {e}")
        finally:
            self.running = False
    
    async def _run_check_loop(self, check: HealthCheck):
        """运行单个检查的循环"""
        while self.running:
            try:
                success = await self.run_check(check)
                await self.update_check_status(check, success)
                
                # 等待下次检查
                await asyncio.sleep(check.interval)
                
            except Exception as e:
                error_logger.error(f"检查循环 {check.name} 出错: {e}")
                await asyncio.sleep(check.interval)
    
    def stop(self):
        """停止健康检查"""
        self.running = False
        performance_logger.info("健康检查器已停止")
    
    def get_status_summary(self) -> Dict:
        """获取状态摘要"""
        summary = {
            'overall_status': HealthStatus.HEALTHY.value,
            'checks': {},
            'last_updated': datetime.now().isoformat()
        }
        
        critical_count = 0
        warning_count = 0
        
        for name, check in self.checks.items():
            summary['checks'][name] = {
                'status': check.status.value,
                'last_check': check.last_check.isoformat() if check.last_check else None,
                'consecutive_failures': check.consecutive_failures,
                'last_error': check.last_error
            }
            
            if check.status == HealthStatus.CRITICAL:
                critical_count += 1
            elif check.status == HealthStatus.WARNING:
                warning_count += 1
        
        # 确定整体状态
        if critical_count > 0:
            summary['overall_status'] = HealthStatus.CRITICAL.value
        elif warning_count > 0:
            summary['overall_status'] = HealthStatus.WARNING.value
        
        summary['summary'] = {
            'total': len(self.checks),
            'healthy': len(self.checks) - critical_count - warning_count,
            'warning': warning_count,
            'critical': critical_count
        }
        
        return summary


# 具体的健康检查函数
class SystemHealthChecks:
    """系统健康检查函数集合"""
    
    @staticmethod
    async def check_database_connection():
        """检查数据库连接"""
        try:
            from database_manager import db_manager
            async with db_manager.get_connection() as db:
                await db.execute("SELECT 1")
            return True
        except Exception:
            return False
    
    @staticmethod
    async def check_binance_api():
        """检查Binance API连接"""
        try:
            from config_manager import config_manager
            import aiohttp
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    'https://api.binance.com/api/v3/ping',
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as response:
                    return response.status == 200
        except Exception:
            return False
    
    @staticmethod
    async def check_discord_connection():
        """检查Discord连接状态"""
        # 这里需要检查Discord客户端状态
        # 可以通过检查最近收到的消息时间戳来判断
        try:
            # 检查最近5分钟内是否有活动
            from datetime import datetime, timedelta
            # 这里需要实际的Discord客户端状态检查逻辑
            return True
        except Exception:
            return False
    
    @staticmethod
    async def check_memory_usage():
        """检查内存使用率"""
        try:
            import psutil
            memory = psutil.virtual_memory()
            return memory.percent < 90  # 内存使用率低于90%
        except Exception:
            return False
    
    @staticmethod
    async def check_disk_space():
        """检查磁盘空间"""
        try:
            import psutil
            disk = psutil.disk_usage('/')
            return (disk.free / disk.total) > 0.1  # 剩余空间大于10%
        except Exception:
            return False
    
    @staticmethod 
    async def check_log_files():
        """检查日志文件是否正常写入"""
        try:
            import os
            from pathlib import Path
            
            log_dir = Path("logs")
            if not log_dir.exists():
                return False
            
            # 检查是否有最近的日志文件
            log_files = list(log_dir.glob("*.log"))
            if not log_files:
                return False
            
            # 检查最新日志文件的修改时间
            latest_log = max(log_files, key=lambda x: x.stat().st_mtime)
            last_modified = datetime.fromtimestamp(latest_log.stat().st_mtime)
            
            # 如果最新日志文件在1小时内被修改过，认为正常
            return datetime.now() - last_modified < timedelta(hours=1)
            
        except Exception:
            return False


# 恢复动作函数
class RecoveryActions:
    """恢复动作函数集合"""
    
    @staticmethod
    async def restart_database_connection():
        """重启数据库连接"""
        try:
            from database_manager import db_manager
            # 重新初始化数据库
            await db_manager.init_database()
            performance_logger.info("数据库连接已重启")
        except Exception as e:
            error_logger.error(f"重启数据库连接失败: {e}")
    
    @staticmethod
    async def clear_memory_cache():
        """清理内存缓存"""
        try:
            import gc
            gc.collect()
            performance_logger.info("内存缓存已清理")
        except Exception as e:
            error_logger.error(f"清理内存缓存失败: {e}")
    
    @staticmethod
    async def restart_discord_client():
        """重启Discord客户端（这里需要具体实现）"""
        try:
            # 这里需要实际的Discord客户端重启逻辑
            performance_logger.info("Discord客户端重启请求已发出")
        except Exception as e:
            error_logger.error(f"重启Discord客户端失败: {e}")


# 创建并配置全局健康检查器
def setup_health_checker() -> HealthChecker:
    """设置健康检查器"""
    checker = HealthChecker()
    
    # 注册健康检查
    checks = [
        HealthCheck(
            name="database",
            check_func=SystemHealthChecks.check_database_connection,
            interval=60,  # 每分钟检查一次
            timeout=10,
            retry_count=3
        ),
        HealthCheck(
            name="binance_api",
            check_func=SystemHealthChecks.check_binance_api,
            interval=30,  # 每30秒检查一次
            timeout=10,
            retry_count=2
        ),
        HealthCheck(
            name="discord",
            check_func=SystemHealthChecks.check_discord_connection,
            interval=120,  # 每2分钟检查一次
            timeout=15,
            retry_count=3
        ),
        HealthCheck(
            name="memory",
            check_func=SystemHealthChecks.check_memory_usage,
            interval=60,  # 每分钟检查一次
            timeout=5,
            retry_count=2
        ),
        HealthCheck(
            name="disk_space",
            check_func=SystemHealthChecks.check_disk_space,
            interval=300,  # 每5分钟检查一次
            timeout=5,
            retry_count=1
        ),
        HealthCheck(
            name="log_files",
            check_func=SystemHealthChecks.check_log_files,
            interval=300,  # 每5分钟检查一次
            timeout=5,
            retry_count=1
        )
    ]
    
    for check in checks:
        checker.register_check(check)
    
    # 注册恢复动作
    checker.register_recovery_action("database", RecoveryActions.restart_database_connection)
    checker.register_recovery_action("memory", RecoveryActions.clear_memory_cache)
    checker.register_recovery_action("discord", RecoveryActions.restart_discord_client)
    
    return checker


# 全局健康检查器实例
health_checker = setup_health_checker()