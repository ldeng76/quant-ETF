"""增强开关组测试：分型/缩量/共振/大盘/时段/偏离/逆势T/重拳份数。"""

from datetime import datetime

from quant_etf.t_trade.classifier import Direction, Trend
from quant_etf.t_trade.engine import BarContext, TtAction, TtEngine
from quant_etf.t_trade.params import TTradeParams


def ctx(rsi=50.0, trend=Trend.SIDEWAYS, t=None, **overrides) -> BarContext:
    base = dict(
        bar_time=t or datetime(2026, 9, 24, 10, 0),
        close=10.0,
        rsi=rsi,
        primary_trend=trend,
        available_to_sell=100_000,
    )
    base.update(overrides)
    return BarContext(**base)


def engine(**param_overrides) -> TtEngine:
    return TtEngine(params=TTradeParams(**param_overrides), unit_cash=5_000.0)


class TestFractalGate:
    def test_blocked_when_no_fractal(self):
        e = engine()
        d = e.on_bar(ctx(rsi=15.0, fractal_buy_ok=False))
        assert d.action == TtAction.NONE

    def test_passes_with_fractal(self):
        e = engine()
        d = e.on_bar(ctx(rsi=15.0, fractal_buy_ok=True))
        assert d.action == TtAction.OPEN_T

    def test_reverse_side_uses_sell_fractal(self):
        # 反T卖出腿在下午窗口（13:30–14:30）
        e = engine()
        d = e.on_bar(ctx(rsi=85.0, fractal_buy_ok=False, fractal_sell_ok=True,
                         t=datetime(2026, 9, 24, 14, 0)))
        assert d.action == TtAction.OPEN_T

    def test_switch_off_disables_gate(self):
        e = engine(confirm_fractal=False)
        d = e.on_bar(ctx(rsi=15.0, fractal_buy_ok=False))
        assert d.action == TtAction.OPEN_T


class TestVolumeGate:
    def test_blocked_without_shrink(self):
        e = engine()
        d = e.on_bar(ctx(rsi=15.0, volume_shrink_ok=False))
        assert d.action == TtAction.NONE

    def test_switch_off_disables_gate(self):
        e = engine(confirm_volume=False)
        d = e.on_bar(ctx(rsi=15.0, volume_shrink_ok=False))
        assert d.action == TtAction.OPEN_T


class TestResonanceGate:
    def test_passes_when_aligned(self):
        e = engine()
        d = e.on_bar(ctx(rsi=15.0, higher_trend=Trend.UP))
        assert d.action == TtAction.OPEN_T

    def test_sideways_higher_is_neutral(self):
        # "走势不一致→不做"仅指方向相反；高级别横盘是中性，放行但不计共振
        e = engine()
        d = e.on_bar(ctx(rsi=15.0, higher_trend=Trend.SIDEWAYS))
        assert d.action == TtAction.OPEN_T
        assert "resonance" not in d.reason

    def test_opposite_higher_blocks(self):
        e = engine()
        d = e.on_bar(ctx(rsi=15.0, higher_trend=Trend.DOWN))
        assert d.action == TtAction.NONE

    def test_none_higher_trend_fails_open(self):
        # 信号不可算（暖机期）→ 放行，不阻断
        e = engine()
        d = e.on_bar(ctx(rsi=15.0, higher_trend=None))
        assert d.action == TtAction.OPEN_T


class TestIndexFilter:
    def test_forward_t_blocked_by_falling_index(self):
        e = engine()
        d = e.on_bar(ctx(rsi=15.0, index_trend=Trend.DOWN))
        assert d.action == TtAction.NONE

    def test_reverse_t_blocked_by_rising_index(self):
        e = engine()
        d = e.on_bar(ctx(rsi=85.0, index_trend=Trend.UP))
        assert d.action == TtAction.NONE

    def test_switch_off_disables_gate(self):
        e = engine(index_filter=False)
        d = e.on_bar(ctx(rsi=15.0, index_trend=Trend.DOWN))
        assert d.action == TtAction.OPEN_T


class TestTimeWindow:
    def test_buy_blocked_outside_morning_window(self):
        # 买入腿仅在 09:35–11:00；13:40 不在窗口
        e = engine()
        d = e.on_bar(ctx(rsi=15.0, t=datetime(2026, 9, 24, 13, 40)))
        assert d.action == TtAction.NONE

    def test_buy_passes_inside_morning_window(self):
        e = engine()
        d = e.on_bar(ctx(rsi=15.0, t=datetime(2026, 9, 24, 10, 30)))
        assert d.action == TtAction.OPEN_T

    def test_reverse_blocked_outside_afternoon_window(self):
        # 反T卖出腿偏好 13:30–14:30；10:00 不在窗口
        e = engine()
        d = e.on_bar(ctx(rsi=85.0, t=datetime(2026, 9, 24, 10, 0)))
        assert d.action == TtAction.NONE

    def test_reverse_passes_in_afternoon_window(self):
        e = engine()
        d = e.on_bar(ctx(rsi=85.0, t=datetime(2026, 9, 24, 14, 0)))
        assert d.action == TtAction.OPEN_T

    def test_switch_off_disables_window(self):
        e = engine(time_window=False)
        d = e.on_bar(ctx(rsi=15.0, t=datetime(2026, 9, 24, 13, 40)))
        assert d.action == TtAction.OPEN_T


class TestOffsideT:
    def test_offside_disabled_by_default(self):
        e = engine()
        d = e.on_bar(ctx(rsi=85.0, trend=Trend.UP))
        assert d.action == TtAction.NONE  # 上行中超买不许反T

    def test_offside_enabled_allows_counter_trend(self):
        e = engine(allow_offside_t=True)
        d = e.on_bar(ctx(rsi=85.0, trend=Trend.UP,
                         t=datetime(2026, 9, 24, 14, 0)))
        assert d.action == TtAction.OPEN_T
        assert d.direction == Direction.REVERSE


class TestResonanceUnits:
    def test_full_alignment_uses_heavy_units(self):
        e = engine()
        d = e.on_bar(ctx(
            rsi=15.0, trend=Trend.UP,
            higher_trend=Trend.UP, index_trend=Trend.UP,
        ))
        assert d.action == TtAction.OPEN_T
        assert d.units == 2  # 重拳出击
        assert d.shares == 900  # 2份=10000 / (10×1.0005)=999.5 → 900 股

    def test_partial_alignment_keeps_single_unit(self):
        e = engine()
        d = e.on_bar(ctx(rsi=15.0, trend=Trend.SIDEWAYS,
                         higher_trend=Trend.UP, index_trend=Trend.UP))
        assert d.units == 1


class TestDeviationExit:
    def test_forward_t_exits_on_dev_above_ma(self):
        e = engine()
        e.on_bar(ctx(rsi=15.0))
        e.on_fill_leg()
        d = e.on_bar(ctx(rsi=50.0, dev_above_ma=True))
        assert d.action == TtAction.CLOSE_T

    def test_reverse_t_exits_on_dev_below_ma(self):
        e = engine()
        e.on_bar(ctx(rsi=85.0, t=datetime(2026, 9, 24, 14, 0)))
        e.on_fill_leg()
        d = e.on_bar(ctx(rsi=50.0, dev_below_ma=True))
        assert d.action == TtAction.CLOSE_T

    def test_dev_th_zero_disables(self):
        e = engine(dev_th=0.0)
        e.on_bar(ctx(rsi=15.0))
        e.on_fill_leg()
        d = e.on_bar(ctx(rsi=50.0, dev_above_ma=True))
        assert d.action == TtAction.NONE


class TestTriggerLabels:
    def test_reason_lists_passed_gates(self):
        e = engine()
        d = e.on_bar(ctx(
            rsi=15.0, trend=Trend.UP,
            fractal_buy_ok=True, volume_shrink_ok=True,
            higher_trend=Trend.UP, index_trend=Trend.UP,
        ))
        assert "fractal" in d.reason and "resonance" in d.reason
        assert "index" in d.reason and "window" in d.reason
