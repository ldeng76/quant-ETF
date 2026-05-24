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

    async def subscribe(self) -> AsyncGenerator[str, None]:
        """订阅 SSE 事件流"""
        queue: asyncio.Queue = asyncio.Queue()
        self._queues.add(queue)
        try:
            # 发送初始连接事件
            yield f"data: {json.dumps({'type': 'connected', 'message': 'SSE connected'})}\n\n"
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    # 心跳保持连接
                    yield f": heartbeat\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            self._queues.discard(queue)
            logger.debug("SSE client disconnected")

    async def broadcast(self, data: dict):
        """广播事件到所有订阅者"""
        for queue in self._queues.copy():
            try:
                await queue.put(data)
            except Exception:
                self._queues.discard(queue)


# 全局单例
sse_manager = SSEManager()
