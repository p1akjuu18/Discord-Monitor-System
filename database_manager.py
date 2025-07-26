# -*- coding: utf-8 -*-
"""
数据库管理模块 - 替换JSON文件存储
提供高性能的SQLite数据库存储方案
"""

import sqlite3
import json
import asyncio
import aiosqlite
from datetime import datetime
from typing import Dict, List, Optional, Any
from pathlib import Path
import logging
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class DatabaseManager:
    """数据库管理器"""
    
    def __init__(self, db_path: str = "data/trading_monitor.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(exist_ok=True)
        self._connection_pool = {}
        
    async def init_database(self):
        """初始化数据库结构"""
        async with aiosqlite.connect(self.db_path) as db:
            # 创建消息表
            await db.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT NOT NULL,
                    channel_name TEXT,
                    user_id TEXT,
                    username TEXT,
                    content TEXT,
                    attachments TEXT,  -- JSON字符串
                    embeds TEXT,       -- JSON字符串
                    message_type TEXT DEFAULT 'general',
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    created_at DATETIME,
                    INDEX(channel_id),
                    INDEX(timestamp),
                    INDEX(message_type)
                )
            """)
            
            # 创建交易信号表
            await db.execute("""
                CREATE TABLE IF NOT EXISTS trading_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_key TEXT UNIQUE,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    entry_price REAL,
                    stop_loss REAL,
                    take_profit REAL,
                    channel TEXT,
                    status TEXT DEFAULT 'pending',
                    executed_at DATETIME,
                    order_id TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    INDEX(symbol),
                    INDEX(status),
                    INDEX(created_at)
                )
            """)
            
            # 创建订单表
            await db.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT UNIQUE,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    order_type TEXT,
                    quantity REAL,
                    price REAL,
                    status TEXT,
                    filled_quantity REAL DEFAULT 0,
                    commission REAL DEFAULT 0,
                    signal_id INTEGER,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(signal_id) REFERENCES trading_signals(id),
                    INDEX(symbol),
                    INDEX(status),
                    INDEX(created_at)
                )
            """)
            
            # 创建系统指标表
            await db.execute("""
                CREATE TABLE IF NOT EXISTS system_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    metric_type TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    value REAL,
                    metadata TEXT,  -- JSON字符串
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    INDEX(metric_type),
                    INDEX(timestamp)
                )
            """)
            
            # 创建告警表
            await db.execute("""
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    level TEXT NOT NULL,
                    category TEXT NOT NULL,
                    message TEXT NOT NULL,
                    data TEXT,  -- JSON字符串
                    acknowledged BOOLEAN DEFAULT FALSE,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    INDEX(level),
                    INDEX(category),
                    INDEX(created_at)
                )
            """)
            
            await db.commit()
            logger.info("数据库初始化完成")
    
    @asynccontextmanager
    async def get_connection(self):
        """获取数据库连接"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db
    
    # 消息相关操作
    async def save_message(self, message_data: Dict):
        """保存消息到数据库"""
        async with self.get_connection() as db:
            await db.execute("""
                INSERT INTO messages (
                    channel_id, channel_name, user_id, username, 
                    content, attachments, embeds, message_type, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                message_data.get('channel_id'),
                message_data.get('channel_name'),
                message_data.get('author_id'),
                message_data.get('author'),
                message_data.get('content'),
                json.dumps(message_data.get('attachments', [])),
                json.dumps(message_data.get('embeds', [])),
                message_data.get('type', 'general'),
                message_data.get('timestamp')
            ))
            await db.commit()
    
    async def get_recent_messages(self, channel_id: str, limit: int = 100) -> List[Dict]:
        """获取频道最近的消息"""
        async with self.get_connection() as db:
            cursor = await db.execute("""
                SELECT * FROM messages 
                WHERE channel_id = ? 
                ORDER BY timestamp DESC 
                LIMIT ?
            """, (channel_id, limit))
            
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    async def cleanup_old_messages(self, days: int = 30):
        """清理旧消息"""
        async with self.get_connection() as db:
            await db.execute("""
                DELETE FROM messages 
                WHERE timestamp < datetime('now', '-{} days')
            """.format(days))
            await db.commit()
    
    # 交易信号相关操作
    async def save_trading_signal(self, signal: Dict) -> int:
        """保存交易信号"""
        async with self.get_connection() as db:
            cursor = await db.execute("""
                INSERT INTO trading_signals (
                    signal_key, symbol, side, entry_price, 
                    stop_loss, take_profit, channel
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                signal.get('signal_key'),
                signal.get('symbol'),
                signal.get('side'),
                signal.get('entry_price'),
                signal.get('stop_loss'),
                signal.get('take_profit'),
                signal.get('channel')
            ))
            await db.commit()
            return cursor.lastrowid
    
    async def update_signal_status(self, signal_id: int, status: str, order_id: str = None):
        """更新信号状态"""
        async with self.get_connection() as db:
            await db.execute("""
                UPDATE trading_signals 
                SET status = ?, order_id = ?, executed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (status, order_id, signal_id))
            await db.commit()
    
    async def get_pending_signals(self) -> List[Dict]:
        """获取待处理的信号"""
        async with self.get_connection() as db:
            cursor = await db.execute("""
                SELECT * FROM trading_signals 
                WHERE status = 'pending' 
                ORDER BY created_at ASC
            """)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    # 订单相关操作
    async def save_order(self, order_data: Dict, signal_id: int = None) -> int:
        """保存订单信息"""
        async with self.get_connection() as db:
            cursor = await db.execute("""
                INSERT INTO orders (
                    order_id, symbol, side, order_type, quantity, 
                    price, status, signal_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                order_data.get('orderId'),
                order_data.get('symbol'),
                order_data.get('side'),
                order_data.get('type'),
                float(order_data.get('origQty', 0)),
                float(order_data.get('price', 0)),
                order_data.get('status'),
                signal_id
            ))
            await db.commit()
            return cursor.lastrowid
    
    async def update_order_status(self, order_id: str, status: str, filled_qty: float = None):
        """更新订单状态"""
        async with self.get_connection() as db:
            if filled_qty is not None:
                await db.execute("""
                    UPDATE orders 
                    SET status = ?, filled_quantity = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE order_id = ?
                """, (status, filled_qty, order_id))
            else:
                await db.execute("""
                    UPDATE orders 
                    SET status = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE order_id = ?
                """, (status, order_id))
            await db.commit()
    
    # 系统指标相关操作
    async def save_metric(self, metric_type: str, metric_name: str, value: float, metadata: Dict = None):
        """保存系统指标"""
        async with self.get_connection() as db:
            await db.execute("""
                INSERT INTO system_metrics (metric_type, metric_name, value, metadata)
                VALUES (?, ?, ?, ?)
            """, (
                metric_type, 
                metric_name, 
                value, 
                json.dumps(metadata) if metadata else None
            ))
            await db.commit()
    
    async def get_metrics(self, metric_type: str = None, hours: int = 24) -> List[Dict]:
        """获取系统指标"""
        async with self.get_connection() as db:
            if metric_type:
                cursor = await db.execute("""
                    SELECT * FROM system_metrics 
                    WHERE metric_type = ? AND timestamp > datetime('now', '-{} hours')
                    ORDER BY timestamp DESC
                """.format(hours), (metric_type,))
            else:
                cursor = await db.execute("""
                    SELECT * FROM system_metrics 
                    WHERE timestamp > datetime('now', '-{} hours')
                    ORDER BY timestamp DESC
                """.format(hours))
            
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    # 告警相关操作
    async def save_alert(self, level: str, category: str, message: str, data: Dict = None):
        """保存告警"""
        async with self.get_connection() as db:
            await db.execute("""
                INSERT INTO alerts (level, category, message, data)
                VALUES (?, ?, ?, ?)
            """, (level, category, message, json.dumps(data) if data else None))
            await db.commit()
    
    async def get_unacknowledged_alerts(self) -> List[Dict]:
        """获取未确认的告警"""
        async with self.get_connection() as db:
            cursor = await db.execute("""
                SELECT * FROM alerts 
                WHERE acknowledged = FALSE 
                ORDER BY created_at DESC
            """)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    # 数据分析相关
    async def get_trading_stats(self, days: int = 7) -> Dict:
        """获取交易统计"""
        async with self.get_connection() as db:
            # 信号统计
            cursor = await db.execute("""
                SELECT status, COUNT(*) as count 
                FROM trading_signals 
                WHERE created_at > datetime('now', '-{} days')
                GROUP BY status
            """.format(days))
            signal_stats = dict(await cursor.fetchall())
            
            # 订单统计
            cursor = await db.execute("""
                SELECT status, COUNT(*) as count, SUM(quantity * price) as volume
                FROM orders 
                WHERE created_at > datetime('now', '-{} days')
                GROUP BY status
            """.format(days))
            order_stats = {row[0]: {'count': row[1], 'volume': row[2] or 0} 
                          for row in await cursor.fetchall()}
            
            return {
                'signals': signal_stats,
                'orders': order_stats,
                'period_days': days
            }
    
    async def export_data(self, output_dir: str):
        """导出数据"""
        output_path = Path(output_dir)
        output_path.mkdir(exist_ok=True)
        
        tables = ['messages', 'trading_signals', 'orders', 'system_metrics', 'alerts']
        
        async with self.get_connection() as db:
            for table in tables:
                cursor = await db.execute(f"SELECT * FROM {table}")
                rows = await cursor.fetchall()
                
                # 转换为JSON格式
                data = [dict(row) for row in rows]
                
                # 保存到文件
                with open(output_path / f"{table}.json", 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        
        logger.info(f"数据已导出到: {output_path}")


# 全局数据库管理器
db_manager = DatabaseManager()