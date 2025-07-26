# -*- coding: utf-8 -*-
import json
import time
import pandas as pd
from datetime import datetime
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO
from Binance_price_monitor import BinanceRestPriceMonitor

app = Flask(__name__)
socketio = SocketIO(app)

# 创建价格监控器
monitor = BinanceRestPriceMonitor(polling_interval=3)
price_thread = None
start_time = None

# 价格数据
price_data = {}  # 存储最新价格数据
price_history = {}  # 存储价格历史数据

# 支持的交易对
AVAILABLE_SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT"}

# 初始化价格历史数据
def init_price_history():
    for symbol in AVAILABLE_SYMBOLS.values():
        price_history[symbol] = []

# 接收和发送价格数据的函数
def background_monitoring():
    """后台监控价格并通过WebSocket发送到客户端"""
    global start_time, price_data
    if not start_time:
        start_time = time.time()
        
    try:
        # 只监控可用的交易对
        symbols_to_monitor = list(AVAILABLE_SYMBOLS.values())
        print(f"开始监控 {len(symbols_to_monitor)} 个交易对: {', '.join(symbols_to_monitor)}")
        
        while monitor.keep_running:
            # 为每个交易对获取价格
            for symbol in symbols_to_monitor:
                try:
                    price = monitor.get_price(symbol)
                    if price:
                        # 保存最新价格数据
                        price_data[symbol] = {
                            'symbol': symbol,
                            'bid': price['bid'],
                            'ask': price['ask'],
                            'mid': price['mid'],
                            'timestamp': price['timestamp'],
                            'time': datetime.now().strftime('%H:%M:%S')
                        }
                        
                        # 保存到历史数据
                        price_history[symbol].append({
                            'price': price['mid'],
                            'time': datetime.now().strftime('%H:%M:%S'),
                            'timestamp': time.time()
                        })
                        
                        # 限制历史数据长度
                        if len(price_history[symbol]) > 100:
                            price_history[symbol] = price_history[symbol][-100:]
                        
                        # 通过WebSocket发送数据
                        socketio.emit('price_update', price_data[symbol])
                except Exception as e:
                    print(f"获取 {symbol} 价格时出错: {e}")
            
            # 发送所有价格数据
            socketio.emit('all_prices', {
                'prices': list(price_data.values()),
                'timestamp': time.time()
            })
            
            socketio.sleep(monitor.polling_interval)
    except Exception as e:
        print(f"监控线程错误: {e}")
    finally:
        monitor.keep_running = False

@app.route('/')
def index():
    """渲染主页"""
    return render_template('price_monitor.html', symbols=AVAILABLE_SYMBOLS)

@socketio.on('connect')
def handle_connect():
    """处理WebSocket连接"""
    print('客户端已连接')
    
    # 发送初始价格数据
    if price_data:
        socketio.emit('all_prices', {
            'prices': list(price_data.values()),
            'timestamp': time.time()
        })
    
    # 发送监控状态
    socketio.emit('monitoring_status', {
        'is_monitoring': monitor.keep_running,
        'start_time': start_time,
        'available_symbols': list(AVAILABLE_SYMBOLS.values())
    })

@socketio.on('start_monitoring')
def handle_start_monitoring():
    """开始价格监控"""
    global price_thread, start_time
    if not monitor.keep_running:
        monitor.keep_running = True
        start_time = time.time()
        price_thread = socketio.start_background_task(background_monitoring)
        socketio.emit('monitoring_status', {
            'is_monitoring': True,
            'start_time': start_time,
            'available_symbols': list(AVAILABLE_SYMBOLS.values())
        })
        return {'status': 'started'}
    return {'status': 'already_running'}

@socketio.on('stop_monitoring')
def handle_stop_monitoring():
    """停止价格监控"""
    monitor.keep_running = False
    socketio.emit('monitoring_status', {
        'is_monitoring': False
    })
    return {'status': 'stopped'}

@socketio.on('get_price_history')
def handle_get_price_history(data):
    """获取价格历史数据"""
    symbol = data.get('symbol')
    if symbol in price_history:
        return {
            'status': 'success',
            'symbol': symbol,
            'history': price_history[symbol]
        }
    return {'status': 'error', 'message': f'没有找到 {symbol} 的历史数据'}

@socketio.on('set_interval')
def handle_set_interval(data):
    """设置更新间隔"""
    try:
        interval = int(data['interval'])
        if 1 <= interval <= 60:
            monitor.polling_interval = interval
            return {'status': 'success', 'interval': interval}
        return {'status': 'error', 'message': '间隔必须在1-60秒之间'}
    except (KeyError, ValueError) as e:
        return {'status': 'error', 'message': str(e)}

@socketio.on('get_available_symbols')
def handle_get_available_symbols():
    """获取可用的交易对列表"""
    return {
        'status': 'success',
        'available_symbols': list(AVAILABLE_SYMBOLS.values())
    }

if __name__ == '__main__':
    # 初始化价格历史数据
    init_price_history()
    
    # 启动监控线程
    monitor.keep_running = True
    price_thread = socketio.start_background_task(background_monitoring)
    
    # 启动Flask应用
    socketio.run(app, debug=True, allow_unsafe_werkzeug=True)