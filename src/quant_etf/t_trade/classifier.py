"""做T判定库：三完全分类 / 纵向共振 / 大盘过滤 / 振幅判定。

纯函数模块：输入为指标序列或标量快照，输出枚举或布尔，无 IO、无副作用。
「132」交易法的"3个完全分类"：96均线上行→正T为主，下行→反T为主，横盘→双向。
"""

from enum import Enum

import pandas as pd


class Trend(Enum):
    """三完全分类：走势有且仅有三种。"""

    UP = "up"
    DOWN = "down"
    SIDEWAYS = "sideways"


def classify_trend(ma: pd.Series, n: int = 8, threshold: float = 0.001) -> pd.Series:
    """按 MA 斜率做三完全分类：slope=(MA_t − MA_{t−n}) / MA_{t−n}。

    slope > +threshold → UP；< −threshold → DOWN；其间（含历史不足）→ SIDEWAYS。
    """
    base = ma.shift(n)
    slope = (ma - base) / base

    def to_trend(s: float) -> Trend:
        if pd.isna(s):
            return Trend.SIDEWAYS
        if s > threshold:
            return Trend.UP
        if s < -threshold:
            return Trend.DOWN
        return Trend.SIDEWAYS

    return slope.map(to_trend)


def resonates(higher: Trend, direction: Trend) -> bool:
    """纵向共振原子：两个级别方向完全一致。"""
    return higher == direction


def index_permits(index_trend: Trend, direction: Trend) -> bool:
    """大盘过滤：正T腿要求大盘非下行，反T腿要求大盘非上行；中性方向不设限。"""
    if direction == Trend.UP:
        return index_trend != Trend.DOWN
    if direction == Trend.DOWN:
        return index_trend != Trend.UP
    return True


def amplitude_sufficient(avg_amp: float, min_amp: float) -> bool:
    """振幅判定：平均振幅达到下限才出手（"不怕不大涨，就怕没振幅"）。"""
    return avg_amp >= min_amp
