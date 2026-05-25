"""
定时任务调度管理
使用 asyncio.create_task 实现轻量调度
"""
import asyncio
from datetime import datetime
from typing import Optional
from loguru import logger

from .strategy_runner import run_strategy, get_task_status
from .sse_manager import sse_manager
from ..db import query, execute


class Scheduler:
    def __init__(self):
        self._tasks: dict[int, asyncio.Task] = {}

    async def start_loop(self, schedule_id: int, strategy: str, interval: int):
        """启动定时循环"""
        logger.info(f"Starting scheduled loop: {strategy} (every {interval}s)")
        while True:
            try:
                logger.info(f"Scheduled run: {strategy} (every {interval}s)")
                run_id = f"sched_{schedule_id}_{datetime.now().timestamp()}"
                await run_strategy(strategy, run_id)

                # 更新最后运行时间
                execute(
                    "UPDATE schedules SET last_run_at = CURRENT_TIMESTAMP WHERE id = ?",
                    [schedule_id]
                )

                # SSE 广播结果
                status = get_task_status(run_id)
                title = status.get("title", strategy) if status else strategy
                await sse_manager.broadcast({
                    "type": "strategy_result",
                    "schedule_id": schedule_id,
                    "strategy": strategy,
                    "strategy_title": title,
                    "run_id": run_id,
                    "timestamp": datetime.now().isoformat(),
                })

            except Exception as e:
                logger.error(f"Scheduled run failed for {strategy}: {e}")
                await sse_manager.broadcast({
                    "type": "strategy_error",
                    "schedule_id": schedule_id,
                    "strategy": strategy,
                    "strategy_title": title,
                    "error": str(e),
                })

            await asyncio.sleep(interval)

    async def start_all(self):
        """启动所有已启用的调度"""
        schedules = query("SELECT * FROM schedules WHERE enabled = 1")
        for s in schedules:
            if s["id"] not in self._tasks:
                task = asyncio.create_task(
                    self.start_loop(s["id"], s["strategy"], s["interval"])
                )
                self._tasks[s["id"]] = task
                logger.info(f"Scheduler started: {s['strategy']} (id={s['id']})")

    async def stop(self, schedule_id: int):
        """停止指定调度"""
        task = self._tasks.pop(schedule_id, None)
        if task:
            task.cancel()
            logger.info(f"Scheduler stopped: id={schedule_id}")

    async def stop_all(self):
        """停止所有调度"""
        for sid in list(self._tasks.keys()):
            await self.stop(sid)

    def is_running(self, schedule_id: int) -> bool:
        return schedule_id in self._tasks and not self._tasks[schedule_id].done()


scheduler = Scheduler()
