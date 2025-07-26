# -*- coding: utf-8 -*-
"""
智能交易风控系统
实现多层次风险控制，包括仓位管理、止损优化、市场风险评估等
"""

import asyncio
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import numpy as np
import pandas as pd
from logger_config import trading_logger, error_logger
from config_manager import config_manager


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class RiskMetrics:
    """风险指标"""
    total_exposure: float  # 总敞口
    position_concentration: float  # 仓位集中度
    max_drawdown: float  # 最大回撤
    volatility: float  # 波动率
    correlation_risk: float  # 相关性风险
    liquidity_risk: float  # 流动性风险
    overall_risk: RiskLevel


@dataclass
class PositionRisk:
    """单仓位风险"""
    symbol: str
    position_size: float
    unrealized_pnl: float
    risk_score: float
    stop_loss_distance: float
    volatility: float
    correlation_score: float


class RiskManager:
    """风险管理器"""
    
    def __init__(self):
        self.risk_limits = {
            'max_total_exposure': 0.8,      # 最大总敞口（占总资金的比例）
            'max_single_position': 0.2,     # 单个仓位最大占比
            'max_correlation': 0.7,         # 最大相关性
            'max_drawdown': 0.15,           # 最大允许回撤
            'min_liquidity_ratio': 0.1,     # 最小流动性比例
            'max_daily_loss': 0.05,         # 单日最大亏损
            'volatility_threshold': 0.5     # 波动率阈值
        }
        
        self.position_history = {}
        self.price_history = {}
        self.correlation_matrix = {}
        self.market_conditions = {}
        
    async def evaluate_signal_risk(self, signal: Dict, current_positions: List[Dict]) -> Tuple[bool, str, float]:
        """
        评估交易信号风险
        
        Returns:
            (is_safe, reason, risk_score)
        """
        try:
            symbol = signal['symbol']
            side = signal['side']
            entry_price = signal['entry_price']
            position_size = signal.get('position_size', 0)
            
            risk_score = 0.0
            risk_factors = []
            
            # 1. 检查仓位集中度
            concentration_risk = await self._check_position_concentration(
                symbol, position_size, current_positions
            )
            if concentration_risk > self.risk_limits['max_single_position']:
                return False, f"仓位集中度过高: {concentration_risk:.1%}", 1.0
            risk_score += concentration_risk * 0.3
            
            # 2. 检查总敞口
            total_exposure = await self._calculate_total_exposure(
                current_positions, symbol, position_size
            )
            if total_exposure > self.risk_limits['max_total_exposure']:
                return False, f"总敞口过高: {total_exposure:.1%}", 1.0
            risk_score += (total_exposure / self.risk_limits['max_total_exposure']) * 0.3
            
            # 3. 检查相关性风险
            correlation_risk = await self._check_correlation_risk(
                symbol, current_positions
            )
            if correlation_risk > self.risk_limits['max_correlation']:
                risk_factors.append(f"相关性风险: {correlation_risk:.2f}")
            risk_score += correlation_risk * 0.2
            
            # 4. 检查市场波动率
            volatility = await self._get_symbol_volatility(symbol)
            if volatility > self.risk_limits['volatility_threshold']:
                risk_factors.append(f"高波动率: {volatility:.1%}")
            risk_score += min(volatility / self.risk_limits['volatility_threshold'], 1.0) * 0.1
            
            # 5. 检查流动性风险
            liquidity_risk = await self._check_liquidity_risk(symbol)
            risk_score += liquidity_risk * 0.1
            
            # 6. 检查当日亏损
            daily_loss = await self._calculate_daily_loss(current_positions)
            if daily_loss > self.risk_limits['max_daily_loss']:
                return False, f"当日亏损已达上限: {daily_loss:.1%}", 1.0
            
            # 综合风险评估
            if risk_score > 0.8:
                return False, f"综合风险过高: {risk_score:.2f}, 风险因素: {', '.join(risk_factors)}", risk_score
            elif risk_score > 0.6:
                reason = f"中等风险: {risk_score:.2f}"
                if risk_factors:
                    reason += f", 注意: {', '.join(risk_factors)}"
                return True, reason, risk_score
            else:
                return True, f"低风险: {risk_score:.2f}", risk_score
                
        except Exception as e:
            error_logger.error(f"评估信号风险时出错: {e}")
            return False, f"风险评估失败: {str(e)}", 1.0
    
    async def _check_position_concentration(self, symbol: str, new_size: float, positions: List[Dict]) -> float:
        """检查仓位集中度"""
        try:
            # 获取账户总价值
            total_balance = 10000  # 这里应该从实际账户获取
            
            # 计算当前该币种的总仓位
            current_exposure = 0
            for pos in positions:
                if pos.get('symbol') == symbol:
                    current_exposure += abs(float(pos.get('positionAmt', 0)))
            
            # 加上新仓位
            total_exposure = current_exposure + new_size
            
            return total_exposure / total_balance
            
        except Exception as e:
            error_logger.error(f"检查仓位集中度失败: {e}")
            return 1.0  # 安全起见返回最高风险
    
    async def _calculate_total_exposure(self, positions: List[Dict], new_symbol: str = None, new_size: float = 0) -> float:
        """计算总敞口"""
        try:
            total_balance = 10000  # 从实际账户获取
            total_exposure = 0
            
            # 现有仓位
            for pos in positions:
                position_value = abs(float(pos.get('positionAmt', 0))) * float(pos.get('markPrice', 0))
                total_exposure += position_value
            
            # 新仓位
            if new_symbol and new_size:
                # 获取当前价格
                price = 50000  # 这里应该获取实际价格
                total_exposure += new_size * price
            
            return total_exposure / total_balance
            
        except Exception as e:
            error_logger.error(f"计算总敞口失败: {e}")
            return 1.0
    
    async def _check_correlation_risk(self, symbol: str, positions: List[Dict]) -> float:
        """检查相关性风险"""
        try:
            if not positions:
                return 0.0
            
            # 获取持仓币种
            held_symbols = [pos.get('symbol') for pos in positions if float(pos.get('positionAmt', 0)) != 0]
            
            if not held_symbols:
                return 0.0
            
            # 计算与现有仓位的相关性
            max_correlation = 0.0
            
            for held_symbol in held_symbols:
                correlation = await self._get_correlation(symbol, held_symbol)
                max_correlation = max(max_correlation, abs(correlation))
            
            return max_correlation
            
        except Exception as e:
            error_logger.error(f"检查相关性风险失败: {e}")
            return 0.5
    
    async def _get_correlation(self, symbol1: str, symbol2: str) -> float:
        """获取两个币种的相关性"""
        try:
            if symbol1 == symbol2:
                return 1.0
            
            # 这里应该计算实际的价格相关性
            # 暂时使用预设的相关性矩阵
            correlation_map = {
                ('BTCUSDT', 'ETHUSDT'): 0.85,
                ('BTCUSDT', 'BNBUSDT'): 0.75,
                ('ETHUSDT', 'BNBUSDT'): 0.80,
            }
            
            key1 = (symbol1, symbol2)
            key2 = (symbol2, symbol1)
            
            return correlation_map.get(key1, correlation_map.get(key2, 0.3))
            
        except Exception:
            return 0.3  # 默认中等相关性
    
    async def _get_symbol_volatility(self, symbol: str) -> float:
        """获取币种波动率"""
        try:
            # 这里应该计算实际的波动率
            # 基于历史价格数据计算
            volatility_map = {
                'BTCUSDT': 0.3,
                'ETHUSDT': 0.4,
                'BNBUSDT': 0.5,
            }
            
            return volatility_map.get(symbol, 0.6)  # 默认高波动率
            
        except Exception:
            return 0.6
    
    async def _check_liquidity_risk(self, symbol: str) -> float:
        """检查流动性风险"""
        try:
            # 主流币种流动性较好
            major_coins = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'ADAUSDT', 'XRPUSDT']
            
            if symbol in major_coins:
                return 0.1  # 低流动性风险
            else:
                return 0.5  # 中等流动性风险
                
        except Exception:
            return 0.8  # 默认高流动性风险
    
    async def _calculate_daily_loss(self, positions: List[Dict]) -> float:
        """计算当日亏损"""
        try:
            total_pnl = 0
            total_balance = 10000
            
            for pos in positions:
                pnl = float(pos.get('unRealizedProfit', 0))
                total_pnl += pnl
            
            # 只考虑亏损
            if total_pnl >= 0:
                return 0.0
            
            return abs(total_pnl) / total_balance
            
        except Exception as e:
            error_logger.error(f"计算当日亏损失败: {e}")
            return 0.0
    
    async def optimize_stop_loss(self, symbol: str, entry_price: float, side: str, current_stop: float) -> float:
        """优化止损位置"""
        try:
            # 获取ATR（平均真实波幅）
            atr = await self._calculate_atr(symbol)
            
            # 获取支撑阻力位
            support_resistance = await self._get_support_resistance(symbol, entry_price)
            
            # 基于波动率的动态止损
            volatility_stop = self._calculate_volatility_stop(entry_price, side, atr)
            
            # 基于技术分析的止损
            technical_stop = self._calculate_technical_stop(entry_price, side, support_resistance)
            
            # 选择更保守的止损
            if side == 'BUY':
                optimized_stop = max(volatility_stop, technical_stop, current_stop)
            else:
                optimized_stop = min(volatility_stop, technical_stop, current_stop)
            
            trading_logger.info(f"止损优化 {symbol}: 原始={current_stop}, 优化={optimized_stop}")
            
            return optimized_stop
            
        except Exception as e:
            error_logger.error(f"优化止损失败: {e}")
            return current_stop
    
    async def _calculate_atr(self, symbol: str, period: int = 14) -> float:
        """计算平均真实波幅"""
        try:
            # 这里应该获取实际的K线数据计算ATR
            # 暂时返回基于波动率的估算值
            volatility = await self._get_symbol_volatility(symbol)
            price = 50000  # 应该获取实际价格
            
            return price * volatility * 0.1  # 简化计算
            
        except Exception:
            return 1000  # 默认ATR
    
    async def _get_support_resistance(self, symbol: str, current_price: float) -> Dict:
        """获取支撑阻力位"""
        try:
            # 这里应该基于技术分析计算支撑阻力位
            # 暂时使用简单的百分比计算
            support = current_price * 0.98
            resistance = current_price * 1.02
            
            return {
                'support': support,
                'resistance': resistance
            }
            
        except Exception:
            return {
                'support': current_price * 0.95,
                'resistance': current_price * 1.05
            }
    
    def _calculate_volatility_stop(self, entry_price: float, side: str, atr: float) -> float:
        """基于波动率计算止损"""
        multiplier = 2.0  # ATR倍数
        
        if side == 'BUY':
            return entry_price - (atr * multiplier)
        else:
            return entry_price + (atr * multiplier)
    
    def _calculate_technical_stop(self, entry_price: float, side: str, sr_levels: Dict) -> float:
        """基于技术分析计算止损"""
        if side == 'BUY':
            # 多单止损设在支撑位下方
            return sr_levels['support'] * 0.999
        else:
            # 空单止损设在阻力位上方
            return sr_levels['resistance'] * 1.001
    
    async def calculate_position_size(self, signal: Dict, account_balance: float, risk_percent: float = 0.02) -> float:
        """
        计算合适的仓位大小
        
        Args:
            signal: 交易信号
            account_balance: 账户余额
            risk_percent: 风险百分比（默认2%）
        """
        try:
            entry_price = signal['entry_price']
            stop_loss = signal['stop_loss']
            
            # 计算止损距离
            if signal['side'] == 'BUY':
                stop_distance = entry_price - stop_loss
            else:
                stop_distance = stop_loss - entry_price
            
            if stop_distance <= 0:
                return 0
            
            # 风险金额
            risk_amount = account_balance * risk_percent
            
            # 计算仓位大小
            position_size = risk_amount / stop_distance
            
            # 应用风控限制
            max_position = account_balance * self.risk_limits['max_single_position']
            position_size = min(position_size, max_position)
            
            trading_logger.info(f"仓位计算: 风险={risk_percent:.1%}, 仓位={position_size:.2f}")
            
            return position_size
            
        except Exception as e:
            error_logger.error(f"计算仓位大小失败: {e}")
            return 0
    
    async def generate_risk_report(self, positions: List[Dict]) -> Dict:
        """生成风险报告"""
        try:
            metrics = await self._calculate_risk_metrics(positions)
            
            report = {
                'timestamp': datetime.now().isoformat(),
                'overall_risk': metrics.overall_risk.value,
                'metrics': {
                    'total_exposure': f"{metrics.total_exposure:.1%}",
                    'position_concentration': f"{metrics.position_concentration:.1%}",
                    'max_drawdown': f"{metrics.max_drawdown:.1%}",
                    'volatility': f"{metrics.volatility:.1%}",
                    'correlation_risk': f"{metrics.correlation_risk:.2f}",
                    'liquidity_risk': f"{metrics.liquidity_risk:.2f}"
                },
                'recommendations': await self._generate_recommendations(metrics),
                'alerts': await self._generate_risk_alerts(metrics)
            }
            
            return report
            
        except Exception as e:
            error_logger.error(f"生成风险报告失败: {e}")
            return {'error': str(e)}
    
    async def _calculate_risk_metrics(self, positions: List[Dict]) -> RiskMetrics:
        """计算风险指标"""
        try:
            total_exposure = await self._calculate_total_exposure(positions)
            position_concentration = max([
                await self._check_position_concentration(pos.get('symbol', ''), 0, positions)
                for pos in positions
            ] or [0])
            
            # 简化的风险计算
            max_drawdown = 0.05  # 应该基于历史数据计算
            volatility = 0.3     # 应该基于持仓组合计算
            correlation_risk = 0.5  # 应该基于相关性矩阵计算
            liquidity_risk = 0.2    # 应该基于流动性指标计算
            
            # 确定整体风险级别
            risk_score = (
                total_exposure * 0.3 +
                position_concentration * 0.25 +
                max_drawdown * 0.2 +
                volatility * 0.15 +
                correlation_risk * 0.1
            )
            
            if risk_score > 0.8:
                overall_risk = RiskLevel.CRITICAL
            elif risk_score > 0.6:
                overall_risk = RiskLevel.HIGH
            elif risk_score > 0.4:
                overall_risk = RiskLevel.MEDIUM
            else:
                overall_risk = RiskLevel.LOW
            
            return RiskMetrics(
                total_exposure=total_exposure,
                position_concentration=position_concentration,
                max_drawdown=max_drawdown,
                volatility=volatility,
                correlation_risk=correlation_risk,
                liquidity_risk=liquidity_risk,
                overall_risk=overall_risk
            )
            
        except Exception as e:
            error_logger.error(f"计算风险指标失败: {e}")
            # 返回保守的风险指标
            return RiskMetrics(
                total_exposure=1.0,
                position_concentration=1.0,
                max_drawdown=1.0,
                volatility=1.0,
                correlation_risk=1.0,
                liquidity_risk=1.0,
                overall_risk=RiskLevel.CRITICAL
            )
    
    async def _generate_recommendations(self, metrics: RiskMetrics) -> List[str]:
        """生成风险建议"""
        recommendations = []
        
        if metrics.total_exposure > 0.7:
            recommendations.append("建议减少总敞口，当前敞口过高")
        
        if metrics.position_concentration > 0.3:
            recommendations.append("建议分散投资，避免仓位过度集中")
        
        if metrics.correlation_risk > 0.6:
            recommendations.append("持仓相关性过高，建议增加不相关资产")
        
        if metrics.volatility > 0.5:
            recommendations.append("当前组合波动率较高，建议降低风险敞口")
        
        return recommendations
    
    async def _generate_risk_alerts(self, metrics: RiskMetrics) -> List[str]:
        """生成风险告警"""
        alerts = []
        
        if metrics.overall_risk == RiskLevel.CRITICAL:
            alerts.append("🚨 风险级别：严重，建议立即调整仓位")
        elif metrics.overall_risk == RiskLevel.HIGH:
            alerts.append("⚠️ 风险级别：偏高，需要密切关注")
        
        if metrics.total_exposure > self.risk_limits['max_total_exposure']:
            alerts.append(f"总敞口超限：{metrics.total_exposure:.1%}")
        
        if metrics.position_concentration > self.risk_limits['max_single_position']:
            alerts.append(f"仓位集中度超限：{metrics.position_concentration:.1%}")
        
        return alerts


# 全局风险管理器
risk_manager = RiskManager()