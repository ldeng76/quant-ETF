"""
定时任务调度管理

采集驱动模式：由 minute_collector_service 在每轮数据采集完成后回调触发。
所有 enabled 调度在每轮采集后统一触发，不区分 bar_interval。
"""
import asyncio
import threading
from concurrent.futures import Future
from datetime import datetime
from loguru import logger

from .strategy_runner import run_strategy, get_task_status
from .sse_manager import sse_manager
from ..db import query, execute


class Scheduler:
    """采集驱动的调度器。

    不再自行维护 sleep 循环，而是由 minute_collector_service
    在每轮5分钟K线采集完成后调用 on_collection_complete() 触发策略重算。
    """

    def __init__(self):
        self._main_loop: asyncio.AbstractEventLoop | None = None
        # schedule_id -> asyncio.Task (在主事件循环上)
        self._running_tasks: dict[int, asyncio.Task] = {}
        self._lock = threading.Lock()

    def set_main_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """注入主事件循环引用（在启动时由 startup_preload 调用）"""
        self._main_loop = loop
        logger.info("Scheduler: main event loop set")

    # ------------------------------------------------------------------
    # 核心回调
    # ------------------------------------------------------------------

    def on_collection_complete(self, now: datetime | None = None) -> None:
        """采集完成回调 — 触发所有 enabled 调度。

        由 minute_collector_service._collect_loop 在每轮采集完毕后调用。
        运行在 collector 线程中，通过 asyncio.run_coroutine_threadsafe
        桥接到主事件循环执行策略。
        """
        if not self._main_loop:
            logger.debug("Scheduler: no main loop set, skipping")
            return

        try:
            schedules = query("SELECT * FROM schedules WHERE enabled = TRUE")
        except Exception as e:
            logger.warning(f"Scheduler: failed to query schedules: {e}")
            return

        if not schedules:
            return

        now = now or datetime.now()
        logger.info(f"Scheduler: collection complete, triggering {len(schedules)} schedule(s)")

        for s in schedules:
            self._trigger_schedule(s, now)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _trigger_schedule(self, schedule: dict, now: datetime) -> None:
        """通过主事件循环触发单个策略执行"""
        sid = schedule["id"]
        strategy = schedule["strategy"]
        bar_interval = schedule.get("bar_interval", "1d")
        run_id = f"sched_{sid}_{now.timestamp()}"

        async def _run():
            try:
                await run_strategy(strategy, run_id, bar_interval=bar_interval)

                # 更新最后运行时间
                execute(
                    "UPDATE schedules SET last_run_at = CURRENT_TIMESTAMP WHERE id = %s",
                    [sid]
                )

                # SSE 广播结果
                status = get_task_status(run_id)
                title = status.get("title", strategy) if status else strategy
                await sse_manager.broadcast({
                    "type": "strategy_result",
                    "schedule_id": sid,
                    "strategy": strategy,
                    "strategy_title": title,
                    "run_id": run_id,
                    "timestamp": datetime.now().isoformat(),
                })
            except Exception as e:
                logger.error(f"Scheduler: run failed for {strategy}: {e}")
                status = get_task_status(run_id)
                title = status.get("title", strategy) if status else strategy
                await sse_manager.broadcast({
                    "type": "strategy_error",
                    "schedule_id": sid,
                    "strategy": strategy,
                    "strategy_title": title,
                    "error": str(e),
                })
            finally:
                with self._lock:
                    self._running_tasks.pop(sid, None)

        try:
            task = asyncio.run_coroutine_threadsafe(_run(), self._main_loop)
            with self._lock:
                self._running_tasks[sid] = task
        except Exception as e:
            logger.error(f"Scheduler: failed to schedule {strategy}: {e}")

    # ------------------------------------------------------------------
    # 状态查询 / 生命周期
    # ------------------------------------------------------------------

    def is_running(self, schedule_id: int) -> bool:
        """检查指定调度是否正在执行"""
        with self._lock:
            task = self._running_tasks.get(schedule_id)
        if task is None:
            return False
        # concurrent.futures.Future
        return not task.done()

    async def stop_all(self) -> None:
        """停止所有正在运行的策略任务（shutdown 时调用）"""
        with self._lock:
            tasks = list(self._running_tasks.values())
            self._running_tasks.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        logger.info(f"Scheduler: stopped {len(tasks)} running task(s)")


# 全局单例
scheduler = Scheduler()
