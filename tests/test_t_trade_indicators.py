"""做T指标库测试：RSI / 分型 / 影线 / 平均振幅（纯函数，pandas 进出）。"""

import numpy as np
import pandas as pd
import pytest

from quant_etf.t_trade.indicators import (
    avg_amplitude,
    bottom_fractal,
    lower_shadow_rejection,
    ma,
    rsi,
    top_fractal,
    upper_shadow_rejection,
)


def _series(values) -> pd.Series:
    return pd.Series(values, dtype=float)


class TestRsi:
    def test_first_value_is_nan(self):
        result = rsi(_series([10.0, 10.5, 10.8]), length=6)
        assert np.isnan(result.iloc[0])

    def test_pure_rise_gives_100(self):
        closes = [10.0 + i for i in range(10)]
        result = rsi(_series(closes), length=6)
        assert (result.iloc[1:] == 100.0).all()

    def test_pure_fall_gives_0(self):
        closes = [20.0 - i for i in range(10)]
        result = rsi(_series(closes), length=6)
        assert (result.iloc[1:] == 0.0).all()

    def test_hand_computed_recursion(self):
        # TDX SMA(X,N,1) 平滑，手算：closes=[10,11,10], N=2
        # chg=[nan,1,-1]; up=[nan,1,0]; abs=[nan,1,1]
        # S_up=[1, (0+1*1)/2=0.5]; S_abs=[1, (1+1*1)/2=1.0] → RSI=[100, 50]
        result = rsi(_series([10.0, 11.0, 10.0]), length=2)
        assert np.isnan(result.iloc[0])
        assert result.iloc[1] == pytest.approx(100.0)
        assert result.iloc[2] == pytest.approx(50.0)

    def test_range_bounded(self):
        rng = np.random.default_rng(42)
        closes = pd.Series(100 + rng.normal(0, 1, 100).cumsum())
        result = rsi(closes, length=6)
        assert ((result.dropna() >= 0) & (result.dropna() <= 100)).all()

    def test_too_short_series_all_nan(self):
        result = rsi(_series([10.0]), length=6)
        assert result.isna().all()


class TestMa:
    def test_rolling_mean_hand_computed(self):
        result = ma(_series([1.0, 2.0, 3.0, 4.0]), length=2)
        assert np.isnan(result.iloc[0])
        assert result.iloc[1] == pytest.approx(1.5)
        assert result.iloc[2] == pytest.approx(2.5)
        assert result.iloc[3] == pytest.approx(3.5)

    def test_window_short_of_full_is_nan(self):
        result = ma(_series([1.0, 2.0, 3.0]), length=96)
        assert result.isna().all()


class TestBottomFractal:
    def test_middle_bar_lowest_confirmed_at_third_bar(self):
        # 三根 bar：中间那根低点、高点都最低 → 底分型，在第三根收盘确认
        high = _series([10.0, 8.0, 10.5])
        low = _series([9.0, 7.0, 9.2])
        result = bottom_fractal(high, low)
        assert bool(result.iloc[2])
        assert not result.iloc[:2].any()

    def test_next_bar_breaking_lower_breaks_pattern(self):
        # 第三根创新低 → 中间 bar 不再是最低 → 非底分型
        high = _series([10.0, 8.0, 7.5])
        low = _series([9.0, 7.0, 6.5])
        result = bottom_fractal(high, low)
        assert not result.any()

    def test_middle_not_lowest(self):
        high = _series([10.0, 10.2, 10.5])
        low = _series([9.0, 9.5, 9.2])
        result = bottom_fractal(high, low)
        assert not result.any()

    def test_equal_extremes_do_not_count(self):
        # 中心 bar 低点与左邻居等低 → 非严格更低 → 非底分型
        high = _series([10.0, 9.0, 10.5])
        low = _series([7.0, 7.0, 9.2])
        result = bottom_fractal(high, low)
        assert not result.any()


class TestTopFractal:
    def test_middle_bar_highest_confirmed_at_third_bar(self):
        high = _series([10.0, 12.0, 10.5])
        low = _series([9.0, 11.0, 9.2])
        result = top_fractal(high, low)
        assert bool(result.iloc[2])
        assert not result.iloc[:2].any()

    def test_next_bar_breaking_higher_breaks_pattern(self):
        high = _series([10.0, 12.0, 12.5])
        low = _series([9.0, 11.0, 11.5])
        result = top_fractal(high, low)
        assert not result.any()


class TestShadowRejection:
    def test_lower_shadow_stabilization(self):
        # 长下影 + 收盘上半部：body=0.2, 下影=1.0 ≥ 2*body，close=10.2 ≥ 中点9.65
        o, h, l, c = _series([10.0]), _series([10.3]), _series([9.0]), _series([10.2])
        assert bool(lower_shadow_rejection(o, h, l, c).iloc[0])

    def test_lower_shadow_too_short(self):
        # 下影=0.1 < 2*body=0.2
        o, h, l, c = _series([10.0]), _series([10.3]), _series([10.1]), _series([10.2])
        assert not bool(lower_shadow_rejection(o, h, l, c).iloc[0])

    def test_lower_shadow_but_close_in_lower_half(self):
        # 下影=0.45 ≥ 2*body=0.3，但 close=9.85 < 中点9.9 → 收盘弱，不算企稳
        o, h, l, c = _series([10.0]), _series([10.4]), _series([9.4]), _series([9.85])
        assert not bool(lower_shadow_rejection(o, h, l, c).iloc[0])

    def test_upper_shadow_rejection(self):
        # 长上影 + 收盘下半部：body=0.2, 上影=1.0 ≥ 0.4，close=9.8 ≤ 中点10.35
        o, h, l, c = _series([10.0]), _series([11.0]), _series([9.7]), _series([9.8])
        assert bool(upper_shadow_rejection(o, h, l, c).iloc[0])

    def test_upper_shadow_too_short(self):
        o, h, l, c = _series([10.0]), _series([10.3]), _series([9.9]), _series([9.8])
        assert not bool(upper_shadow_rejection(o, h, l, c).iloc[0])


class TestAvgAmplitude:
    def test_hand_computed_rolling_mean(self):
        # 日振幅 = (高-低)/昨收：
        # day2: (12-10)/10.5 = 0.190476…；day3: (11.5-11)/11 = 0.0454545…
        # window=2 → day3 均值 = 0.117965…
        high = _series([11.0, 12.0, 11.5])
        low = _series([10.0, 10.0, 11.0])
        close = _series([10.5, 11.0, 11.2])
        result = avg_amplitude(high, low, close, window=2)
        assert np.isnan(result.iloc[0])
        # day2 是首个有效振幅，但窗口内只有 1 个非 NaN 样本 → NaN（严格窗口）
        assert np.isnan(result.iloc[1])
        assert result.iloc[2] == pytest.approx((2.0 / 10.5 + 0.5 / 11.0) / 2)

    def test_window_not_full_is_nan(self):
        # 窗口不满 → NaN（fail-closed），振幅过滤暖机期停手
        high = _series([11.0, 12.0])
        low = _series([10.0, 10.0])
        close = _series([10.5, 11.0])
        result = avg_amplitude(high, low, close, window=96)
        assert np.isnan(result.iloc[1])
