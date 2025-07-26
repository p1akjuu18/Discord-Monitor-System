#!/usr/bin/env python
# -*- coding: utf-8 -*-

# 在这里设置你的 API key
API_KEY = "CG-ehs2qYuohPUSjPVArq8rGyWV"

import aiohttp
import logging
from typing import Optional, Dict, List, Any
import asyncio
from datetime import datetime
import requests
import pandas as pd
import time
import os

# 设置日志
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class CoinGeckoAPI:
    def __init__(self, api_key: str, base_url: str = "https://pro-api.coingecko.com/api/v3/onchain/dex"):
        self.api_key = api_key
        self.base_url = base_url
        self.headers = {
            "x-cg-pro-api-key": api_key,
            "Content-Type": "application/json"
        }
        
    async def _make_request(self, endpoint: str, params: Optional[Dict] = None) -> Dict:
        """发送API请求的通用方法"""
        url = f"{self.base_url}{endpoint}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=self.headers,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"API请求失败: {response.status}, URL: {url}, 错误: {error_text}")
                        return {"error": f"API请求失败: {response.status}", "details": error_text}
                    
                    return await response.json()
                    
        except Exception as e:
            logger.error(f"请求失败: {str(e)}, URL: {url}")
            return {"error": f"请求异常: {str(e)}"}

    async def get_token_pools(self, network: str, token_address: str) -> Dict:
        """
        获取特定网络上代币的流动性池信息
        
        参数:
            network (str): 网络名称，如 'ethereum', 'binance-smart-chain' 等
            token_address (str): 代币合约地址
            
        返回:
            Dict: 包含代币池信息的字典
        """
        endpoint = f"/networks/{network}/tokens/{token_address}/pools"
        logger.info(f"正在获取 {network} 网络上代币 {token_address} 的池信息")
        
        return await self._make_request(endpoint)

    async def get_token_info(self, network: str, token_address: str) -> Dict:
        """
        获取代币基本信息
        
        参数:
            network (str): 网络名称
            token_address (str): 代币合约地址
            
        返回:
            Dict: 包含代币信息的字典
        """
        endpoint = f"/onchain/networks/{network}/tokens/{token_address}"
        return await self._make_request(endpoint)

    async def get_token_market_chart(self, network: str, token_address: str, vs_currency: str = "usd", days: str = "1") -> Dict:
        """
        获取代币市场数据
        
        参数:
            network (str): 网络名称
            token_address (str): 代币合约地址
            vs_currency (str): 计价货币，默认 usd
            days (str): 数据天数，默认 1 天
            
        返回:
            Dict: 包含市场数据的字典
        """
        endpoint = f"/onchain/networks/{network}/tokens/{token_address}/market_chart"
        params = {
            "vs_currency": vs_currency,
            "days": days
        }
        return await self._make_request(endpoint, params)

    async def get_token_holders(self, network: str, token_address: str) -> Dict:
        """
        获取代币持有者信息
        
        参数:
            network (str): 网络名称
            token_address (str): 代币合约地址
            
        返回:
            Dict: 包含持有者信息的字典
        """
        endpoint = f"/onchain/networks/{network}/tokens/{token_address}/holders"
        return await self._make_request(endpoint)

    @staticmethod
    def get_supported_networks() -> List[str]:
        """获取支持的网络列表"""
        return [
            "ethereum",
            "binance-smart-chain",
            "polygon-pos",
            "fantom",
            "avalanche",
            "arbitrum-one",
            "optimism",
            "base",
            "kava"
        ]

    def is_supported_network(self, network: str) -> bool:
        """检查网络是否支持"""
        return network.lower() in self.get_supported_networks()

def clean_text(text, max_length=32000):
    """
    清理文本数据，移除特殊字符并限制长度
    """
    if not isinstance(text, str):
        return ''
    # 移除可能导致Excel保存问题的字符
    text = text.replace('\r', ' ').replace('\n', ' ').replace('\t', ' ')
    # 限制文本长度
    return text[:max_length] if len(text) > max_length else text

def clean_list(lst, max_length=1000):
    """
    清理列表数据，将其转换为字符串并限制长度
    """
    if not lst:
        return ''
    text = ', '.join(str(item) for item in lst if item)
    return clean_text(text, max_length)

async def process_token_info(start_index=0, batch_size=10):
    """
    批量处理Excel文件中的代币地址并获取信息
    
    Args:
        start_index: 开始处理的索引
        batch_size: 每批处理的数量
    """
    try:
        # 获取data目录路径
        data_dir = os.path.join(os.getcwd(), 'data')
        
        # 直接读取meme.xlsx文件
        input_file_path = os.path.join(data_dir, 'meme.xlsx')
        if not os.path.exists(input_file_path):
            logger.error("未找到meme.xlsx文件")
            return
            
        # 获取当前时间戳
        current_time = time.strftime('%Y%m%d_%H%M%S')
        output_excel_path = os.path.join(data_dir, f'token_info_basic_{current_time}.xlsx')
        
        # 读取Excel文件
        logger.info(f"正在读取文件: {input_file_path}")
        df = pd.read_excel(input_file_path)
        
        logger.info("Excel文件中的列名: %s", df.columns.tolist())
        logger.info(f"总行数: {len(df)}")
        logger.info(f"将从第 {start_index} 个代币开始处理...")
        
        # 添加新列
        if 'description' not in df.columns:
            df['description'] = ''
        if 'websites' not in df.columns:
            df['websites'] = ''
        if 'twitter_handle' not in df.columns:
            df['twitter_handle'] = ''
        if 'telegram_handle' not in df.columns:
            df['telegram_handle'] = ''
        
        # 初始化API客户端，使用直接定义的 API_KEY
        client = CoinGeckoAPI(API_KEY)
        
        # 用于记录上次保存的时间
        last_save_time = time.time()
        modified = False
        
        # 统计计数器
        processed_count = 0
        success_count = 0
        error_count = 0
        
        # 遍历每个代币
        for index, row in df.iloc[start_index:].iterrows():
            try:
                token_address = row['内容']
                channel_id = row['频道ID']
                time_stamp = row['时间']
                
                if pd.isna(token_address):
                    logger.warning(f"跳过空地址，索引 {index}")
                    continue
                
                processed_count += 1
                logger.info(f"正在获取索引 {index} ({processed_count}/{len(df)}) 的基本信息...")
                
                # 清理和验证token地址
                if isinstance(token_address, str):
                    # 移除可能的空格和特殊字符
                    token_address = token_address.strip()
                    # 验证Solana地址长度（通常是32-44个字符）
                    if len(token_address) < 32 or len(token_address) > 44:
                        logger.warning(f"跳过无效的Solana地址格式: {token_address}")
                        continue
                
                # 获取代币信息
                token_info = await client.get_token_info("solana", token_address)
                
                if token_info and 'data' in token_info and 'attributes' in token_info['data']:
                    # 清理并保存数据
                    df.at[index, 'description'] = clean_text(token_info['data']['attributes'].get('description', ''))
                    df.at[index, 'websites'] = clean_list(token_info['data']['attributes'].get('websites', []))
                    df.at[index, 'twitter_handle'] = clean_text(token_info['data']['attributes'].get('twitter_handle', ''))
                    df.at[index, 'telegram_handle'] = clean_text(token_info['data']['attributes'].get('telegram_handle', ''))
                    modified = True
                    success_count += 1
                    logger.info(f"已保存代币 {token_address} 的信息")
                else:
                    error_count += 1
                    logger.error(f"无法获取 {token_address} 的基本信息")
                
                # 每处理batch_size条数据或者经过60秒就保存一次
                current_time = time.time()
                if modified and (processed_count % batch_size == 0 or current_time - last_save_time >= 60):
                    try:
                        logger.info(f"准备保存进度，已处理 {processed_count} 条数据...")
                        # 创建临时文件
                        temp_file = output_excel_path.replace('.xlsx', '_temp.xlsx')
                        df.to_excel(temp_file, index=False)
                        # 如果临时文件创建成功，则替换原文件
                        if os.path.exists(output_excel_path):
                            os.remove(output_excel_path)
                        os.rename(temp_file, output_excel_path)
                        logger.info(f"已保存当前进度到: {output_excel_path}")
                        last_save_time = current_time
                        modified = False
                    except Exception as save_error:
                        logger.error(f"保存文件时出错: {str(save_error)}")
                
                time.sleep(1)  # 添加延迟以避免触发API限制
                
            except Exception as e:
                error_count += 1
                logger.error(f"处理代币 {token_address} 时出错: {str(e)}")
                continue
        
        # 最后保存一次
        if modified:
            try:
                temp_file = output_excel_path.replace('.xlsx', '_temp.xlsx')
                df.to_excel(temp_file, index=False)
                if os.path.exists(output_excel_path):
                    os.remove(output_excel_path)
                os.rename(temp_file, output_excel_path)
                logger.info("最终数据已保存")
            except Exception as final_save_error:
                logger.error(f"最终保存文件时出错: {str(final_save_error)}")
        
        # 打印统计信息
        logger.info("\n处理统计信息:")
        logger.info(f"总记录数: {len(df)}")
        logger.info(f"处理记录数: {processed_count}")
        logger.info(f"成功处理数: {success_count}")
        logger.info(f"失败记录数: {error_count}")
        
    except Exception as e:
        logger.error(f"处理过程中出现错误: {str(e)}")

async def main():
    """测试代码"""
    # 使用直接定义的 API_KEY
    api_key = API_KEY
    
    if not api_key:
        logger.error("API密钥未设置")
        return
    
    # 创建API实例
    api = CoinGeckoAPI(api_key)
    
    # 测试获取代币池信息（使用Solana测试代币）
    test_token = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # USDC on Solana
    result = await api.get_token_pools("solana", test_token)
    
    if "error" not in result:
        logger.info(f"成功获取池信息: {len(result.get('pools', []))} 个池")
        # 打印第一个池的信息作为示例
        if result.get('pools'):
            logger.info(f"示例池信息: {result['pools'][0]}")
    else:
        logger.error(f"获取池信息失败: {result['error']}")

if __name__ == "__main__":
    # asyncio.run(main())
    # asyncio.run(process_token_info())  # 改为调用process_token_info函数
    
    # 在Windows上设置事件循环策略
    if os.name == 'nt':  # Windows系统
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    asyncio.run(process_token_info())