# -*- coding: utf-8 -*-
"""
异步工具模块
提供异步文件I/O和其他异步操作的实用工具
"""

import asyncio
import aiofiles
import aiohttp
import json
import os
from typing import Dict, List, Any, Optional
from pathlib import Path
import time
from logger_config import performance_logger


async def async_read_json(file_path: str) -> Optional[Dict]:
    """异步读取JSON文件"""
    start_time = time.time()
    try:
        async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
            content = await f.read()
            data = json.loads(content)
            
        duration = time.time() - start_time
        performance_logger.log_performance('async_read_json', duration, file_path=file_path)
        return data
        
    except FileNotFoundError:
        return None
    except Exception as e:
        duration = time.time() - start_time
        performance_logger.log_performance('async_read_json_error', duration, 
                                         file_path=file_path, error=str(e))
        raise


async def async_write_json(file_path: str, data: Dict, ensure_dir: bool = True) -> bool:
    """异步写入JSON文件"""
    start_time = time.time()
    try:
        if ensure_dir:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        json_str = json.dumps(data, ensure_ascii=False, indent=2)
        
        async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
            await f.write(json_str)
        
        duration = time.time() - start_time
        performance_logger.log_performance('async_write_json', duration, 
                                         file_path=file_path, size=len(json_str))
        return True
        
    except Exception as e:
        duration = time.time() - start_time
        performance_logger.log_performance('async_write_json_error', duration,
                                         file_path=file_path, error=str(e))
        raise


async def async_append_json(file_path: str, data: Dict) -> bool:
    """异步追加JSON数据到文件"""
    start_time = time.time()
    try:
        # 读取现有数据
        existing_data = await async_read_json(file_path) or []
        
        # 如果不是列表，转换为列表
        if not isinstance(existing_data, list):
            existing_data = [existing_data]
        
        # 追加新数据
        existing_data.append(data)
        
        # 写回文件
        await async_write_json(file_path, existing_data)
        
        duration = time.time() - start_time
        performance_logger.log_performance('async_append_json', duration,
                                         file_path=file_path, total_items=len(existing_data))
        return True
        
    except Exception as e:
        duration = time.time() - start_time
        performance_logger.log_performance('async_append_json_error', duration,
                                         file_path=file_path, error=str(e))
        raise


class AsyncFileManager:
    """异步文件管理器"""
    
    def __init__(self, base_dir: str = "data"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(exist_ok=True)
        self._write_queue = asyncio.Queue()
        self._running = False
    
    async def start(self):
        """启动异步文件写入队列处理"""
        if self._running:
            return
        
        self._running = True
        asyncio.create_task(self._process_write_queue())
    
    async def stop(self):
        """停止异步文件写入队列处理"""
        self._running = False
        # 等待队列清空
        await self._write_queue.join()
    
    async def _process_write_queue(self):
        """处理文件写入队列"""
        while self._running:
            try:
                # 等待写入任务
                write_task = await asyncio.wait_for(self._write_queue.get(), timeout=1.0)
                
                # 执行写入
                await write_task['func'](**write_task['kwargs'])
                
                # 标记任务完成
                self._write_queue.task_done()
                
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                performance_logger.log_performance('file_write_queue_error', 0, error=str(e))
                self._write_queue.task_done()
    
    async def queue_write_json(self, file_path: str, data: Dict, **kwargs):
        """将JSON写入操作加入队列"""
        full_path = self.base_dir / file_path
        await self._write_queue.put({
            'func': async_write_json,
            'kwargs': {'file_path': str(full_path), 'data': data, **kwargs}
        })
    
    async def queue_append_json(self, file_path: str, data: Dict, **kwargs):
        """将JSON追加操作加入队列"""
        full_path = self.base_dir / file_path
        await self._write_queue.put({
            'func': async_append_json,
            'kwargs': {'file_path': str(full_path), 'data': data, **kwargs}
        })
    
    async def read_json(self, file_path: str) -> Optional[Dict]:
        """读取JSON文件"""
        full_path = self.base_dir / file_path
        return await async_read_json(str(full_path))


class AsyncHTTPClient:
    """异步HTTP客户端"""
    
    def __init__(self, timeout: int = 30, max_connections: int = 100):
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.connector = aiohttp.TCPConnector(limit=max_connections)
        self.session = None
    
    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            timeout=self.timeout,
            connector=self.connector
        )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    async def get(self, url: str, **kwargs) -> Optional[Dict]:
        """发送GET请求"""
        start_time = time.time()
        try:
            async with self.session.get(url, **kwargs) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    duration = time.time() - start_time
                    performance_logger.log_performance('http_get', duration,
                                                     url=url, status=response.status)
                    return data
                else:
                    duration = time.time() - start_time
                    performance_logger.log_performance('http_get_error', duration,
                                                     url=url, status=response.status)
                    return None
                    
        except Exception as e:
            duration = time.time() - start_time
            performance_logger.log_performance('http_get_exception', duration,
                                             url=url, error=str(e))
            raise
    
    async def post(self, url: str, data: Dict = None, json_data: Dict = None, **kwargs) -> Optional[Dict]:
        """发送POST请求"""
        start_time = time.time()
        try:
            kwargs_copy = kwargs.copy()
            if data:
                kwargs_copy['data'] = data
            if json_data:
                kwargs_copy['json'] = json_data
            
            async with self.session.post(url, **kwargs_copy) as response:
                if response.status in [200, 201]:
                    result = await response.json()
                    
                    duration = time.time() - start_time
                    performance_logger.log_performance('http_post', duration,
                                                     url=url, status=response.status)
                    return result
                else:
                    duration = time.time() - start_time
                    performance_logger.log_performance('http_post_error', duration,
                                                     url=url, status=response.status)
                    return None
                    
        except Exception as e:
            duration = time.time() - start_time
            performance_logger.log_performance('http_post_exception', duration,
                                             url=url, error=str(e))
            raise


async def batch_process(items: List[Any], processor_func, batch_size: int = 10, delay: float = 0.1):
    """批量异步处理"""
    start_time = time.time()
    results = []
    
    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        
        # 并发处理批次
        batch_tasks = [processor_func(item) for item in batch]
        batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)
        
        results.extend(batch_results)
        
        # 批次间延迟
        if delay > 0 and i + batch_size < len(items):
            await asyncio.sleep(delay)
    
    duration = time.time() - start_time
    performance_logger.log_performance('batch_process', duration,
                                     total_items=len(items), batch_size=batch_size)
    
    return results


# 全局异步文件管理器
file_manager = AsyncFileManager()