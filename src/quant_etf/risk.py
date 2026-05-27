from enum import Enum
import pandas as pd
import numpy as np
from loguru import logger
from dataclasses import dataclass

from quant_etf.bar_interval import get_interval, bars_for_days, DEFAULT_INTERVAL


class RiskLevel(Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"  # 严重风险，建议清仓


@dataclass
class RiskStatus:
    level: RiskLevel
    reason: str
    suggested_action: str  # "KEEP", "REDUCE", "CLEAR"


class RiskManager:
    def __init__(self, bar_interval: str = DEFAULT_INTERVAL):
        self._bar_interval = get_interval(bar_interval)
        self._bpd = self._bar_interval.bars_per_day
        self.high_percentile_threshold = 0.85
        self.ma_fast = bars_for_days(20, self._bar_interval)
        self.ma_slow = bars_for_days(60, self._bar_interval)
        self.rsi_period = bars_for_days(14, self._bar_interval)
        self.rsi_threshold = 80
        self.percentile_lookback = bars_for_days(250, self._bar_interval)

    def calculate_rsi(self, series: pd.Series, period: int = 14) -> float:
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.iloc[-1] if not np.isnan(rsi.iloc[-1]) else 50.0

    def check_risk(self, df: pd.DataFrame) -> RiskStatus:
        min_bars = min(bars_for_days(60, self._bar_interval), len(df))
        if df.empty or len(df) < min_bars:
            return RiskStatus(RiskLevel.NORMAL, "Insufficient data", "KEEP")

        close_prices = df["close"]
        current_price = close_prices.iloc[-1]

        lookback_window = min(len(df), self.percentile_lookback)
        recent_history = close_prices.iloc[-lookback_window:]
        percentile = (recent_history < current_price).mean()

        is_high_position = percentile > self.high_percentile_threshold

        rsi = self.calculate_rsi(close_prices, self.rsi_period)
        is_overbought = rsi > self.rsi_threshold

        ma20 = close_prices.rolling(window=self.ma_fast).mean()

        is_below_ma20 = current_price < ma20.iloc[-1]

        if (is_high_position or is_overbought) and is_below_ma20:
            reason = []
            if is_high_position:
                reason.append(f"High Percentile ({percentile:.2%})")
            if is_overbought:
                reason.append(f"High RSI ({rsi:.2f})")
            reason.append("Breaking MA20")

            return RiskStatus(
                level=RiskLevel.CRITICAL,
                reason=", ".join(reason),
                suggested_action="CLEAR",
            )

        if is_high_position or is_overbought:
            return RiskStatus(
                level=RiskLevel.WARNING,
                reason=f"High Position (Pct: {percentile:.2%}, RSI: {rsi:.2f})",
                suggested_action="REDUCE",
            )

        return RiskStatus(RiskLevel.NORMAL, "Normal", "KEEP")
