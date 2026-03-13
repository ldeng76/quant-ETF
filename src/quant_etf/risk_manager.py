"""
风险管理模块

技术位止损（前低、均线、趋势线等）
"""

import pandas as pd
import numpy as np
from loguru import logger
from typing import Optional, Tuple
from dataclasses import dataclass

from quant_etf.strategies.momentum_breakthrough import StrategySignal


@dataclass
class RiskLevel:
    """风险级别"""

    stop_loss: float
    take_profit: float
    risk_reward_ratio: float
    confidence: str


class RiskManager:
    """风险管理器"""

    def __init__(self, atr_period: int = 14, risk_ratio: float = 0.02):
        """
        初始化风险管理器
        :param atr_period: ATR周期
        :param risk_ratio: 风险比例（止损占价格的比例）
        """
        self.atr_period = atr_period
        self.risk_ratio = risk_ratio

    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """
        计算ATR（平均真实波幅）
        """
        if df.empty or len(df) < period + 1:
            return 0.0

        df = df.copy()
        df["high_low"] = df["high"] - df["low"]
        df["high_close"] = (df["high"] - df["close"].shift(1)).abs()
        df["low_close"] = (df["low"] - df["close"].shift(1)).abs()

        df["true_range"] = df[["high_low", "high_close", "low_close"]].max(axis=1)

        atr = df["true_range"].rolling(window=period).mean().iloc[-1]
        return float(atr) if np.isfinite(atr) else 0.0

    def find_recent_low(self, df: pd.DataFrame, period: int = 30) -> Tuple[float, int]:
        """
        查找近期最低点
        """
        if df.empty or len(df) < period:
            return 0.0, 0

        recent = df.tail(period)
        min_idx = recent["low"].idxmin()
        min_price = float(recent["low"].min())

        return min_price, min_idx

    def calculate_support_level(
        self, df: pd.DataFrame, entry_price: float
    ) -> Tuple[float, str]:
        """
        计算支撑位
        :return: (支撑位, 支撑类型)
        """
        if df.empty:
            return entry_price * 0.98, "固定比例"

        recent_low, _ = self.find_recent_low(df, 30)
        ma20 = df["close"].rolling(window=20).mean().iloc[-1]
        ma30 = df["close"].rolling(window=30).mean().iloc[-1]

        support_levels = {
            "近期低点": recent_low,
            "MA20": ma20,
            "MA30": ma30,
        }

        valid_levels = {
            k: v for k, v in support_levels.items() if v > 0 and v < entry_price
        }

        if not valid_levels:
            atr = self.calculate_atr(df)
            return entry_price - atr, "ATR"

        lowest_valid = min(valid_levels.values())

        for level_type, level_price in valid_levels.items():
            if level_price == lowest_valid:
                return lowest_valid, level_type

        return lowest_valid, "支撑位"

    def calculate_stop_loss(
        self, df: pd.DataFrame, entry_price: float
    ) -> Tuple[float, str]:
        """
        计算止损位
        :return: (止损位, 止损类型)
        """
        atr = self.calculate_atr(df)

        support_level, support_type = self.calculate_support_level(df, entry_price)

        atr_stop = entry_price - 2 * atr
        ratio_stop = entry_price * (1 - self.risk_ratio)

        stop_loss = max(support_level, atr_stop, ratio_stop)

        stop_type = support_type
        if stop_loss == atr_stop:
            stop_type = "ATR止损"
        elif stop_loss == ratio_stop:
            stop_type = "固定比例"

        return stop_loss, stop_type

    def calculate_take_profit(
        self, df: pd.DataFrame, entry_price: float, stop_loss: float
    ) -> Tuple[float, str]:
        """
        计算止盈位
        :return: (止盈位, 止盈类型)
        """
        risk_amount = entry_price - stop_loss
        reward_amount = risk_amount * 2

        take_profit = entry_price + reward_amount

        recent_high = df["high"].tail(30).max()
        resistance = max(recent_high, take_profit)

        return min(take_profit, resistance), "盈亏比2:1"

    def calculate_risk_level(self, df: pd.DataFrame, entry_price: float) -> RiskLevel:
        """
        计算风险级别
        """
        stop_loss, stop_type = self.calculate_stop_loss(df, entry_price)
        take_profit, profit_type = self.calculate_take_profit(
            df, entry_price, stop_loss
        )

        risk_amount = entry_price - stop_loss
        reward_amount = take_profit - entry_price

        risk_reward_ratio = reward_amount / risk_amount if risk_amount > 0 else 0

        if risk_reward_ratio >= 2.0:
            confidence = "高"
        elif risk_reward_ratio >= 1.5:
            confidence = "中"
        else:
            confidence = "低"

        return RiskLevel(
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward_ratio=risk_reward_ratio,
            confidence=confidence,
        )

    def update_signal_risk(
        self, signal: StrategySignal, df: pd.DataFrame
    ) -> StrategySignal:
        """
        更新信号的风险信息
        """
        risk_level = self.calculate_risk_level(df, signal.entry_price)

        signal.stop_loss = risk_level.stop_loss
        signal.take_profit = risk_level.take_profit

        return signal
