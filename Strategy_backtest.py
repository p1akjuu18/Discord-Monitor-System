import pandas as pd
import numpy as np
from datetime import datetime
import os
import glob
import shutil

class StrategyBacktester:
    def __init__(self, strategy_file, market_data_path, column_mappings=None):
        """
        初始化回测器
        
        参数:
        strategy_file: 策略数据文件路径
        market_data_path: 市场数据文件路径或文件夹路径
        column_mappings: 列名映射字典，用于处理不同的列名
        """
        # 检查策略文件是否存在
        if not os.path.exists(strategy_file):
            raise FileNotFoundError(f"策略文件不存在: {strategy_file}")
        
        # 根据文件扩展名选择读取方法
        file_extension = os.path.splitext(strategy_file)[1].lower()
        if file_extension in ['.xlsx', '.xls']:
            self.strategies_df = pd.read_excel(strategy_file)
        else:
            # 尝试用不同编码读取CSV文件
            try:
                self.strategies_df = pd.read_csv(strategy_file)
            except UnicodeDecodeError:
                self.strategies_df = pd.read_csv(strategy_file, encoding='gbk')
        
        # 打印列名，便于调试
        print("原始策略文件列名:", self.strategies_df.columns.tolist())
        
        # 处理多个入场点位、止盈点位和止损点位
        self._process_multiple_price_points()
        
        # 应用列名映射（如果提供）
        if column_mappings:
            self.strategies_df = self.strategies_df.rename(columns=column_mappings)
        
        # 打印处理后的列名
        print("处理后的策略文件列名:", self.strategies_df.columns.tolist())
        
        # 确保必要的列存在
        required_columns = ['发布时间', '交易币种', '入场点位', '止盈点位', '止损点位']
        missing_columns = [col for col in required_columns if col not in self.strategies_df.columns]
        
        if missing_columns:
            raise ValueError(f"策略文件缺少必要的列: {missing_columns}")
        
        # 确保日期格式正确
        try:
            # 首先尝试移除可能的空格
            self.strategies_df['发布时间'] = self.strategies_df['发布时间'].str.strip()
            # 尝试多种时间格式
            self.strategies_df['发布时间'] = pd.to_datetime(
                self.strategies_df['发布时间'],
                format='mixed',
                errors='coerce'  # 将无法解析的时间设为NaT
            )
            # 移除时区信息
            self.strategies_df['发布时间'] = self.strategies_df['发布时间'].dt.tz_localize(None)
            
            # 检查是否有无效的时间
            invalid_times = self.strategies_df['发布时间'].isna()
            if invalid_times.any():
                print(f"警告: 有 {invalid_times.sum()} 条记录的时间格式无法解析")
                print("问题时间记录:")
                print(self.strategies_df[invalid_times][['发布时间']])
        except Exception as e:
            print(f"警告: 时间格式解析出错: {e}")
            print("尝试使用更宽松的时间格式解析...")
            try:
                # 尝试更宽松的解析方式
                self.strategies_df['发布时间'] = pd.to_datetime(
                    self.strategies_df['发布时间'],
                    format='mixed',
                    errors='coerce',
                    dayfirst=True  # 考虑日期可能以日/月/年的格式
                )
                # 移除时区信息
                self.strategies_df['发布时间'] = self.strategies_df['发布时间'].dt.tz_localize(None)
            except Exception as e2:
                print(f"错误: 无法解析时间格式: {e2}")
                print("请检查时间格式是否正确")
                raise
        
        # 保存市场数据路径，而不是直接加载所有数据
        self.market_data_path = market_data_path
    
    def _process_multiple_price_points(self):
        """
        处理多个入场点位、止盈点位和止损点位，选择第一个有效的点位
        """
        # 检查是否有多个点位列
        entry_cols = [col for col in self.strategies_df.columns if '入场点位' in col]
        tp_cols = [col for col in self.strategies_df.columns if '止盈点位' in col]
        sl_cols = [col for col in self.strategies_df.columns if '止损点位' in col]
        
        # 打印找到的列，便于调试
        print(f"找到入场点位列: {entry_cols}")
        print(f"找到止盈点位列: {tp_cols}")
        print(f"找到止损点位列: {sl_cols}")
        
        # 创建新列，选择第一个非空的点位
        if entry_cols:
            self.strategies_df['入场点位'] = self.strategies_df[entry_cols].apply(
                lambda row: next((val for val in row if pd.notna(val)), None), axis=1
            )
        
        if tp_cols:
            self.strategies_df['止盈点位'] = self.strategies_df[tp_cols].apply(
                lambda row: next((val for val in row if pd.notna(val)), None), axis=1
            )
        
        if sl_cols:
            self.strategies_df['止损点位'] = self.strategies_df[sl_cols].apply(
                lambda row: next((val for val in row if pd.notna(val)), None), axis=1
            )
        
        # 重命名时间戳列
        if '时间戳' in self.strategies_df.columns and '发布时间' not in self.strategies_df.columns:
            self.strategies_df = self.strategies_df.rename(columns={'时间戳': '发布时间'})
    
    def _load_market_data_for_symbol(self, symbol, start_time, end_time=None):
        """
        仅加载特定币种和时间范围的市场数据
        
        参数:
        symbol: 币种符号
        start_time: 开始时间
        end_time: 结束时间，默认为None（表示不限制结束时间）
        
        返回:
        DataFrame，包含该币种在指定时间范围内的市场数据
        """
        if end_time is None:
            # 默认加载3天内的数据
            end_time = start_time + pd.Timedelta(days=3)
        
        print(f"为 {symbol} 加载市场数据，时间范围: {start_time} 至 {end_time}")
        
        # 标准化币种名称
        symbol_variations = [
            symbol.lower(),
            symbol.upper(),
            f"{symbol.lower()}usdt",
            f"{symbol.upper()}usdt",
            f"{symbol.lower()}_usdt",
            f"{symbol.upper()}_usdt"
        ]
        
        # 检查是否是目录
        if os.path.isdir(self.market_data_path):
            # 尝试从所有可能的文件名找到匹配的文件
            for symbol_var in symbol_variations:
                # 查找可能的文件
                potential_files = []
                for filename in os.listdir(self.market_data_path):
                    if symbol_var in filename.lower():
                        potential_files.append(os.path.join(self.market_data_path, filename))
                
                if potential_files:
                    print(f"为 {symbol} 找到 {len(potential_files)} 个可能的文件：{[os.path.basename(f) for f in potential_files]}")
                    # 读取所有找到的文件
                    dfs = []
                    for file in potential_files:
                        try:
                            print(f"尝试读取文件: {os.path.basename(file)}")
                            # 尝试只读取几行来推断文件格式
                            df_sample = pd.read_csv(file, nrows=5)
                            
                            # 检查并识别时间戳列
                            timestamp_col = None
                            possible_timestamp_cols = ['timestamp', 'time', 'date', 'datetime', 'open_time', 'close_time']
                            for col in possible_timestamp_cols:
                                if col in df_sample.columns:
                                    timestamp_col = col
                                    break
                            
                            if timestamp_col is None:
                                print(f"警告: 文件 {file} 中未找到时间戳列，跳过此文件")
                                continue
                            
                            # 尝试解析日期范围以过滤数据
                            try:
                                # 读取时使用日期解析器
                                df = pd.read_csv(file, parse_dates=[timestamp_col])
                                
                                # 重命名时间戳列为标准名称
                                if timestamp_col != 'timestamp':
                                    df = df.rename(columns={timestamp_col: 'timestamp'})
                                
                                # 确保时间戳没有时区信息
                                df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_localize(None)
                                
                                # 过滤时间范围
                                df = df[(df['timestamp'] >= start_time) & (df['timestamp'] <= end_time)]
                                
                                if len(df) > 0:
                                    # 添加symbol列（如果不存在）
                                    if 'symbol' not in df.columns:
                                        df['symbol'] = symbol.upper()
                                    
                                    # 确保必要的列存在
                                    required_cols = ['timestamp', 'symbol', 'high', 'low', 'close']
                                    missing_cols = [col for col in required_cols if col not in df.columns]
                                    
                                    if missing_cols:
                                        print(f"警告: 文件 {file} 缺少必要列: {missing_cols}")
                                        # 尝试推断或创建缺失的列
                                        if 'high' in missing_cols and 'low' in missing_cols and 'close' in df.columns:
                                            # 使用close作为high和low
                                            df['high'] = df['close']
                                            df['low'] = df['close']
                                        elif 'close' in missing_cols and 'high' in df.columns and 'low' in df.columns:
                                            # 使用high和low的平均值作为close
                                            df['close'] = (df['high'] + df['low']) / 2
                                    
                                    dfs.append(df)
                                    print(f"从文件 {os.path.basename(file)} 读取了 {len(df)} 条符合时间范围的记录")
                                else:
                                    print(f"文件 {os.path.basename(file)} 中没有符合时间范围的数据")
                            
                            except Exception as e:
                                print(f"处理文件 {file} 时出错: {e}")
                                continue
                        
                        except Exception as e:
                            print(f"读取文件 {file} 时出错: {e}")
                            continue
                    
                    if dfs:
                        market_data = pd.concat(dfs, ignore_index=True)
                        market_data.sort_values(['symbol', 'timestamp'], inplace=True)
                        print(f"为 {symbol} 总共加载了 {len(market_data)} 条市场数据")
                        return market_data
        else:
            # 单个文件的情况
            try:
                df = pd.read_csv(self.market_data_path)
                
                # 过滤币种和时间
                if 'symbol' in df.columns:
                    df = df[df['symbol'].str.contains(symbol, case=False)]
                
                # 识别并处理时间戳列
                timestamp_col = None
                possible_timestamp_cols = ['timestamp', 'time', 'date', 'datetime', 'open_time', 'close_time']
                for col in possible_timestamp_cols:
                    if col in df.columns:
                        timestamp_col = col
                        break
                
                if timestamp_col:
                    # 重命名时间戳列为标准名称
                    if timestamp_col != 'timestamp':
                        df = df.rename(columns={timestamp_col: 'timestamp'})
                    
                    # 确保时间戳没有时区信息
                    df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_localize(None)
                    
                    # 过滤时间范围
                    df = df[(df['timestamp'] >= start_time) & (df['timestamp'] <= end_time)]
                    
                    if len(df) > 0:
                        print(f"从单个文件加载了 {len(df)} 条符合条件的市场数据")
                        return df
            except Exception as e:
                print(f"读取市场数据文件时出错: {e}")
        
        # 如果没有找到匹配的数据，返回空DataFrame
        print(f"警告：未找到 {symbol} 在时间范围内的市场数据")
        return pd.DataFrame(columns=['timestamp', 'symbol', 'high', 'low', 'close'])

    def backtest_strategy(self, strategy_index):
        """
        回测单个策略，支持多个入场点位和移动止损
        
        参数:
        strategy_index: 策略在DataFrame中的索引
        
        返回:
        字典，包含回测结果
        """
        strategy = self.strategies_df.iloc[strategy_index]
        
        # 首先将原始策略的所有列数据复制到结果中
        result = strategy.to_dict()
        
        # 获取关键策略参数
        strategy_time = strategy['发布时间']
        symbol = str(strategy['交易币种']).strip() if pd.notna(strategy['交易币种']) else None
        
        # 获取所有入场点位
        entry_cols = [col for col in strategy.index if '入场点位' in col]
        entry_points = [strategy[col] for col in entry_cols if pd.notna(strategy[col])]
        
        # 获取所有止损点位
        sl_cols = [col for col in strategy.index if '止损点位' in col]
        stop_losses = [strategy[col] for col in sl_cols if pd.notna(strategy[col])]
        
        # 获取所有止盈点位
        tp_cols = [col for col in strategy.index if '止盈点位' in col]
        take_profits = [strategy[col] for col in tp_cols if pd.notna(strategy[col])]
        
        # 如果没有入场点位，返回错误结果
        if not entry_points:
            result['status'] = 'error'
            result['message'] = '没有有效的入场点位'
            result['outcome'] = 'unknown'
            return result
        
        # 如果币种为空，返回错误结果
        if not symbol:
            result['status'] = 'error'
            result['message'] = '交易币种为空'
            result['outcome'] = 'unknown'
            return result
        
        # 获取交易方向
        if '方向' in strategy:
            direction = str(strategy['方向']).strip()
            if direction == '多':
                is_long = True
            elif direction == '空':
                is_long = False
            else:
                is_long = True  # 默认多头
        else:
            is_long = True  # 默认多头
        
        direction_text = "多头" if is_long else "空头"
        result['direction_text'] = direction_text
        
        print(f"正在回测 {symbol} {direction_text}策略，发布时间: {strategy_time}")
        print(f"入场点位数量: {len(entry_points)}")
        print(f"止损点位数量: {len(stop_losses)}")
        
        # 获取对应的市场数据（只加载这一个币种的数据）
        strategy_end_time = strategy_time + pd.Timedelta(days=3)
        symbol_data = self._load_market_data_for_symbol(symbol, strategy_time, strategy_end_time)
        
        if len(symbol_data) == 0:
            result['status'] = 'error'
            result['message'] = '未找到策略发布后的市场数据'
            result['outcome'] = 'unknown'
            return result
        
        # 记录实际匹配到的币种
        matched_symbol = symbol_data['symbol'].iloc[0] if 'symbol' in symbol_data.columns else symbol
        result['matched_symbol'] = matched_symbol
        
        # 如果没有止损点位，设置默认值
        if not stop_losses:
            print(f"警告：策略没有设置止损点位，使用默认计算方法")
            if is_long:
                # 多头策略默认止损：入场价的2%下方
                default_sl = entry_points[0] * 0.98
            else:
                # 空头策略默认止损：入场价的2%上方
                default_sl = entry_points[0] * 1.02
            stop_losses = [default_sl]
            print(f"设置默认止损点: {default_sl}")
        
        # 如果没有止盈点位，设置默认值
        if not take_profits:
            print(f"警告：策略没有设置止盈点位，使用默认计算方法")
            if is_long:
                # 多头策略默认止盈：入场价的3%上方(风险回报比1.5)
                default_tp = entry_points[0] * 1.03
            else:
                # 空头策略默认止盈：入场价的3%下方(风险回报比1.5)
                default_tp = entry_points[0] * 0.97
            take_profits = [default_tp]
            print(f"设置默认止盈点: {default_tp}")
        
        # 初始化结果列表，用于存储每个入场点的结果
        entry_results = []
        total_weighted_profit = 0
        total_weight = 0
        
        # 根据入场点数量分配权重
        weights = []
        if len(entry_points) == 1:
            weights = [1.0]
        elif len(entry_points) == 2:
            weights = [0.5, 0.5]
        elif len(entry_points) == 3:
            weights = [0.3, 0.3, 0.4]
        else:
            # 如果超过3个入场点，平均分配权重
            weights = [1.0 / len(entry_points)] * len(entry_points)
        
        # 记录入场点信息
        entry_points_info = []
        total_entry_cost = 0
        total_entry_weight = 0
        
        # 对每个入场点进行回测
        for i, (entry_price, weight) in enumerate(zip(entry_points, weights)):
            print(f"处理入场点 {i+1}, 价格: {entry_price}")
            
            entry_result = {
                'entry_point': i + 1,
                'entry_price': entry_price,
                'weight': weight,
                'status': 'pending'
            }
            
            # 找到实际入场的时间点
            if is_long:
                entry_hit_data = symbol_data[symbol_data['low'] <= entry_price]
            else:
                entry_hit_data = symbol_data[symbol_data['high'] >= entry_price]
            
            # 检查是否实际入场
            actual_entry = len(entry_hit_data) > 0
            
            # 记录入场点信息
            entry_point_info = {
                'entry_point': i + 1,
                'target_price': entry_price,
                'weight': weight,
                'actual_entry': actual_entry,
                'entry_time': entry_hit_data.iloc[0]['timestamp'] if actual_entry else None,
                'actual_price': entry_hit_data.iloc[0]['close'] if actual_entry else None
            }
            entry_points_info.append(entry_point_info)
            
            if actual_entry:
                total_entry_cost += entry_hit_data.iloc[0]['close'] * weight
                total_entry_weight += weight
                print(f"入场点 {i+1} 成功入场，入场时间: {entry_hit_data.iloc[0]['timestamp']}")
            
            if not actual_entry:
                entry_result['status'] = 'no_entry'
                entry_result['message'] = '价格未达到入场点位'
                entry_results.append(entry_result)
                continue
            
            # 实际入场时间
            entry_time = entry_hit_data.iloc[0]['timestamp']
            entry_result['entry_hit'] = True
            entry_result['actual_entry_time'] = entry_time
            
            # 只考虑入场后的数据
            post_entry_data = symbol_data[symbol_data['timestamp'] >= entry_time].copy()
            
            # 计算入场1小时后的时间点
            one_hour_after_entry = entry_time + pd.Timedelta(hours=1)
            
            # 检查是否触及止损或止盈
            if is_long:
                # 多头情况
                # 检查在入场后的数据中，是否触及止损或止盈
                sl_hit_data = post_entry_data[post_entry_data['timestamp'] >= one_hour_after_entry]
                sl_hit_data = sl_hit_data[sl_hit_data['low'] <= stop_losses[0]]
                
                tp_hit_data = post_entry_data[post_entry_data['high'] >= take_profits[0]]
                
                # 获取最早的触发时间
                sl_time = sl_hit_data.iloc[0]['timestamp'] if len(sl_hit_data) > 0 else None
                tp_time = tp_hit_data.iloc[0]['timestamp'] if len(tp_hit_data) > 0 else None
                
                # 判断哪个先触发
                if sl_time and tp_time:
                    if sl_time < tp_time:
                        # 止损先触发
                        entry_result['outcome'] = 'stop_loss'
                        entry_result['exit_time'] = sl_time
                        entry_result['exit_price'] = stop_losses[0]
                        if is_long:
                            profit_pct = (stop_losses[0] - entry_price) / entry_price * 100
                        else:
                            profit_pct = (entry_price - stop_losses[0]) / entry_price * 100
                    else:
                        # 止盈先触发
                        entry_result['outcome'] = 'take_profit'
                        entry_result['exit_time'] = tp_time
                        entry_result['exit_price'] = take_profits[0]
                        if is_long:
                            profit_pct = (take_profits[0] - entry_price) / entry_price * 100
                        else:
                            profit_pct = (entry_price - take_profits[0]) / entry_price * 100
                elif sl_time:
                    # 只触发止损
                    entry_result['outcome'] = 'stop_loss'
                    entry_result['exit_time'] = sl_time
                    entry_result['exit_price'] = stop_losses[0]
                    if is_long:
                        profit_pct = (stop_losses[0] - entry_price) / entry_price * 100
                    else:
                        profit_pct = (entry_price - stop_losses[0]) / entry_price * 100
                elif tp_time:
                    # 只触发止盈
                    entry_result['outcome'] = 'take_profit'
                    entry_result['exit_time'] = tp_time
                    entry_result['exit_price'] = take_profits[0]
                    if is_long:
                        profit_pct = (take_profits[0] - entry_price) / entry_price * 100
                    else:
                        profit_pct = (entry_price - take_profits[0]) / entry_price * 100
            else:
                # 空头情况
                # 检查在入场后的数据中，是否触及止损或止盈
                sl_hit_data = post_entry_data[post_entry_data['timestamp'] >= one_hour_after_entry]
                sl_hit_data = sl_hit_data[sl_hit_data['high'] >= stop_losses[0]]
                
                tp_hit_data = post_entry_data[post_entry_data['low'] <= take_profits[0]]
                
                # 获取最早的触发时间
                sl_time = sl_hit_data.iloc[0]['timestamp'] if len(sl_hit_data) > 0 else None
                tp_time = tp_hit_data.iloc[0]['timestamp'] if len(tp_hit_data) > 0 else None
                
                # 判断哪个先触发
                if sl_time and tp_time:
                    if sl_time < tp_time:
                        # 止损先触发
                        entry_result['outcome'] = 'stop_loss'
                        entry_result['exit_time'] = sl_time
                        entry_result['exit_price'] = stop_losses[0]
                        profit_pct = (entry_price - stop_losses[0]) / entry_price * 100
                    else:
                        # 止盈先触发
                        entry_result['outcome'] = 'take_profit'
                        entry_result['exit_time'] = tp_time
                        entry_result['exit_price'] = take_profits[0]
                        profit_pct = (entry_price - take_profits[0]) / entry_price * 100
                elif sl_time:
                    # 只触发止损
                    entry_result['outcome'] = 'stop_loss'
                    entry_result['exit_time'] = sl_time
                    entry_result['exit_price'] = stop_losses[0]
                    profit_pct = (entry_price - stop_losses[0]) / entry_price * 100
                elif tp_time:
                    # 只触发止盈
                    entry_result['outcome'] = 'take_profit'
                    entry_result['exit_time'] = tp_time
                    entry_result['exit_price'] = take_profits[0]
                    profit_pct = (entry_price - take_profits[0]) / entry_price * 100
            
            entry_result['profit_pct'] = profit_pct
            entry_result['weighted_profit'] = profit_pct * weight
            total_weighted_profit += entry_result['weighted_profit']
            total_weight += weight
            
            # 计算持仓时间
            try:
                if entry_result.get('outcome') == 'take_profit' or entry_result.get('outcome') == 'stop_loss':
                    # 已平仓的策略，使用exit_time
                    entry_result['holding_period_minutes'] = (entry_result['exit_time'] - entry_time).total_seconds() / 60
                    print(f"计算持有时间(已平仓): 入场点{entry_result.get('entry_point')}，退出时间 {entry_result['exit_time']}，入场时间 {entry_time}，持有分钟: {entry_result['holding_period_minutes']}")
                else:
                    # 未平仓的策略，使用last_time
                    entry_result['holding_period_minutes'] = (entry_result['exit_time'] - entry_time).total_seconds() / 60
                    print(f"计算持有时间(未平仓): 入场点{entry_result.get('entry_point')}，最后时间 {entry_result['exit_time']}，入场时间 {entry_time}，持有分钟: {entry_result['holding_period_minutes']}")
            except Exception as e:
                print(f"计算持有时间出错: {e}")
                entry_result['holding_period_minutes'] = 0  # 设置默认值
            
            # 在确定单个入场点结果的地方添加保本判断逻辑
            if is_long and abs(entry_result.get('exit_price', 0) - entry_price) / entry_price < 0.001:
                entry_result['outcome'] = 'stopped_be'
            elif not is_long and abs(entry_price - entry_result.get('exit_price', 0)) / entry_price < 0.001:
                entry_result['outcome'] = 'stopped_be'
            
            # 在添加到entry_results前，检查并确保有退出信息
            if entry_result.get('entry_hit', False) and 'outcome' not in entry_result:
                # 如果入场了但没有设置outcome，则设为open
                entry_result['outcome'] = 'open'
                entry_result['last_time'] = post_entry_data.iloc[-1]['timestamp']
                entry_result['last_price'] = post_entry_data.iloc[-1]['close']
                print(f"警告: 入场点{entry_result.get('entry_point')}没有明确的退出结果，设为open状态")
            
            # 根据状态打印不同的时间信息
            if entry_result.get('outcome') in ['take_profit', 'stop_loss', 'stopped_be']:
                print(f"入场点 {i+1} 设置退出时间: {entry_result.get('exit_time')}, 原因: {entry_result['outcome']}")
            else:
                print(f"入场点 {i+1} 最后更新时间: {entry_result.get('exit_time')}, 状态: {entry_result['outcome']}")
            
            entry_results.append(entry_result)
        
        # 合并所有入场点的结果
        result['entry_results'] = entry_results
        result['status'] = 'success'
        
        # 计算总体结果
        if total_weight > 0:
            result['total_weighted_profit_pct'] = total_weighted_profit / total_weight
        else:
            result['total_weighted_profit_pct'] = 0
        
        # 计算综合入场成本
        if total_entry_weight > 0:
            result['average_entry_cost'] = total_entry_cost / total_entry_weight
        else:
            result['average_entry_cost'] = None
        
        # 添加入场点信息
        result['entry_points_info'] = entry_points_info
        
        # 计算总收益明细
        total_profit_details = {
            'total_profit_pct': 0,
            'tp_profit_pct': 0,
            'sl_profit_pct': 0,
            'open_profit_pct': 0,
            'tp_weight': 0,
            'sl_weight': 0,
            'open_weight': 0,
            'risk_reward_ratio': 0  # 添加盈亏比
        }
        
        # 计算每个入场点的盈亏比
        for entry in entry_results:
            if entry.get('outcome') == 'take_profit':
                total_profit_details['tp_profit_pct'] += entry.get('profit_pct', 0)
                total_profit_details['tp_weight'] += entry.get('weight', 0)
            elif entry.get('outcome') == 'stop_loss':
                total_profit_details['sl_profit_pct'] += entry.get('profit_pct', 0) * entry.get('weight', 0)
                total_profit_details['sl_weight'] += entry.get('weight', 0)
            elif entry.get('outcome') == 'open':
                total_profit_details['open_profit_pct'] += entry.get('profit_pct', 0) * entry.get('weight', 0)
                total_profit_details['open_weight'] += entry.get('weight', 0)
            elif entry.get('outcome') == 'stopped_be':
                # 保本出场的盈亏计入止损类别或创建新类别
                total_profit_details['sl_profit_pct'] += entry.get('profit_pct', 0) * entry.get('weight', 0)
                total_profit_details['sl_weight'] += entry.get('weight', 0)
            
            # 计算该入场点的盈亏比
            entry_price = entry.get('entry_price', 0)
            if entry_price > 0:
                # 获取该入场点对应的止损价
                entry_index = entry.get('entry_point', 1) - 1
                if len(stop_losses) > 0:
                    sl_price = stop_losses[entry_index] if entry_index < len(stop_losses) else stop_losses[-1]
                else:
                    sl_price = entry_price  # 如果没有止损点，使用入场价作为止损价
                
                # 获取该入场点对应的止盈价
                if len(take_profits) > 0:
                    tp_price = take_profits[0]  # 使用第一个止盈点计算盈亏比
                else:
                    tp_price = entry_price * 1.01  # 如果没有止盈点，使用1%作为止盈价
                
                if is_long:
                    # 多头：止盈距离/止损距离
                    tp_distance = abs(tp_price - entry_price)
                    sl_distance = abs(sl_price - entry_price)
                    if sl_distance > 0:
                        entry['risk_reward_ratio'] = tp_distance / sl_distance
                else:
                    # 空头：止盈距离/止损距离
                    tp_distance = abs(entry_price - tp_price)
                    sl_distance = abs(entry_price - sl_price)
                    if sl_distance > 0:
                        entry['risk_reward_ratio'] = tp_distance / sl_distance
        
        # 计算总收益
        total_profit_details['total_profit_pct'] = (
            total_profit_details['tp_profit_pct'] +
            total_profit_details['sl_profit_pct'] +
            total_profit_details['open_profit_pct']
        )
        
        # 计算总体盈亏比（加权平均）
        total_rr = 0
        total_rr_weight = 0
        
        # 获取三个入场点的盈亏比
        entry_rrs = []
        for entry in entry_results:
            if 'risk_reward_ratio' in entry:
                entry_rrs.append(entry['risk_reward_ratio'])
        
        # 如果有三个入场点，计算三马
        if len(entry_rrs) == 3:
            # 三马计算：第一个入场点权重0.3，第二个入场点权重0.3，第三个入场点权重0.4
            total_profit_details['risk_reward_ratio'] = (
                entry_rrs[0] * 0.3 +
                entry_rrs[1] * 0.3 +
                entry_rrs[2] * 0.4
            )
        else:
            # 如果不是三个入场点，使用加权平均
            for entry in entry_results:
                if 'risk_reward_ratio' in entry:
                    total_rr += entry['risk_reward_ratio'] * entry.get('weight', 0)
                    total_rr_weight += entry.get('weight', 0)
            
            if total_rr_weight > 0:
                total_profit_details['risk_reward_ratio'] = total_rr / total_rr_weight
            else:
                total_profit_details['risk_reward_ratio'] = 0
        
        result['total_profit_details'] = total_profit_details
        
        # 确定最终结果
        if all(entry['status'] == 'no_entry' for entry in entry_results):
            result['outcome'] = 'no_entry'
        elif all(entry.get('outcome') == 'take_profit' for entry in entry_results if entry.get('outcome')):
            result['outcome'] = 'take_profit'
        elif all(entry.get('outcome') == 'stop_loss' for entry in entry_results if entry.get('outcome')):
            result['outcome'] = 'stop_loss'
        elif all(entry.get('outcome') == 'stopped_be' for entry in entry_results if entry.get('outcome')):
            result['outcome'] = 'stopped_be'  # 保本出场
        else:
            result['outcome'] = 'mixed'
        
        # 计算平均持仓时间
        holding_times = [entry.get('holding_period_minutes', 0) for entry in entry_results if entry.get('holding_period_minutes')]
        print(f"持有时间列表: {holding_times}")
        if holding_times:
            result['avg_holding_period_minutes'] = sum(holding_times) / len(holding_times)
            print(f"计算得到平均持有时间: {result['avg_holding_period_minutes']}")
        else:
            print(f"警告: 无法计算平均持有时间，没有有效的持仓时间数据")
        
        # 在计算平均持仓时间前添加
        for entry in entry_results:
            if entry.get('entry_hit', False) and 'holding_period_minutes' not in entry:
                # 已入场但没有持有时间
                if 'last_time' in entry:
                    entry['holding_period_minutes'] = (entry['last_time'] - entry.get('actual_entry_time')).total_seconds() / 60
                    print(f"为入场点{entry.get('entry_point')}补充计算持有时间: {entry['holding_period_minutes']}分钟")
                elif 'exit_time' in entry:
                    entry['holding_period_minutes'] = (entry['exit_time'] - entry.get('actual_entry_time')).total_seconds() / 60
                    print(f"为入场点{entry.get('entry_point')}补充计算持有时间: {entry['holding_period_minutes']}分钟")
                else:
                    print(f"警告: 入场点{entry.get('entry_point')}既没有exit_time也没有last_time")
        
        # 立即保存当前策略的结果
        self.save_single_strategy_result(result)
        
        return result
    
    def save_single_strategy_result(self, result):
        """保存单个策略的回测结果"""
        try:
            # 创建回测结果文件夹
            desktop_path = os.path.expanduser("~/Desktop")
            results_folder = os.path.join(desktop_path, "回测结果")
            os.makedirs(results_folder, exist_ok=True)
            
            # 保存完整结果（包括嵌套结构）到单个Excel文件
            # 这将被desktop_data_processor.py处理
            complete_result = {
                # 保留原始数据的所有列
                **{col: result[col] for col in self.strategies_df.columns if col in result},
                '方向': result.get('direction_text', '未知'),
                '平均入场成本': result.get('average_entry_cost', 0),
                '总加权盈亏百分比': result.get('total_weighted_profit_pct', 0),
                '平均持仓时间(分钟)': result.get('avg_holding_period_minutes', 0),
                '最终结果': result.get('outcome', 'unknown'),
                '处理状态': result.get('status', 'unknown'),
                '错误信息': result.get('message', ''),
                # 保留原始嵌套结构
                'entry_results': result.get('entry_results', []),
                'entry_points_info': result.get('entry_points_info', []),
                'total_profit_details': result.get('total_profit_details', {})
            }
            
            # 将完整结果保存为DataFrame
            complete_df = pd.DataFrame([complete_result])
            
            # 根据处理状态分别保存结果
            if result.get('status') == 'success':
                # 成功处理的结果保存到回测结果.xlsx
                result_file = os.path.join(desktop_path, "回测结果.xlsx")
            else:
                # 未成功处理的结果保存到未处理成功.xlsx
                result_file = os.path.join(desktop_path, "未处理成功.xlsx")
            
            # 如果文件存在，追加数据
            if os.path.exists(result_file):
                try:
                    existing_results = pd.read_excel(result_file)
                    complete_df = pd.concat([existing_results, complete_df], ignore_index=True)
                except Exception as e:
                    print(f"读取现有结果时出错: {e}")
            
            # 保存完整结果
            try:
                complete_df.to_excel(result_file, index=False)
                print(f"结果已保存至: {result_file}")
            except Exception as e:
                print(f"保存结果时出错: {e}")
            
        except Exception as e:
            print(f"保存策略结果时出错: {str(e)}")
            import traceback
            traceback.print_exc()  # 打印完整的错误堆栈
    
    def backtest_all_strategies(self):
        """
        回测所有策略并返回结果，每处理一条就保存一次
        
        返回:
        DataFrame，包含所有策略的回测结果
        """
        results = []
        results_file = os.path.join(os.path.join(os.path.expanduser("~"), "Desktop"), "回测结果", "回测结果.xlsx")
        
        # 如果文件已存在，读取现有数据
        try:
            if os.path.exists(results_file):
                existing_results = pd.read_excel(results_file, engine='openpyxl')
                print(f"读取现有回测结果，包含 {len(existing_results)} 条记录")
                results = existing_results.to_dict('records')
        except Exception as e:
            print(f"读取现有回测结果文件时出错: {e}")
            print("将创建新的回测结果文件")
            results = []
        
        for i in range(len(self.strategies_df)):
            try:
                # 回测单个策略
                result = self.backtest_strategy(i)
                
                # 移除detailed_data字段，因为它包含DataFrame对象，无法直接保存到Excel
                if 'detailed_data' in result:
                    del result['detailed_data']
                
                results.append(result)
                
                # 立即保存当前结果
                results_df = pd.DataFrame(results)
                # 移除时区信息
                datetime_columns = results_df.select_dtypes(include=['datetime64[ns, UTC]']).columns
                for col in datetime_columns:
                    results_df[col] = results_df[col].dt.tz_localize(None)
                
                # 保存到Excel
                results_df.to_excel(results_file, index=False, engine='openpyxl')
                print(f"已保存第 {i+1} 条策略的回测结果，当前共 {len(results)} 条记录")
            except Exception as e:
                print(f"处理第 {i+1} 条策略时出错: {e}")
                continue
        
        return pd.DataFrame(results)
    
    def generate_summary(self, results_df):
        """
        生成回测结果摘要
        
        参数:
        results_df: 回测结果DataFrame
        
        返回:
        字典，包含回测摘要
        """
        # 检查是否有outcome列
        if 'outcome' not in results_df.columns:
            return {
                'total_strategies': len(results_df),
                'error_strategies': len(results_df),
                'error_message': '所有策略未找到市场数据'
            }
        
        # 过滤出有效的交易（非错误状态）
        valid_trades = results_df[results_df['status'] != 'error'] if 'status' in results_df.columns else results_df
        
        if len(valid_trades) == 0:
            return {
                'total_strategies': len(results_df),
                'error_strategies': len(results_df),
                'error_message': '所有策略未找到市场数据'
            }
        
        # 过滤出已关闭的交易
        closed_trades = valid_trades[valid_trades['outcome'].isin(['take_profit', 'stop_loss'])]
        
        if len(closed_trades) == 0:
            return {
                'total_strategies': len(results_df),
                'valid_strategies': len(valid_trades),
                'no_closed_trades': True,
                'message': '没有已关闭的交易'
            }
        
        winning_trades = closed_trades[closed_trades['outcome'] == 'take_profit']
        
        summary = {
            'total_strategies': len(results_df),
            'valid_strategies': len(valid_trades),
            'closed_trades': len(closed_trades),
            'win_rate': len(winning_trades) / len(closed_trades) if len(closed_trades) > 0 else 0,
            'avg_profit_pct': closed_trades['profit_pct'].mean() if 'profit_pct' in closed_trades.columns else 0,
            'winning_trades_avg_pct': winning_trades['profit_pct'].mean() if len(winning_trades) > 0 and 'profit_pct' in winning_trades.columns else 0,
            'losing_trades_avg_pct': closed_trades[closed_trades['outcome'] == 'stop_loss']['profit_pct'].mean() if len(closed_trades[closed_trades['outcome'] == 'stop_loss']) > 0 and 'profit_pct' in closed_trades.columns else 0,
            'avg_holding_period_minutes': closed_trades['holding_period_minutes'].mean() if 'holding_period_minutes' in closed_trades.columns else 0
        }
        
        return summary

# 使用示例
def main():
    try:
        # 直接读取桌面的merged.xlsx文件
        desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
        strategy_file = os.path.join(desktop_path, "merged.xlsx")
        
        # 如果找不到文件，让用户手动输入
        if not os.path.exists(strategy_file):
            print(f"在桌面上找不到merged.xlsx文件")
            strategy_file = input("请输入策略文件完整路径: ")
        else:
            print(f"找到策略文件: {strategy_file}")
        
        # 设置默认的市场数据路径为crypto_data文件夹
        default_market_data_path = os.path.join(desktop_path, "crypto_data")
        if os.path.exists(default_market_data_path):
            print(f"找到默认市场数据文件夹: {default_market_data_path}")
            market_data_path = default_market_data_path
        else:
            market_data_path = input("请输入市场数据文件或文件夹路径: ")
        
        # 可能的列名映射，根据实际表格调整
        column_mappings = {
            'timestamp': '发布时间',
            'analysis_交易币种': '交易币种'
            # 不需要映射其他列名，因为已经在_process_multiple_price_points方法中处理
        }
        
        # 创建回测器实例
        backtest = StrategyBacktester(strategy_file, market_data_path, column_mappings)
        
        # 创建结果文件夹
        results_folder = os.path.join(desktop_path, "回测结果")
        if not os.path.exists(results_folder):
            os.makedirs(results_folder)
            
        # 生成文件名，包含日期时间
        current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # 保存处理后的策略文件
        processed_strategy_file = os.path.join(results_folder, f"处理后的策略_{current_time}.xlsx")
        # 移除时区信息
        strategies_df_to_save = backtest.strategies_df.copy()
        if '发布时间' in strategies_df_to_save.columns:
            strategies_df_to_save['发布时间'] = strategies_df_to_save['发布时间'].dt.tz_localize(None)
        strategies_df_to_save.to_excel(processed_strategy_file, index=False, engine='openpyxl')
        print(f"处理后的策略文件已保存到: {processed_strategy_file}")
        
        # 复制原始策略文件
        original_file_extension = os.path.splitext(strategy_file)[1]
        original_strategy_copy = os.path.join(results_folder, f"原始策略_{current_time}{original_file_extension}")
        shutil.copy2(strategy_file, original_strategy_copy)
        print(f"原始策略文件已复制到: {original_strategy_copy}")
        
        # 回测所有策略（每处理一条就保存一次）
        results = backtest.backtest_all_strategies()
        
        # 生成摘要
        summary = backtest.generate_summary(results)
        print("\n回测摘要:")
        for key, value in summary.items():
            print(f"{key}: {value}")
            
        # 保存摘要到文件
        summary_df = pd.DataFrame([summary])
        summary_file = os.path.join(results_folder, f"回测摘要_{current_time}.xlsx")
        summary_df.to_excel(summary_file, index=False, engine='openpyxl')
        print(f"回测摘要已保存到: {summary_file}")
        
    except FileNotFoundError as e:
        print(f"错误: {e}")
        print("请确保提供了正确的文件路径。")
    except ValueError as e:
        print(f"错误: {e}")
        print("请检查文件格式和列名是否正确。")
    except Exception as e:
        print(f"发生错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main() 