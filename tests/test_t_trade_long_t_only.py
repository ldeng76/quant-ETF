"""long_t_only 开关：只做正T，禁止反T。

依据（回测）：10 只高振幅主题 ETF、2 年 90 笔中，正T 68 笔 +743 元，
反T 22 笔 −202 元——反T 是负贡献。但方向许可属于决策核心，必须锁死行为。
"""

from dataclasses import replace

import pytest

from quant_etf.t_trade.classifier import Trend
from quant_etf.t_trade.engine import BarContext, TtEngine
from quant_etf.t_trade.params import TTradeParams


def _ctx(trend: Trend, rsi: float, **kw) -> BarContext:
    base = dict(
        bar_time=__import__("datetime").datetime(2026, 1, 5, 10, 0),
        close=1.0, rsi=rsi, primary_trend=trend,
        available_to_sell=10_000, day_amp_ok=True,
        fractal_buy_ok=False, fractal_sell_ok=False, volume_shrink_ok=True,
        higher_trend=trend, index_trend=trend, in_buy_window=True,
        in_sell_window=True, dev_above_ma=False, dev_below_ma=False,
    )
    base.update(kw)
    return BarContext(**base)


def _params(**kw) -> TTradeParams:
    return replace(TTradeParams(allow_offside_t=False), **kw)


@pytest.mark.parametrize("trend,allows_reverse", [
    (Trend.UP, False),      # 上行趋势下 RSI 80 不开反T（逆势，需 allow_offside_t）
    (Trend.SIDEWAYS, True),  # 横盘双向
    (Trend.DOWN, True),      # 下行趋势的顺势反T
])
def test_default_keeps_reverse_direction(trend, allows_reverse):
    """默认（开关关）行为不变：RSI 达上阈时，反T 只在非上行趋势成立。"""
    e = TtEngine(_params(), unit_cash=50_000)
    got = e._entry_direction(_ctx(trend, 80.0)) is not None
    assert got is allows_reverse, f"{trend} 下反T 许可判断与默认行为不符"


def test_long_t_only_blocks_reverse():
    e = TtEngine(_params(long_t_only=True), unit_cash=50_000)
    # 下行趋势 + RSI 80：默认会开反T，开关后必须无方向
    assert e._entry_direction(_ctx(Trend.DOWN, 80.0)) is None
    # 上行趋势 + RSI 20：正T 不受影响
    assert e._entry_direction(_ctx(Trend.UP, 20.0)) is not None


def test_long_t_only_blocks_reverse_in_sideways():
    """横盘本可双向，开关后只剩正T。"""
    e = TtEngine(_params(long_t_only=True), unit_cash=50_000)
    assert e._entry_direction(_ctx(Trend.SIDEWAYS, 80.0)) is None
    assert e._entry_direction(_ctx(Trend.SIDEWAYS, 20.0)) is not None


def test_unknown_never_trades():
    e = TtEngine(_params(long_t_only=True), unit_cash=50_000)
    assert e._entry_direction(_ctx(Trend.UNKNOWN, 20.0)) is None
    assert e._entry_direction(_ctx(Trend.UNKNOWN, 80.0)) is None


def test_long_t_only_does_not_touch_offside():
    """allow_offside_t 与 long_t_only 同时开：逆势正T 允许，逆势反T 仍禁。"""
    p = _params(allow_offside_t=True, long_t_only=True)
    e = TtEngine(p, unit_cash=50_000)
    # 下行趋势 + RSI20 = 逆势正T，allow_offside_t 放开后应允许
    assert e._entry_direction(_ctx(Trend.DOWN, 20.0)) is not None
    assert e._entry_direction(_ctx(Trend.DOWN, 80.0)) is None
