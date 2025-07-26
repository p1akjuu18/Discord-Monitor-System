import json
import threading
import os
from datetime import datetime
from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO
from Binance_price_monitor import BinanceRestPriceMonitor
from trade_analyzer import analyze_trade_history
import pandas as pd

app = Flask(__name__)
socketio = SocketIO(app)

# 创建价格监控器
monitor = BinanceRestPriceMonitor(polling_interval=3)
symbols_to_monitor = ["btcusdt", "ethusdt", "bnbusdt"]
price_thread = None
csv_file_path = "price_history.csv"  # 默认CSV文件路径

# 接收和发送价格数据的函数
def background_monitoring():
    """后台监控价格并通过WebSocket发送到客户端"""
    try:
        while monitor.keep_running:
            for symbol in symbols_to_monitor:
                symbol_upper = symbol.upper()
                price_data = monitor.get_price(symbol)
                if price_data:
                    # 保存到监控器内部存储
                    monitor.prices[symbol_upper] = price_data
                    # 通过WebSocket发送数据
                    socketio.emit('price_update', {
                        'symbol': symbol_upper,
                        'bid': price_data['bid'],
                        'ask': price_data['ask'],
                        'mid': price_data['mid']
                    })
            socketio.sleep(monitor.polling_interval)
    except Exception as e:
        print(f"监控线程错误: {e}")
    finally:
        monitor.keep_running = False

def get_csv_file_info():
    """获取CSV文件信息"""
    try:
        if os.path.exists(csv_file_path):
            file_stats = os.stat(csv_file_path)
            file_size = file_stats.st_size
            last_modified = datetime.fromtimestamp(file_stats.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
            
            # 文件大小格式化
            if file_size < 1024:
                size_str = f"{file_size} B"
            elif file_size < 1024 * 1024:
                size_str = f"{file_size/1024:.2f} KB"
            else:
                size_str = f"{file_size/(1024*1024):.2f} MB"
            
            # 尝试读取CSV文件的订单数量
            active_orders = []
            if hasattr(monitor, 'history_df') and not monitor.history_df.empty:
                active_orders = monitor.history_df['symbol'].unique().tolist()
            
            return {
                'filename': csv_file_path,
                'file_size': size_str,
                'last_modified': last_modified,
                'last_checked': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'active_orders': active_orders
            }
        else:
            return {
                'filename': csv_file_path,
                'file_size': '0 B',
                'last_modified': '文件不存在',
                'last_checked': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'message': '文件不存在，将在首次监控时创建'
            }
    except Exception as e:
        print(f"获取CSV文件信息出错: {e}")
        return {
            'filename': csv_file_path,
            'message': f'获取文件信息出错: {str(e)}'
        }

@app.route('/')
def index():
    """渲染主页"""
    return render_template('index.html', symbols=symbols_to_monitor)

@socketio.on('connect')
def handle_connect():
    """处理WebSocket连接"""
    print('客户端已连接')
    # 发送当前价格数据
    for symbol in symbols_to_monitor:
        symbol_upper = symbol.upper()
        if symbol_upper in monitor.prices:
            price = monitor.prices[symbol_upper]
            socketio.emit('price_update', {
                'symbol': symbol_upper,
                'bid': price['bid'],
                'ask': price['ask'],
                'mid': price['mid']
            })

@socketio.on('start_monitoring')
def handle_start_monitoring():
    """开始价格监控"""
    global price_thread
    if not monitor.keep_running:
        monitor.keep_running = True
        # 使用带历史记录的监控
        monitor.start_monitoring_with_history(symbols_to_monitor, csv_file_path)
        price_thread = socketio.start_background_task(background_monitoring)
        # 发送文件状态更新
        socketio.emit('csv_status', get_csv_file_info())
        return {'status': 'started'}
    return {'status': 'already_running'}

@socketio.on('stop_monitoring')
def handle_stop_monitoring():
    """停止价格监控"""
    monitor.keep_running = False
    # 发送文件状态更新
    socketio.emit('csv_status', get_csv_file_info())
    return {'status': 'stopped'}

@socketio.on('get_csv_status')
def handle_get_csv_status():
    """获取CSV文件状态"""
    csv_info = get_csv_file_info()
    socketio.emit('csv_status', csv_info)
    return csv_info

@socketio.on('refresh_csv')
def handle_refresh_csv():
    """刷新CSV文件"""
    try:
        if hasattr(monitor, 'history_df'):
            if os.path.exists(csv_file_path):
                # 备份现有文件
                backup_file = f"{csv_file_path}.bak"
                try:
                    os.rename(csv_file_path, backup_file)
                except Exception as e:
                    print(f"备份CSV文件失败: {e}")
                    socketio.emit('csv_status', {
                        'message': f'备份文件失败: {str(e)}'
                    })
                    return {'status': 'error', 'message': str(e)}
            
            # 保存历史数据到CSV
            monitor.history_df.to_csv(csv_file_path, index=False)
            
            # 获取并发送更新后的状态
            csv_info = get_csv_file_info()
            csv_info['message'] = f'CSV文件已刷新，包含 {len(monitor.history_df)} 条记录'
            socketio.emit('csv_status', csv_info)
            socketio.emit('orders_update', {
                'active_orders': csv_info.get('active_orders', []),
                'message': '订单数据已更新'
            })
            
            return {'status': 'success', 'message': '文件已刷新'}
        else:
            socketio.emit('csv_status', {
                'message': '没有历史数据可保存，请先开始监控'
            })
            return {'status': 'error', 'message': '没有历史数据'}
    except Exception as e:
        error_message = f'刷新CSV文件时出错: {str(e)}'
        print(error_message)
        socketio.emit('csv_status', {'message': error_message})
        return {'status': 'error', 'message': str(e)}

@app.route('/generate_trade_analysis', methods=['POST'])
def generate_trade_analysis():
    try:
        # 获取桌面路径
        desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
        history_file = os.path.join(desktop_path, "history.csv")
        
        if not os.path.exists(history_file):
            return jsonify({
                'success': False,
                'message': '未找到交易历史文件'
            })
        
        # 分析交易历史
        analyze_trade_history(history_file)
        
        # 读取生成的Excel报告
        excel_file = os.path.join(desktop_path, "交易分析报告.xlsx")
        if not os.path.exists(excel_file):
            return jsonify({
                'success': False,
                'message': '分析报告生成失败'
            })
        
        # 读取Excel中的统计数据
        stats = []
        with pd.ExcelFile(excel_file) as xls:
            # 读取交易统计sheet
            if '交易统计' in xls.sheet_names:
                df = pd.read_excel(xls, '交易统计')
                for _, row in df.iterrows():
                    if row['交易对'] != '总体统计':
                        stats.append({
                            'symbol': row['交易对'],
                            'total_trades': int(row['总交易次数']),
                            'win_rate': float(row['胜率'].strip('%')),
                            'total_profit': float(row['总盈亏']),
                            'avg_return': float(row['平均收益率'].strip('%'))
                        })
        
        # 读取图表数据
        charts_dir = os.path.join(desktop_path, "交易分析图表")
        charts = {
            'daily_return': None,
            'cumulative_return': None
        }
        
        # 读取每日收益率数据
        daily_stats_file = os.path.join(charts_dir, "币种每日收益率分析.xlsx")
        if os.path.exists(daily_stats_file):
            with pd.ExcelFile(daily_stats_file) as xls:
                for sheet_name in xls.sheet_names:
                    if sheet_name.endswith('_每日统计'):
                        df = pd.read_excel(xls, sheet_name)
                        if '日收益率' in df.columns:
                            charts['daily_return'] = {
                                'labels': df.index.tolist(),
                                'datasets': [{
                                    'label': '日收益率',
                                    'data': df['日收益率'].tolist(),
                                    'backgroundColor': ['#2ecc71' if x >= 0 else '#e74c3c' for x in df['日收益率']]
                                }]
                            }
                        if '累计收益率' in df.columns:
                            charts['cumulative_return'] = {
                                'labels': df.index.tolist(),
                                'datasets': [{
                                    'label': '累计收益率',
                                    'data': df['累计收益率'].tolist(),
                                    'borderColor': '#3498db',
                                    'fill': False
                                }]
                            }
                        break
        
        return jsonify({
            'success': True,
            'stats': stats,
            'charts': charts
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        })

@app.route('/api/price_history_latest', methods=['GET'])
def get_price_history_latest():
    """获取price_history.csv中的最新价格数据"""
    try:
        # 检查CSV文件是否存在
        csv_path = os.path.join(os.getcwd(), 'data', 'price_history.csv')
        app.logger.info(f"尝试读取价格文件: {csv_path}")
        
        # 如果data目录下找不到，尝试直接读取根目录下的文件
        if not os.path.exists(csv_path):
            csv_path = os.path.join(os.getcwd(), 'price_history.csv')
            app.logger.info(f"尝试读取根目录价格文件: {csv_path}")
        
        if not os.path.exists(csv_path):
            app.logger.error("price_history.csv文件不存在")
            return jsonify({
                'status': 'error',
                'message': 'price_history.csv文件不存在',
                'checked_paths': [
                    os.path.join(os.getcwd(), 'data', 'price_history.csv'),
                    os.path.join(os.getcwd(), 'price_history.csv')
                ]
            })
        
        # 读取CSV文件
        prices = {}
        try:
            app.logger.info(f"开始读取CSV文件: {csv_path}")
            
            # 先尝试获取文件总行数
            with open(csv_path, 'r') as f:
                line_count = sum(1 for _ in f)
            app.logger.info(f"CSV文件总行数: {line_count}")
            
            # 如果文件很大，只读取最后1000行
            if line_count > 1000:
                # 使用pandas的skiprows参数跳过前面的行
                skip_rows = list(range(1, line_count - 1000))
                df = pd.read_csv(csv_path, skiprows=skip_rows)
                app.logger.info(f"文件较大，只读取最后1000行，实际读取: {len(df)}行")
            else:
                df = pd.read_csv(csv_path)
                app.logger.info(f"读取整个CSV文件，共{len(df)}行")
            
            app.logger.info(f"CSV文件读取成功，列名: {df.columns.tolist()}")
            app.logger.info(f"CSV文件最后5行数据: {df.tail().to_dict()}")
            
            # 确保必要的列存在
            required_columns = ['symbol', 'bid']
            missing_columns = [col for col in required_columns if col not in df.columns]
            if missing_columns:
                app.logger.error(f"CSV文件缺少必要列: {missing_columns}")
                return jsonify({
                    'status': 'error',
                    'message': f'CSV文件缺少必要列: {missing_columns}，必须包含symbol和bid列',
                    'available_columns': df.columns.tolist()
                })
            
            # 获取每个交易对的最新价格 - 假设最后几行是最新数据
            # 按symbol分组并获取每组的最后一行
            latest_data = df.groupby('symbol').last().reset_index()
            app.logger.info(f"按symbol分组后的最新数据: {latest_data.to_dict()}")
            
            # 获取每个交易对的最新价格
            unique_symbols = latest_data['symbol'].unique()
            app.logger.info(f"找到的交易对: {unique_symbols}")
            
            for symbol in unique_symbols:
                symbol_df = latest_data[latest_data['symbol'] == symbol].iloc[0]
                app.logger.info(f"处理交易对 {symbol}, 数据: {symbol_df.to_dict()}")
                
                # 严格要求使用bid列作为价格
                if 'bid' not in symbol_df or pd.isna(symbol_df['bid']):
                    app.logger.warning(f"交易对 {symbol} 没有有效的bid价格，跳过")
                    continue
                
                # 获取bid价格
                price = float(symbol_df['bid'])
                app.logger.info(f"使用bid列作为价格: {price}")
                
                # 将symbol转为大写
                symbol_upper = symbol.upper()
                prices[symbol_upper] = price
                
                # 同时添加不带USDT的基础币种
                if symbol_upper.endswith('USDT'):
                    base_symbol = symbol_upper.replace('USDT', '')
                    prices[base_symbol] = price
                
                # 添加一些常见的币种别名映射
                symbol_aliases = {
                    'BTCUSDT': ['BTC', 'BITCOIN'],
                    'ETHUSDT': ['ETH', 'ETHEREUM'],
                    'SOLUSDT': ['SOL', 'SOLANA'],
                    'XRPUSDT': ['XRP', 'RIPPLE'],
                    'BNBUSDT': ['BNB', 'BINANCE'],
                    'ADAUSDT': ['ADA', 'CARDANO'],
                    'DOGEUSDT': ['DOGE', 'DOGECOIN'],
                    'DOTUSDT': ['DOT', 'POLKADOT']
                }
                
                if symbol_upper in symbol_aliases:
                    for alias in symbol_aliases[symbol_upper]:
                        prices[alias] = price
                        app.logger.info(f"添加币种别名: {alias} = {price}")
            
            app.logger.info(f"从price_history.csv加载了{len(prices)}个交易对的最新价格: {prices}")
            
            # 添加一个特殊的调试字段，帮助前端识别价格数据来源
            return jsonify({
                'status': 'success',
                'prices': prices,
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'source': 'price_history.csv',
                'file_path': csv_path,
                'row_count': len(df),
                'price_type': 'bid' # 明确标识使用的是bid价格
            })
            
        except Exception as e:
            app.logger.error(f"读取price_history.csv出错: {str(e)}")
            import traceback
            app.logger.error(traceback.format_exc())
            return jsonify({
                'status': 'error',
                'message': f'读取CSV文件出错: {str(e)}',
                'traceback': traceback.format_exc()
            })
            
    except Exception as e:
        app.logger.error(f"获取最新价格数据出错: {str(e)}")
        import traceback
        app.logger.error(traceback.format_exc())
        return jsonify({
            'status': 'error',
            'message': str(e),
            'traceback': traceback.format_exc()
        })

if __name__ == '__main__':
    try:
        monitor.keep_running = True
        # 使用带历史记录的监控启动
        monitor.start_monitoring_with_history(symbols_to_monitor, csv_file_path)
        price_thread = socketio.start_background_task(background_monitoring)
        print("服务器正在启动，请访问 http://8.209.208.159:8080")
        socketio.run(app, debug=True, allow_unsafe_werkzeug=True, port=8080, host='0.0.0.0')
    except Exception as e:
        print(f"启动服务器时出错: {str(e)}")
        import traceback
        traceback.print_exc()