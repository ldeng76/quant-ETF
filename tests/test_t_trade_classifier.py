"""做T判定库测试：三完全分类 / 纵向共振 / 大盘过滤 / 振幅判定。"""

import pandas as pd
import pytest

from quant_etf.t_trade.classifier import (
    Trend,
    amplitude_sufficient,
    classify_trend,
    index_permits,
    resonates,
)


def _ma(values) -> pd.Series:
    return pd.Series(values, dtype=float)


class TestClassifyTrend:
    def test_flat_ma_is_sideways(self):
        result = classify_trend(_ma([100.0] * 12), n=8, threshold=0.001)
        assert (result == Trend.SIDEWAYS).all()

    def test_rising_ma_beyond_threshold_is_up(self):
        # 前 8 根历史不足 → SIDEWAYS；t=8 起 slope=(101-100)/100=1% > 0.1% → UP
        values = [100.0] * 8 + [101.0, 102.0, 103.0, 104.0]
        result = classify_trend(_ma(values), n=8, threshold=0.001)
        assert (result.iloc[:8] == Trend.SIDEWAYS).all()
        assert (result.iloc[8:] == Trend.UP).all()

    def test_falling_ma_beyond_threshold_is_down(self):
        values = [100.0] * 8 + [99.0, 98.0, 97.0, 96.0]
        result = classify_trend(_ma(values), n=8, threshold=0.001)
        assert (result.iloc[8:] == Trend.DOWN).all()

    def test_slope_equal_to_threshold_is_sideways(self):
        # slope = (100.1-100)/100 = 0.1% == threshold，严格大于才算 UP
        values = [100.0] * 8 + [100.1, 100.2]
        result = classify_trend(_ma(values), n=8, threshold=0.001)
        assert result.iloc[8] == Trend.SIDEWAYS

    def test_insufficient_history_is_sideways(self):
        result = classify_trend(_ma([100.0, 101.0]), n=8, threshold=0.001)
        assert (result == Trend.SIDEWAYS).all()


class TestResonates:
    def test_same_direction_resonates(self):
        assert resonates(Trend.UP, Trend.UP)
        assert resonates(Trend.DOWN, Trend.DOWN)
        assert resonates(Trend.SIDEWAYS, Trend.SIDEWAYS)

    def test_mixed_direction_does_not_resonate(self):
        assert not resonates(Trend.UP, Trend.DOWN)
        assert not resonates(Trend.SIDEWAYS, Trend.UP)
        assert not resonates(Trend.DOWN, Trend.SIDEWAYS)


class TestIndexPermits:
    def test_forward_t_blocked_by_falling_index(self):
        assert not index_permits(Trend.DOWN, Trend.UP)

    def test_forward_t_allowed_by_rising_or_sideways_index(self):
        assert index_permits(Trend.UP, Trend.UP)
        assert index_permits(Trend.SIDEWAYS, Trend.UP)

    def test_reverse_t_blocked_by_rising_index(self):
        assert not index_permits(Trend.UP, Trend.DOWN)

    def test_reverse_t_allowed_by_falling_or_sideways_index(self):
        assert index_permits(Trend.DOWN, Trend.DOWN)
        assert index_permits(Trend.SIDEWAYS, Trend.DOWN)

    def test_sideways_direction_not_restricted(self):
        # 文章仅对正T/反T腿定义大盘限制；中性方向不设限
        assert index_permits(Trend.UP, Trend.SIDEWAYS)
        assert index_permits(Trend.DOWN, Trend.SIDEWAYS)


class TestAmplitudeSufficient:
    def test_at_or_above_threshold_passes(self):
        assert amplitude_sufficient(0.03, 0.03)
        assert amplitude_sufficient(0.05, 0.03)

    def test_below_threshold_fails(self):
        assert not amplitude_sufficient(0.02, 0.03)
