"""
APScheduler 调度服务入口

启动 4 个后台任务，每 180 秒重算一次策略（1d / 60m / 30m / 15m）。
Windows 兼容：超时控制使用 threading.Timer。
"""
import threading
from datetime import datetime
from loguru import logger

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from quant_etf.scheduler_engine import run_job_for_interval, get_all_codes
from quant_etf.scheduler_db import insert_job_run, update_job_run, get_all_users
from quant_etf.scheduler_cache import get_cache


# ============================================================
# Constants
# ============================================================

JOB_INTERVAL = 180  # seconds
JOB_TIMEOUT = 150  # seconds
ALL_INTERVALS = ("1d", "60m", "30m", "15m")

# 每个周期的独立计时器（避免并发线程竞争）
_timers: dict[str, threading.Timer | None] = {}


# ============================================================
# Job wrapper with timeout
# ============================================================

def _run_job(interval: str) -> None:
    """Job 执行入口：记录 → 超时保护 → 运行 → 更新状态。"""
    started_at = datetime.now()
    logger.info(f"[Scheduler] Job triggered: interval={interval}")

    # 记录 job_runs
    run_id = insert_job_run(interval, started_at)

    # 启动超时保护（每个 interval 独立 Timer，避免并发竞争）
    def timeout_fire() -> None:
        logger.error(f"[Scheduler] Timeout ({JOB_TIMEOUT}s) reached for interval={interval}, exiting...")
        import sys
        sys.exit(1)

    timer = threading.Timer(JOB_TIMEOUT, timeout_fire)
    timer.daemon = True
    timer.start()
    _timers[interval] = timer

    try:
        run_job_for_interval(interval, run_id)
    except Exception as e:
        logger.exception(f"[Scheduler] Job error: interval={interval}: {e}")
        update_job_run(run_id, datetime.now(), "failed", error_msg=str(e))
    finally:
        timer.cancel()
        _timers[interval] = None


# ============================================================
# Scheduler lifecycle
# ============================================================

def start_scheduler() -> BackgroundScheduler:
    """
    启动 APScheduler，注册 4 个定时任务（1d/60m/30m/15m）。
    每个任务按 180 秒间隔执行。
    返回 BackgroundScheduler 实例供调用方持有。
    """
    logger.info("[Scheduler] Initializing APScheduler...")

    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

    for interval in ALL_INTERVALS:
        scheduler.add_job(
            _run_job,
            IntervalTrigger(seconds=JOB_INTERVAL),
            args=[interval],
            id=f"job_{interval}",
            name=f"Strategy Job ({interval})",
            misfire_grace_time=30,
            coalesce=True,
            replace_existing=True,
        )
        logger.info(f"[Scheduler] Registered job: {interval} @ {JOB_INTERVAL}s")

    scheduler.start()
    logger.info(f"[Scheduler] APScheduler started ({len(ALL_INTERVALS)} jobs registered)")

    # 打印初始状态
    try:
        cache = get_cache()
        users = get_all_users()
        codes_1d = get_all_codes("1d")
        logger.info(
            f"[Scheduler] Initial state: "
            f"{len(users)} users, {len(codes_1d)} securities in 1d pool, "
            f"cache size={cache.size}"
        )
    except Exception as e:
        logger.warning(f"[Scheduler] Could not log initial state: {e}")

    return scheduler


def run_scheduler_blocking() -> None:
    """
    阻塞模式启动调度服务（CLI 命令内部使用）。
    收到 KeyboardInterrupt / SystemExit 后优雅关闭。
    """
    scheduler = start_scheduler()
    try:
        while True:
            import time
            time.sleep(10)
    except (KeyboardInterrupt, SystemExit):
        logger.info("[Scheduler] Received shutdown signal, stopping...")
        scheduler.shutdown(wait=False)
        logger.info("[Scheduler] Stopped.")