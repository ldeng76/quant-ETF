"""
分钟K线交易时段自动采集服务

在 Dashboard 启动时检查是否在交易时段窗口内,启动后台采集循环。
遵循 IS_PRIMARY 多节点机制。
"""
import threading
from datetime import datetime, time, timedelta

from loguru import logger


# 交易时段窗口(前后 5 分钟缓冲)
TRADING_WINDOWS = [
    (time(9, 25), time(11, 35)),   # 上午窗口:09:25-11:35
    (time(12, 55), time(15, 5)),   # 下午窗口:12:55-15:05
]

# 采集参数(硬编码,后续可扩展为可配置)
COLLECT_COUNT = 50       # 每次采集 K 线条数
COLLECT_INTERVAL = 60    # 采集间隔秒数


def is_in_trading_window(now: datetime | None = None) -> bool:
    """
    判断当前时间是否在交易时段窗口内。

    :param now: 当前时间,None 则使用 datetime.now()
    :return: 是否在窗口内
    """
    if now is None:
        now = datetime.now()
    current = now.time()

    for start, end in TRADING_WINDOWS:
        if start <= current <= end:
            return True
    return False


def calc_wait_seconds(now: datetime | None = None) -> int:
    """
    计算到下一交易窗口开始的等待秒数。

    :param now: 当前时间,None 则使用 datetime.now()
    :return: 等待秒数,0 表示已在窗口内
    """
    if now is None:
        now = datetime.now()
    current = now.time()

    # 已在窗口内
    if is_in_trading_window(now):
        return 0

    # 计算到下一窗口的等待时间
    if current < time(9, 25):
        # 等待上午窗口
        target = datetime.combine(now.date(), time(9, 25))
        return int((target - now).total_seconds())

    elif current < time(12, 55):
        # 等待下午窗口
        target = datetime.combine(now.date(), time(12, 55))
        return int((target - now).total_seconds())

    else:
        # 已过下午窗口,等待次日上午窗口
        next_day = now.date() + timedelta(days=1)
        target = datetime.combine(next_day, time(9, 25))
        return int((target - now).total_seconds())


def get_next_window_name(now: datetime | None = None) -> str:
    """
    获取下一窗口名称(用于日志)。

    :param now: 当前时间
    :return: 窗口名称 "上午" / "下午"
    """
    if now is None:
        now = datetime.now()
    current = now.time()

    if is_in_trading_window(now):
        return "当前窗口"
    elif current < time(9, 25):
        return "上午"
    elif current < time(12, 55):
        return "下午"
    else:
        return "次日上午"


class MinuteCollectorService:
    """
    分钟K线交易时段采集服务。

    在交易时段窗口内每分钟采集 ETF_POOL 的分钟K线数据。
    """

    def __init__(self):
        self._stop_event = threading.Event()
        self._timer: threading.Timer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """
        启动采集服务。

        检查 IS_PRIMARY，若非主节点则跳过。
        若在窗口内立即开始采集，否则等待下一窗口。
        """
        from quant_etf.dashboard.config import IS_PRIMARY

        if not IS_PRIMARY:
            logger.debug("minute_collector_service: skipped (not primary)")
            return

        self._stop_event.clear()
        logger.info("minute_collector_service: starting (IS_PRIMARY=True)")

        wait_seconds = calc_wait_seconds()

        if wait_seconds == 0:
            # 已在窗口内，立即开始
            logger.info("minute_collector_service: in window, starting collect loop")
            self._start_collect_thread()
        else:
            # 等待下一窗口
            next_window = get_next_window_name()
            logger.info(
                f"minute_collector_service: waiting for next window ({next_window}), "
                f"wait {wait_seconds}s"
            )
            self._timer = threading.Timer(wait_seconds, self._on_window_start)
            self._timer.daemon = True
            self._timer.start()

    def _on_window_start(self) -> None:
        """Timer 触发：窗口开始时的回调"""
        if self._stop_event.is_set():
            return
        logger.info("minute_collector_service: window started, beginning collect loop")
        self._start_collect_thread()

    def _start_collect_thread(self) -> None:
        """启动采集线程"""
        self._thread = threading.Thread(
            target=self._collect_loop,
            daemon=True,
            name="minute-collector"
        )
        self._thread.start()

    def _collect_loop(self) -> None:
        """采集循环：在窗口内每 60 秒采集一次"""
        from quant_etf.conf import ETF_POOL
        from quant_etf.minute_collector import (
            get_minute_bars,
            get_latest_minute_time,
            save_minute_data_from_dicts,
        )

        while not self._stop_event.is_set() and is_in_trading_window():
            collected_count = 0
            new_bars_count = 0

            for code in ETF_POOL:
                try:
                    data = get_minute_bars(code, count=COLLECT_COUNT)
                    if not data:
                        continue

                    # 过滤已存在的记录
                    latest = get_latest_minute_time(code)
                    if latest:
                        filtered = [b for b in data if b.get("time") and b["time"] > latest]
                    else:
                        filtered = [b for b in data if b.get("time")]

                    if filtered:
                        saved = save_minute_data_from_dicts(code, filtered)
                        if saved:
                            collected_count += 1
                            new_bars_count += len(filtered)
                            logger.debug(
                                f"minute_collector_service: collected {code} - "
                                f"{len(filtered)} new bars"
                            )
                except Exception as e:
                    logger.warning(f"minute_collector_service: collect {code} failed: {e}")

            if collected_count > 0:
                logger.info(
                    f"minute_collector_service: round complete - "
                    f"{collected_count}/{len(ETF_POOL)} codes, {new_bars_count} new bars"
                )

            # 等待下一轮
            if not self._stop_event.is_set() and is_in_trading_window():
                self._timer = threading.Timer(COLLECT_INTERVAL, self._collect_loop)
                self._timer.daemon = True
                self._timer.start()
                break  # 退出当前循环，由 Timer 触发下一轮

        # 窗口结束，调度下一窗口
        if not self._stop_event.is_set():
            self._schedule_next_window()

    def _schedule_next_window(self) -> None:
        """窗口结束后调度下一窗口"""
        wait_seconds = calc_wait_seconds()
        next_window = get_next_window_name()

        logger.info(
            f"minute_collector_service: window ended, "
            f"scheduling next ({next_window}), wait {wait_seconds}s"
        )

        self._timer = threading.Timer(wait_seconds, self._on_window_start)
        self._timer.daemon = True
        self._timer.start()

    def stop(self) -> None:
        """停止服务（用于测试）"""
        self._stop_event.set()
        if self._timer:
            self._timer.cancel()
            self._timer = None
        logger.info("minute_collector_service: stopped")


# 单例实例
_service: MinuteCollectorService | None = None


def start_minute_collector_service() -> None:
    """
    启动分钟采集服务的对外入口。

    在 Dashboard 启动时调用。
    """
    global _service
    if _service is None:
        _service = MinuteCollectorService()
    _service.start()


def stop_minute_collector_service() -> None:
    """停止服务（用于测试）"""
    global _service
    if _service:
        _service.stop()
