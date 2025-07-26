import json
import threading
import os
from datetime import datetime
from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO
from Binance_price_monitor import BinanceRestPriceMonitor
from trade_analyzer import analyze_trade_history

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
        import pandas as pd
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

if __name__ == '__main__':
    try:
        monitor.keep_running = True
        # 使用带历史记录的监控启动
        monitor.start_monitoring_with_history(symbols_to_monitor, csv_file_path)
        price_thread = socketio.start_background_task(background_monitoring)
        print("服务器正在启动，请访问 http://47.239.197.28:8080")
        socketio.run(app, debug=True, allow_unsafe_werkzeug=True, port=8080, host='47.239.197.28')
    except Exception as e:
        print(f"启动服务器时出错: {str(e)}")
        import traceback
        traceback.print_exc()