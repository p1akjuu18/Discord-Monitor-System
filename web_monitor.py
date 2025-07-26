# -*- coding: utf-8 -*-
"""
Web实时监控界面
提供系统状态、交易情况、风险指标的实时Web展示
"""

from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
import asyncio
import json
from datetime import datetime, timedelta
import threading
import time
from typing import Dict, List
from logger_config import app_logger, performance_logger
from monitor_system import system_monitor
from health_checker import health_checker
from database_manager import db_manager


class WebMonitor:
    """Web监控界面"""
    
    def __init__(self):
        self.app = Flask(__name__)
        self.app.config['SECRET_KEY'] = 'your-secret-key-here'
        self.socketio = SocketIO(self.app, cors_allowed_origins="*")
        
        self.setup_routes()
        self.setup_socket_events()
        
        # 启动后台数据更新任务
        self.running = False
        self.update_thread = None
    
    def setup_routes(self):
        """设置路由"""
        
        @self.app.route('/')
        def dashboard():
            """主监控页面"""
            return render_template('monitor_dashboard.html')
        
        @self.app.route('/api/system_status')
        def get_system_status():
            """获取系统状态"""
            try:
                status = system_monitor.get_system_status()
                health_status = health_checker.get_status_summary()
                
                return jsonify({
                    'success': True,
                    'system': status,
                    'health': health_status,
                    'timestamp': datetime.now().isoformat()
                })
            except Exception as e:
                app_logger.error(f"获取系统状态失败: {e}")
                return jsonify({'success': False, 'error': str(e)})
        
        @self.app.route('/api/trading_stats')
        def get_trading_stats():
            """获取交易统计"""
            try:
                # 这里应该从数据库获取实际交易数据
                stats = {
                    'today': {
                        'total_signals': 15,
                        'executed_orders': 12,
                        'success_rate': 0.8,
                        'pnl': 250.50,
                        'volume': 15000
                    },
                    'this_week': {
                        'total_signals': 89,
                        'executed_orders': 75,
                        'success_rate': 0.72,
                        'pnl': 1250.30,
                        'volume': 95000
                    },
                    'positions': [
                        {
                            'symbol': 'BTCUSDT',
                            'side': 'LONG',
                            'size': 0.5,
                            'entry_price': 45000,
                            'current_price': 46200,
                            'pnl': 600,
                            'pnl_percent': 2.67
                        },
                        {
                            'symbol': 'ETHUSDT', 
                            'side': 'SHORT',
                            'size': 2.0,
                            'entry_price': 3200,
                            'current_price': 3150,
                            'pnl': 100,
                            'pnl_percent': 1.56
                        }
                    ]
                }
                
                return jsonify({
                    'success': True,
                    'data': stats,
                    'timestamp': datetime.now().isoformat()
                })
            except Exception as e:
                app_logger.error(f"获取交易统计失败: {e}")
                return jsonify({'success': False, 'error': str(e)})
        
        @self.app.route('/api/risk_metrics')
        def get_risk_metrics():
            """获取风险指标"""
            try:
                # 这里应该从风险管理系统获取实际数据
                metrics = {
                    'overall_risk': 'medium',
                    'total_exposure': 0.65,
                    'max_drawdown': 0.08,
                    'volatility': 0.25,
                    'var_1d': 850.30,  # 1日风险价值
                    'sharpe_ratio': 1.2,
                    'alerts': [
                        {
                            'level': 'warning',
                            'message': 'BTC仓位集中度较高',
                            'timestamp': datetime.now().isoformat()
                        }
                    ]
                }
                
                return jsonify({
                    'success': True,
                    'data': metrics,
                    'timestamp': datetime.now().isoformat()
                })
            except Exception as e:
                app_logger.error(f"获取风险指标失败: {e}")
                return jsonify({'success': False, 'error': str(e)})
        
        @self.app.route('/api/recent_activities')
        def get_recent_activities():
            """获取最近活动"""
            try:
                activities = [
                    {
                        'time': (datetime.now() - timedelta(minutes=5)).isoformat(),
                        'type': 'signal',
                        'message': '收到BTCUSDT买入信号',
                        'details': {'symbol': 'BTCUSDT', 'side': 'BUY', 'price': 46100}
                    },
                    {
                        'time': (datetime.now() - timedelta(minutes=12)).isoformat(),
                        'type': 'order',
                        'message': 'ETHUSDT限价单已成交',
                        'details': {'symbol': 'ETHUSDT', 'side': 'SELL', 'quantity': 1.5}
                    },
                    {
                        'time': (datetime.now() - timedelta(minutes=18)).isoformat(),
                        'type': 'alert',
                        'message': '内存使用率达到85%',
                        'details': {'usage': 0.85}
                    }
                ]
                
                return jsonify({
                    'success': True,
                    'data': activities,
                    'timestamp': datetime.now().isoformat()
                })
            except Exception as e:
                app_logger.error(f"获取最近活动失败: {e}")
                return jsonify({'success': False, 'error': str(e)})
        
        @self.app.route('/api/performance_chart')
        def get_performance_chart():
            """获取性能图表数据"""
            try:
                # 生成模拟的性能数据
                now = datetime.now()
                times = [(now - timedelta(hours=i)).isoformat() for i in range(24, 0, -1)]
                
                chart_data = {
                    'pnl_curve': {
                        'labels': times,
                        'datasets': [{
                            'label': '累计收益',
                            'data': [100 + i * 2.5 + (i % 3) * 10 for i in range(24)],
                            'borderColor': '#2ecc71',
                            'fill': False
                        }]
                    },
                    'memory_usage': {
                        'labels': times[-12:],  # 最近12小时
                        'datasets': [{
                            'label': '内存使用率',
                            'data': [60 + (i % 5) * 5 for i in range(12)],
                            'borderColor': '#3498db',
                            'backgroundColor': 'rgba(52, 152, 219, 0.1)',
                            'fill': True
                        }]
                    },
                    'api_response_time': {
                        'labels': times[-6:],  # 最近6小时
                        'datasets': [{
                            'label': '平均响应时间(ms)',
                            'data': [200 + (i % 3) * 50 for i in range(6)],
                            'borderColor': '#f39c12',
                            'fill': False
                        }]
                    }
                }
                
                return jsonify({
                    'success': True,
                    'data': chart_data,
                    'timestamp': datetime.now().isoformat()
                })
            except Exception as e:
                app_logger.error(f"获取性能图表数据失败: {e}")
                return jsonify({'success': False, 'error': str(e)})
    
    def setup_socket_events(self):
        """设置WebSocket事件"""
        
        @self.socketio.on('connect')
        def handle_connect():
            """客户端连接"""
            app_logger.info(f"客户端已连接: {request.sid}")
            emit('connected', {'message': '连接成功'})
        
        @self.socketio.on('disconnect')
        def handle_disconnect():
            """客户端断开连接"""
            app_logger.info(f"客户端已断开: {request.sid}")
        
        @self.socketio.on('subscribe_updates')
        def handle_subscribe(data):
            """订阅实时更新"""
            app_logger.info(f"客户端订阅更新: {data}")
            # 可以根据data中的类型来订阅不同的更新
    
    def start_background_updates(self):
        """启动后台数据更新"""
        self.running = True
        self.update_thread = threading.Thread(target=self._background_update_loop)
        self.update_thread.daemon = True
        self.update_thread.start()
        app_logger.info("后台数据更新已启动")
    
    def stop_background_updates(self):
        """停止后台数据更新"""
        self.running = False
        if self.update_thread:
            self.update_thread.join()
        app_logger.info("后台数据更新已停止")
    
    def _background_update_loop(self):
        """后台更新循环"""
        while self.running:
            try:
                # 每30秒推送一次系统状态更新
                self._push_system_updates()
                time.sleep(30)
                
                # 每10秒推送一次实时数据
                self._push_realtime_updates()
                time.sleep(10)
                
            except Exception as e:
                app_logger.error(f"后台更新出错: {e}")
                time.sleep(30)
    
    def _push_system_updates(self):
        """推送系统状态更新"""
        try:
            status = system_monitor.get_system_status()
            self.socketio.emit('system_update', {
                'type': 'system_status',
                'data': status,
                'timestamp': datetime.now().isoformat()
            })
        except Exception as e:
            app_logger.error(f"推送系统更新失败: {e}")
    
    def _push_realtime_updates(self):
        """推送实时数据更新"""
        try:
            # 推送价格更新
            price_update = {
                'BTCUSDT': 46250.50,
                'ETHUSDT': 3145.25,
                'BNBUSDT': 315.80
            }
            
            self.socketio.emit('price_update', {
                'type': 'price_data',
                'data': price_update,
                'timestamp': datetime.now().isoformat()
            })
            
            # 推送交易活动
            if hasattr(self, '_last_activity_time'):
                activities = self._get_new_activities_since(self._last_activity_time)
                if activities:
                    self.socketio.emit('activity_update', {
                        'type': 'new_activities',
                        'data': activities,
                        'timestamp': datetime.now().isoformat()
                    })
            
            self._last_activity_time = datetime.now()
            
        except Exception as e:
            app_logger.error(f"推送实时更新失败: {e}")
    
    def _get_new_activities_since(self, since: datetime) -> List[Dict]:
        """获取指定时间之后的新活动"""
        # 这里应该查询数据库获取新活动
        # 暂时返回模拟数据
        return []
    
    def run(self, host='0.0.0.0', port=5000, debug=False):
        """启动Web服务器"""
        try:
            self.start_background_updates()
            app_logger.info(f"Web监控界面启动: http://{host}:{port}")
            self.socketio.run(self.app, host=host, port=port, debug=debug, allow_unsafe_werkzeug=True)
        except Exception as e:
            app_logger.error(f"启动Web服务器失败: {e}")
        finally:
            self.stop_background_updates()


# 创建Web监控模板
def create_dashboard_template():
    """创建监控面板HTML模板"""
    template_dir = "templates"
    import os
    os.makedirs(template_dir, exist_ok=True)
    
    html_content = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>交易监控面板</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f5f6fa; }
        .dashboard { display: grid; grid-template-columns: 250px 1fr; min-height: 100vh; }
        .sidebar { background: #2c3e50; color: white; padding: 20px; }
        .sidebar h2 { margin-bottom: 30px; color: #ecf0f1; }
        .nav-item { padding: 10px; margin: 5px 0; cursor: pointer; border-radius: 5px; transition: background 0.3s; }
        .nav-item:hover { background: #34495e; }
        .nav-item.active { background: #3498db; }
        .main-content { padding: 20px; overflow-y: auto; }
        .header { display: flex; justify-content: between; align-items: center; margin-bottom: 30px; }
        .status-indicator { display: inline-block; width: 12px; height: 12px; border-radius: 50%; margin-right: 10px; }
        .status-healthy { background: #2ecc71; }
        .status-warning { background: #f39c12; }
        .status-critical { background: #e74c3c; }
        .cards-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .card { background: white; border-radius: 8px; padding: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        .card-title { font-size: 18px; font-weight: 600; margin-bottom: 15px; color: #2c3e50; }
        .metric { display: flex; justify-content: space-between; margin: 10px 0; }
        .metric-value { font-weight: 600; }
        .chart-container { height: 300px; }
        .activity-list { max-height: 400px; overflow-y: auto; }
        .activity-item { padding: 10px; border-left: 4px solid #3498db; margin: 10px 0; background: #f8f9fa; }
        .activity-time { font-size: 12px; color: #7f8c8d; }
        .alert { padding: 10px; margin: 5px 0; border-radius: 5px; }
        .alert-warning { background: #fff3cd; border: 1px solid #ffeaa7; color: #856404; }
        .alert-error { background: #f8d7da; border: 1px solid #f5c6cb; color: #721c24; }
        .positions-table { width: 100%; border-collapse: collapse; }
        .positions-table th, .positions-table td { padding: 10px; text-align: left; border-bottom: 1px solid #ddd; }
        .positions-table th { background: #f8f9fa; font-weight: 600; }
        .positive { color: #2ecc71; }
        .negative { color: #e74c3c; }
    </style>
</head>
<body>
    <div class="dashboard">
        <div class="sidebar">
            <h2>监控面板</h2>
            <div class="nav-item active" onclick="showTab('overview')">系统概览</div>
            <div class="nav-item" onclick="showTab('trading')">交易状态</div>
            <div class="nav-item" onclick="showTab('risk')">风险监控</div>
            <div class="nav-item" onclick="showTab('performance')">性能指标</div>
            <div class="nav-item" onclick="showTab('activities')">活动日志</div>
        </div>
        
        <div class="main-content">
            <div class="header">
                <h1>交易监控系统</h1>
                <div>
                    <span class="status-indicator status-healthy"></span>
                    <span id="system-status">系统正常</span>
                    <span style="margin-left: 20px; color: #7f8c8d;" id="last-update">最后更新: --</span>
                </div>
            </div>
            
            <!-- 系统概览标签页 -->
            <div id="overview-tab" class="tab-content">
                <div class="cards-grid">
                    <div class="card">
                        <div class="card-title">系统资源</div>
                        <div class="metric">
                            <span>CPU使用率</span>
                            <span class="metric-value" id="cpu-usage">--</span>
                        </div>
                        <div class="metric">
                            <span>内存使用率</span>
                            <span class="metric-value" id="memory-usage">--</span>
                        </div>
                        <div class="metric">
                            <span>磁盘使用率</span>
                            <span class="metric-value" id="disk-usage">--</span>
                        </div>
                    </div>
                    
                    <div class="card">
                        <div class="card-title">连接状态</div>
                        <div class="metric">
                            <span>Discord</span>
                            <span class="metric-value" id="discord-status">--</span>
                        </div>
                        <div class="metric">
                            <span>Binance API</span>
                            <span class="metric-value" id="binance-status">--</span>
                        </div>
                        <div class="metric">
                            <span>数据库</span>
                            <span class="metric-value" id="database-status">--</span>
                        </div>
                    </div>
                    
                    <div class="card">
                        <div class="card-title">今日统计</div>
                        <div class="metric">
                            <span>处理信号</span>
                            <span class="metric-value" id="signals-today">--</span>
                        </div>
                        <div class="metric">
                            <span>执行订单</span>
                            <span class="metric-value" id="orders-today">--</span>
                        </div>
                        <div class="metric">
                            <span>成功率</span>
                            <span class="metric-value" id="success-rate">--</span>
                        </div>
                    </div>
                    
                    <div class="card">
                        <div class="card-title">收益概况</div>
                        <div class="metric">
                            <span>今日PnL</span>
                            <span class="metric-value" id="daily-pnl">--</span>
                        </div>
                        <div class="metric">
                            <span>总余额</span>
                            <span class="metric-value" id="total-balance">--</span>
                        </div>
                        <div class="metric">
                            <span>未实现PnL</span>
                            <span class="metric-value" id="unrealized-pnl">--</span>
                        </div>
                    </div>
                </div>
                
                <div class="card">
                    <div class="card-title">收益曲线</div>
                    <div class="chart-container">
                        <canvas id="pnl-chart"></canvas>
                    </div>
                </div>
            </div>
            
            <!-- 其他标签页内容 -->
            <div id="trading-tab" class="tab-content" style="display: none;">
                <div class="card">
                    <div class="card-title">当前持仓</div>
                    <table class="positions-table">
                        <thead>
                            <tr>
                                <th>交易对</th>
                                <th>方向</th>
                                <th>数量</th>
                                <th>入场价</th>
                                <th>当前价</th>
                                <th>PnL</th>
                                <th>收益率</th>
                            </tr>
                        </thead>
                        <tbody id="positions-tbody">
                            <!-- 动态填充 -->
                        </tbody>
                    </table>
                </div>
            </div>
            
            <div id="activities-tab" class="tab-content" style="display: none;">
                <div class="card">
                    <div class="card-title">最近活动</div>
                    <div class="activity-list" id="activities-list">
                        <!-- 动态填充 -->
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        // WebSocket连接
        const socket = io();
        
        // 图表对象
        let pnlChart;
        
        // 初始化
        document.addEventListener('DOMContentLoaded', function() {
            initCharts();
            loadInitialData();
            
            // 订阅实时更新
            socket.emit('subscribe_updates', {types: ['system', 'trading', 'activities']});
        });
        
        // WebSocket事件处理
        socket.on('connected', function(data) {
            console.log('WebSocket连接成功');
        });
        
        socket.on('system_update', function(data) {
            updateSystemStatus(data.data);
        });
        
        socket.on('price_update', function(data) {
            updatePrices(data.data);
        });
        
        socket.on('activity_update', function(data) {
            updateActivities(data.data);
        });
        
        // 标签页切换
        function showTab(tabName) {
            // 隐藏所有标签页
            document.querySelectorAll('.tab-content').forEach(tab => {
                tab.style.display = 'none';
            });
            
            // 移除所有活动状态
            document.querySelectorAll('.nav-item').forEach(item => {
                item.classList.remove('active');
            });
            
            // 显示目标标签页
            document.getElementById(tabName + '-tab').style.display = 'block';
            
            // 设置活动状态
            event.target.classList.add('active');
        }
        
        // 初始化图表
        function initCharts() {
            const ctx = document.getElementById('pnl-chart').getContext('2d');
            pnlChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [{
                        label: '累计收益',
                        data: [],
                        borderColor: '#2ecc71',
                        backgroundColor: 'rgba(46, 204, 113, 0.1)',
                        fill: true
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        y: {
                            beginAtZero: false
                        }
                    }
                }
            });
        }
        
        // 加载初始数据
        function loadInitialData() {
            fetch('/api/system_status')
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        updateSystemStatus(data.system);
                    }
                });
            
            fetch('/api/trading_stats')
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        updateTradingStats(data.data);
                    }
                });
        }
        
        // 更新系统状态
        function updateSystemStatus(data) {
            if (data.memory) {
                document.getElementById('memory-usage').textContent = data.memory.percent.toFixed(1) + '%';
            }
            if (data.cpu) {
                document.getElementById('cpu-usage').textContent = data.cpu.percent.toFixed(1) + '%';
            }
            
            document.getElementById('last-update').textContent = '最后更新: ' + new Date().toLocaleTimeString();
        }
        
        // 更新交易统计
        function updateTradingStats(data) {
            if (data.today) {
                document.getElementById('signals-today').textContent = data.today.total_signals;
                document.getElementById('orders-today').textContent = data.today.executed_orders;
                document.getElementById('success-rate').textContent = (data.today.success_rate * 100).toFixed(1) + '%';
                document.getElementById('daily-pnl').textContent = '$' + data.today.pnl.toFixed(2);
            }
            
            // 更新持仓表格
            if (data.positions) {
                updatePositionsTable(data.positions);
            }
        }
        
        // 更新持仓表格
        function updatePositionsTable(positions) {
            const tbody = document.getElementById('positions-tbody');
            tbody.innerHTML = '';
            
            positions.forEach(pos => {
                const row = tbody.insertRow();
                row.innerHTML = `
                    <td>${pos.symbol}</td>
                    <td>${pos.side}</td>
                    <td>${pos.size}</td>
                    <td>$${pos.entry_price}</td>
                    <td>$${pos.current_price}</td>
                    <td class="${pos.pnl >= 0 ? 'positive' : 'negative'}">$${pos.pnl.toFixed(2)}</td>
                    <td class="${pos.pnl_percent >= 0 ? 'positive' : 'negative'}">${pos.pnl_percent.toFixed(2)}%</td>
                `;
            });
        }
        
        // 更新价格
        function updatePrices(prices) {
            // 这里可以更新实时价格显示
            console.log('价格更新:', prices);
        }
        
        // 更新活动列表
        function updateActivities(activities) {
            const list = document.getElementById('activities-list');
            
            activities.forEach(activity => {
                const item = document.createElement('div');
                item.className = 'activity-item';
                item.innerHTML = `
                    <div class="activity-time">${new Date(activity.time).toLocaleString()}</div>
                    <div>${activity.message}</div>
                `;
                list.insertBefore(item, list.firstChild);
            });
            
            // 限制显示数量
            while (list.children.length > 50) {
                list.removeChild(list.lastChild);
            }
        }
    </script>
</body>
</html>
'''
    
    with open(f"{template_dir}/monitor_dashboard.html", 'w', encoding='utf-8') as f:
        f.write(html_content)


# 全局Web监控实例
web_monitor = WebMonitor()

if __name__ == '__main__':
    # 创建模板文件
    create_dashboard_template()
    
    # 启动Web监控
    web_monitor.run(debug=True)