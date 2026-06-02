"""
Bar interval configuration for flexible timeframes (1d/5m/15m/30m/60m).
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class BarInterval:
    name: str
    label: str
    pandas_freq: str
    bars_per_day: int
    tdx_category: int
    tdx_category_direct: Optional[int] = None
    is_daily: bool = False

    def unit_label(self, n_bars: int) -> str:
        """返回 n_bars 根 K 线的中文描述"""
        return f"{n_bars}根"


INTERVALS: dict[str, BarInterval] = {
    "1d": BarInterval("1d", "日线", "1D", 1, 9, is_daily=True),
    "5m": BarInterval("5m", "5分钟", "5min", 48, 0, is_daily=False),
    "15m": BarInterval("15m", "15分钟", "15min", 16, 8, tdx_category_direct=1),
    "30m": BarInterval("30m", "30分钟", "30min", 8, 8, tdx_category_direct=2),
    "60m": BarInterval("60m", "60分钟", "60min", 4, 8, tdx_category_direct=3),
}

DEFAULT_INTERVAL = "1d"


def get_interval(name: str) -> BarInterval:
    if name not in INTERVALS:
        raise ValueError(
            f"Unknown bar interval: {name}. Supported: {list(INTERVALS.keys())}"
        )
    return INTERVALS[name]


def bars_for_days(days: int, interval: BarInterval) -> int:
    return days * interval.bars_per_day
