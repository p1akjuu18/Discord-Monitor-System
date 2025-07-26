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

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 硬编码配置信息
BINANCE_API_KEY = "VIJFNRp99K0dvJ7GEZFsnCVbuLhruo2H1Kh4xADeviXjegoV0NQKzFv8I2cSMcvF"
BINANCE_API_SECRET = "jmEqAmrFYOhfnT65YFOvxn6nhOQ7TYw9pJlGltOY0xxDCyrZDrSQdiK9z4Yygc9a"

# 交易配置
TRADING_CONFIG = {
    'position_size': 200,  # 固定交易金额（USDT）
    'leverage': 5,       # 杠杆倍数
    'margin_type': 'CROSSED',  # 保证金类型：ISOLATED(逐仓) 或 CROSSED(全仓)
}

# 支持的交易对及其精度
SUPPORTED_SYMBOLS = {}  # 将由get_all_supported_symbols方法动态填充

class BinanceTrader:
    def __init__(self, api_key: str = None, api_secret: str = None):
        """
        初始化币安交易客户端
        
        Args:
            api_key: 币安API Key
            api_secret: 币安API Secret
        """
        # 使用硬编码的API密钥
        self.api_key = api_key or BINANCE_API_KEY
        self.api_secret = api_secret or BINANCE_API_SECRET
        
        if not self.api_key or not self.api_secret:
            raise ValueError("必须提供币安API密钥和密钥")
        
        # 初始化币安客户端
        self.client = Client(self.api_key, self.api_secret)
        
        # 初始化时间偏移量
        self.time_offset = 0
        
        # 同步服务器时间（最多重试3次）
        for _ in range(3):
            try:
                server_time = self.client.get_server_time()
                local_time = int(time.time() * 1000)
                self.time_offset = server_time['serverTime'] - local_time
                logger.info(f"服务器时间差: {self.time_offset}ms")
                if abs(self.time_offset) > 1000:  # 如果时间差超过1秒
                    logger.warning(f"系统时间与服务器时间不同步，已自动调整时间差")
                break
            except Exception as e:
                logger.error(f"同步服务器时间失败: {e}")
                time.sleep(1)  # 等待1秒后重试
        
        # 使用硬编码的交易配置
        self.trading_config = TRADING_CONFIG.copy()
        
        # 初始化交易状态
        self.active_orders = {}
        self.position_info = {}
        
        # 分析结果文件路径
        self.analysis_file = os.path.join('data', 'analysis_results', 'all_analysis_results.csv')
        
        # 已执行订单记录文件
        self.executed_orders_file = os.path.join('data', 'executed_orders.json')
        
        # 加载已执行的订单记录
        self.executed_signals = self.load_executed_signals()
        
        # 订单时间记录
        self.order_timestamps = {}
        
        # 获取所有支持的交易对信息
        self.supported_symbols = self.get_all_supported_symbols()
        
        # 检查账户持仓情况
        try:
            # 检查当前持仓模式
            position_mode = self._request(self.client.futures_get_position_mode)
            logger.info(f"当前持仓模式: {'双向持仓' if position_mode['dualSidePosition'] else '单向持仓'}")
            
            # 如果不是单向持仓，则设置为单向持仓
            if position_mode['dualSidePosition']:
                self._request(self.client.futures_change_position_mode, dualSidePosition=False)
                logger.info("已设置持仓模式为单向持仓")
            
            # 设置保证金类型和杠杆
            for symbol_info in self.supported_symbols.values():
                try:
                    # 先设置保证金类型
                    self._request(self.client.futures_change_margin_type, symbol=symbol_info['symbol'], marginType=self.trading_config['margin_type'])
                    logger.info(f"已设置{symbol_info['symbol']}保证金类型为{self.trading_config['margin_type']}")
                    
                    # 再设置杠杆倍数
                    self._request(self.client.futures_change_leverage, symbol=symbol_info['symbol'], leverage=self.trading_config['leverage'])
                    logger.info(f"已设置{symbol_info['symbol']}杠杆倍数为{self.trading_config['leverage']}倍")
                    
                except BinanceAPIException as e:
                    if e.code == -4046:  # 保证金类型已经是目标类型
                        logger.info(f"{symbol_info['symbol']}保证金类型已经是{self.trading_config['margin_type']}")
                    elif e.code == -4047:  # 杠杆已经是目标倍数
                        logger.info(f"{symbol_info['symbol']}杠杆倍数已经是{self.trading_config['leverage']}倍")
                    else:
                        logger.error(f"设置{symbol_info['symbol']}合约参数时出错: {e}")
            
        except BinanceAPIException as e:
            logger.error(f"初始化合约设置时出错: {e}")
        
        logger.info("币安合约交易客户端初始化完成")
    
    def load_executed_signals(self) -> set:
        """
        从文件加载已执行的订单记录
        
        Returns:
            set: 已执行的订单记录集合
        """
        try:
            if os.path.exists(self.executed_orders_file):
                with open(self.executed_orders_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    logger.info(f"已加载 {len(data)} 条已执行订单记录")
                    return set(data)
            return set()
        except Exception as e:
            logger.error(f"加载已执行订单记录失败: {e}")
            return set()

    def save_executed_signals(self):
        """
        保存已执行的订单记录到文件
        """
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(self.executed_orders_file), exist_ok=True)
            
            # 保存记录
            with open(self.executed_orders_file, 'w', encoding='utf-8') as f:
                json.dump(list(self.executed_signals), f, ensure_ascii=False, indent=2)
            logger.info(f"已保存 {len(self.executed_signals)} 条已执行订单记录")
        except Exception as e:
            logger.error(f"保存已执行订单记录失败: {e}")

    def get_account_info(self) -> Dict:
        """
        获取合约账户信息
        
        Returns:
            Dict: 账户信息
        """
        try:
            return self._request(self.client.futures_account)
        except BinanceAPIException as e:
            logger.error(f"获取合约账户信息失败: {e}")
            return {}
    
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
            return 0.0
    
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
        获取当前价格
        
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
                
            # 使用期货API获取价格
            ticker = self._request(self.client.futures_symbol_ticker, symbol=base_symbol)
            price = float(ticker['price'])
            logger.info(f"获取{base_symbol}期货价格: {price}")
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
                raise ValueError(f"不支持的交易对: {symbol}")
            
            # 格式化价格
            precision = symbol_info['price_precision']
            formatted_price = float(f"{{:.{precision}f}}".format(price))
            
            return formatted_price
            
        except Exception as e:
            logger.error(f"格式化价格时出错: {e}")
            raise

    def place_order(self, 
                   symbol: str, 
                   side: str, 
                   order_type: str, 
                   quantity: float = None,
                   price: float = None,
                   stop_price: float = None,
                   time_in_force: str = 'GTC',
                   notional: float = None) -> Dict:
        """
        下合约单
        
        Args:
            symbol: 交易对符号
            side: 方向 (BUY/SELL)
            order_type: 订单类型
            quantity: 数量（可选）
            price: 价格（限价单需要）
            stop_price: 触发价格（止损/止盈单需要）
            time_in_force: 订单有效期
            notional: 名义金额（可选）
            
        Returns:
            Dict: 订单信息
        """
        try:
            logger.info(f"准备下单: {symbol} {side} {order_type}")
            logger.info(f"当前支持的交易对: {list(self.supported_symbols.keys())}")
            
            # 检查当前持仓模式
            position_mode = self._request(self.client.futures_get_position_mode)
            is_hedge_mode = position_mode['dualSidePosition']
            logger.info(f"当前持仓模式: {'对冲模式' if is_hedge_mode else '单向持仓'}")
            
            # 确保保证金类型和杠杆设置正确
            try:
                # 先设置保证金类型
                self._request(self.client.futures_change_margin_type, symbol=symbol, marginType=self.trading_config['margin_type'])
                logger.info(f"已设置{symbol}保证金类型为{self.trading_config['margin_type']}")
                
                # 再设置杠杆倍数
                self._request(self.client.futures_change_leverage, symbol=symbol, leverage=self.trading_config['leverage'])
                logger.info(f"已设置{symbol}杠杆倍数为{self.trading_config['leverage']}倍")
                
            except BinanceAPIException as e:
                if e.code not in [-4046, -4047]:  # 忽略"已经是目标类型/倍数"的错误
                    raise
                else:
                    logger.info(f"保证金类型或杠杆倍数已经是目标值: {e}")
            
            # 获取交易对信息
            symbol_info = None
            for key, value in self.supported_symbols.items():
                if value['symbol'] == symbol:
                    symbol_info = value
                    break
            
            if not symbol_info:
                logger.error(f"交易对 {symbol} 不在支持列表中")
                logger.error(f"当前支持的交易对: {list(self.supported_symbols.keys())}")
                raise ValueError(f"不支持的交易对: {symbol}")
            
            logger.info(f"找到交易对信息: {symbol_info}")
            
            # 格式化价格
            if price:
                price = self.format_price(symbol, price)
                logger.info(f"格式化后的价格: {price}")
            if stop_price:
                stop_price = self.format_price(symbol, stop_price)
                logger.info(f"格式化后的止损价格: {stop_price}")
            
            # 构建订单参数
            params = {
                'symbol': symbol,
                'side': side,
                'type': order_type
            }
            
            # 根据持仓模式设置positionSide
            if is_hedge_mode:
                # 对冲模式下，需要设置positionSide
                params['positionSide'] = 'LONG' if side == 'BUY' else 'SHORT'
                logger.info(f"对冲模式，设置positionSide: {params['positionSide']}")
            
            # 如果提供了名义金额，使用名义金额下单
            if notional:
                # 获取当前价格用于计算数量
                current_price = self.get_current_price(symbol)
                if not current_price:
                    raise ValueError(f"无法获取{symbol}当前价格")
                
                logger.info(f"使用名义金额下单: {notional} USDT")
                logger.info(f"当前价格: {current_price}")
                
                # 计算最小所需数量以满足200 USDT的名义金额要求
                min_quantity = notional / current_price
                
                # 根据精度格式化数量
                precision = symbol_info['quantity_precision']
                if precision == 0:
                    # 对于整数精度的币种，向上取整并加1
                    min_quantity = int(min_quantity) + 1
                else:
                    # 对于小数精度的币种，增加5%的余量以确保超过最小要求
                    min_quantity = min_quantity * 1.05
                    min_quantity = float(f"{{:.{precision}f}}".format(min_quantity))
                
                # 验证名义金额是否满足要求
                actual_notional = min_quantity * current_price
                if actual_notional < notional:
                    # 如果仍然不满足要求，继续增加数量
                    min_quantity = notional / current_price
                    if precision == 0:
                        min_quantity = int(min_quantity) + 1
                    else:
                        min_quantity = min_quantity * 1.05
                        min_quantity = float(f"{{:.{precision}f}}".format(min_quantity))
                
                # 使用计算出的最小数量
                quantity = min_quantity
                logger.info(f"计算出的交易数量: {quantity} (名义金额: {quantity * current_price:.2f} USDT)")
                
                params['quantity'] = quantity
                
                # 根据订单类型设置不同的参数
                if order_type in ['STOP_MARKET', 'TAKE_PROFIT_MARKET']:
                    # 止损/止盈单使用市价单
                    params['type'] = order_type
                    params['stopPrice'] = stop_price
                    params['workingType'] = 'MARK_PRICE'
                else:
                    # 其他订单使用限价单
                    if not price:
                        raise ValueError("使用名义金额下单时必须提供价格")
                    params['type'] = 'LIMIT'
                    params['timeInForce'] = 'GTC'
                    params['price'] = price
            else:
                # 否则使用数量下单
                if quantity:
                    quantity = self.format_quantity(symbol, quantity)
                    params['quantity'] = quantity
                    # 只有在使用数量下单时才添加timeInForce
                    if order_type in ['LIMIT', 'STOP_LIMIT']:
                        params['timeInForce'] = time_in_force
                        params['price'] = price
                    # 只有在使用数量下单时才添加reduceOnly和closePosition
                    params['reduceOnly'] = False
                    params['closePosition'] = False
            
            logger.info(f"最终订单参数: {params}")
            
            # 下单
            order = self._request(self.client.futures_create_order, **params)
            logger.info(f"下合约单成功: {symbol} {side} {order_type} {params.get('quantity')}")
            return order
            
        except Exception as e:
            logger.error(f"下合约单失败: {e}")
            raise
    
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
    
    def place_stop_loss_order(self, symbol: str, side: str, quantity: float, stop_price: float) -> Dict:
        """
        下合约止损单
        
        Args:
            symbol: 交易对符号
            side: 方向 (BUY/SELL)
            quantity: 数量
            stop_price: 触发价格
            
        Returns:
            Dict: 订单信息
        """
        return self.place_order(symbol, side, 'STOP_MARKET', quantity, stop_price=stop_price)
    
    def place_take_profit_order(self, symbol: str, side: str, quantity: float, stop_price: float) -> Dict:
        """
        下合约止盈单
        
        Args:
            symbol: 交易对符号
            side: 方向 (BUY/SELL)
            quantity: 数量
            stop_price: 触发价格
            
        Returns:
            Dict: 订单信息
        """
        return self.place_order(symbol, side, 'TAKE_PROFIT_MARKET', quantity, stop_price=stop_price)
    
    def update_trading_config(self, config: Dict):
        """
        更新交易配置
        
        Args:
            config: 新的配置字典
        """
        self.trading_config.update(config)
        logger.info(f"交易配置已更新: {config}")
    
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
                    
                    logger.info(f"处理交易信号: {symbol}")
                    logger.info(f"当前支持的交易对: {list(SUPPORTED_SYMBOLS.keys())}")
                    
                    # 标准化交易对
                    normalized_symbol = None
                    # 移除可能的后缀（如USDT）
                    base_symbol = symbol.replace('USDT', '').strip()
                    logger.info(f"基础币种: {base_symbol}")
                    
                    # 检查是否在支持的交易对中
                    if base_symbol in SUPPORTED_SYMBOLS:
                        normalized_symbol = SUPPORTED_SYMBOLS[base_symbol]['symbol']
                        logger.info(f"找到匹配的交易对: {normalized_symbol}")
                    else:
                        # 尝试直接匹配完整的交易对
                        for base, info in SUPPORTED_SYMBOLS.items():
                            if info['symbol'] == symbol:
                                normalized_symbol = symbol
                                logger.info(f"通过完整匹配找到交易对: {normalized_symbol}")
                                break
                    
                    if not normalized_symbol:
                        logger.warning(f"不支持的交易对: {symbol}")
                        continue
                    
                    # 获取方向
                    direction = str(row['analysis.方向']).strip()
                    if '空' in direction or 'short' in direction.lower() or 'sell' in direction.lower():
                        side = 'SELL'
                    else:
                        side = 'BUY'
                    
                    # 获取入场价格
                    try:
                        entry_price = float(row['analysis.入场点位1'])
                        if entry_price <= 0:
                            continue
                    except (ValueError, TypeError):
                        continue
                    
                    # 获取止损价格
                    try:
                        stop_loss = float(row['analysis.止损点位1'])
                        if stop_loss <= 0:
                            continue
                    except (ValueError, TypeError):
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
                            except (ValueError, TypeError):
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
                        'target_price': target_price  # 可能为None
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
        # 格式化价格，确保精度一致
        entry_price = self.format_price(signal['symbol'], signal['entry_price'])
        
        # 只使用交易对、方向和入场价格作为标识
        signal_key = f"{signal['symbol']}_{signal['side']}_{entry_price}"
        logger.info(f"生成信号标识: {signal_key}")
        return signal_key

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
        if signal_key in self.order_timestamps:
            last_execution_time = self.order_timestamps[signal_key]
            if current_time - last_execution_time < 4 * 3600:  # 4小时 = 4 * 3600秒
                logger.info(f"信号 {signal_key} 在4小时内已执行过，跳过")
                return True
        
        return signal_key in self.executed_signals

    def mark_signal_executed(self, signal: Dict):
        """
        标记交易信号为已执行
        
        Args:
            signal: 交易信号字典
        """
        signal_key = self.get_signal_key(signal)
        current_time = time.time()
        
        # 更新执行时间
        self.order_timestamps[signal_key] = current_time
        
        # 添加到已执行集合
        self.executed_signals.add(signal_key)
        
        # 保存到文件
        self.save_executed_signals()
        logger.info(f"标记信号为已执行: {signal_key}")

    def execute_trading_signals(self, signals: List[Dict]):
        """
        执行交易信号
        
        Args:
            signals: 交易信号列表
        """
        try:
            for signal in signals:
                try:
                    # 检查信号是否已执行
                    if self.is_signal_executed(signal):
                        logger.info(f"跳过已执行的信号: {signal}")
                        continue
                    
                    # 获取交易对信息
                    symbol = signal.get('symbol')
                    if not symbol:
                        logger.error("交易信号缺少交易对信息")
                        continue
                    
                    # 获取交易方向
                    side = signal.get('side')
                    if not side:
                        logger.error(f"交易信号缺少方向信息: {signal}")
                        continue
                    
                    # 获取入场价格
                    entry_price = signal.get('entry_price')
                    if not entry_price or entry_price <= 0:
                        logger.error(f"交易信号缺少有效的入场价格: {signal}")
                        continue
                    
                    # 获取止损价格
                    stop_loss = signal.get('stop_loss')
                    if not stop_loss or stop_loss <= 0:
                        logger.error(f"交易信号缺少有效的止损价格: {signal}")
                        continue
                    
                    # 获取止盈价格（可选）
                    target_price = signal.get('target_price')
                    
                    # 获取当前市场价格
                    current_price = self.get_current_price(symbol)
                    if not current_price:
                        logger.error(f"无法获取{symbol}当前价格")
                        continue
                    
                    # 格式化价格
                    entry_price = self.format_price(symbol, entry_price)
                    stop_loss = self.format_price(symbol, stop_loss)
                    if target_price:
                        target_price = self.format_price(symbol, target_price)
                    
                    # 检查价格是否合适
                    price_tolerance = 0.001  # 0.1%的价格容差
                    
                    if side == 'BUY':
                        # 做多时，入场价应该低于当前价格
                        if entry_price >= current_price * (1 + price_tolerance):
                            logger.warning(f"做多入场价格 {entry_price} 高于当前价格 {current_price}，跳过信号")
                            continue
                        # 止损价应该低于入场价
                        if stop_loss >= entry_price * (1 - price_tolerance):
                            logger.warning(f"做多止损价格 {stop_loss} 太接近入场价格 {entry_price}，跳过信号")
                            continue
                        # 止盈价应该高于入场价
                        if target_price and target_price <= entry_price * (1 + price_tolerance):
                            logger.warning(f"做多止盈价格 {target_price} 太接近入场价格 {entry_price}，跳过信号")
                            continue
                    else:  # SELL
                        # 做空时，入场价应该高于当前价格
                        if entry_price <= current_price * (1 - price_tolerance):
                            logger.warning(f"做空入场价格 {entry_price} 低于当前价格 {current_price}，跳过信号")
                            continue
                        # 止损价应该高于入场价
                        if stop_loss <= entry_price * (1 + price_tolerance):
                            logger.warning(f"做空止损价格 {stop_loss} 太接近入场价格 {entry_price}，跳过信号")
                            continue
                        # 止盈价应该低于入场价
                        if target_price and target_price >= entry_price * (1 - price_tolerance):
                            logger.warning(f"做空止盈价格 {target_price} 太接近入场价格 {entry_price}，跳过信号")
                            continue
                    
                    # 检查余额是否足够
                    if not self.check_balance_sufficient(symbol, 200):
                        logger.error(f"余额不足，跳过交易信号: {symbol}")
                        continue
                    
                    # 直接使用名义金额下单
                    logger.info(f"准备下单: {symbol} {side} 名义金额: 200 USDT @ {entry_price}")
                    logger.info(f"当前价格: {current_price}, 止损价格: {stop_loss}")
                    
                    # 下入场限价单
                    entry_order = self.place_order(symbol, side, 'LIMIT', price=entry_price, notional=200)
                    if not entry_order:
                        logger.error(f"下入场单失败: {symbol} {side} @ {entry_price}")
                        continue
                    
                    logger.info(f"下入场单成功: {symbol} {side} @ {entry_price}")
                    
                    # 下止损单
                    stop_loss_side = 'SELL' if side == 'BUY' else 'BUY'
                    stop_loss_order = self.place_order(symbol, stop_loss_side, 'STOP_MARKET', stop_price=stop_loss, notional=200)
                    if not stop_loss_order:
                        logger.error(f"下止损单失败: {symbol} {stop_loss_side} @ {stop_loss}")
                        continue
                    
                    logger.info(f"下止损单成功: {symbol} {stop_loss_side} @ {stop_loss}")
                    
                    # 标记信号为已执行（在入场单和止损单都成功后立即标记）
                    self.mark_signal_executed(signal)
                    logger.info(f"已标记信号为已执行: {self.get_signal_key(signal)}")
                    
                    # 如果有止盈价格，尝试下止盈单（但不影响信号标记）
                    if target_price:
                        try:
                            take_profit_side = 'SELL' if side == 'BUY' else 'BUY'
                            take_profit_order = self.place_order(symbol, take_profit_side, 'TAKE_PROFIT_MARKET', stop_price=target_price, notional=200)
                            if not take_profit_order:
                                logger.warning(f"下止盈单失败: {symbol} {take_profit_side} @ {target_price}")
                            else:
                                logger.info(f"下止盈单成功: {symbol} {take_profit_side} @ {target_price}")
                        except Exception as e:
                            logger.warning(f"下止盈单时出错（不影响交易）: {e}")
                    
                    # 等待订单执行
                    time.sleep(1)
                    
                except Exception as e:
                    logger.error(f"执行交易信号时出错: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"执行交易信号时出错: {e}")

    def monitor_and_trade(self, interval: int = 60):
        """
        监控并执行交易
        
        Args:
            interval: 检查间隔（秒）
        """
        logger.info("开始监控交易信号...")
        while True:
            try:
                # 读取交易信号
                signals = self.read_trading_signals()
                if signals:
                    logger.info(f"发现 {len(signals)} 个交易信号")
                    # 过滤掉已执行的信号
                    new_signals = [s for s in signals if not self.is_signal_executed(s)]
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

    def get_cross_margin_account(self) -> Dict:
        """
        获取全仓账户详情
        
        Returns:
            Dict: 全仓账户信息
        """
        try:
            account = self._request(self.client.get_margin_account)
            
            logger.info("全仓账户信息:")
            logger.info(f"账户类型: {account.get('accountType', 'N/A')}")
            logger.info(f"保证金水平: {account.get('marginLevel', 'N/A')}")
            logger.info(f"总资产: {account.get('totalCollateralValueInUSDT', 'N/A')} USDT")
            logger.info(f"总负债: {account.get('totalLiabilityOfBtc', 'N/A')} BTC")
            logger.info(f"净资产: {account.get('totalNetAssetOfBtc', 'N/A')} BTC")
            
            # 打印各个币种的资产信息
            logger.info("\n各币种资产详情:")
            for asset in account.get('userAssets', []):
                logger.info(f"{asset['asset']}:")
                logger.info(f"  可用: {asset['free']}")
                logger.info(f"  已借: {asset['borrowed']}")
                logger.info(f"  利息: {asset['interest']}")
                logger.info(f"  锁定: {asset['locked']}")
                logger.info(f"  净资产: {asset['netAsset']}")
            
            return account
        except BinanceAPIException as e:
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
            logger.info("开始获取合约交易对信息...")
            exchange_info = self._request(self.client.futures_exchange_info)
            supported_symbols = {}
            
            logger.info(f"获取到 {len(exchange_info['symbols'])} 个交易对")
            
            # 处理从API获取的交易对
            for symbol_info in exchange_info['symbols']:
                # 只处理USDT合约
                if symbol_info['quoteAsset'] == 'USDT' and symbol_info['status'] == 'TRADING':
                    symbol = symbol_info['symbol']
                    base_asset = symbol_info['baseAsset']
                    
                    logger.info(f"处理交易对: {symbol} (基础资产: {base_asset})")
                    
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
                    
                    supported_symbols[base_asset] = {
                        'symbol': symbol,
                        'quantity_precision': quantity_precision,
                        'price_precision': price_precision,
                        'min_qty': min_qty,
                        'min_notional': min_notional
                    }
                    logger.info(f"添加交易对 {symbol} 到支持列表")
            
            logger.info(f"已加载 {len(supported_symbols)} 个USDT合约交易对")
            logger.info(f"支持的交易对列表: {list(supported_symbols.keys())}")
            
            # 打印所有支持的交易对详细信息
            for base_asset, info in supported_symbols.items():
                logger.info(f"交易对 {base_asset}: {info}")
            
            # 确保全局变量被更新
            global SUPPORTED_SYMBOLS
            SUPPORTED_SYMBOLS = supported_symbols
            
            return supported_symbols
            
        except Exception as e:
            logger.error(f"获取支持的交易对信息失败: {e}")
            # 如果API调用失败，返回空字典
            logger.info("API调用失败，返回空字典")
            return {}

    def _request(self, method, *args, **kwargs):
        """
        发送请求到币安API，自动处理时间同步
        
        Args:
            method: API方法
            *args: 位置参数
            **kwargs: 关键字参数
            
        Returns:
            请求结果
        """
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                # 更新请求时间戳
                if 'timestamp' in kwargs:
                    kwargs['timestamp'] = self.get_timestamp()
                return method(*args, **kwargs)
            except BinanceAPIException as e:
                if e.code == -1021:  # 时间戳错误
                    retry_count += 1
                    if retry_count < max_retries:
                        # 重新同步时间
                        try:
                            server_time = self.client.get_server_time()
                            local_time = int(time.time() * 1000)
                            self.time_offset = server_time['serverTime'] - local_time
                            logger.info(f"重新同步服务器时间，新的时间差: {self.time_offset}ms")
                            time.sleep(0.5)  # 等待0.5秒后重试
                            continue
                        except Exception as sync_error:
                            logger.error(f"重新同步时间失败: {sync_error}")
                    else:
                        logger.error(f"重试{max_retries}次后仍然失败")
                raise
            except Exception as e:
                logger.error(f"请求失败: {e}")
                raise

def main():
    try:
        # 创建交易实例
        trader = BinanceTrader()
        
        # 获取全仓账户信息
        cross_margin_account = trader.get_cross_margin_account()
        
        # 获取当前持仓信息
        position_info = trader.get_position_info()
        
        # 获取当前价格
        print("\n当前价格:")
        for key, value in SUPPORTED_SYMBOLS.items():
            price = trader.get_current_price(value['symbol'])
            if price:
                print(f"{value['symbol']}: {price}")
        
        # 显示交易配置
        print("\n当前交易配置:")
        print(json.dumps(trader.trading_config, indent=2))
        
        # 开始监控交易
        trader.monitor_and_trade()
        
    except Exception as e:
        logger.error(f"程序运行出错: {e}")

if __name__ == "__main__":
    main() 