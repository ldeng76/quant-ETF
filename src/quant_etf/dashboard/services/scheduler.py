"""
定时任务调度管理
使用 asyncio.create_task 实现轻量调度
"""
import asyncio
from datetime import datetime, time
from loguru import logger

from .strategy_runner import run_strategy, get_task_status
from .sse_manager import sse_manager
from ..db import query, execute


def is_in_trading_window() -> bool:
    """
    判断当前是否在交易时段的前后10分钟窗口内
    - 09:20-11:30 (上午预热9:20, 上午收盘11:30)
    - 12:50-15:00 (下午预热12:50, 下午收盘15:00)
    """
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    current = now.time()
    return (time(9, 20) <= current <= time(11, 30) or
            time(12, 50) <= current <= time(15, 0))


class Scheduler:
    def __init__(self):
        self._tasks: dict[int, asyncio.Task] = {}

    async def start_loop(self, schedule_id: int, strategy: str, interval: int, bar_interval: str = "1d"):
        """启动定时循环"""
        logger.info(f"Starting scheduled loop: {strategy} (every {interval}s, interval={bar_interval})")
        while True:
            try:
                # 检查是否在交易窗口内
                if not is_in_trading_window():
                    logger.debug(f"Outside trading window, skipping run: {strategy}")
                    await asyncio.sleep(interval)
                    continue

                logger.info(f"Scheduled run: {strategy} (every {interval}s, interval={bar_interval})")
                run_id = f"sched_{schedule_id}_{datetime.now().timestamp()}"
                await run_strategy(strategy, run_id, bar_interval=bar_interval)

                # 更新最后运行时间
                execute(
                    "UPDATE schedules SET last_run_at = CURRENT_TIMESTAMP WHERE id = %s",
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
        schedules = query("SELECT * FROM schedules WHERE enabled = TRUE")
        for s in schedules:
            if s["id"] not in self._tasks:
                bar_interval = s.get("bar_interval", "1d")
                task = asyncio.create_task(
                    self.start_loop(s["id"], s["strategy"], s["interval"], bar_interval)
                )
                self._tasks[s["id"]] = task
                logger.info(f"Scheduler started: {s['strategy']} (id={s['id']}, interval={bar_interval})")

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