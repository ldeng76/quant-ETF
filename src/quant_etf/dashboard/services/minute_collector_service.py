"""
分钟K线交易时段自动采集服务

在 Dashboard 启动时检查是否在交易时段窗口内,启动后台采集循环。
遵循 IS_PRIMARY 多节点机制。
"""
from datetime import datetime, time, timedelta


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
