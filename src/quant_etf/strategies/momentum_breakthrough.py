"""
动量突破策略（均线突破）

使用10/20/30均线系统，捕捉均线金叉和价格突破信号
"""

import pandas as pd
import numpy as np
from loguru import logger
from typing import Optional, List
from dataclasses import dataclass
from enum import Enum

from quant_etf.market_analyzer import MarketType


class SignalType(Enum):
    """信号类型"""

    LONG = "做多"
    SHORT = "做空"
    NEUTRAL = "中性"


@dataclass
class StrategySignal:
    """策略信号数据类"""

    code: str
    strategy_name: str
    signal_type: SignalType
    direction: str
    score: float
    entry_price: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    reason: str
    ma10: float
    ma20: float
    ma30: float
    ma10_prev: float
    ma20_prev: float
    ma30_prev: float


class MomentumBreakthroughStrategy:
    """动量突破策略（均线突破）"""

    def __init__(self, ma_short: int = 10, ma_mid: int = 20, ma_long: int = 30):
        """
        初始化策略
        :param ma_short: 短期均线周期
        :param ma_mid: 中期均线周期
        :param ma_long: 长期均线周期
        """
        self.ma_short = ma_short
        self.ma_mid = ma_mid
        self.ma_long = ma_long
        self.strategy_name = "动量突破策略"

    def calculate_mas(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        计算均线
        """
        df = df.copy()
        df[f"ma{self.ma_short}"] = df["close"].rolling(window=self.ma_short).mean()
        df[f"ma{self.ma_mid}"] = df["close"].rolling(window=self.ma_mid).mean()
        df[f"ma{self.ma_long}"] = df["close"].rolling(window=self.ma_long).mean()
        return df

    def analyze(self, code: str, df: pd.DataFrame) -> Optional[StrategySignal]:
        """
        分析单个ETF
        :param code: ETF代码
        :param df: 15分钟K线数据
        :return: StrategySignal或None
        """
        if df.empty or len(df) < self.ma_long + 5:
            return None

        df = self.calculate_mas(df)

        ma_col_short = f"ma{self.ma_short}"
        ma_col_mid = f"ma{self.ma_mid}"
        ma_col_long = f"ma{self.ma_long}"

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        ma10 = latest[ma_col_short]
        ma20 = latest[ma_col_mid]
        ma30 = latest[ma_col_long]
        ma10_prev = prev[ma_col_short]
        ma20_prev = prev[ma_col_mid]
        ma30_prev = prev[ma_col_long]

        current_close = latest["close"]
        prev_close = prev["close"]

        ma10_above_ma20 = ma10 > ma20
        ma20_above_ma30 = ma20 > ma30
        ma10_prev_above_ma20_prev = ma10_prev > ma20_prev

        golden_cross = (not ma10_prev_above_ma20_prev) and ma10_above_ma20

        price_above_ma10 = current_close > ma10

        if golden_cross and price_above_ma10 and ma20_above_ma30:
            score = 0.8 + 0.2 * (ma10 - ma20) / ma20

            ma_slope = (ma10 - ma10_prev) / ma10_prev if ma10_prev > 0 else 0
            if ma_slope > 0:
                score += 0.1

            reason = f"MA{self.ma_short}上穿MA{self.ma_mid}形成金叉，价格站上MA{self.ma_short}，均线多头排列"

            return StrategySignal(
                code=code,
                strategy_name=self.strategy_name,
                signal_type=SignalType.LONG,
                direction="buy",
                score=min(score, 1.0),
                entry_price=current_close,
                stop_loss=ma20,
                take_profit=None,
                reason=reason,
                ma10=ma10,
                ma20=ma20,
                ma30=ma30,
                ma10_prev=ma10_prev,
                ma20_prev=ma20_prev,
                ma30_prev=ma30_prev,
            )

        if ma10_above_ma20 and ma20_above_ma30 and price_above_ma10:
            ma10_up = (ma10 - ma10_prev) / ma10_prev > 0.001
            ma20_up = (ma20 - ma20_prev) / ma20_prev > 0.0005

            if ma10_up and ma20_up:
                score = 0.7 + 0.2 * (ma10 - ma20) / ma20

                reason = f"均线多头排列且向上发散，价格在MA{self.ma_short}之上"

                return StrategySignal(
                    code=code,
                    strategy_name=self.strategy_name,
                    signal_type=SignalType.LONG,
                    direction="buy",
                    score=min(score, 1.0),
                    entry_price=current_close,
                    stop_loss=ma20,
                    take_profit=None,
                    reason=reason,
                    ma10=ma10,
                    ma20=ma20,
                    ma30=ma30,
                    ma10_prev=ma10_prev,
                    ma20_prev=ma20_prev,
                    ma30_prev=ma30_prev,
                )

        return None

    def analyze_pool(self, pool_data: dict[str, pd.DataFrame]) -> List[StrategySignal]:
        """
        分析ETF池
        :param pool_data: {code: DataFrame} 字典
        :return: 信号列表
        """
        signals = []
        for code, df in pool_data.items():
            try:
                signal = self.analyze(code, df)
                if signal:
                    signals.append(signal)
            except Exception as e:
                logger.error(f"Failed to analyze {code}: {e}")

        signals.sort(key=lambda x: x.score, reverse=True)
        return signals

    def is_suitable_for_market(self, market_type: MarketType) -> bool:
        """
        判断策略是否适合当前市场
        """
        return market_type in [MarketType.BULL, MarketType.RANGE]
