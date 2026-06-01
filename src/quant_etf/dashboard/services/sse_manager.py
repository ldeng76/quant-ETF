"""
SSE (Server-Sent Events) 连接管理与事件广播
"""
import asyncio
import json
from typing import AsyncGenerator
from loguru import logger


class SSEManager:
    def __init__(self):
        self._queues: set[asyncio.Queue] = set()
        self._tasks: set[asyncio.Task] = set()  # 跟踪活跃的 SSE 生成器任务

    async def subscribe(self) -> AsyncGenerator[str, None]:
        """订阅 SSE 事件流"""
        queue: asyncio.Queue = asyncio.Queue()
        self._queues.add(queue)
        # 记录当前任务，以便 shutdown 时强制取消
        task = asyncio.current_task()
        if task:
            self._tasks.add(task)
        try:
            # 发送初始连接事件
            yield f"data: {json.dumps({'type': 'connected', 'message': 'SSE connected'})}\n\n"
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30)
                    if data is None:  # shutdown sentinel
                        break
                    yield f"data: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    # 心跳保持连接
                    yield f": heartbeat\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            self._queues.discard(queue)
            if task:
                self._tasks.discard(task)
            logger.debug("SSE client disconnected")

    async def broadcast(self, data: dict):
        """广播事件到所有订阅者"""
        for queue in self._queues.copy():
            try:
                await queue.put(data)
            except Exception:
                self._queues.discard(queue)

    async def close(self):
        """关闭所有连接（shutdown时调用）"""
        # 1. 发送 sentinel 唤醒阻塞在 queue.get() 上的协程
        for queue in self._queues.copy():
            try:
                await queue.put(None)  # sentinel to unblock waiters
            except Exception:
                self._queues.discard(queue)
        # 2. 取消所有活跃的 SSE 生成器任务
        for task in self._tasks.copy():
            if not task.done():
                task.cancel()
        # 3. 等待取消完成（最多 1 秒）
        if self._tasks:
            await asyncio.wait(self._tasks.copy(), timeout=1.0)
        self._tasks.clear()
        logger.debug("SSE manager closed")


# 全局单例
sse_manager = SSEManager()