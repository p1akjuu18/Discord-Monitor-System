import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties

class PositionAllocationStrategy:
    def __init__(self, stats_file='交易统计结果.xlsx'):
        """
        初始化仓位分配策略
        
        参数:
            stats_file: 交易统计结果文件路径
        """
        self.stats_df = pd.read_excel(stats_file)
        # 移除总计行
        self.stats_df = self.stats_df[self.stats_df['频道'] != '总计']
        print(f"已加载 {len(self.stats_df)} 个频道的交易统计数据")
    
    def calculate_position_weights(self, 
                                  min_trades=5,
                                  win_rate_weight=0.4, 
                                  avg_profit_weight=0.3, 
                                  sample_size_weight=0.2, 
                                  risk_weight=0.1):
        """
        计算各频道的仓位权重
        
        参数:
            min_trades: 最少交易数量要求
            win_rate_weight: 胜率的权重
            avg_profit_weight: 平均收益的权重
            sample_size_weight: 样本大小(总交易数)的权重
            risk_weight: 风险评估(基于最大亏损)的权重
            
        返回:
            带有仓位权重的DataFrame
        """
        # 复制数据，避免修改原始数据
        df = self.stats_df.copy()
        
        # 过滤掉交易次数过少的频道
        df = df[df['总交易数'] >= min_trades]
        
        if len(df) == 0:
            print(f"警告: 没有频道满足最少交易数 {min_trades} 的要求")
            return pd.DataFrame()
            
        # 计算各项指标的分数 (0-100)
        
        # 1. 胜率评分
        df['胜率评分'] = df['胜率(%)'] 
        
        # 2. 平均收益评分 (标准化到0-100)
        min_profit = df['平均收益(%)'].min()
        max_profit = df['平均收益(%)'].max()
        profit_range = max_profit - min_profit
        
        if profit_range > 0:
            df['收益评分'] = 50 + 50 * (df['平均收益(%)'] - min_profit) / profit_range
        else:
            df['收益评分'] = 50  # 如果所有频道平均收益相同
            
        # 3. 样本大小评分 (对数变换后标准化到0-100)
        df['样本评分'] = 100 * np.log1p(df['总交易数']) / np.log1p(df['总交易数'].max())
        
        # 4. 风险评分 (基于最小收益，即最大亏损)
        # 将最小收益转换为风险评分，较高的最小收益(较小的亏损)对应较高的评分
        min_loss = df['最小收益(%)'].min()
        max_loss = df['最小收益(%)'].max()
        loss_range = max_loss - min_loss
        
        if loss_range > 0:
            df['风险评分'] = 100 * (df['最小收益(%)'] - min_loss) / loss_range
        else:
            df['风险评分'] = 50
            
        # 计算综合得分
        df['综合评分'] = (win_rate_weight * df['胜率评分'] + 
                       avg_profit_weight * df['收益评分'] + 
                       sample_size_weight * df['样本评分'] + 
                       risk_weight * df['风险评分'])
        
        # 计算仓位权重
        total_score = df['综合评分'].sum()
        if total_score > 0:
            df['仓位权重'] = df['综合评分'] / total_score
            df['建议仓位比例'] = (df['仓位权重'] * 100).round(2)
        else:
            df['仓位权重'] = 1 / len(df)
            df['建议仓位比例'] = 100 / len(df)
            
        # 计算风险系数：总体评分越高，风险系数越低
        max_score = df['综合评分'].max()
        df['风险系数'] = 1 - (df['综合评分'] / max_score * 0.5)  # 风险系数范围0.5-1
        
        # 排序
        df = df.sort_values('建议仓位比例', ascending=False)
        
        # 保留需要的列并重命名
        result_df = df[['频道', '总交易数', '胜率(%)', '平均收益(%)', 
                       '最大收益(%)', '最小收益(%)', '建议仓位比例', '风险系数']]
        
        # 添加跟单建议
        def generate_recommendation(row):
            win_rate = row['胜率(%)']
            avg_profit = row['平均收益(%)']
            position = row['建议仓位比例']
            risk = row['风险系数']
            
            if position < 5:
                return "不建议跟单或极小仓位试验"
            elif win_rate < 40:
                return "高风险，建议谨慎低仓位跟单"
            elif win_rate > 60 and avg_profit > 0:
                return "优质信号源，建议标准仓位跟单"
            elif avg_profit > 0:
                return "可以考虑适中仓位跟单"
            else:
                return "建议降低仓位或暂时观察"
                
        result_df['跟单建议'] = result_df.apply(generate_recommendation, axis=1)
        
        return result_df
    
    def visualize_strategy(self, result_df):
        """可视化仓位分配策略结果"""
        if len(result_df) == 0:
            print("没有数据可供可视化")
            return
            
        # 设置中文字体
        try:
            font = FontProperties(fname=r"C:\Windows\Fonts\simhei.ttf")
            plt.rcParams['font.sans-serif'] = ['SimHei']  # 用来正常显示中文标签
            plt.rcParams['axes.unicode_minus'] = False    # 用来正常显示负号
        except:
            font = None
            print("警告: 无法加载中文字体，图表中的中文可能无法正确显示")
        
        # 创建图表
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 8))
        
        # 1. 仓位分配饼图
        channels = result_df['频道'].tolist()
        sizes = result_df['建议仓位比例'].tolist()
        
        # 只显示前8个频道，其余归为"其他"
        if len(channels) > 8:
            other_size = sum(sizes[7:])
            channels = channels[:7] + ['其他']
            sizes = sizes[:7] + [other_size]
        
        ax1.pie(sizes, labels=channels, autopct='%1.1f%%', startangle=90, shadow=True)
        ax1.set_title('建议仓位分配比例')
        
        # 2. 胜率和收益率对比柱状图
        top_channels = result_df.head(8)
        x = np.arange(len(top_channels))
        width = 0.35
        
        ax2.bar(x - width/2, top_channels['胜率(%)'], width, label='胜率(%)')
        ax2.bar(x + width/2, top_channels['平均收益(%)'], width, label='平均收益(%)')
        
        ax2.set_xticks(x)
        ax2.set_xticklabels(top_channels['频道'], rotation=45)
        ax2.legend()
        ax2.set_title('顶级频道胜率与收益率对比')
        
        plt.tight_layout()
        plt.savefig('仓位分配策略.png', dpi=300, bbox_inches='tight')
        plt.close()
        
        print("可视化结果已保存至'仓位分配策略.png'")
    
    def generate_strategy(self, output_file='跟单策略.xlsx'):
        """生成完整的仓位分配策略并保存到Excel"""
        # 使用默认参数计算仓位权重
        result_df = self.calculate_position_weights()
        
        if len(result_df) > 0:
            # 保存结果到Excel
            result_df.to_excel(output_file, index=False)
            print(f"跟单策略已保存至'{output_file}'")
            
            # 生成可视化结果
            self.visualize_strategy(result_df)
            
            # 打印结果摘要
            print("\n========= 跟单策略摘要 =========")
            print(f"分析了 {len(self.stats_df)} 个频道，{len(result_df)} 个频道符合跟单要求")
            print("\n推荐优先跟单的前3个频道:")
            for i, row in result_df.head(3).iterrows():
                print(f"{i+1}. {row['频道']}: 建议仓位 {row['建议仓位比例']}%, 胜率 {row['胜率(%)']}%, 平均收益 {row['平均收益(%)']}%")
                print(f"   {row['跟单建议']}")
            print("\n=============================")
            
            return result_df
        else:
            print("无法生成策略：没有符合要求的频道")
            return None
    
    def adaptive_position_sizing(self, channel, capital, risk_tolerance='中'):
        """
        计算特定频道的自适应仓位大小
        
        参数:
            channel: 频道名称
            capital: 总资本
            risk_tolerance: 风险承受能力 ('低', '中', '高')
            
        返回:
            建议的仓位大小
        """
        result_df = self.calculate_position_weights()
        if len(result_df) == 0 or channel not in result_df['频道'].values:
            print(f"警告: 找不到频道 '{channel}' 的数据或没有符合条件的频道")
            return 0
            
        # 获取该频道的数据
        channel_data = result_df[result_df['频道'] == channel].iloc[0]
        
        # 基础仓位比例
        base_position_pct = channel_data['建议仓位比例'] / 100
        
        # 根据风险承受能力调整仓位
        risk_multiplier = {
            '低': 0.5,  # 保守
            '中': 1.0,  # 标准
            '高': 1.5   # 激进
        }.get(risk_tolerance, 1.0)
        
        # 计算建议仓位金额
        position_size = capital * base_position_pct * risk_multiplier
        
        # 使用风险系数进一步调整 (风险系数越高，实际仓位越小)
        position_size = position_size * (1 - (channel_data['风险系数'] - 0.5) / 0.5 * 0.3)
        
        return round(position_size, 2)

if __name__ == "__main__":
    try:
        # 创建策略实例
        strategy = PositionAllocationStrategy()
        
        # 生成跟单策略
        result = strategy.generate_strategy()
        
        # 示例: 计算特定频道的自适应仓位
        if result is not None and len(result) > 0:
            top_channel = result.iloc[0]['频道']
            capital = 10000  # 假设总资金10000
            
            print(f"\n自适应仓位示例 (总资金: {capital}):")
            print(f"低风险: {strategy.adaptive_position_sizing(top_channel, capital, '低')} (保守)")
            print(f"中风险: {strategy.adaptive_position_sizing(top_channel, capital, '中')} (标准)")
            print(f"高风险: {strategy.adaptive_position_sizing(top_channel, capital, '高')} (激进)")
            
    except Exception as e:
        print(f"生成策略时出错: {e}") 