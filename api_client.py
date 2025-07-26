#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
import aiohttp
from aiohttp_socks import ProxyConnector
import requests
from typing import Optional

logger = logging.getLogger(__name__)

class SiliconFlowClient:
    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = "https://api.siliconflow.com/v1"
        self.session = None
        
    async def _ensure_session(self):
        if self.session is None:
            # 创建带有代理和SSL配置的会话
            connector = ProxyConnector.from_url(
                'http://127.0.0.1:7890',
                ssl=False  # 禁用SSL验证
            )
            
            # 设置超时
            timeout = aiohttp.ClientTimeout(total=300)
            
            self.session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                trust_env=True,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }
            )
    
    async def chat_completion(self, messages, model="deepseek-ai/DeepSeek-V3", **kwargs):
        await self._ensure_session()
        try:
            async with self.session.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    **kwargs
                }
            ) as response:
                return await response.json()
        except Exception as e:
            logger.error(f"API调用出错: {str(e)}")
            return None
            
    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None 

class APIClient:
    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
    
    def get_completion(self, prompt: str, 
                      model: str = "deepseek-ai/DeepSeek-V3",
                      max_tokens: int = 1024) -> Optional[str]:
        """发送请求到API并获取响应"""
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=self.headers,
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens
                }
            )
            
            if response.status_code == 200:
                return response.json()['choices'][0]['message']['content']
            return None
            
        except Exception as e:
            print(f"API请求失败: {e}")
            return None 