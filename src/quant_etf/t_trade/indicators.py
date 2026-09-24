"""做T策略指标库：MA / RSI / 分型 / 影线 / 平均振幅。

纯函数模块：pandas Series 进出，无 IO、无副作用。
指标口径与通达信一致（96均线、RSI(6) 为「做T狂人」体系参数）。
"""

import numpy as np
import pandas as pd


def rsi(close: pd.Series, length: int = 6) -> pd.Series:
    """RSI，通达信口径：SMA(MAX(CLOSE-LC,0),N,1) / SMA(ABS(CLOSE-LC),N,1) * 100。

    SMA(X,N,1) 为 Wilder 式平滑（alpha=1/N），首值用第一个差分初始化。
    """
    lc = close.shift(1)
    up = (close - lc).clip(lower=0.0)
    abs_chg = (close - lc).abs()

    def sma(x: pd.Series, n: int) -> pd.Series:
        out = x.copy()
        for i in range(1, len(out)):
            prev = out.iloc[i - 1]
            cur = out.iloc[i]
            out.iloc[i] = (cur + (n - 1) * prev) / n
        return out

    result = pd.Series(np.nan, index=close.index, dtype=float)
    if len(close) < 2:
        return result

    sma_up = sma(up.iloc[1:], length)
    sma_abs = sma(abs_chg.iloc[1:], length)
    values = sma_up / sma_abs * 100.0
    result.iloc[1:] = values.values
    return result


def bottom_fractal(high: pd.Series, low: pd.Series) -> pd.Series:
    """底分型：连续三根 bar 中间那根低点、高点均严格低于两侧。

    返回布尔序列，True 落在第三根（确认）bar 上——收盘即知，供引擎直接消费。
    """
    center_low_lt_prev = low < low.shift(1)
    center_low_lt_next = low < low.shift(-1)
    center_high_lt_prev = high < high.shift(1)
    center_high_lt_next = high < high.shift(-1)

    center_confirmed = (
        center_low_lt_prev & center_low_lt_next & center_high_lt_prev & center_high_lt_next
    )
    # center 在 t-1，确认在 t
    return center_confirmed.shift(1, fill_value=False)


def top_fractal(high: pd.Series, low: pd.Series) -> pd.Series:
    """顶分型：底分型的镜像。True 落在第三根（确认）bar 上。"""
    center_low_gt_prev = low > low.shift(1)
    center_low_gt_next = low > low.shift(-1)
    center_high_gt_prev = high > high.shift(1)
    center_high_gt_next = high > high.shift(-1)

    center_confirmed = (
        center_low_gt_prev & center_low_gt_next & center_high_gt_prev & center_high_gt_next
    )
    return center_confirmed.shift(1, fill_value=False)


def lower_shadow_rejection(
    open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series
) -> pd.Series:
    """下影线企稳：下影线 ≥ 2×实体 且 收盘位于 bar 上半部（"跌不动的证明"）。"""
    body = (close - open_).abs()
    lower_shadow = pd.concat([open_, close], axis=1).min(axis=1) - low
    close_in_upper_half = close >= (high + low) / 2.0
    return ((lower_shadow >= 2 * body) & (lower_shadow > 0) & close_in_upper_half).astype(bool)


def upper_shadow_rejection(
    open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series
) -> pd.Series:
    """上影线承压：上影线 ≥ 2×实体 且 收盘位于 bar 下半部（"涨不动的证明"）。"""
    body = (close - open_).abs()
    upper_shadow = high - pd.concat([open_, close], axis=1).max(axis=1)
    close_in_lower_half = close <= (high + low) / 2.0
    return ((upper_shadow >= 2 * body) & (upper_shadow > 0) & close_in_lower_half).astype(bool)


def avg_amplitude(
    high: pd.Series, low: pd.Series, close: pd.Series, window: int
) -> pd.Series:
    """滚动平均日振幅：(high - low) / 昨收，window 日均值。

    样本不足 window 时用可得样本（min_periods=1），便于回补初期使用。
    """
    amplitude = (high - low) / close.shift(1)
    return amplitude.rolling(window, min_periods=1).mean()
