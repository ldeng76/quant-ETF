"""
量价配合策略（量能突破）

捕捉成交量突破配合价格上涨的信号
"""

import pandas as pd
import numpy as np
from loguru import logger
from typing import Optional, List

from quant_etf.market_analyzer import MarketType
from quant_etf.strategies.momentum_breakthrough import SignalType, StrategySignal


class VolumePriceStrategy:
    """量价配合策略（量能突破）"""

    def __init__(
        self,
        volume_ma_short: int = 20,
        volume_ma_long: int = 60,
        volume_threshold: float = 1.5,
    ):
        """
        初始化策略
        :param volume_ma_short: 短期成交量均线周期
        :param volume_ma_long: 长期成交量均线周期
        :param volume_threshold: 量能突破阈值
        """
        self.volume_ma_short = volume_ma_short
        self.volume_ma_long = volume_ma_long
        self.volume_threshold = volume_threshold
        self.strategy_name = "量价配合策略"

    def calculate_volume_mas(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        计算成交量均线
        """
        df = df.copy()
        df[f"vol_ma{self.volume_ma_short}"] = (
            df["volume"].rolling(window=self.volume_ma_short).mean()
        )
        df[f"vol_ma{self.volume_ma_long}"] = (
            df["volume"].rolling(window=self.volume_ma_long).mean()
        )
        return df

    def analyze(self, code: str, df: pd.DataFrame) -> Optional[StrategySignal]:
        """
        分析单个ETF
        :param code: ETF代码
        :param df: 15分钟K线数据
        :return: StrategySignal或None
        """
        if df.empty or len(df) < self.volume_ma_long + 5:
            return None

        df = self.calculate_volume_mas(df)

        vol_ma_short_col = f"vol_ma{self.volume_ma_short}"
        vol_ma_long_col = f"vol_ma{self.volume_ma_long}"

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        current_volume = latest["volume"]
        current_close = latest["close"]
        prev_close = prev["close"]

        vol_ma_short = latest[vol_ma_short_col]
        vol_ma_long = latest[vol_ma_long_col]

        vol_ma_short_prev = prev[vol_ma_short_col]

        price_change = (
            (current_close - prev_close) / prev_close if prev_close > 0 else 0
        )

        vol_ratio_short = current_volume / vol_ma_short if vol_ma_short > 0 else 0
        vol_ratio_long = current_volume / vol_ma_long if vol_ma_long > 0 else 0

        volume_breakthrough = (
            vol_ratio_short >= self.volume_threshold
            or vol_ratio_long >= self.volume_threshold
        )

        ma10 = df["close"].rolling(window=10).mean().iloc[-1]
        ma20 = df["close"].rolling(window=20).mean().iloc[-1]
        ma30 = df["close"].rolling(window=30).mean().iloc[-1]

        ma10_prev = df["close"].rolling(window=10).mean().iloc[-2]
        ma20_prev = df["close"].rolling(window=20).mean().iloc[-2]

        price_above_ma = current_close > ma10

        if volume_breakthrough and price_change > 0.005 and price_above_ma:
            score = (
                0.6
                + 0.3 * min(vol_ratio_short - 1, 1.0)
                + 0.1 * min(price_change * 10, 1.0)
            )

            ma10_above_ma20 = ma10 > ma20
            ma20_above_ma30 = ma20 > ma30

            if ma10_above_ma20 and ma20_above_ma30:
                score += 0.1
                reason = f"量能突破（量比{vol_ratio_short:.2f}），价格上涨{price_change * 100:.2f}%，均线多头排列"
            else:
                reason = f"量能突破（量比{vol_ratio_short:.2f}），价格上涨{price_change * 100:.2f}%，价格站上MA10"

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
                ma30_prev=0,
            )

        if volume_breakthrough and price_change > 0 and price_above_ma:
            vol_ma_rising = vol_ma_short > vol_ma_short_prev

            if vol_ma_rising:
                score = (
                    0.5
                    + 0.2 * min(vol_ratio_short - 1, 1.0)
                    + 0.1 * min(price_change * 10, 1.0)
                )

                reason = f"量能放大且均量上升（量比{vol_ratio_short:.2f}），价格上涨{price_change * 100:.2f}%"

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
                    ma30_prev=0,
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
