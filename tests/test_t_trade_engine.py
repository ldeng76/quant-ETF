"""做T引擎状态机测试：入场/对冲/强平/纪律上限。"""

from datetime import datetime

from quant_etf.t_trade.classifier import Direction, Trend
from quant_etf.t_trade.engine import BarContext, TtAction, TtState, TtEngine
from quant_etf.t_trade.params import TTradeParams

PARAMS = TTradeParams()


def ctx(rsi=50.0, trend=Trend.UP, price=10.0, available=100_000,
        t=None) -> BarContext:
    return BarContext(
        bar_time=t or datetime(2026, 9, 24, 10, 30),
        close=price,
        rsi=rsi,
        primary_trend=trend,
        available_to_sell=available,
    )


def engine(unit_cash=5_000.0, **param_overrides) -> TtEngine:
    return TtEngine(params=TTradeParams(**param_overrides), unit_cash=unit_cash)


class TestForwardTFlow:
    def test_entry_signal_submits_forward_t(self):
        e = engine()
        d = e.on_bar(ctx(rsi=15.0, trend=Trend.UP))
        assert d.action == TtAction.OPEN_T
        assert d.direction == Direction.FORWARD
        assert d.units == 1
        # 含滑点口径：5000 / (10 × 1.0005) = 499.75 → 400 股
        assert d.shares == 400
        assert e.state == TtState.LEG1_SUBMITTED

    def test_after_open_fill_waits_for_exit_signal(self):
        e = engine()
        e.on_bar(ctx(rsi=15.0, trend=Trend.UP))
        e.on_fill_leg()
        assert e.state == TtState.LEG2_PENDING
        # 无退出信号 → 不动
        d = e.on_bar(ctx(rsi=50.0, trend=Trend.UP))
        assert d.action == TtAction.NONE
        # RSI 到超买 → 对冲腿
        d = e.on_bar(ctx(rsi=85.0, trend=Trend.UP))
        assert d.action == TtAction.CLOSE_T
        e.on_fill_leg()
        assert e.state == TtState.IDLE

    def test_reverse_t_entry_mirror(self):
        e = engine()
        d = e.on_bar(ctx(rsi=85.0, trend=Trend.DOWN))
        assert d.action == TtAction.OPEN_T
        assert d.direction == Direction.REVERSE


class TestEntryGuards:
    def test_no_entry_when_direction_not_permitted(self):
        e = engine()
        # 上行趋势中超买不许反T（逆势T默认关）
        d = e.on_bar(ctx(rsi=85.0, trend=Trend.UP))
        assert d.action == TtAction.NONE
        # 下行趋势中超卖不许正T
        d2 = engine().on_bar(ctx(rsi=15.0, trend=Trend.DOWN))
        assert d2.action == TtAction.NONE

    def test_unknown_trend_never_trades(self):
        e = engine()
        d = e.on_bar(ctx(rsi=15.0, trend=Trend.UNKNOWN))
        assert d.action == TtAction.NONE

    def test_insufficient_available_blocks_entry(self):
        # 可卖量不足一份 → 反T开仓腿卖不出去，正T对冲腿也卖不出去
        e = engine()
        d = e.on_bar(ctx(rsi=85.0, trend=Trend.DOWN, available=300))
        assert d.action == TtAction.NONE
        d2 = engine().on_bar(ctx(rsi=15.0, trend=Trend.UP, available=300))
        assert d2.action == TtAction.NONE

    def test_no_entry_in_leg2_pending(self):
        e = engine()
        e.on_bar(ctx(rsi=15.0, trend=Trend.UP))
        e.on_fill_leg()
        d = e.on_bar(ctx(rsi=15.0, trend=Trend.UP))  # 仍在场，不许再开
        assert d.action == TtAction.NONE

    def test_unit_buys_less_than_one_lot_never_trades(self):
        # 价太高：一份机动资金买不满一手 → 份耗尽语义，不出手
        e = engine()
        d = e.on_bar(ctx(rsi=15.0, trend=Trend.UP, price=1_000.0))
        assert d.action == TtAction.NONE


class TestEodClosure:
    def test_force_close_at_eod_time(self):
        e = engine()
        e.on_bar(ctx(rsi=15.0, trend=Trend.UP))
        e.on_fill_leg()
        d = e.on_bar(ctx(rsi=50.0, trend=Trend.UP,
                         t=datetime(2026, 9, 24, 14, 55)))
        assert d.action == TtAction.FORCE_CLOSE

    def test_no_force_close_when_flat(self):
        e = engine()
        d = e.on_bar(ctx(rsi=50.0, trend=Trend.UP,
                         t=datetime(2026, 9, 24, 14, 56)))
        assert d.action == TtAction.NONE

    def test_no_new_entry_after_eod(self):
        # 14:55 后不许开新 T 单——当日闭环无法保证
        e = engine()
        d = e.on_bar(ctx(rsi=15.0, trend=Trend.UP,
                         t=datetime(2026, 9, 24, 14, 56)))
        assert d.action == TtAction.NONE

    def test_force_close_when_leg1_unconfirmed_at_eod(self):
        # 开仓腿决策后成交确认丢失 → EOD 仍须发出强平，由编排方核对账户
        e = engine()
        e.on_bar(ctx(rsi=15.0, trend=Trend.UP,
                     t=datetime(2026, 9, 24, 14, 50)))
        assert e.state == TtState.LEG1_SUBMITTED
        d = e.on_bar(ctx(rsi=15.0, trend=Trend.UP,
                         t=datetime(2026, 9, 24, 14, 55)))
        assert d.action == TtAction.FORCE_CLOSE

    def test_resolve_stale_order_resets_to_idle(self):
        e = engine()
        e.on_bar(ctx(rsi=15.0, trend=Trend.UP,
                     t=datetime(2026, 9, 24, 14, 50)))
        e.resolve_stale_order()
        assert e.state == TtState.IDLE

    def test_loose_mode_allows_overnight(self):
        # strict_eod=False：EOD 不强平、尾盘仍可开仓（隔夜T单对照实验）
        e = engine(strict_eod=False)
        e.on_bar(ctx(rsi=15.0, trend=Trend.UP))
        e.on_fill_leg()
        d = e.on_bar(ctx(rsi=50.0, trend=Trend.UP,
                         t=datetime(2026, 9, 24, 14, 55)))
        assert d.action == TtAction.NONE
        d2 = engine(strict_eod=False).on_bar(ctx(rsi=15.0, trend=Trend.UP,
                                                 t=datetime(2026, 9, 24, 14, 56)))
        assert d2.action == TtAction.OPEN_T

    def test_stale_leg1_resets_on_day_rollover(self):
        # 严格模式下跨日的 LEG1_SUBMITTED 是死单，自动复位后可正常开仓
        e = engine()
        e.on_bar(ctx(rsi=15.0, trend=Trend.UP,
                     t=datetime(2026, 9, 24, 14, 50)))
        assert e.state == TtState.LEG1_SUBMITTED
        d = e.on_bar(ctx(rsi=15.0, trend=Trend.UP,
                         t=datetime(2026, 9, 25, 9, 40)))
        assert d.action == TtAction.OPEN_T  # 复位成功才可能再次开仓


class TestDailyLimit:
    def test_max_trades_per_day(self):
        e = engine()
        for _ in range(PARAMS.max_trades_per_day):
            d = e.on_bar(ctx(rsi=15.0, trend=Trend.UP))
            assert d.action == TtAction.OPEN_T
            e.on_fill_leg()
            e.on_bar(ctx(rsi=85.0, trend=Trend.UP))
            e.on_fill_leg()
        d = e.on_bar(ctx(rsi=15.0, trend=Trend.UP))
        assert d.action == TtAction.NONE  # 当日第4单被拒

    def test_counter_resets_next_day(self):
        e = engine()
        for _ in range(PARAMS.max_trades_per_day):
            e.on_bar(ctx(rsi=15.0, trend=Trend.UP))
            e.on_fill_leg()
            e.on_bar(ctx(rsi=85.0, trend=Trend.UP))
            e.on_fill_leg()
        d = e.on_bar(ctx(rsi=15.0, trend=Trend.UP,
                         t=datetime(2026, 9, 25, 10, 0)))
        assert d.action == TtAction.OPEN_T  # 次日恢复
