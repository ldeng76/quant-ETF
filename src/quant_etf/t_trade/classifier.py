"""做T判定库：三完全分类 / 纵向共振 / 大盘过滤 / 振幅判定。

纯函数模块：输入为指标序列或标量快照，输出枚举或布尔，无 IO、无副作用。
「132」交易法的"3个完全分类"：96均线上行→正T为主，下行→反T为主，横盘→双向。
"""

from enum import Enum

import pandas as pd


class Trend(Enum):
    """三完全分类：走势有且仅有三种；UNKNOWN = 指标暖机未满，无法分类。"""

    UP = "up"
    DOWN = "down"
    SIDEWAYS = "sideways"
    UNKNOWN = "unknown"


def classify_trend(ma: pd.Series, slope_bars: int = 8, threshold: float = 0.001) -> pd.Series:
    """按 MA 斜率做三完全分类：slope=(MA_t − MA_{t−n}) / MA_{t−n}。

    slope > +threshold → UP；< −threshold → DOWN；其间 → SIDEWAYS；
    历史不足 n 根（slope 不可算）→ UNKNOWN，引擎不得据此出手。
    """
    base = ma.shift(slope_bars)
    slope = (ma - base) / base

    def to_trend(s: float) -> Trend:
        if pd.isna(s):
            return Trend.UNKNOWN
        if s > threshold:
            return Trend.UP
        if s < -threshold:
            return Trend.DOWN
        return Trend.SIDEWAYS

    return slope.map(to_trend)


def resonates(higher_tf: Trend, direction: Trend) -> bool:
    """纵向共振原子：高级别与交易方向完全一致（含"都盘整"的盘整共振）。"""
    return higher_tf == direction


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
