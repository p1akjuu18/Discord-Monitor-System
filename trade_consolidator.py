import pandas as pd
import numpy as np
import os
import re
from datetime import datetime, timedelta
import argparse
import sys
from pathlib import Path

class TradeConsolidator:
    def __init__(self, order_file, price_data_dir, time_window_hours=24, verbose=False):
        """初始化交易合并器
        
        参数:
            order_file: 订单文件路径
            price_data_dir: 价格数据目录
            time_window_hours: 同一交易组内的最大时间窗口(小时)
            verbose: 是否输出详细日志
        """
        self.order_file = order_file
        self.price_data_dir = price_data_dir
        self.crypto_price_cache = {}  # 缓存已加载的价格数据
        self.time_window_hours = time_window_hours
        self.verbose = verbose

    def log(self, message):
        """根据verbose设置输出日志"""
        if self.verbose:
            print(message)

    def load_orders(self):
        """加载交易订单"""
        print(f"加载订单文件: {self.order_file}")
        self.orders_df = pd.read_excel(self.order_file)
        
        # 打印Excel文件的列名，帮助调试
        print(f"Excel文件中的列: {list(self.orders_df.columns)}")
        
        # 标准化列名 - 处理可能的列名不一致问题
        self._standardize_column_names()
        
        # 确保时间戳列为datetime格式
        if 'timestamp' not in self.orders_df.columns:
            print(f"错误: 未找到时间戳列。当前列有: {list(self.orders_df.columns)}")
            raise ValueError("订单文件中缺少时间戳列，请确保文件包含时间相关列并命名为如'timestamp'、'time'、'datetime'等")
            
        try:
            # 使用format='mixed'参数自动检测不同格式
            self.orders_df['timestamp'] = pd.to_datetime(self.orders_df['timestamp'], format='mixed')
        except Exception as e:
            print(f"时间戳解析错误: {e}")
            print("尝试ISO8601格式解析...")
            try:
                self.orders_df['timestamp'] = pd.to_datetime(self.orders_df['timestamp'], format='ISO8601')
            except:
                print("ISO8601解析失败，尝试手动解析...")
                # 手动解析
                timestamps = []
                for ts in self.orders_df['timestamp']:
                    try:
                        if isinstance(ts, str) and 'T' in ts:
                            dt = datetime.fromisoformat(ts)
                        else:
                            dt = pd.to_datetime(ts)
                        timestamps.append(dt)
                    except:
                        print(f"无法解析时间戳: {ts}")
                        timestamps.append(pd.NaT)
                self.orders_df['timestamp'] = timestamps
        
        # 移除时区信息以确保一致性
        if hasattr(self.orders_df['timestamp'].iloc[0], 'tzinfo') and self.orders_df['timestamp'].iloc[0].tzinfo is not None:
            self.orders_df['timestamp'] = self.orders_df['timestamp'].dt.tz_localize(None)
            
        print(f"成功加载 {len(self.orders_df)} 条订单记录")
        return self.orders_df

    def _standardize_column_names(self):
        """标准化列名以处理不同格式的订单文件"""
        # 定义可能的列名映射
        column_mappings = {
            # 时间戳
            'timestamp': ['timestamp', 'time', 'datetime', '时间戳', '时间', 'date', 'dates', '日期', '日期时间', 
                         'created_at', 'created', 'updated_at', 'update_time', '创建时间', '更新时间', 
                         'trade_time', 'trading_time', '交易时间', 'order_time', '订单时间', 'date_time'],
            
            # 交易币种
            'analysis_交易币种': ['analysis_交易币种', 'coin', 'crypto', 'symbol', '币种', '交易币种', 'token', 'asset', 'currency'],
            
            # 交易方向
            'analysis_方向': ['analysis_方向', 'direction', 'side', 'type', '方向', '交易方向', 'trade_type', 'position', 'long_short', '多空'],
            
            # 杠杆
            'analysis_杠杆': ['analysis_杠杆', 'leverage', '杠杆'],
            
            # 入场点位
            'analysis_入场点位1': ['analysis_入场点位1', 'entry1', 'entry_price1', '入场点位1', '入场价1'],
            'analysis_入场点位2': ['analysis_入场点位2', 'entry2', 'entry_price2', '入场点位2', '入场价2'],
            'analysis_入场点位3': ['analysis_入场点位3', 'entry3', 'entry_price3', '入场点位3', '入场价3'],
            
            # 止损点位
            'analysis_止损点位1': ['analysis_止损点位1', 'sl1', 'stop_loss1', '止损点位1', '止损价1'],
            'analysis_止损点位2': ['analysis_止损点位2', 'sl2', 'stop_loss2', '止损点位2', '止损价2'],
            'analysis_止损点位3': ['analysis_止损点位3', 'sl3', 'stop_loss3', '止损点位3', '止损价3'],
            
            # 止盈点位
            'analysis_止盈点位1': ['analysis_止盈点位1', 'tp1', 'take_profit1', '止盈点位1', '止盈价1'],
            'analysis_止盈点位2': ['analysis_止盈点位2', 'tp2', 'take_profit2', '止盈点位2', '止盈价2'],
            'analysis_止盈点位3': ['analysis_止盈点位3', 'tp3', 'take_profit3', '止盈点位3', '止盈价3'],
            
            # 分析内容
            'analysis_分析内容': ['analysis_分析内容', 'content', 'analysis', 'note', '分析内容', '备注']
        }
        
        # 获取当前的列名
        current_columns = self.orders_df.columns.tolist()
        new_columns = current_columns.copy()
        
        # 标准化列名
        for standard_col, possible_cols in column_mappings.items():
            # 检查是否已经有标准列名
            if standard_col in current_columns:
                continue
                
            # 查找可能匹配的列名
            for col in possible_cols:
                if col in current_columns:
                    # 找到匹配的列，重命名
                    col_idx = new_columns.index(col)
                    new_columns[col_idx] = standard_col
                    self.log(f"将列 '{col}' 重命名为标准列名 '{standard_col}'")
                    break
        
        # 更新DataFrame的列名
        if new_columns != current_columns:
            self.orders_df.columns = new_columns
            print(f"已标准化 {sum([1 for a, b in zip(current_columns, new_columns) if a != b])} 个列名")
        
        # 检查必要的列是否存在
        required_columns = ['timestamp', 'analysis_交易币种', 'analysis_方向']
        missing_columns = [col for col in required_columns if col not in new_columns]
        
        if missing_columns:
            print(f"警告: 缺少必要的列: {missing_columns}")
            
            # 如果缺少时间戳列，尝试创建一个
            if 'timestamp' in missing_columns:
                # 方法1: 查找以time结尾的列
                if any(col.lower().endswith('time') for col in new_columns):
                    time_col = next(col for col in new_columns if col.lower().endswith('time'))
                    self.orders_df['timestamp'] = self.orders_df[time_col]
                    print(f"使用 '{time_col}' 列作为时间戳替代")
                    missing_columns.remove('timestamp')
                # 方法2: 尝试检测可能是日期的列
                else:
                    # 检查所有列，查找可能是日期的内容
                    for col in new_columns:
                        # 尝试将前5行转换为日期，如果成功率高则认为是日期列
                        sample = self.orders_df[col].head(5)
                        date_count = 0
                        
                        for val in sample:
                            if pd.notna(val):  # 不是NA值
                                try:
                                    # 尝试解析为日期
                                    pd.to_datetime(val)
                                    date_count += 1
                                except:
                                    pass
                        
                        # 如果超过60%的样本可以转换为日期，则使用此列
                        if date_count / len(sample) >= 0.6:
                            print(f"检测到列 '{col}' 可能包含日期数据，使用它作为timestamp")
                            self.orders_df['timestamp'] = self.orders_df[col]
                            missing_columns.remove('timestamp')
                            break
                
                if 'timestamp' in missing_columns:
                    print("无法自动检测时间戳列，请检查数据文件格式")
        
        return self.orders_df

    def get_crypto_price_data(self, crypto, date):
        """获取加密货币的价格数据"""
        # 通用名称映射
        crypto_map = {
            'BTC': 'btcusdt',
            'ETH': 'ethusdt',
            'SOL': 'solusdt'
        }
        
        # 如果已经缓存了该币种的数据，直接返回
        cache_key = f"{crypto}_{date.strftime('%Y-%m')}"
        if cache_key in self.crypto_price_cache:
            return self.crypto_price_cache[cache_key]
        
        # 处理格式如"aaveusdt_202410"的代币名称
        cleaned_crypto = crypto.lower()
        
        # 如果包含下划线，可能是带日期的格式
        if '_' in cleaned_crypto:
            # 分离币种名称和日期部分
            parts = cleaned_crypto.split('_')
            base_name = parts[0]
            
            # 去除usdt后缀以获取基础币种名称
            if base_name.endswith('usdt'):
                base_name = base_name[:-4]
            
            # 使用干净的币种名称
            prefix = base_name
        else:
            # 构建文件名前缀
            prefix = crypto_map.get(crypto.upper(), f"{cleaned_crypto}usdt")
            
            # 如果已经有usdt后缀，不需要再添加
            if prefix.endswith('usdt'):
                prefix = prefix
        
        # 获取年月并构建文件路径
        year_month = date.strftime('%Y-%m')
        year_month_compact = date.strftime('%Y%m')
        
        # 尝试两种可能的文件名格式
        file_path_standard = os.path.join(self.price_data_dir, f"{prefix}_history_{year_month}.csv")
        file_path_compact = os.path.join(self.price_data_dir, f"{prefix}_{year_month_compact}.csv")
        
        file_path = file_path_standard
        if os.path.exists(file_path_compact) and not os.path.exists(file_path_standard):
            file_path = file_path_compact
            print(f"使用紧凑格式文件名: {file_path}")
        
        # 如果文件不存在，查找最接近的月份
        if not os.path.exists(file_path):
            print(f"警告: 未找到 {file_path}, 尝试查找最接近的月份数据")
            data_files = os.listdir(self.price_data_dir)
            # 同时匹配两种可能的文件名格式
            crypto_files = [f for f in data_files if f.startswith(prefix) and 
                           (("_history_" in f) or ("_20" in f))]
            
            if not crypto_files:
                print(f"错误: 未找到任何 {crypto} 相关的价格数据")
                return None
            
            # 按时间排序找到最接近的文件
            closest_file = None
            min_diff = float('inf')
            
            # 匹配两种可能的日期格式: YYYY-MM 或 YYYYMM
            date_patterns = [
                re.compile(r'(\d{4}-\d{2})\.csv$'),  # 匹配 YYYY-MM.csv
                re.compile(r'_(\d{6})\.csv$')        # 匹配 _YYYYMM.csv
            ]
            
            for file in crypto_files:
                file_date = None
                
                # 尝试用两种模式匹配
                for pattern in date_patterns:
                    match = pattern.search(file)
                    if match:
                        date_str = match.group(1)
                        try:
                            if len(date_str) == 7:  # YYYY-MM 格式
                                file_date = datetime.strptime(date_str, '%Y-%m')
                            elif len(date_str) == 6:  # YYYYMM 格式
                                file_date = datetime.strptime(date_str, '%Y%m')
                            break
                        except ValueError:
                            continue
                
                if file_date:
                    diff = abs((file_date.year - date.year) * 12 + file_date.month - date.month)
                    if diff < min_diff:
                        min_diff = diff
                        closest_file = file
            
            if closest_file:
                file_path = os.path.join(self.price_data_dir, closest_file)
                print(f"使用最接近的文件: {file_path}")
            else:
                return None
        
        # 加载价格数据
        try:
            # 读取CSV文件
            price_df = pd.read_csv(file_path)
            
            # 处理币安格式的CSV文件 (有timestamp和close列)
            if 'timestamp' in price_df.columns and 'close' in price_df.columns:
                print(f"检测到币安格式数据文件，使用'timestamp'作为时间列，'close'作为价格列")
                
                # 检查timestamp列的数据类型
                first_timestamp = price_df['timestamp'].iloc[0]
                print(f"时间戳格式样例: {first_timestamp}, 类型: {type(first_timestamp)}")
                
                # 如果是字符串时间格式 (如 "2024-10-31 16:00:00")
                if isinstance(first_timestamp, str) and not first_timestamp.isdigit():
                    try:
                        # 直接解析日期时间字符串
                        price_df['open_time'] = pd.to_datetime(price_df['timestamp'])
                        print("使用标准datetime解析处理时间戳")
                    except Exception as e:
                        print(f"时间戳转换错误: {e}")
                        return None
                else:
                    # 尝试作为毫秒时间戳处理
                    try:
                        price_df['open_time'] = pd.to_datetime(price_df['timestamp'], unit='ms')
                    except:
                        # 如果转换失败，尝试标准datetime解析
                        try:
                            price_df['open_time'] = pd.to_datetime(price_df['timestamp'])
                        except Exception as e:
                            print(f"时间戳转换错误: {e}")
                            return None
                
                # 其他列保持不变
                if 'close' in price_df.columns:
                    # 确认close列已存在
                    pass
            else:
                # 兼容不同的列名格式
                # 检查是否有open_time列，如果没有尝试其他可能的列名
                time_column = None
                possible_time_columns = ['open_time', 'timestamp', 'time', 'datetime', 'date', 'open_date']
                
                for col in possible_time_columns:
                    if col in price_df.columns:
                        time_column = col
                        break
                
                # 如果找不到时间列，尝试第一列
                if time_column is None and len(price_df.columns) > 0:
                    time_column = price_df.columns[0]
                    print(f"警告: 未找到标准时间列名，尝试使用第一列 '{time_column}' 作为时间列")
                
                if time_column is None:
                    print(f"错误: 无法在CSV文件中找到时间列")
                    return None
                    
                # 确保时间列为datetime格式
                # 根据列名猜测日期格式
                if time_column == 'timestamp':
                    # 可能是毫秒时间戳
                    try:
                        price_df['open_time'] = pd.to_datetime(price_df[time_column], unit='ms')
                    except:
                        # 如果转换失败，尝试标准datetime解析
                        price_df['open_time'] = pd.to_datetime(price_df[time_column])
                else:
                    price_df['open_time'] = pd.to_datetime(price_df[time_column])
                
                # 如果time_column不是'open_time'，确保有'open_time'列用于后续处理
                if time_column != 'open_time':
                    price_df['open_time'] = price_df['open_time']
                
                # 检查是否有close列，如果没有尝试其他可能的列名
                price_column = None
                possible_price_columns = ['close', 'price', 'last', 'last_price', 'Close']
                
                for col in possible_price_columns:
                    if col in price_df.columns:
                        price_column = col
                        break
                
                # 如果找不到价格列，尝试第二列或最后一列
                if price_column is None and len(price_df.columns) > 1:
                    price_column = price_df.columns[1]  # 尝试使用第二列
                    print(f"警告: 未找到标准价格列名，尝试使用 '{price_column}' 作为价格列")
                
                if price_column is None:
                    print(f"错误: 无法在CSV文件中找到价格列")
                    return None
                    
                # 如果price_column不是'close'，确保有'close'列用于后续处理
                if price_column != 'close':
                    price_df['close'] = price_df[price_column]
            
            # 移除时区信息以确保一致性
            if hasattr(price_df['open_time'].iloc[0], 'tz_localize'):
                price_df['open_time'] = price_df['open_time'].dt.tz_localize(None)
            
            # 缓存结果
            self.crypto_price_cache[cache_key] = price_df
            print(f"已加载 {len(price_df)} 条 {crypto} 价格数据 ({price_df['open_time'].min()} 到 {price_df['open_time'].max()})")
            print(f"CSV文件列名: {list(price_df.columns)}")
            return price_df
        except Exception as e:
            print(f"加载价格数据出错: {str(e)}")
            print(f"尝试查看CSV文件前5行:")
            try:
                with open(file_path, 'r') as f:
                    for i, line in enumerate(f):
                        if i < 5:
                            print(f"行 {i+1}: {line.strip()}")
                        else:
                            break
            except Exception as read_error:
                print(f"读取CSV文件失败: {str(read_error)}")
            return None

    def find_price_at_time(self, price_df, timestamp):
        """查找特定时间点的价格"""
        if price_df is None or len(price_df) == 0:
            return None
        
        # 确保没有时区信息
        if hasattr(timestamp, 'tz_localize') and timestamp.tzinfo is not None:
            timestamp = timestamp.replace(tzinfo=None)
        
        # 找到最接近的时间点
        closest_idx = (price_df['open_time'] - timestamp).abs().idxmin()
        return price_df.iloc[closest_idx]['close']

    def identify_trade_groups(self):
        """识别属于同一交易的订单组"""
        orders = self.orders_df.copy()
        
        # 初始化分组ID列
        orders['trade_group_id'] = np.nan
        
        # 按交易币种和时间戳排序
        orders = orders.sort_values(['analysis_交易币种', 'timestamp'])
        
        # 根据交易币种、交易方向以及是否有新入场点位来分组
        current_group_id = 0
        active_groups = {}  # 记录活跃的交易组 {币种_方向: 组信息}
        
        for idx, order in orders.iterrows():
            crypto = order['analysis_交易币种']
            direction = order['analysis_方向']
            
            if pd.isna(crypto) or pd.isna(direction):
                continue  # 跳过没有币种或方向的订单
            
            # 检查这个订单是否有入场点位
            has_entry = not pd.isna(order['analysis_入场点位1']) or not pd.isna(order['analysis_入场点位2']) or not pd.isna(order['analysis_入场点位3'])
            
            # 生成交易标识符：币种_方向
            trade_key = f"{crypto}_{direction}"
            
            # 如果有入场点位，总是创建新的交易组，不管之前是否有活跃的同类交易
            if has_entry:
                current_group_id += 1
                orders.at[idx, 'trade_group_id'] = current_group_id
                
                # 提取入场点位价格
                entry_points = []
                for col in ['analysis_入场点位1', 'analysis_入场点位2', 'analysis_入场点位3']:
                    if not pd.isna(order[col]):
                        try:
                            entry_price = float(order[col])
                            entry_points.append(entry_price)
                        except (ValueError, TypeError):
                            pass
                
                # 更新活跃交易组信息
                active_groups[trade_key] = {
                    'group_id': current_group_id,
                    'last_timestamp': order['timestamp'],
                    'entry_order_idx': idx,
                    'entry_points': entry_points
                }
                self.log(f"创建新交易组 #{current_group_id}: {crypto} {direction} @ {order['timestamp'].strftime('%Y-%m-%d %H:%M')} 入场价格: {entry_points}")
            else:
                # 如果没有入场点位，尝试匹配到现有活跃交易组
                if trade_key in active_groups:
                    # 检查时间是否在配置的时间窗口内
                    time_diff = order['timestamp'] - active_groups[trade_key]['last_timestamp']
                    if time_diff.total_seconds() < self.time_window_hours * 3600:
                        orders.at[idx, 'trade_group_id'] = active_groups[trade_key]['group_id']
                        active_groups[trade_key]['last_timestamp'] = order['timestamp']
                        self.log(f"订单更新 → 交易组 #{active_groups[trade_key]['group_id']}: {crypto} {direction} @ {order['timestamp'].strftime('%Y-%m-%d %H:%M')} (距上次更新: {time_diff.total_seconds()/3600:.1f}小时)")
        
        # 确保有分组ID的订单被处理
        self.grouped_orders = orders.dropna(subset=['trade_group_id'])
        print(f"识别出 {self.grouped_orders['trade_group_id'].nunique()} 个交易组")
        return self.grouped_orders

    def add_price_data(self):
        """为每个订单添加对应时间的价格数据"""
        if not hasattr(self, 'grouped_orders'):
            self.identify_trade_groups()
        
        orders = self.grouped_orders.copy()
        orders['price'] = np.nan
        
        for idx, order in orders.iterrows():
            crypto = order['analysis_交易币种']
            if pd.isna(crypto):
                continue
            
            # 获取价格数据
            price_df = self.get_crypto_price_data(crypto, order['timestamp'])
            if price_df is not None:
                # 找到对应时间的价格
                price = self.find_price_at_time(price_df, order['timestamp'])
                if price is not None:
                    orders.at[idx, 'price'] = price
        
        self.orders_with_price = orders
        print(f"已为 {orders['price'].notna().sum()} 条订单添加价格数据")
        return orders

    def consolidate_trades(self):
        """将同一交易的多条记录合并成一条完整记录"""
        if not hasattr(self, 'orders_with_price'):
            self.add_price_data()
        
        orders = self.orders_with_price.copy()
        
        # 创建空的合并结果DataFrame
        consolidated_columns = ['trade_id', 'crypto', 'direction', 'leverage', 
                              'entry_time', 'entry_price',
                              'exit_time', 'exit_price', 
                              'stop_loss_price', 'take_profit_price',
                              'pnl_percentage', 'outcome', 'source_group_id',
                              'trade_date', 'duration_hours', 
                              'has_explicit_exit', 'exit_type', 'exit_source',
                              'is_incomplete']
        
        consolidated_trades = pd.DataFrame(columns=consolidated_columns)
        
        # 按组ID分组处理
        for group_id, group in orders.groupby('trade_group_id'):
            # 按时间排序
            group = group.sort_values('timestamp')
            
            # 检查组是否有足够信息
            if len(group) == 0:
                continue
            
            # 第一条记录通常是入场信号
            first_order = group.iloc[0]
            crypto = first_order['analysis_交易币种']
            direction = first_order['analysis_方向']
            trade_date = first_order['timestamp'].strftime('%Y-%m-%d')
            
            # 提取入场信息
            entry_time = first_order['timestamp']
            entry_price = None
            for col in ['analysis_入场点位1', 'analysis_入场点位2', 'analysis_入场点位3']:
                if not pd.isna(first_order[col]):
                    try:
                        entry_price = float(first_order[col])
                        break
                    except (ValueError, TypeError):
                        continue
            
            # 如果没有找到有效的入场价格，使用当时的市场价格
            if entry_price is None and not pd.isna(first_order['price']):
                entry_price = first_order['price']
                print(f"交易 {group_id} 没有明确的入场价格，使用当时市场价格: {entry_price}")
            
            # 如果仍找不到入场价格，跳过这个交易组
            if entry_price is None:
                print(f"警告: 交易组 {group_id} 没有有效的入场价格，跳过")
                continue
            
            # 初始化出场信息
            exit_time = None
            exit_price = None
            stop_loss_price = None
            take_profit_price = None
            exit_type = None  # 记录出场类型：止盈/止损
            exit_source = None  # 记录出场来源：明确指定、文本推断、价格推断
            has_explicit_exit = False  # 标记是否有明确的出场指令
            
            # 记录止盈止损点位（如果有）
            # 入场订单中的止盈止损点位
            for col in ['analysis_止盈点位1', 'analysis_止盈点位2', 'analysis_止盈点位3']:
                if not pd.isna(first_order[col]):
                    try:
                        take_profit_price = float(first_order[col])
                    except (ValueError, TypeError):
                        pass
            
            for col in ['analysis_止损点位1', 'analysis_止损点位2', 'analysis_止损点位3']:
                if not pd.isna(first_order[col]):
                    try:
                        stop_loss_price = float(first_order[col])
                    except (ValueError, TypeError):
                        pass
            
            # 策略一：寻找同币种的下一个订单作为出场点
            # 从所有订单中找出入场后的同币种订单
            next_orders = self.orders_df[(self.orders_df['analysis_交易币种'] == crypto) & 
                                        (self.orders_df['timestamp'] > entry_time)]
            
            # 添加方向一致性筛选
            direction_match = False
            if direction in ['多单', '多头', '多', 'LONG', 'long', 'Long']:
                direction_type = '多'
                next_orders = next_orders[next_orders['analysis_方向'].isin(['多单', '多头', '多', 'LONG', 'long', 'Long'])]
                direction_match = True
            elif direction in ['空单', '空头', '空', 'SHORT', 'short', 'Short']:
                direction_type = '空'
                next_orders = next_orders[next_orders['analysis_方向'].isin(['空单', '空头', '空', 'SHORT', 'short', 'Short'])]
                direction_match = True
            
            if not direction_match:
                print(f"警告: 无法识别方向 '{direction}', 不执行方向过滤")
            
            # 时间限制：2天内的订单
            time_limit = entry_time + timedelta(days=2)
            next_orders = next_orders[next_orders['timestamp'] <= time_limit]
            
            # 按时间排序
            next_orders = next_orders.sort_values('timestamp')
            
            if len(next_orders) > 0:  # 只需要至少一个符合条件的订单
                # 使用排序后的第一个符合条件的订单（即入场后的下一个同币种、同方向订单）
                next_order = next_orders.iloc[0]  # 使用第一个符合条件的订单
                exit_time = next_order['timestamp']
                exit_source = '下一个同币种订单'
                
                # 增加日志以便调试
                print(f"币种: {crypto}, 入场时间: {entry_time}, 出场时间: {exit_time}")
                if len(next_orders) > 0:
                    print(f"  选择的订单时间: {next_orders.iloc[0]['timestamp']} (索引0)")
                if len(next_orders) > 1:
                    print(f"  后续订单时间: {next_orders.iloc[1]['timestamp']} (索引1)")
                
                # 尝试获取价格数据
                if 'price' in next_order and not pd.isna(next_order['price']):
                    exit_price = next_order['price']
                else:
                    # 从价格历史中获取
                    price_df = self.get_crypto_price_data(crypto, exit_time)
                    if price_df is not None:
                        exit_price = self.find_price_at_time(price_df, exit_time)
            
            # 策略二：如果还没有出场信息，尝试从组内后续订单寻找明确的止盈止损信息
            if exit_price is None and len(group) > 1:
                for _, order in group.iloc[1:].iterrows():
                    has_explicit_tp = False
                    has_explicit_sl = False
                    
                    # 检查是否有明确的止盈点位
                    for col in ['analysis_止盈点位1', 'analysis_止盈点位2', 'analysis_止盈点位3']:
                        if not pd.isna(order[col]):
                            try:
                                tp_price = float(order[col])
                                take_profit_price = tp_price
                                has_explicit_tp = True
                            except (ValueError, TypeError):
                                continue
                    
                    # 检查是否有明确的止损点位
                    for col in ['analysis_止损点位1', 'analysis_止损点位2', 'analysis_止损点位3']:
                        if not pd.isna(order[col]):
                            try:
                                sl_price = float(order[col])
                                stop_loss_price = sl_price
                                has_explicit_sl = True
                            except (ValueError, TypeError):
                                continue
                    
                    # 如果有明确的止盈或止损，标记为出场
                    if has_explicit_tp or has_explicit_sl:
                        if exit_time is None:  # 只有在策略一没找到时间时才更新
                            exit_time = order['timestamp']
                        if has_explicit_tp:
                            exit_type = '止盈'
                            exit_source = '明确指定'
                        else:
                            exit_type = '止损'
                            exit_source = '明确指定'
                        has_explicit_exit = True
                        break  # 找到明确的出场信号，不再继续查找
                    
                    # 检查分析内容是否提到止盈止损
                    if not pd.isna(order['analysis_分析内容']):
                        content = str(order['analysis_分析内容']).lower()
                        if ('止盈' in content or 'tp' in content or 'take profit' in content) and not pd.isna(order['price']):
                            if exit_time is None:  # 只有在策略一没找到时间时才更新
                                exit_time = order['timestamp']
                            exit_price = order['price']
                            exit_type = '止盈'
                            exit_source = '文本推断'
                            has_explicit_exit = True
                            break
                        elif ('止损' in content or 'sl' in content or 'stop loss' in content) and not pd.isna(order['price']):
                            if exit_time is None:  # 只有在策略一没找到时间时才更新
                                exit_time = order['timestamp']
                            exit_price = order['price']
                            exit_type = '止损'
                            exit_source = '文本推断'
                            has_explicit_exit = True
                            break
            
            # 策略三：如果仍然没有出场价格但有出场时间，从价格历史中获取
            if exit_price is None and exit_time is not None:
                price_df = self.get_crypto_price_data(crypto, exit_time)
                if price_df is not None:
                    exit_price = self.find_price_at_time(price_df, exit_time)
                    
                    # 根据价格和方向判断是止盈还是止损
                    if exit_price is not None:
                        if exit_type is None:  # 只有在之前没确定类型时才更新
                            if direction == 'LONG' or direction == '多':
                                exit_type = '止盈' if exit_price > entry_price else '止损'
                            elif direction == 'SHORT' or direction == '空':
                                exit_type = '止盈' if exit_price < entry_price else '止损'
                            
                            if exit_source is None:
                                exit_source = '价格推断'
                            print(f"交易组 {group_id}: 从价格历史中找到出场价格={exit_price}，类型={exit_type}")
            
            # 如果仍然没有出场信息，标记为未完成交易
            if exit_time is None or exit_price is None:
                exit_time = first_order['timestamp']  # 使用入场时间
                exit_price = entry_price  # 使用入场价格
                exit_type = '未完成'
                exit_source = '无出场数据'
                is_incomplete = True  # 标记为未完成交易
                print(f"警告: 交易组 {group_id} 没有出场信息，标记为未完成交易")
            else:
                is_incomplete = False  # 标记为已完成交易
            
            # 计算交易持续时间（小时）
            duration_hours = (exit_time - entry_time).total_seconds() / 3600 if exit_time and entry_time else None
            
            # 计算收益率
            pnl_percentage = None
            outcome = None
            
            if entry_price is not None and exit_price is not None:
                leverage = 1.0  # 默认杠杆为1
                # 尝试获取杠杆值
                if not pd.isna(first_order['analysis_杠杆']):
                    try:
                        leverage = float(first_order['analysis_杠杆'])
                    except (ValueError, TypeError):
                        print(f"交易 {group_id} 杠杆值无效，使用默认值 1.0")
                
                # 增强方向判断逻辑，扩大匹配范围
                direction_type = None
                if direction in ['LONG', 'long', 'Long', '多', '多单', '多头', '买入', 'BUY', 'buy', 'Buy']:
                    direction_type = '多'
                elif direction in ['SHORT', 'short', 'Short', '空', '空单', '空头', '卖出', 'SELL', 'sell', 'Sell']:
                    direction_type = '空'
                
                if direction_type == '多':
                    pnl_percentage = (exit_price - entry_price) / entry_price * 100 * leverage
                    outcome = "盈利" if exit_price > entry_price else "亏损"
                    print(f"交易 {group_id}: 多单 入场价={entry_price}, 出场价={exit_price}, 结果={outcome}")
                elif direction_type == '空':
                    pnl_percentage = (entry_price - exit_price) / entry_price * 100 * leverage
                    outcome = "盈利" if exit_price < entry_price else "亏损"
                    print(f"交易 {group_id}: 空单 入场价={entry_price}, 出场价={exit_price}, 结果={outcome}")
                else:
                    print(f"交易 {group_id} 方向无效: {direction}")
                    # 尝试从方向字段字符串中推断
                    if isinstance(direction, str):
                        direction_lower = direction.lower()
                        if '多' in direction_lower or 'long' in direction_lower or 'buy' in direction_lower:
                            pnl_percentage = (exit_price - entry_price) / entry_price * 100 * leverage
                            outcome = "盈利" if exit_price > entry_price else "亏损"
                            print(f"从字符串中推断方向为多单: {direction}")
                        elif '空' in direction_lower or 'short' in direction_lower or 'sell' in direction_lower:
                            pnl_percentage = (entry_price - exit_price) / entry_price * 100 * leverage
                            outcome = "盈利" if exit_price < entry_price else "亏损"
                            print(f"从字符串中推断方向为空单: {direction}")
                
                # 如果是未完成交易，特殊处理结果
                if is_incomplete:
                    outcome = "未完成"
                    print(f"交易 {group_id} 标记为未完成交易，不计入盈亏统计")
            
            # 确保如果有价格数据但outcome仍为None，给出明确的日志
            if entry_price is not None and exit_price is not None and outcome is None:
                print(f"警告: 交易 {group_id} 有价格数据但无法确定outcome。入场价={entry_price}, 出场价={exit_price}, 方向={direction}")
            
            # 创建合并后的交易记录
            trade = {
                'trade_id': int(group_id),
                'crypto': crypto,
                'direction': direction,
                'leverage': first_order['analysis_杠杆'],
                'entry_time': entry_time,
                'entry_price': entry_price,
                'exit_time': exit_time,
                'exit_price': exit_price,
                'stop_loss_price': stop_loss_price,
                'take_profit_price': take_profit_price,
                'pnl_percentage': pnl_percentage,
                'outcome': outcome,
                'source_group_id': group_id,
                'trade_date': trade_date,
                'duration_hours': duration_hours,
                'has_explicit_exit': has_explicit_exit,
                'exit_type': exit_type,
                'exit_source': exit_source,
                'is_incomplete': is_incomplete
            }
            
            # 添加到结果中
            consolidated_trades = pd.concat([consolidated_trades, pd.DataFrame([trade])], ignore_index=True)
        
        self.consolidated_trades = consolidated_trades
        print(f"已合并 {len(consolidated_trades)} 条交易记录")
        
        # 输出不同类型出场的统计
        exit_counts = consolidated_trades['exit_source'].value_counts()
        print("\n出场来源统计:")
        for source, count in exit_counts.items():
            print(f"  {source}: {count}条 ({count/len(consolidated_trades)*100:.1f}%)")
        
        return consolidated_trades

    def calculate_performance(self):
        """计算交易策略的整体表现"""
        if not hasattr(self, 'consolidated_trades'):
            self.consolidate_trades()
        
        trades = self.consolidated_trades
        
        # 过滤掉未完成的交易
        completed_trades = trades[trades['outcome'] != '未完成']
        
        # 计算总体表现
        total_trades = len(completed_trades)
        profitable_trades = len(completed_trades[completed_trades['outcome'] == '盈利'])
        win_rate = profitable_trades / total_trades * 100 if total_trades > 0 else 0
        
        # 计算平均盈亏
        avg_profit = completed_trades[completed_trades['outcome'] == '盈利']['pnl_percentage'].mean()
        avg_loss = completed_trades[completed_trades['outcome'] == '亏损']['pnl_percentage'].mean()
        
        # 按币种分组统计，只统计已完成的交易
        by_crypto = completed_trades.groupby('crypto').agg({
            'trade_id': 'count',
            'pnl_percentage': ['mean', 'sum'],
            'outcome': lambda x: (x == '盈利').mean() * 100
        })
        
        by_crypto.columns = ['交易次数', '平均收益率', '总收益率', '胜率']
        
        # 计算未完成交易数量
        incomplete_count = len(trades) - len(completed_trades)
        
        # 输出结果
        performance = {
            'total_trades': total_trades,
            'win_rate': win_rate,
            'avg_profit': avg_profit,
            'avg_loss': avg_loss,
            'by_crypto': by_crypto,
            'incomplete_trades': incomplete_count
        }
        
        # 打印统计信息
        print("\n====== 交易表现统计 ======")
        print(f"总交易次数: {total_trades} (另有 {incomplete_count} 笔未完成交易)")
        print(f"胜率: {win_rate:.2f}%")
        print(f"平均盈利: {avg_profit:.2f}%")
        print(f"平均亏损: {avg_loss:.2f}%")
        print("\n按币种统计:")
        print(by_crypto)
        
        self.performance = performance
        return performance

    def run(self):
        """运行整个流程"""
        self.load_orders()
        self.identify_trade_groups()
        self.add_price_data()
        self.consolidate_trades()
        self.calculate_performance()
        
        # 创建新的原始订单合并DataFrame
        merged_orders = []
        
        # 遍历每个交易组
        for group_id, group_data in self.consolidated_trades.iterrows():
            # 查找入场订单
            entry_orders = self.orders_df[
                (self.orders_df['analysis_交易币种'] == group_data['crypto']) & 
                (self.orders_df['timestamp'] == group_data['entry_time'])
            ]
            
            # 查找出场订单
            exit_orders = self.orders_df[
                (self.orders_df['analysis_交易币种'] == group_data['crypto']) & 
                (self.orders_df['timestamp'] == group_data['exit_time'])
            ]
            
            if len(entry_orders) > 0 and len(exit_orders) > 0:
                entry_order = entry_orders.iloc[0].copy()
                exit_order = exit_orders.iloc[0].copy()
                
                # 合并信息
                merged_order = entry_order.copy()
                
                # 添加出场信息
                merged_order['exit_time'] = group_data['exit_time']
                merged_order['exit_price'] = group_data['exit_price']
                merged_order['outcome'] = group_data['outcome']
                merged_order['pnl_percentage'] = group_data['pnl_percentage']
                merged_order['duration_hours'] = group_data['duration_hours']
                merged_order['trade_group_id'] = group_data['source_group_id']
                
                # 根据交易结果确定止盈止损
                if group_data['outcome'] == '盈利':
                    if pd.isna(merged_order['analysis_止盈点位1']):
                        merged_order['analysis_止盈点位1'] = group_data['exit_price']
                        # 同时更新原始订单数据
                        if len(entry_orders) > 0:
                            self.orders_df.loc[entry_orders.iloc[0].name, 'analysis_止盈点位1'] = group_data['exit_price']
                            print(f"回填止盈到入场订单: ID {entry_orders.iloc[0].name}, 价格 {group_data['exit_price']}")
                        if len(exit_orders) > 0:
                            self.orders_df.loc[exit_orders.iloc[0].name, 'analysis_止盈点位1'] = group_data['exit_price']
                            print(f"回填止盈到出场订单: ID {exit_orders.iloc[0].name}, 价格 {group_data['exit_price']}")
                            
                elif group_data['outcome'] == '亏损':
                    if pd.isna(merged_order['analysis_止损点位1']):
                        merged_order['analysis_止损点位1'] = group_data['exit_price']
                        # 同时更新原始订单数据
                        if len(entry_orders) > 0:
                            self.orders_df.loc[entry_orders.iloc[0].name, 'analysis_止损点位1'] = group_data['exit_price']
                            print(f"回填止损到入场订单: ID {entry_orders.iloc[0].name}, 价格 {group_data['exit_price']}")
                        if len(exit_orders) > 0:
                            self.orders_df.loc[exit_orders.iloc[0].name, 'analysis_止损点位1'] = group_data['exit_price']
                            print(f"回填止损到出场订单: ID {exit_orders.iloc[0].name}, 价格 {group_data['exit_price']}")
                
                # 复制出场订单的重要信息
                # 如果出场订单有额外的分析内容，也合并过来
                if not pd.isna(exit_order['analysis_分析内容']):
                    if pd.isna(merged_order['analysis_分析内容']):
                        merged_order['analysis_分析内容'] = exit_order['analysis_分析内容']
                    else:
                        merged_order['analysis_分析内容'] = str(merged_order['analysis_分析内容']) + " | 出场分析: " + str(exit_order['analysis_分析内容'])
                
                # 添加到合并列表
                merged_orders.append(merged_order)
        
        # 创建合并订单DataFrame
        if merged_orders:
            merged_df = pd.DataFrame(merged_orders)
            # 保存合并后的订单
            merged_file = os.path.join(os.path.dirname(self.order_file), 'merged_original_orders.xlsx')
            merged_df.to_excel(merged_file, index=False)
            print(f"\n已保存合并后的原始订单: {merged_file}")
        else:
            print("\n警告: 没有找到可合并的订单")
        
        # 保存回填后的原始订单
        self.orders_df.to_excel(os.path.join(os.path.dirname(self.order_file), 'updated_orders.xlsx'), index=False)
        print(f"已保存回填后的原始订单: {os.path.join(os.path.dirname(self.order_file), 'updated_orders.xlsx')}")
        
        # 保存合并后的交易记录
        output_file = os.path.join(os.path.dirname(self.order_file), 'consolidated_trades.xlsx')
        
        # 创建Excel写入器
        with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
            # 保存合并后的交易记录 - 按入场时间排序
            consolidated_sorted = self.consolidated_trades.sort_values('entry_time')
            consolidated_sorted.to_excel(writer, sheet_name='合并交易', index=False)
            
            # 保存原始带价格的订单（按时间戳排序）
            # 添加交易组ID（如果有）到原始订单
            all_orders = self.orders_df.copy()
            if 'trade_group_id' in self.grouped_orders.columns:
                # 将分组ID合并回原始数据
                group_ids = self.grouped_orders[['timestamp', 'analysis_交易币种', 'analysis_方向', 'trade_group_id']]
                # 使用左连接保留所有原始记录
                merged = pd.merge(all_orders, group_ids, 
                                  on=['timestamp', 'analysis_交易币种', 'analysis_方向'], 
                                  how='left')
                # 使用合并后的数据框
                all_orders = merged
            
            # 如果有价格数据，合并回原始数据
            if hasattr(self, 'orders_with_price') and 'price' in self.orders_with_price.columns:
                # 提取交易组ID和价格
                price_data = self.orders_with_price[['timestamp', 'analysis_交易币种', 'analysis_方向', 'price']]
                # 使用左连接保留所有原始记录
                merged = pd.merge(all_orders, price_data, 
                                  on=['timestamp', 'analysis_交易币种', 'analysis_方向'], 
                                  how='left')
                all_orders = merged
            
            # 按时间戳排序
            all_orders = all_orders.sort_values('timestamp')
            
            # 保存所有订单数据
            all_orders.to_excel(writer, sheet_name='全部订单', index=False)
            
            # 保存已分组的订单（按交易组和时间戳排序）
            if hasattr(self, 'grouped_orders'):
                grouped = self.grouped_orders.sort_values(['trade_group_id', 'timestamp'])
                grouped.to_excel(writer, sheet_name='已分组订单', index=False)
            
            # 保存带价格的订单（按时间戳排序）
            if hasattr(self, 'orders_with_price'):
                orders_with_price_sorted = self.orders_with_price.sort_values('timestamp')
                orders_with_price_sorted.to_excel(writer, sheet_name='带价格订单', index=False)
            
            # 保存按币种分组的统计信息
            self.performance['by_crypto'].to_excel(writer, sheet_name='统计信息')
            
            # 添加详细的交易组统计
            if hasattr(self, 'consolidated_trades') and len(self.consolidated_trades) > 0:
                # 按交易组计算多种统计指标
                group_stats = self.consolidated_trades.groupby('trade_id').agg({
                    'crypto': 'first',  
                    'direction': 'first',
                    'leverage': 'first',
                    'entry_time': 'first',
                    'exit_time': 'last',
                    'entry_price': 'first', 
                    'exit_price': 'last',
                    'pnl_percentage': 'sum',
                    'outcome': 'first',
                    'duration_hours': 'max',
                    'is_incomplete': 'first',
                    'exit_type': 'first',
                    'exit_source': 'first'
                }).reset_index()
                
                # 添加详细统计
                group_stats['持仓时间(小时)'] = group_stats['duration_hours']
                group_stats['交易结果'] = group_stats['outcome']
                group_stats['收益率%'] = group_stats['pnl_percentage']
                group_stats['交易完成状态'] = group_stats['is_incomplete'].apply(
                    lambda x: '未完成' if x else '已完成')
                group_stats['出场类型'] = group_stats['exit_type']
                group_stats['出场来源'] = group_stats['exit_source']
                
                # 添加注释列，对未完成交易提供额外说明
                group_stats['备注'] = ''
                incomplete_mask = group_stats['is_incomplete'] == True
                group_stats.loc[incomplete_mask, '备注'] = '交易未完成，价格和收益为参考值'
                
                # 按入场时间排序
                group_stats = group_stats.sort_values('entry_time')
                
                # 保存到Excel
                display_columns = ['trade_id', 'crypto', 'direction', 'leverage', 
                                  'entry_time', 'exit_time', 'entry_price', 'exit_price',
                                  '收益率%', '交易结果', '持仓时间(小时)', 
                                  '交易完成状态', '出场类型', '出场来源', '备注']
                group_stats[display_columns].to_excel(writer, sheet_name='交易组统计', index=False)
        
        print(f"\n结果已保存至: {output_file}")
        return self.consolidated_trades, self.performance

def main():
    """主函数，处理命令行参数并运行交易合并流程"""
    # 创建命令行参数解析器
    parser = argparse.ArgumentParser(description='交易订单合并与回测工具')
    
    # 添加参数
    parser.add_argument('--order-file', '-f', type=str, 
                        default='C:/Users/wtadministrator/Desktop/result2.xlsx',
                        help='交易订单文件路径 (默认: C:/Users/wtadministrator/Desktop/result2.xlsx)')
    
    parser.add_argument('--price-dir', '-p', type=str, 
                        default='C:/Users/wtadministrator/Desktop/crypto_data',
                        help='价格数据目录路径 (默认: C:/Users/wtadministrator/Desktop/crypto_data)')
    
    parser.add_argument('--output', '-o', type=str, 
                        default=None,
                        help='输出文件路径 (默认: 与订单文件同目录的consolidated_trades.xlsx)')
    
    parser.add_argument('--min-duration', '-d', type=float, 
                        default=0,
                        help='最小交易持续时间（小时）筛选 (默认: 0)')
    
    parser.add_argument('--time-window', '-t', type=float, 
                        default=24,
                        help='同一交易组的最大时间窗口（小时）(默认: 24)')
    
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='输出详细处理日志')
    
    # 解析命令行参数
    args = parser.parse_args()
    
    # 检查文件路径是否存在
    order_file = Path(args.order_file)
    if not order_file.exists():
        print(f"错误: 订单文件 '{args.order_file}' 不存在")
        sys.exit(1)
    
    price_dir = Path(args.price_dir)
    if not price_dir.exists() or not price_dir.is_dir():
        print(f"错误: 价格数据目录 '{args.price_dir}' 不存在或不是目录")
        sys.exit(1)
    
    # 设置输出文件路径
    if args.output:
        output_file = args.output
    else:
        output_file = str(order_file.parent / 'consolidated_trades.xlsx')
    
    # 创建并运行交易合并器
    processor = TradeConsolidator(
        str(order_file), 
        str(price_dir), 
        time_window_hours=args.time_window,
        verbose=args.verbose
    )
    trades, performance = processor.run()
    
    # 如果设置了最小交易持续时间，筛选结果
    if args.min_duration > 0:
        filtered_trades = trades[trades['duration_hours'] >= args.min_duration]
        if len(filtered_trades) < len(trades):
            print(f"\n已筛选出持续时间 >= {args.min_duration} 小时的交易: {len(filtered_trades)}/{len(trades)}")
            
            # 重新计算筛选后的性能指标
            total_trades = len(filtered_trades)
            profitable_trades = len(filtered_trades[filtered_trades['outcome'] == '盈利'])
            win_rate = profitable_trades / total_trades * 100 if total_trades > 0 else 0
            
            avg_profit = filtered_trades[filtered_trades['outcome'] == '盈利']['pnl_percentage'].mean()
            avg_loss = filtered_trades[filtered_trades['outcome'] == '亏损']['pnl_percentage'].mean()
            
            print(f"筛选后胜率: {win_rate:.2f}%")
            print(f"筛选后平均盈利: {avg_profit:.2f}%")
            print(f"筛选后平均亏损: {avg_loss:.2f}%")
            
            # 保存筛选后的结果
            filtered_output = output_file.replace('.xlsx', f'_min{args.min_duration}h.xlsx')
            with pd.ExcelWriter(filtered_output) as writer:
                filtered_trades.to_excel(writer, sheet_name='合并交易', index=False)
            print(f"筛选后结果已保存至: {filtered_output}")
    
    # 打印总结
    print("\n============ 交易回测总结 ============")
    print(f"分析文件: {order_file.name}")
    print(f"价格数据: {price_dir}")
    print(f"总交易数: {performance['total_trades']}")
    print(f"总体胜率: {performance['win_rate']:.2f}%")
    print(f"平均盈利: {performance['avg_profit']:.2f}%")
    print(f"平均亏损: {performance['avg_loss']:.2f}%")
    print(f"交易时间窗口: {args.time_window}小时")
    print(f"结果保存: {output_file}")
    print("=====================================")

if __name__ == "__main__":
    main() 