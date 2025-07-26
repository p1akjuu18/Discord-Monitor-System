# -*- coding: utf-8 -*-
import os
import time
import json
import logging
import pandas as pd
from datetime import datetime
from typing import Dict, List, Optional, Union
from decimal import Decimal
from binance.client import Client
from binance.exceptions import BinanceAPIException
from binance.enums import *
import requests
from functools import lru_cache
from tenacity import retry, stop_after_attempt, wait_exponential
from config_manager import config_manager

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 支持的交易对及其精度
SUPPORTED_SYMBOLS = {}  # 将由get_all_supported_symbols方法动态填充

class BinanceTrader:
    def __init__(self, api_key: str = None, api_secret: str = None):
        """
        初始化币安交易客户端
        
        Args:
            api_key: 币安API Key (可选，优先使用环境变量)
            api_secret: 币安API Secret (可选，优先使用环境变量)
        """
        # 优先使用环境变量，然后使用传入参数
        self.api_key = api_key or config_manager.get_binance_api_key()
        self.api_secret = api_secret or config_manager.get_binance_api_secret()
        
        if not self.api_key or not self.api_secret:
            raise ValueError("必须提供币安API密钥，请在.env文件中设置BINANCE_API_KEY和BINANCE_API_SECRET")
        
        # 初始化币安客户端
        self.client = Client(self.api_key, self.api_secret)
        
        # 初始化BTC仓位配置
        btc_config = config_manager.get_btc_config()
        self.btc_initial_capital = btc_config.get('initial_capital', 1000)
        self.btc_leverage = btc_config.get('leverage', 60)
        self.btc_position_file = os.path.join(os.path.expanduser('~'), 'Desktop', 'btc仓位.xlsx')
        self.btc_channel_positions = self.load_btc_position_config()
        
        # 初始化时间偏移量
        self.time_offset = 0
        
        # 同步服务器时间（最多重试3次）
        self._sync_server_time()
        
        # 使用配置管理器的交易配置
        self.trading_config = config_manager.get_trading_config()
        
        # 初始化交易状态
        self.active_orders = {}
        self.position_info = {}
        
        # 分析结果文件路径
        self.analysis_file = os.path.join('data', 'analysis_results', 'all_analysis_results.csv')
        
        # 已执行订单记录文件
        self.executed_orders_file = os.path.join('data', 'executed_orders.json')
        
        # 订单配对关系文件
        self.order_pairs_file = os.path.join('data', 'order_pairs.json')
        
        # 加载已执行的订单记录
        self.executed_signals = self.load_executed_signals()
        
        # 加载订单配对关系
        self.order_pairs = self.load_order_pairs()
        
        # 清理过期的执行记录
        self.clean_expired_signals()
        
        # 获取所有支持的交易对信息
        self.supported_symbols = self.get_all_supported_symbols()
        
        logger.info("币安合约交易客户端初始化完成")
    
    def _sync_server_time(self):
        """同步服务器时间"""
        for attempt in range(3):
            try:
                server_time = self.client.get_server_time()
                local_time = int(time.time() * 1000)
                self.time_offset = server_time['serverTime'] - local_time
                logger.info(f"服务器时间差: {self.time_offset}ms")
                if abs(self.time_offset) > 1000:
                    logger.warning(f"系统时间与服务器时间不同步，已自动调整时间差")
                break
            except Exception as e:
                logger.error(f"同步服务器时间失败 (尝试 {attempt + 1}/3): {e}")
                if attempt < 2:
                    time.sleep(1)
                else:
                    logger.warning("无法同步服务器时间，将使用本地时间")
    
    @lru_cache(maxsize=100)
    def _cached_get_price(self, symbol: str, cache_key: int) -> Optional[float]:
        """
        带缓存的价格获取方法
        cache_key基于时间生成，实现5秒缓存
        """
        try:
            ticker = self._request(self.client.futures_symbol_ticker, symbol=symbol)
            price = float(ticker['price'])
            return price if price > 0 else None
        except Exception as e:
            logger.error(f"获取{symbol}价格失败: {e}")
            return None
    
    def load_executed_signals(self) -> Dict:
        """
        从文件加载已执行的订单记录
        
        Returns:
            Dict: 已执行的订单记录字典，格式为 {signal_key: execution_time}
        """
        try:
            if os.path.exists(self.executed_orders_file):
                with open(self.executed_orders_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # 将列表转换为字典，如果没有时间戳则使用当前时间
                    signals_dict = {}
                    for item in data:
                        if isinstance(item, dict):
                            signals_dict[item['signal_key']] = item.get('execution_time', time.time())
                        else:
                            signals_dict[item] = time.time()
                    logger.info(f"已加载 {len(signals_dict)} 条已执行订单记录")
                    return signals_dict
            return {}
        except Exception as e:
            logger.error(f"加载已执行订单记录失败: {e}")
            return {}

    def save_executed_signals(self):
        """
        保存已执行的订单记录到文件
        """
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(self.executed_orders_file), exist_ok=True)
            
            # 将字典转换为列表格式
            data = [{'signal_key': key, 'execution_time': value} for key, value in self.executed_signals.items()]
            
            # 保存记录
            with open(self.executed_orders_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"已保存 {len(self.executed_signals)} 条已执行订单记录")
        except Exception as e:
            logger.error(f"保存已执行订单记录失败: {e}")

    def get_account_info(self) -> Dict:
        """
        获取账户信息
        Returns:
            Dict: 包含账户信息的字典
        """
        try:
            # 获取账户信息
            account = self.client.futures_account()
            # 获取USDT余额
            usdt_balance = 0.0
            for asset in account['assets']:
                if asset['asset'] == 'USDT':
                    usdt_balance = float(asset['availableBalance'])
                    break
            return {
                'available_balance': usdt_balance,
                'total_balance': float(account['totalWalletBalance']),
                'unrealized_pnl': float(account['totalUnrealizedProfit'])
            }
        except Exception as e:
            logger.error(f"获取账户信息失败: {e}")
            return None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    def get_balance(self, asset: str = 'USDT') -> float:
        """
        获取指定资产的合约余额
        
        Args:
            asset: 资产名称，默认USDT
            
        Returns:
            float: 余额
        """
        try:
            account = self._request(self.client.futures_account)
            for balance in account['assets']:
                if balance['asset'] == asset:
                    return float(balance['walletBalance'])
            return 0.0
        except BinanceAPIException as e:
            logger.error(f"获取{asset}合约余额失败: {e}")
            raise
    
    def get_symbol_info(self, symbol: str) -> Dict:
        """
        获取合约交易对信息
        
        Args:
            symbol: 交易对名称，如 'BTCUSDT'
            
        Returns:
            Dict: 交易对信息
        """
        try:
            info = self._request(self.client.futures_exchange_info)
            for symbol_info in info['symbols']:
                if symbol_info['symbol'] == symbol:
                    return symbol_info
            return {}
        except BinanceAPIException as e:
            logger.error(f"获取{symbol}合约交易对信息失败: {e}")
            return {}
    
    def get_current_price(self, symbol: str) -> float:
        """
        获取当前价格（带缓存）
        
        Args:
            symbol: 交易对符号
            
        Returns:
            float: 当前价格
        """
        try:
            # 确保使用正确的交易对符号
            if isinstance(symbol, dict):
                symbol = symbol['symbol']
            
            # 移除可能的后缀（如_250926）
            base_symbol = symbol.split('_')[0]
            
            # 使用5秒缓存
            cache_key = int(time.time() / 5)  # 每5秒更新一次缓存
            price = self._cached_get_price(base_symbol, cache_key)
            
            if price is None or price <= 0:
                logger.error(f"获取到{symbol}无效价格: {price}")
                return None
                
            return price
        except Exception as e:
            logger.error(f"获取{symbol}当前价格失败: {e}")
            return None

    def get_open_orders(self, symbol: str = None) -> List[Dict]:
        """
        获取未完成订单
        
        Args:
            symbol: 交易对符号
            
        Returns:
            List[Dict]: 未完成订单列表
        """
        try:
            # 确保使用正确的交易对符号
            if isinstance(symbol, dict):
                symbol = symbol['symbol']
                
            return self._request(self.client.futures_get_open_orders, symbol=symbol)
        except Exception as e:
            logger.error(f"获取未完成订单失败: {e}")
            return []

    def format_quantity(self, symbol: str, quantity: float) -> float:
        """
        格式化交易数量，确保符合币安精度要求
        
        Args:
            symbol: 交易对符号
            quantity: 原始数量
            
        Returns:
            float: 格式化后的数量
        """
        try:
            # 确保使用正确的交易对符号
            if isinstance(symbol, dict):
                symbol = symbol['symbol']
                
            # 获取交易对信息
            symbol_info = None
            for key, value in SUPPORTED_SYMBOLS.items():
                if value['symbol'] == symbol:
                    symbol_info = value
                    break
            
            if not symbol_info:
                raise ValueError(f"不支持的交易对: {symbol}")
            
            # 获取当前价格
            current_price = self.get_current_price(symbol)
            if not current_price:
                raise ValueError(f"无法获取{symbol}当前价格")
            
            # 格式化数量
            precision = symbol_info['quantity_precision']
            min_qty = symbol_info['min_qty']
            
            # 确保数量不小于最小交易量
            quantity = max(quantity, min_qty)
            
            # 根据精度格式化
            if precision == 0:
                # 如果是整数精度，直接取整
                formatted_qty = int(quantity)
            else:
                formatted_qty = float(f"{{:.{precision}f}}".format(quantity))
            
            # 验证名义金额是否满足要求
            notional = formatted_qty * current_price
            if notional < 100:
                # 如果名义金额小于100，增加数量
                formatted_qty = 100 / current_price
                if precision == 0:
                    formatted_qty = int(formatted_qty)
                else:
                    formatted_qty = float(f"{{:.{precision}f}}".format(formatted_qty))
                logger.info(f"调整交易数量以满足最小名义金额要求: {formatted_qty}")
            
            return formatted_qty
            
        except Exception as e:
            logger.error(f"格式化数量时出错: {e}")
            raise

    def format_price(self, symbol: str, price: float) -> float:
        """
        格式化价格，确保符合币安精度要求
        
        Args:
            symbol: 交易对符号
            price: 原始价格
            
        Returns:
            float: 格式化后的价格
        """
        try:
            # 确保使用正确的交易对符号
            if isinstance(symbol, dict):
                symbol = symbol['symbol']
                
            # 获取交易对信息
            symbol_info = None
            for key, value in SUPPORTED_SYMBOLS.items():
                if value['symbol'] == symbol:
                    symbol_info = value
                    break
            
            if not symbol_info:
                logger.warning(f"未找到交易对 {symbol} 的精度信息，使用原始价格")
                return price
            
            # 格式化价格
            precision = symbol_info['price_precision']
            formatted_price = float(f"{{:.{precision}f}}".format(price))
            
            return formatted_price
            
        except Exception as e:
            logger.error(f"格式化价格时出错: {e}")
            return price  # 出错时返回原始价格

    def place_order(self, 
                   symbol: str, 
                   side: str, 
                   order_type: str, 
                   quantity: float = None,
                   price: float = None,
                   stop_price: float = None,
                   time_in_force: str = 'GTC',
                   notional: float = None,
                   reduce_only: bool = False) -> Dict:
        """
        下单函数
        
        Args:
            symbol: 交易对
            side: 买卖方向 (BUY/SELL)
            order_type: 订单类型 (LIMIT/MARKET/STOP_LOSS/TAKE_PROFIT)
            quantity: 数量
            price: 价格
            stop_price: 触发价格
            time_in_force: 订单有效期
            notional: 名义价值
            
        Returns:
            Dict: 订单信息
        """
        try:
            # 检查交易对是否支持
            if symbol not in SUPPORTED_SYMBOLS:
                logger.error(f"不支持的交易对: {symbol}")
                return {}
                
            # 获取交易对信息
            symbol_info = self.get_symbol_info(symbol)
            if not symbol_info:
                return {}
                
            # 格式化价格和数量
            if price:
                price = self.format_price(symbol, price)
            if stop_price:
                stop_price = self.format_price(symbol, stop_price)
                
            # 处理数量
            if notional:
                # 计算最小名义价值
                min_notional = float(symbol_info.get('filters', [{}])[2].get('minNotional', 0))
                if notional < min_notional:
                    logger.error(f"名义价值 {notional} 小于最小要求 {min_notional}")
                    return {}
                    
                # 计算数量
                current_price = self.get_current_price(symbol)
                if not current_price:
                    return {}
                quantity = notional / current_price
                
            if quantity:
                quantity = self.format_quantity(symbol, quantity)
                
            # 构建订单参数
            order_params = {
                'symbol': symbol,
                'side': side,
                'type': order_type,
                'timeInForce': time_in_force
            }
            # 只有BTCUSDT和ETHUSDT加positionSide
            if symbol in ['BTCUSDT', 'ETHUSDT']:
                order_params['positionSide'] = 'SHORT' if side == 'SELL' else 'LONG'
            
            # 添加平仓标志
            if reduce_only:
                order_params['reduceOnly'] = True
            
            # 根据订单类型添加参数
            if order_type == 'LIMIT':
                order_params.update({
                    'price': price,
                    'quantity': quantity
                })
            elif order_type == 'MARKET':
                if notional:
                    order_params['quoteOrderQty'] = notional
                else:
                    order_params['quantity'] = quantity
            elif order_type in ['STOP_MARKET', 'TAKE_PROFIT_MARKET']:
                order_params.update({
                    'stopPrice': stop_price,
                    'quantity': quantity
                })
            elif order_type in ['STOP_LOSS', 'TAKE_PROFIT']:
                order_params.update({
                    'stopPrice': stop_price,
                    'price': price,
                    'quantity': quantity
                })
                
            # 下单
            order = self._request(self.client.futures_create_order, **order_params)
            logger.info(f"下单成功: {symbol} {side} {order_type}")
            return order
            
        except Exception as e:
            logger.error(f"下单失败: {e}")
            return {}
    
    def cancel_order(self, symbol: str, order_id: int) -> Dict:
        """
        取消合约订单
        
        Args:
            symbol: 交易对名称
            order_id: 订单ID
            
        Returns:
            Dict: 取消结果
        """
        try:
            result = self._request(self.client.futures_cancel_order, symbol=symbol, orderId=order_id)
            
            # 从活跃订单中移除
            if order_id in self.active_orders:
                del self.active_orders[order_id]
            
            logger.info(f"取消合约订单成功: {symbol} {order_id}")
            return result
            
        except BinanceAPIException as e:
            logger.error(f"取消合约订单失败: {e}")
            return {}
    
    def get_order_status(self, symbol: str, order_id: int) -> Dict:
        """
        获取合约订单状态
        
        Args:
            symbol: 交易对名称
            order_id: 订单ID
            
        Returns:
            Dict: 订单状态
        """
        try:
            order = self._request(self.client.futures_get_order, symbol=symbol, orderId=order_id)
            return order
        except BinanceAPIException as e:
            logger.error(f"获取合约订单状态失败: {e}")
            return {}
    
    def place_market_order(self, symbol: str, side: str, quantity: float) -> Dict:
        """
        下合约市价单
        
        Args:
            symbol: 交易对符号
            side: 方向 (BUY/SELL)
            quantity: 数量
            
        Returns:
            Dict: 订单信息
        """
        return self.place_order(symbol, side, 'MARKET', quantity)
    
    def place_limit_order(self, symbol: str, side: str, quantity: float, price: float) -> Dict:
        """
        下合约限价单
        
        Args:
            symbol: 交易对符号
            side: 方向 (BUY/SELL)
            quantity: 数量
            price: 价格
            
        Returns:
            Dict: 订单信息
        """
        return self.place_order(symbol, side, 'LIMIT', quantity, price=price)
    
    def place_stop_loss_order(self, symbol: str, side: str, quantity: float, stop_price: float, reduce_only: bool = False) -> Dict:
        """
        下合约止损单
        
        Args:
            symbol: 交易对符号
            side: 方向 (BUY/SELL)
            quantity: 数量
            stop_price: 触发价格
            reduce_only: 是否为平仓单
            
        Returns:
            Dict: 订单信息
        """
        return self.place_order(symbol, side, 'STOP_MARKET', quantity, stop_price=stop_price, reduce_only=reduce_only)
    
    def place_take_profit_order(self, symbol: str, side: str, quantity: float, stop_price: float, reduce_only: bool = False) -> Dict:
        """
        下合约止盈单
        
        Args:
            symbol: 交易对符号
            side: 方向 (BUY/SELL)
            quantity: 数量
            stop_price: 触发价格
            reduce_only: 是否为平仓单
            
        Returns:
            Dict: 订单信息
        """
        return self.place_order(symbol, side, 'TAKE_PROFIT_MARKET', quantity, stop_price=stop_price, reduce_only=reduce_only)
    
    def update_trading_config(self, config: Dict):
        """
        更新交易配置
        
        Args:
            config: 新的配置字典
        """
        self.trading_config.update(config)
        logger.info(f"交易配置已更新: {config}")
    
    def calculate_win_rate_statistics(self, lookback_days: int = 7) -> Dict:
        """
        计算动态胜率统计
        
        Args:
            lookback_days: 回看天数
            
        Returns:
            Dict: 胜率统计信息
        """
        try:
            import sqlite3
            from datetime import datetime, timedelta
            
            # 连接到数据库
            db_path = os.path.join('data', 'trading_history.db')
            
            if not os.path.exists(db_path):
                logger.warning("交易历史数据库不存在，返回默认统计")
                return {
                    'overall_win_rate': 0.5,
                    'recent_win_rate': 0.5,
                    'total_trades': 0,
                    'winning_trades': 0,
                    'losing_trades': 0,
                    'avg_profit': 0.0,
                    'avg_loss': 0.0,
                    'profit_factor': 1.0,
                    'max_consecutive_wins': 0,
                    'max_consecutive_losses': 0,
                    'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
            
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # 创建表（如果不存在）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    pnl REAL,
                    commission REAL DEFAULT 0,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'open'
                )
            ''')
            
            # 计算整体胜率
            cursor.execute('''
                SELECT 
                    COUNT(*) as total_trades,
                    COUNT(CASE WHEN pnl > 0 THEN 1 END) as winning_trades,
                    COUNT(CASE WHEN pnl < 0 THEN 1 END) as losing_trades,
                    AVG(CASE WHEN pnl > 0 THEN pnl END) as avg_profit,
                    AVG(CASE WHEN pnl < 0 THEN pnl END) as avg_loss
                FROM trades 
                WHERE status = 'closed' AND pnl IS NOT NULL
            ''')
            
            overall_stats = cursor.fetchone()
            
            # 计算最近指定天数的胜率
            cutoff_date = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute('''
                SELECT 
                    COUNT(*) as recent_total,
                    COUNT(CASE WHEN pnl > 0 THEN 1 END) as recent_winning
                FROM trades 
                WHERE status = 'closed' AND pnl IS NOT NULL AND timestamp >= ?
            ''', (cutoff_date,))
            
            recent_stats = cursor.fetchone()
            
            # 计算连续盈亏记录
            cursor.execute('''
                SELECT pnl FROM trades 
                WHERE status = 'closed' AND pnl IS NOT NULL 
                ORDER BY timestamp DESC
            ''')
            
            pnl_history = [row[0] for row in cursor.fetchall()]
            
            # 计算最大连续盈利和亏损
            max_consecutive_wins = 0
            max_consecutive_losses = 0
            current_wins = 0
            current_losses = 0
            
            for pnl in pnl_history:
                if pnl > 0:
                    current_wins += 1
                    current_losses = 0
                    max_consecutive_wins = max(max_consecutive_wins, current_wins)
                else:
                    current_losses += 1
                    current_wins = 0
                    max_consecutive_losses = max(max_consecutive_losses, current_losses)
            
            conn.close()
            
            # 处理统计数据
            total_trades, winning_trades, losing_trades, avg_profit, avg_loss = overall_stats
            recent_total, recent_winning = recent_stats
            
            overall_win_rate = winning_trades / total_trades if total_trades > 0 else 0.5
            recent_win_rate = recent_winning / recent_total if recent_total > 0 else overall_win_rate
            
            profit_factor = abs(avg_profit) / abs(avg_loss) if avg_loss and avg_loss < 0 else 1.0
            
            statistics = {
                'overall_win_rate': round(overall_win_rate, 4),
                'recent_win_rate': round(recent_win_rate, 4),
                'total_trades': total_trades,
                'winning_trades': winning_trades,
                'losing_trades': losing_trades,
                'avg_profit': round(avg_profit or 0.0, 2),
                'avg_loss': round(avg_loss or 0.0, 2),
                'profit_factor': round(profit_factor, 2),
                'max_consecutive_wins': max_consecutive_wins,
                'max_consecutive_losses': max_consecutive_losses,
                'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            
            logger.info(f"胜率统计计算完成: {statistics}")
            return statistics
            
        except Exception as e:
            logger.error(f"计算胜率统计失败: {e}")
            return {
                'overall_win_rate': 0.5,
                'recent_win_rate': 0.5,
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'avg_profit': 0.0,
                'avg_loss': 0.0,
                'profit_factor': 1.0,
                'max_consecutive_wins': 0,
                'max_consecutive_losses': 0,
                'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'error': str(e)
            }
    
    def calculate_position_size(self, symbol: str, signal_quality: float = 0.5, risk_percentage: float = 0.02) -> float:
        """
        基于动态胜率统计计算仓位大小
        
        Args:
            symbol: 交易对符号
            signal_quality: 信号质量评分 (0-1)
            risk_percentage: 基础风险百分比
            
        Returns:
            float: 建议仓位大小
        """
        try:
            # 获取账户余额
            account_info = self.get_account_info()
            if not account_info:
                logger.error("无法获取账户信息")
                return 0.0
            
            available_balance = float(account_info.get('availableBalance', 0))
            
            # 获取胜率统计
            win_stats = self.calculate_win_rate_statistics()
            
            # 基础仓位计算参数
            base_position_percentage = risk_percentage
            
            # 胜率调整因子
            recent_win_rate = win_stats['recent_win_rate']
            overall_win_rate = win_stats['overall_win_rate']
            
            # 动态调整因子计算
            win_rate_factor = 1.0
            if recent_win_rate > 0.6:  # 最近胜率较高
                win_rate_factor = 1.2
            elif recent_win_rate < 0.4:  # 最近胜率较低
                win_rate_factor = 0.7
            
            # 盈利因子调整
            profit_factor = win_stats['profit_factor']
            if profit_factor > 1.5:
                profit_factor_adjustment = 1.1
            elif profit_factor < 0.8:
                profit_factor_adjustment = 0.8
            else:
                profit_factor_adjustment = 1.0
            
            # 连续亏损保护
            max_consecutive_losses = win_stats['max_consecutive_losses']
            consecutive_loss_factor = 1.0
            if max_consecutive_losses > 5:
                consecutive_loss_factor = 0.6
            elif max_consecutive_losses > 3:
                consecutive_loss_factor = 0.8
            
            # 信号质量调整
            signal_quality_factor = 0.5 + (signal_quality * 0.5)  # 0.5-1.0 范围
            
            # 综合调整因子
            total_adjustment = (
                win_rate_factor * 
                profit_factor_adjustment * 
                consecutive_loss_factor * 
                signal_quality_factor
            )
            
            # 计算最终仓位百分比
            final_position_percentage = base_position_percentage * total_adjustment
            
            # 限制仓位大小（最大不超过5%）
            final_position_percentage = min(final_position_percentage, 0.05)
            final_position_percentage = max(final_position_percentage, 0.001)  # 最小0.1%
            
            # 计算美元金额
            position_amount = available_balance * final_position_percentage
            
            # 获取当前价格来计算数量
            current_price = self.get_current_price(symbol)
            if not current_price:
                logger.error(f"无法获取{symbol}当前价格")
                return 0.0
            
            # 计算数量（考虑杠杆）
            leverage = self.get_leverage(symbol)
            quantity = (position_amount * leverage) / current_price
            
            # 按照交易对精度调整
            symbol_info = self.supported_symbols.get(symbol)
            if symbol_info:
                quantity_precision = symbol_info.get('quantity_precision', 3)
                quantity = round(quantity, quantity_precision)
            
            logger.info(f"动态仓位计算 - {symbol}:")
            logger.info(f"  账户余额: ${available_balance:.2f}")
            logger.info(f"  基础风险: {risk_percentage*100:.1f}%")
            logger.info(f"  胜率调整: {win_rate_factor:.2f}")
            logger.info(f"  盈利因子调整: {profit_factor_adjustment:.2f}")
            logger.info(f"  连亏保护: {consecutive_loss_factor:.2f}")
            logger.info(f"  信号质量: {signal_quality_factor:.2f}")
            logger.info(f"  最终调整: {total_adjustment:.2f}")
            logger.info(f"  仓位百分比: {final_position_percentage*100:.2f}%")
            logger.info(f"  建议数量: {quantity}")
            
            return quantity
            
        except Exception as e:
            logger.error(f"计算仓位大小失败: {e}")
            return 0.0
    
    def record_trade(self, symbol: str, side: str, quantity: float, entry_price: float, 
                    exit_price: float = None, pnl: float = None, status: str = 'open'):
        """
        记录交易到数据库
        
        Args:
            symbol: 交易对符号
            side: 交易方向
            quantity: 数量
            entry_price: 入场价格
            exit_price: 出场价格
            pnl: 盈亏
            status: 状态 ('open'/'closed')
        """
        try:
            import sqlite3
            
            # 确保数据目录存在
            os.makedirs('data', exist_ok=True)
            db_path = os.path.join('data', 'trading_history.db')
            
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # 创建表（如果不存在）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    pnl REAL,
                    commission REAL DEFAULT 0,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'open'
                )
            ''')
            
            # 插入交易记录
            cursor.execute('''
                INSERT INTO trades (symbol, side, quantity, entry_price, exit_price, pnl, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (symbol, side, quantity, entry_price, exit_price, pnl, status))
            
            conn.commit()
            conn.close()
            
            logger.info(f"交易记录已保存: {symbol} {side} {quantity} @ {entry_price}")
            
        except Exception as e:
            logger.error(f"记录交易失败: {e}")
    
    def get_risk_adjusted_position_size(self, symbol: str, signal_confidence: float = 0.5) -> Dict:
        """
        获取风险调整后的仓位建议
        
        Args:
            symbol: 交易对符号
            signal_confidence: 信号置信度 (0-1)
            
        Returns:
            Dict: 仓位建议信息
        """
        try:
            # 计算基础仓位大小
            base_quantity = self.calculate_position_size(symbol, signal_confidence)
            
            # 获取胜率统计
            win_stats = self.calculate_win_rate_statistics()
            
            # 获取当前价格
            current_price = self.get_current_price(symbol)
            
            # 计算建议的止损和止盈价格
            risk_reward_ratio = 2.0  # 默认风险收益比 1:2
            stop_loss_pct = 0.02  # 2% 止损
            take_profit_pct = stop_loss_pct * risk_reward_ratio  # 4% 止盈
            
            if current_price:
                stop_loss_price = current_price * (1 - stop_loss_pct)
                take_profit_price = current_price * (1 + take_profit_pct)
            else:
                stop_loss_price = 0
                take_profit_price = 0
            
            position_suggestion = {
                'symbol': symbol,
                'suggested_quantity': base_quantity,
                'current_price': current_price,
                'stop_loss_price': round(stop_loss_price, 2),
                'take_profit_price': round(take_profit_price, 2),
                'risk_percentage': stop_loss_pct * 100,
                'reward_percentage': take_profit_pct * 100,
                'risk_reward_ratio': risk_reward_ratio,
                'signal_confidence': signal_confidence,
                'win_rate_stats': win_stats,
                'position_value_usd': base_quantity * current_price if current_price else 0,
                'max_loss_usd': base_quantity * current_price * stop_loss_pct if current_price else 0,
                'max_profit_usd': base_quantity * current_price * take_profit_pct if current_price else 0
            }
            
            return position_suggestion
            
        except Exception as e:
            logger.error(f"获取风险调整仓位失败: {e}")
            return {
                'symbol': symbol,
                'suggested_quantity': 0,
                'error': str(e)
            }
    
    def get_trading_config(self) -> Dict:
        """
        获取当前交易配置
        
        Returns:
            Dict: 交易配置
        """
        return self.trading_config
    
    def get_active_orders(self) -> Dict:
        """
        获取当前活跃订单
        
        Returns:
            Dict: 活跃订单字典
        """
        return self.active_orders
    
    def close_all_positions(self, symbol: str = None):
        """
        平掉所有合约仓位
        
        Args:
            symbol: 交易对名称，如果为None则平掉所有交易对的仓位
        """
        try:
            # 获取所有未完成订单
            open_orders = self.get_open_orders(symbol)
            
            # 取消所有未完成订单
            for order in open_orders:
                self.cancel_order(order['symbol'], order['orderId'])
            
            # 获取账户信息
            account = self.get_account_info()
            
            # 遍历所有持仓
            for position in account['positions']:
                if float(position['positionAmt']) != 0:
                    # 构建平仓参数
                    close_params = {
                        'symbol': position['symbol'],
                        'side': 'SELL' if float(position['positionAmt']) > 0 else 'BUY',
                        'type': 'MARKET',
                        'quantity': abs(float(position['positionAmt'])),
                        'reduceOnly': True
                    }
                    
                    # 平仓
                    self._request(self.client.futures_create_order, **close_params)
            
            logger.info("所有合约仓位已平仓")
            
        except BinanceAPIException as e:
            logger.error(f"平仓失败: {e}")

    def read_trading_signals(self) -> List[Dict]:
        """
        读取交易信号文件
        
        Returns:
            List[Dict]: 交易信号列表
        """
        try:
            if not os.path.exists(self.analysis_file):
                logger.warning(f"分析结果文件不存在: {self.analysis_file}")
                return []
            
            # 尝试不同的编码方式读取CSV文件
            encodings = ['utf-8', 'gbk', 'gb2312', 'gb18030', 'latin1']
            df = None
            
            for encoding in encodings:
                try:
                    df = pd.read_csv(self.analysis_file, encoding=encoding)
                    logger.info(f"成功使用 {encoding} 编码读取文件")
                    break
                except UnicodeDecodeError:
                    continue
                except Exception as e:
                    logger.error(f"使用 {encoding} 编码读取文件时出错: {e}")
                    continue
            
            if df is None:
                logger.error("无法使用任何编码方式读取文件")
                return []
            
            # 获取必要的列
            required_columns = ['analysis.交易币种', 'analysis.方向', 'analysis.入场点位1', 'analysis.止损点位1']
            if not all(col in df.columns for col in required_columns):
                logger.error("CSV文件缺少必要的列")
                return []
            
            # 获取当前时间戳（毫秒）
            current_time = int(time.time() * 1000)
            
            # 过滤出有效的交易信号
            signals = []
            for _, row in df.iterrows():
                try:
                    # 检查所有必要字段是否都存在且有效
                    if any(pd.isna(row[col]) for col in required_columns):
                        continue
                        
                    # 获取交易币种
                    symbol = str(row['analysis.交易币种']).strip().upper()
                    if not symbol or symbol == 'NAN':
                        continue
                    
                    # 标准化交易对
                    normalized_symbol = None
                    channel = None
                    
                    # 获取频道信息
                    channel_cols = [col for col in df.columns if '频道' in col or 'channel' in col.lower()]
                    if channel_cols:
                        channel = str(row[channel_cols[0]]).strip()
                        if not channel or channel == 'NAN':
                            channel = 'default'
                    else:
                        channel = 'default'
                    
                    # 统一处理所有交易对名称
                    # 移除可能的后缀（如USDT）
                    base_symbol = symbol.replace('USDT', '').strip()
                    # 添加USDT后缀
                    normalized_symbol = f"{base_symbol}USDT"
                    
                    # 检查交易对是否在支持的列表中
                    if normalized_symbol not in SUPPORTED_SYMBOLS:
                        logger.warning(f"不支持的交易对: {normalized_symbol}")
                        continue
                    
                    # 获取方向
                    direction = str(row['analysis.方向']).strip()
                    if '空' in direction or 'short' in direction.lower() or 'sell' in direction.lower():
                        side = 'SELL'
                    else:
                        side = 'BUY'
                    
                    # 获取入场价格
                    try:
                        entry_price = float(row['analysis.入场点位1']) if not pd.isna(row['analysis.入场点位1']) else None
                        if entry_price is None or entry_price <= 0:
                            logger.warning(f"无效的入场价格: {row['analysis.入场点位1']}")
                            continue
                    except (ValueError, TypeError) as e:
                        logger.warning(f"转换入场价格失败: {e}")
                        continue
                    
                    # 获取止损价格
                    try:
                        stop_loss = float(row['analysis.止损点位1']) if not pd.isna(row['analysis.止损点位1']) else None
                        if stop_loss is None or stop_loss <= 0:
                            logger.warning(f"无效的止损价格: {row['analysis.止损点位1']}")
                            continue
                    except (ValueError, TypeError) as e:
                        logger.warning(f"转换止损价格失败: {e}")
                        continue
                    
                    # 获取止盈价格（如果有）
                    target_price = None
                    target_cols = [col for col in df.columns if '止盈' in col or '目标' in col.lower()]
                    for col in target_cols:
                        if not pd.isna(row[col]):
                            try:
                                target_price = float(row[col])
                                if target_price > 0:
                                    break
                            except (ValueError, TypeError) as e:
                                logger.warning(f"转换止盈价格失败: {e}")
                                continue
                    
                    # 验证价格关系
                    if side == 'BUY':
                        if stop_loss >= entry_price:
                            logger.warning(f"做多信号价格关系无效: 止损 {stop_loss} >= 入场 {entry_price}")
                            continue
                        if target_price and target_price <= entry_price:
                            logger.warning(f"做多信号止盈价格无效: 止盈 {target_price} <= 入场 {entry_price}")
                            continue
                    else:  # SELL
                        if stop_loss <= entry_price:
                            logger.warning(f"做空信号价格关系无效: 止损 {stop_loss} <= 入场 {entry_price}")
                            continue
                        if target_price and target_price >= entry_price:
                            logger.warning(f"做空信号止盈价格无效: 止盈 {target_price} >= 入场 {entry_price}")
                            continue
                    
                    # 创建交易信号
                    signal = {
                        'symbol': normalized_symbol,
                        'side': side,
                        'entry_price': entry_price,
                        'stop_loss': stop_loss,
                        'target_price': target_price,  # 可能为None
                        'channel': channel,  # 添加频道信息
                        'timestamp': current_time  # 添加时间戳
                    }
                    
                    # 检查信号是否已执行
                    if self.is_signal_executed(signal):
                        logger.info(f"跳过已执行的信号: {signal}")
                        continue
                    
                    signals.append(signal)
                    logger.info(f"添加新交易信号: {signal}")
                    
                except Exception as e:
                    logger.error(f"处理交易信号时出错: {e}")
                    continue
            
            return signals
            
        except Exception as e:
            logger.error(f"读取交易信号文件时出错: {e}")
            return []

    def check_balance_sufficient(self, symbol: str, notional: float) -> bool:
        """
        检查账户余额是否足够开仓
        
        Args:
            symbol: 交易对
            notional: 名义金额
            
        Returns:
            bool: 余额是否足够
        """
        try:
            # 获取账户信息
            account = self._request(self.client.futures_account)
            
            # 获取可用余额
            available_balance = 0
            for asset in account['assets']:
                if asset['asset'] == 'USDT':
                    available_balance = float(asset['availableBalance'])
                    break
            
            # 计算所需保证金
            required_margin = notional / self.trading_config['leverage']
            
            logger.info(f"账户信息:")
            logger.info(f"  可用余额: {available_balance:.2f} USDT")
            logger.info(f"  开仓所需保证金: {required_margin:.2f} USDT")
            
            # 检查是否有足够的保证金
            if available_balance < required_margin:
                logger.error(f"余额不足: 需要 {required_margin:.2f} USDT，当前可用 {available_balance:.2f} USDT")
                return False
                
            return True
            
        except BinanceAPIException as e:
            logger.error(f"检查余额失败: {e}")
            return False

    def get_signal_key(self, signal: Dict) -> str:
        """
        生成交易信号的唯一标识
        
        Args:
            signal: 交易信号字典
            
        Returns:
            str: 信号唯一标识
        """
        try:
            # 保证格式化时不会变成0，保留真实价格
            entry_price = float(signal['entry_price']) if signal['entry_price'] is not None else 0
            stop_loss = float(signal['stop_loss']) if signal['stop_loss'] is not None else 0
            target_price = float(signal.get('target_price', 0)) if signal.get('target_price') is not None else 0
            channel = signal.get('channel', 'default')
            
            # 使用更精确的时间戳（精确到分钟）
            timestamp = int(time.time() / 60)  # 按分钟取整
            
            # 添加更多特征到信号标识中
            signal_key = f"{signal['symbol']}_{signal['side']}_{entry_price}_{stop_loss}_{target_price}_{channel}_{timestamp}"
            logger.info(f"生成信号标识: {signal_key}")
            return signal_key
        except Exception as e:
            logger.error(f"生成信号标识时出错: {e}")
            # 返回一个基本的标识，避免完全失败
            return f"{signal.get('symbol', 'UNKNOWN')}_{signal.get('side', 'UNKNOWN')}_{int(time.time() / 60)}"

    def is_signal_executed(self, signal: Dict) -> bool:
        """
        检查交易信号是否已执行
        
        Args:
            signal: 交易信号字典
            
        Returns:
            bool: 是否已执行
        """
        signal_key = self.get_signal_key(signal)
        current_time = time.time()
        
        # 检查是否有相同入场价格的订单在4小时内执行过
        if signal_key in self.executed_signals:
            last_execution_time = self.executed_signals[signal_key]
            if current_time - last_execution_time < 4 * 3600:  # 4小时 = 4 * 3600秒
                logger.info(f"信号 {signal_key} 在4小时内已执行过，跳过")
                return True
        
        # 检查是否有相同特征的订单在4小时内执行过（忽略时间戳）
        base_key = '_'.join(signal_key.split('_')[:-1])  # 移除时间戳部分
        for key in self.executed_signals.keys():
            if key.startswith(base_key):
                last_execution_time = self.executed_signals[key]
                if current_time - last_execution_time < 4 * 3600:
                    logger.info(f"发现相似信号 {key} 在4小时内已执行过，跳过")
                    return True
        
        return False

    def mark_signal_executed(self, signal: Dict):
        """
        标记交易信号为已执行
        
        Args:
            signal: 交易信号字典
        """
        signal_key = self.get_signal_key(signal)
        current_time = time.time()
        
        # 更新执行时间
        self.executed_signals[signal_key] = current_time
        
        # 保存到文件
        self.save_executed_signals()
        logger.info(f"标记信号为已执行: {signal_key}")

    def check_existing_orders(self, symbol: str, side: str, entry_price: float) -> bool:
        """
        检查是否存在相同信号的挂单
        
        Args:
            symbol: 交易对
            side: 交易方向
            entry_price: 入场价格
            
        Returns:
            bool: 是否存在相同信号的挂单
        """
        try:
            # 获取所有未完成订单
            open_orders = self.get_open_orders(symbol)
            
            # 检查是否有相同信号的挂单
            for order in open_orders:
                # 检查是否是限价单
                if order['type'] == 'LIMIT':
                    # 检查方向是否相同
                    if order['side'] == side:
                        # 检查价格是否接近（允许0.1%的误差）
                        order_price = float(order['price'])
                        price_diff = abs(order_price - entry_price) / entry_price
                        if price_diff <= 0.001:  # 0.1%的误差
                            logger.info(f"发现相同信号的挂单: {order}")
                            return True
            
            # 检查订单配对关系中是否有相同信号的活跃订单
            for pair in self.order_pairs.values():
                if pair['status'] == 'active':
                    if (pair['symbol'] == symbol and 
                        pair['side'] == side and 
                        abs(float(pair['entry_price']) - entry_price) / entry_price <= 0.001):
                        logger.info(f"发现相同信号的活跃订单: {pair}")
                        return True
            
            return False
            
        except Exception as e:
            logger.error(f"检查现有挂单时出错: {e}")
            return False

    def execute_trading_signals(self, signals: List[Dict]):
        """
        执行交易信号
        
        Args:
            signals: 交易信号列表
        """
        for signal in signals:
            try:
                # 验证信号
                if not self.validate_signal(signal):
                    continue
                    
                # 获取信号信息
                symbol = signal.get('symbol')
                side = signal.get('side')
                entry_price = signal.get('entry_price')
                stop_loss = signal.get('stop_loss')
                take_profit = signal.get('take_profit')
                channel = signal.get('channel')
                
                # 检查必要参数
                if not all([symbol, side, entry_price]):
                    logger.error(f"信号缺少必要参数: {signal}")
                    continue
                    
                # 检查价格是否为None或无效
                try:
                    entry_price = float(entry_price) if entry_price is not None else None
                    stop_loss = float(stop_loss) if stop_loss is not None else None
                    take_profit = float(take_profit) if take_profit is not None else None
                    
                    if entry_price is None or entry_price <= 0:
                        logger.error(f"无效的入场价格: {signal.get('entry_price')}")
                        continue
                        
                    if stop_loss is None or stop_loss <= 0:
                        logger.error(f"无效的止损价格: {signal.get('stop_loss')}")
                        continue
                except (ValueError, TypeError) as e:
                    logger.error(f"转换价格时出错: {e}")
                    continue
                    
                # 检查是否已执行
                if self.is_signal_executed(signal):
                    continue
                    
                # 检查是否已有相同订单
                if self.check_existing_orders(symbol, side, entry_price):
                    continue
                    
                # 获取BTC仓位大小（比例）
                position_size = self.get_btc_position_size(channel)
                if position_size is None:
                    logger.error(f"无法获取BTC仓位大小: {channel}")
                    continue
                
                # 如果仓位为负数，反转交易方向
                if position_size < 0:
                    side = 'SELL' if side == 'BUY' else 'BUY'
                    logger.info(f"仓位为负数，反转交易方向为: {side}")
                
                # 检查余额是否足够
                if not self.check_balance_sufficient(symbol, abs(position_size)):
                    logger.error(f"余额不足: {symbol} {position_size}")
                    continue
                    
                # 下单
                order = self.place_order(
                    symbol=symbol,
                    side=side,
                    order_type='LIMIT',
                    price=entry_price,
                    notional=abs(position_size)  # 使用绝对值
                )
                
                if not order:
                    logger.error(f"下单失败: {symbol} {side}")
                    continue
                    
                # 记录已执行信号
                self.mark_signal_executed(signal)
                
                # 设置止损
                if stop_loss and stop_loss > 0:
                    try:
                        stop_order = self.place_stop_loss_order(
                            symbol=symbol,
                            side='SELL' if side == 'BUY' else 'BUY',
                            quantity=float(order['origQty']),
                            stop_price=stop_loss,
                            reduce_only=True  # 关键：平仓订单
                        )
                        if not stop_order:
                            logger.error(f"设置止损单失败: {symbol} {stop_loss}")
                    except Exception as e:
                        logger.error(f"设置止损单时出错: {e}")
                    
                # 设置止盈
                if take_profit and take_profit > 0:
                    try:
                        take_profit_order = self.place_take_profit_order(
                            symbol=symbol,
                            side='SELL' if side == 'BUY' else 'BUY',
                            quantity=float(order['origQty']),
                            stop_price=take_profit,
                            reduce_only=True  # 关键：平仓订单
                        )
                        if not take_profit_order:
                            logger.error(f"设置止盈单失败: {symbol} {take_profit}")
                    except Exception as e:
                        logger.error(f"设置止盈单时出错: {e}")
                    
            except Exception as e:
                logger.error(f"处理交易信号时出错: {e}")
                continue

    def check_order_status(self):
        """
        检查所有订单的状态，更新订单配对关系
        """
        try:
            for entry_order_id, pair in list(self.order_pairs.items()):
                if pair['status'] != 'active':
                    continue
                
                try:
                    # 检查入场单状态
                    entry_order = self.get_order_status(pair['symbol'], int(entry_order_id))
                    if not entry_order:
                        continue
                    
                    # 如果入场单已成交
                    if entry_order['status'] == 'FILLED':
                        # 检查止损单状态
                        if pair['stop_loss_order_id']:
                            stop_loss_order = self.get_order_status(pair['symbol'], pair['stop_loss_order_id'])
                            if stop_loss_order and stop_loss_order['status'] == 'FILLED':
                                pair['status'] = 'closed_by_stop_loss'
                                logger.info(f"订单 {entry_order_id} 已通过止损平仓")
                        
                        # 检查止盈单状态
                        if pair['take_profit_order_id']:
                            take_profit_order = self.get_order_status(pair['symbol'], pair['take_profit_order_id'])
                            if take_profit_order and take_profit_order['status'] == 'FILLED':
                                pair['status'] = 'closed_by_take_profit'
                                logger.info(f"订单 {entry_order_id} 已通过止盈平仓")
                    
                    # 如果入场单已取消
                    elif entry_order['status'] == 'CANCELED':
                        # 取消对应的止损止盈单
                        if pair['stop_loss_order_id']:
                            try:
                                self.cancel_order(pair['symbol'], pair['stop_loss_order_id'])
                            except:
                                pass
                        if pair['take_profit_order_id']:
                            try:
                                self.cancel_order(pair['symbol'], pair['take_profit_order_id'])
                            except:
                                pass
                        pair['status'] = 'canceled'
                        logger.info(f"订单 {entry_order_id} 已取消")
                    
                except Exception as e:
                    logger.error(f"检查订单 {entry_order_id} 状态时出错: {e}")
                    continue
            
            # 保存更新后的订单配对关系
            self.save_order_pairs()
            
        except Exception as e:
            logger.error(f"检查订单状态时出错: {e}")

    def monitor_and_trade(self, interval: int = 60):
        """
        监控并执行交易
        
        Args:
            interval: 检查间隔（秒）
        """
        logger.info("开始监控交易信号...")
        last_cleanup_time = time.time()
        
        while True:
            try:
                current_time = time.time()
                
                # 每4小时清理一次过期记录
                if current_time - last_cleanup_time >= 4 * 3600:  # 4小时
                    self.clean_expired_signals()
                    last_cleanup_time = current_time
                    logger.info("已清理过期记录")
                
                # 检查订单状态
                self.check_order_status()
                
                # 读取交易信号
                signals = self.read_trading_signals()
                if signals:
                    logger.info(f"发现 {len(signals)} 个交易信号")
                    
                    # 过滤掉已执行的信号
                    new_signals = []
                    for signal in signals:
                        try:
                            # 检查信号是否已执行
                            if self.is_signal_executed(signal):
                                logger.info(f"跳过已执行的信号: {self.get_signal_key(signal)}")
                                continue
                            
                            # 检查是否有相同信号的挂单
                            if self.check_existing_orders(signal['symbol'], signal['side'], signal['entry_price']):
                                logger.info(f"已存在相同信号的挂单，跳过: {signal['symbol']} {signal['side']} @ {signal['entry_price']}")
                                continue
                            
                            # 验证信号的有效性
                            if not self.validate_signal(signal):
                                logger.warning(f"信号验证失败: {signal}")
                                continue
                            
                            new_signals.append(signal)
                            logger.info(f"添加新信号到处理队列: {self.get_signal_key(signal)}")
                            
                        except Exception as e:
                            logger.error(f"处理信号时出错: {e}")
                            continue
                    
                    if new_signals:
                        logger.info(f"执行 {len(new_signals)} 个新交易信号")
                        self.execute_trading_signals(new_signals)
                    else:
                        logger.info("没有新的交易信号需要执行")
                
                # 等待下一次检查
                time.sleep(interval)
                
            except Exception as e:
                logger.error(f"监控交易时出错: {e}")
                time.sleep(interval)

    def validate_signal(self, signal: Dict) -> bool:
        """
        验证交易信号是否有效
        
        Args:
            signal: 交易信号字典
            
        Returns:
            bool: 信号是否有效
        """
        try:
            logger.info(f"开始验证信号: {signal}")
            
            # 验证时间戳
            if 'timestamp' not in signal:
                logger.error("信号缺少时间戳")
                return False
                
            current_time = int(time.time() * 1000)
            signal_time = signal['timestamp']
            time_diff = current_time - signal_time
            
            # 检查信号是否超过12小时
            if time_diff > 12 * 3600 * 1000:  # 12小时转换为毫秒
                logger.warning(f"信号已超过12小时，跳过执行")
                return False
                
            # 检查信号是否在最近4小时内已执行
            if self.is_signal_executed(signal):
                logger.warning(f"信号在最近4小时内已执行，跳过")
                return False
            
            # 获取当前市场价格
            current_price = self.get_current_price(signal['symbol'])
            if not current_price:
                logger.error(f"无法获取{signal['symbol']}当前价格")
                return False
            logger.info(f"当前市场价格: {current_price}")
            
            # 检查入场价格和止损价格
            entry_price = float(signal['entry_price'])
            stop_loss = float(signal['stop_loss'])
            
            # 检查价格方向是否正确
            if signal['side'] == 'BUY':
                # 买单：入场价格必须低于当前价格
                if entry_price >= current_price:
                    logger.warning(f"买单入场价格 {entry_price} 必须低于当前价格 {current_price}")
                    return False
            else:  # SELL
                # 卖单：入场价格必须高于当前价格
                if entry_price <= current_price:
                    logger.warning(f"卖单入场价格 {entry_price} 必须高于当前价格 {current_price}")
                    return False
            
            # 检查止损价格与当前价格的差异
            price_diff_percent = abs(stop_loss - current_price) / current_price
            if price_diff_percent < 0.001:  # 如果价格差异小于0.1%
                logger.warning(f"止损价格 {stop_loss} 太接近当前价格 {current_price}，差异: {price_diff_percent*100:.2f}%")
                return False
            
            # 检查账户余额是否足够
            account_info = self.get_account_info()
            if not account_info:
                logger.error("无法获取账户信息")
                return False
            
            # 计算开仓所需保证金
            required_margin = self.trading_config['position_size'] / self.trading_config['leverage']
            
            logger.info("账户信息:")
            logger.info(f"  可用余额: {account_info['available_balance']:.2f} USDT")
            logger.info(f"  开仓所需保证金: {required_margin:.2f} USDT")
            
            if account_info['available_balance'] < required_margin:
                logger.warning(f"可用余额不足，需要 {required_margin:.2f} USDT，当前余额 {account_info['available_balance']:.2f} USDT")
                return False
            
            logger.info(f"信号验证通过: {signal}")
            return True
            
        except Exception as e:
            logger.error(f"验证信号时出错: {e}")
            return False

    def get_cross_margin_account(self) -> Dict:
        """
        获取全仓账户信息
        
        Returns:
            Dict: 全仓账户信息
        """
        try:
            account = self._request(self.client.futures_account)
            return account
        except Exception as e:
            logger.error(f"获取全仓账户信息失败: {e}")
            return {}

    def get_position_info(self) -> Dict:
        """
        获取当前持仓信息
        
        Returns:
            Dict: 持仓信息
        """
        try:
            positions = self._request(self.client.futures_position_information)
            total_position_value = 0
            has_position = False
            
            logger.info("\n当前持仓信息:")
            for position in positions:
                position_amt = float(position['positionAmt'])
                if position_amt != 0:  # 只显示有持仓的
                    has_position = True
                    entry_price = float(position['entryPrice'])
                    mark_price = float(position['markPrice'])
                    position_value = abs(position_amt * mark_price)
                    total_position_value += position_value
                    
                    logger.info(f"交易对: {position['symbol']}")
                    logger.info(f"  持仓方向: {'多' if position_amt > 0 else '空'}")
                    logger.info(f"  持仓数量: {abs(position_amt)}")
                    logger.info(f"  入场价格: {entry_price}")
                    logger.info(f"  标记价格: {mark_price}")
                    logger.info(f"  持仓价值: {position_value:.2f} USDT")
                    logger.info(f"  未实现盈亏: {float(position['unRealizedProfit']):.2f} USDT")
                    logger.info("-------------------")
            
            if has_position:
                logger.info(f"\n总持仓价值: {total_position_value:.2f} USDT")
            else:
                logger.info("当前没有持仓")
                
            return positions
            
        except BinanceAPIException as e:
            logger.error(f"获取持仓信息失败: {e}")
            return {}

    def get_server_time(self) -> int:
        """
        获取币安服务器时间
        
        Returns:
            int: 服务器时间戳（毫秒）
        """
        try:
            server_time = self._request(self.client.get_server_time)
            return server_time['serverTime']
        except Exception as e:
            logger.error(f"获取服务器时间失败: {e}")
            return int(time.time() * 1000)  # 如果失败则返回本地时间

    def get_timestamp(self) -> int:
        """
        获取当前时间戳，考虑服务器时间差
        
        Returns:
            int: 调整后的时间戳（毫秒）
        """
        return int(time.time() * 1000) + self.time_offset

    def get_all_supported_symbols(self) -> Dict:
        """
        获取所有支持的USDT合约交易对信息
        
        Returns:
            Dict: 交易对信息字典
        """
        try:
            # 获取所有合约交易对信息
            exchange_info = self._request(self.client.futures_exchange_info)
            supported_symbols = {}
            
            # 处理从API获取的交易对
            for symbol_info in exchange_info['symbols']:
                # 只处理USDT合约
                if symbol_info['quoteAsset'] == 'USDT' and symbol_info['status'] == 'TRADING':
                    symbol = symbol_info['symbol']
                    
                    # 获取数量精度
                    quantity_precision = 0
                    min_qty = 0.001  # 默认值
                    for filter in symbol_info['filters']:
                        if filter['filterType'] == 'LOT_SIZE':
                            step_size = float(filter['stepSize'])
                            quantity_precision = len(str(step_size).rstrip('0').split('.')[-1]) if '.' in str(step_size) else 0
                            min_qty = float(filter['minQty'])
                            break
                    
                    # 获取价格精度
                    price_precision = 0
                    for filter in symbol_info['filters']:
                        if filter['filterType'] == 'PRICE_FILTER':
                            tick_size = float(filter['tickSize'])
                            price_precision = len(str(tick_size).rstrip('0').split('.')[-1]) if '.' in str(tick_size) else 0
                            break
                    
                    # 获取最小名义金额
                    min_notional = 5  # 默认值
                    for filter in symbol_info['filters']:
                        if filter['filterType'] == 'MIN_NOTIONAL':
                            min_notional = float(filter['notional'])
                            break
                    
                    supported_symbols[symbol] = {
                        'symbol': symbol,
                        'quantity_precision': quantity_precision,
                        'price_precision': price_precision,
                        'min_qty': min_qty,
                        'min_notional': min_notional
                    }
            
            # 确保全局变量被更新
            global SUPPORTED_SYMBOLS
            SUPPORTED_SYMBOLS = supported_symbols
            
            logger.info(f"已加载 {len(supported_symbols)} 个USDT合约交易对")
            return supported_symbols
            
        except Exception as e:
            logger.error(f"获取支持的交易对信息失败: {e}")
            # 如果API调用失败，返回空字典
            return {}

    def _request(self, method, *args, **kwargs):
        """
        发送请求到币安API，包含重试机制
        
        Args:
            method: 要调用的API方法
            *args: 位置参数
            **kwargs: 关键字参数
            
        Returns:
            请求结果
        """
        max_retries = 3  # 最大重试次数
        retry_delay = 2  # 重试延迟（秒）
        
        for attempt in range(max_retries):
            try:
                # 调用API方法
                response = method(*args, **kwargs)
                return response
                
            except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError) as e:
                if attempt < max_retries - 1:  # 如果不是最后一次尝试
                    logger.warning(f"代理连接失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                    time.sleep(retry_delay * (attempt + 1))  # 递增延迟
                    continue
                else:  # 最后一次尝试失败
                    logger.error(f"代理连接失败，已达到最大重试次数: {e}")
                    raise
                    
            except BinanceAPIException as e:
                if e.code == -4068:  # 持仓模式错误
                    logger.warning("无法更改持仓模式：当前有持仓")
                    return None
                elif attempt < max_retries - 1:  # 如果不是最后一次尝试
                    logger.warning(f"API请求失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                    time.sleep(retry_delay * (attempt + 1))  # 递增延迟
                    continue
                else:  # 最后一次尝试失败
                    logger.error(f"API请求失败，已达到最大重试次数: {e}")
                    raise
                    
            except Exception as e:
                if attempt < max_retries - 1:  # 如果不是最后一次尝试
                    logger.warning(f"API请求失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                    time.sleep(retry_delay * (attempt + 1))  # 递增延迟
                    continue
                else:  # 最后一次尝试失败
                    logger.error(f"API请求失败，已达到最大重试次数: {e}")
                    raise

    def clean_expired_signals(self):
        """
        清理过期的执行记录（超过4小时的记录）
        """
        try:
            current_time = time.time()
            expired_keys = []
            
            # 找出过期的记录
            for signal_key, execution_time in self.executed_signals.items():
                # 检查是否超过4小时
                if current_time - execution_time >= 4 * 3600:  # 4小时 = 4 * 3600秒
                    # 检查信号是否已经完成（通过订单配对关系）
                    signal_parts = signal_key.split('_')
                    if len(signal_parts) >= 4:
                        try:
                            symbol = signal_parts[0]
                            side = signal_parts[1]
                            entry_price = float(signal_parts[2]) if signal_parts[2] is not None else None
                            
                            if entry_price is None:
                                logger.warning(f"跳过无效的入场价格: {signal_key}")
                                continue
                            
                            # 检查是否有对应的已完成订单
                            has_completed_order = False
                            for pair in self.order_pairs.values():
                                if (pair['symbol'] == symbol and 
                                    pair['side'] == side and 
                                    pair['entry_price'] is not None and
                                    abs(float(pair['entry_price']) - entry_price) / entry_price <= 0.001):
                                    if pair['status'] in ['closed_by_stop_loss', 'closed_by_take_profit']:
                                        has_completed_order = True
                                        break
                            
                            # 如果没有已完成订单，则保留记录
                            if not has_completed_order:
                                continue
                        except (ValueError, TypeError) as e:
                            logger.warning(f"处理信号 {signal_key} 时出错: {e}")
                            continue
                    
                    expired_keys.append(signal_key)
            
            # 删除过期记录
            for key in expired_keys:
                del self.executed_signals[key]
            
            if expired_keys:
                logger.info(f"已清理 {len(expired_keys)} 条过期记录")
                # 保存更新后的记录
                self.save_executed_signals()
                
        except Exception as e:
            logger.error(f"清理过期记录时出错: {e}")

    def load_btc_position_config(self) -> Dict:
        """
        加载BTC仓位配置
        
        Returns:
            Dict: BTC仓位配置，格式为 {channel: {'position_ratio': float}}
        """
        try:
            if os.path.exists(self.btc_position_file):
                df = pd.read_excel(self.btc_position_file)
                config = {}
                
                # 检查列名
                size_column = None
                for col in df.columns:
                    if 'size' in col.lower():
                        size_column = col
                        break
                
                if size_column is None:
                    logger.error("未找到仓位大小列，请确保Excel文件包含'position_size'列")
                    return {}
                
                for _, row in df.iterrows():
                    channel = str(row['channel']).strip() if 'channel' in df.columns else str(row['频道']).strip()
                    if channel and not pd.isna(channel):
                        try:
                            position_ratio = float(row[size_column]) if not pd.isna(row[size_column]) else 0
                            config[channel] = {
                                'position_ratio': position_ratio
                            }
                        except (ValueError, TypeError) as e:
                            logger.error(f"解析渠道 {channel} 的仓位比例失败: {e}")
                            continue
                
                logger.info(f"已加载 {len(config)} 个BTC仓位配置")
                return config
            else:
                logger.warning(f"BTC仓位配置文件不存在: {self.btc_position_file}")
                return {}
        except Exception as e:
            logger.error(f"加载BTC仓位配置失败: {e}")
            return {}

    def get_btc_position_size(self, channel: str) -> Optional[float]:
        """
        获取指定渠道的BTC仓位大小
        
        Args:
            channel: 渠道名称
            
        Returns:
            Optional[float]: 仓位大小（USDT），如果未找到或比例为0则返回None
        """
        try:
            if channel in self.btc_channel_positions:
                position_ratio = self.btc_channel_positions[channel]['position_ratio']
                if position_ratio == 0:
                    logger.info(f"渠道 {channel} 的仓位比例为0，跳过下单")
                    return None
                    
                # 计算基础仓位（position_ratio已经是百分比，例如3.3表示3.3%）
                base_position = self.btc_initial_capital * (position_ratio / 100)
                
                # 限制基础仓位最大为300 USDT
                max_base_position = 300  # 最大基础仓位300 USDT
                base_position = min(base_position, max_base_position)
                
                # 应用杠杆
                leveraged_position = base_position * self.btc_leverage
                
                logger.info(f"渠道 {channel} 的BTC仓位计算详情:")
                logger.info(f"  初始资金: {self.btc_initial_capital} USDT")
                logger.info(f"  仓位比例: {position_ratio}%")
                logger.info(f"  基础仓位: {base_position:.2f} USDT")
                logger.info(f"  杠杆倍数: {self.btc_leverage}x")
                logger.info(f"  杠杆后仓位: {leveraged_position:.2f} USDT")
                logger.info(f"  所需保证金: {base_position:.2f} USDT")
                
                return leveraged_position
                
            logger.info(f"未找到渠道 {channel} 的BTC仓位配置，跳过下单")
            return None
        except Exception as e:
            logger.error(f"获取BTC仓位大小失败: {e}")
            return None

    def get_btc_position_side(self, channel: str, original_side: str) -> str:
        """
        根据仓位比例确定交易方向
        
        Args:
            channel: 渠道名称
            original_side: 原始交易方向（BUY/SELL）
            
        Returns:
            str: 实际交易方向（BUY/SELL）
        """
        try:
            if channel in self.btc_channel_positions:
                position_ratio = self.btc_channel_positions[channel]['position_ratio']
                if position_ratio < 0:
                    # 如果比例为负，则反转交易方向
                    return 'SELL' if original_side == 'BUY' else 'BUY'
            return original_side
        except Exception as e:
            logger.error(f"获取BTC交易方向失败: {e}")
            return original_side

    def update_btc_position_config(self, new_config: Dict) -> bool:
        """
        更新BTC仓位配置
        
        Args:
            new_config: 新的配置字典，格式为 {channel: {'position_ratio': float}}
            
        Returns:
            bool: 是否更新成功
        """
        try:
            # 更新内存中的配置
            self.btc_channel_positions.update(new_config)
            
            # 保存到文件
            df = pd.DataFrame([
                {
                    'channel': channel,
                    'position_ratio': config['position_ratio']
                }
                for channel, config in self.btc_channel_positions.items()
            ])
            
            df.to_excel(self.btc_position_file, index=False)
            logger.info(f"已更新 {len(new_config)} 个BTC仓位配置")
            return True
        except Exception as e:
            logger.error(f"更新BTC仓位配置失败: {e}")
            return False

    def get_all_btc_channel_positions(self) -> Dict:
        """
        获取所有BTC渠道的仓位配置
        
        Returns:
            Dict: 所有渠道的仓位配置
        """
        return self.btc_channel_positions.copy()

    def save_order_pairs(self):
        """
        保存订单配对关系到文件
        """
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(self.order_pairs_file), exist_ok=True)
            
            # 保存记录
            with open(self.order_pairs_file, 'w', encoding='utf-8') as f:
                json.dump(self.order_pairs, f, ensure_ascii=False, indent=2)
            logger.info(f"已保存 {len(self.order_pairs)} 条订单配对关系")
        except Exception as e:
            logger.error(f"保存订单配对关系失败: {e}")

    def load_order_pairs(self) -> Dict:
        """
        加载订单配对关系
        
        Returns:
            Dict: 订单配对关系字典
        """
        try:
            if os.path.exists(self.order_pairs_file):
                with open(self.order_pairs_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    logger.info(f"已加载 {len(data)} 条订单配对关系")
                    return data
            return {}
        except Exception as e:
            logger.error(f"加载订单配对关系失败: {e}")
            return {}

def main():
    try:
        # 创建交易实例
        trader = BinanceTrader()
        
        # 开始监控交易
        trader.monitor_and_trade()
        
    except Exception as e:
        logger.error(f"程序运行出错: {e}")

if __name__ == "__main__":
    main() 