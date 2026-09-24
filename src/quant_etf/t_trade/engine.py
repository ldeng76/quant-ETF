"""做T引擎状态机：纯决策、不碰钱、不碰存储（ADR-0002）。

状态：IDLE → LEG1_OPEN(开仓腿已决策) → LEG2_PENDING(持仓待对冲) → IDLE。
输入：BarContext（bar 快照 + 指标 + 账户查询快照）；输出：EngineDecision。
回测与实时共用本模块——第二期只换数据驱动方式，不改决策逻辑。
"""

from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum
from typing import Optional

from .classifier import Trend
from .params import TTradeParams


class TtState(Enum):
    IDLE = "idle"
    LEG1_OPEN = "leg1_open"
    LEG2_PENDING = "leg2_pending"


class TtAction(Enum):
    NONE = "none"
    OPEN_T = "open_t"
    CLOSE_T = "close_t"
    FORCE_CLOSE = "force_close"


@dataclass(frozen=True)
class BarContext:
    """单根 bar 的决策输入快照。"""

    bar_time: datetime  # bar 收盘时间
    close: float
    rsi: float
    primary_trend: Trend
    available_to_sell: int  # 账户 T+1 可卖量快照


@dataclass(frozen=True)
class EngineDecision:
    action: TtAction
    direction: Optional[str] = None  # "up"=正T / "down"=反T
    units: int = 0
    shares: int = 0
    reason: str = ""


_NONE = EngineDecision(action=TtAction.NONE)


class TtEngine:
    """每标的一个实例。所有资金变动经账户执行，引擎只产生决策。"""

    def __init__(self, params: TTradeParams, unit_cash: float):
        self.params = params
        self.unit_cash = unit_cash
        self.state = TtState.IDLE
        self._pending_direction: Optional[str] = None
        self._trades_today = 0
        self._current_day: Optional[datetime.date] = None
        self._eod_time = time.fromisoformat(params.eod_force_time)

    # ---- 决策 ----

    def on_bar(self, ctx: BarContext) -> EngineDecision:
        self._roll_day(ctx.bar_time)

        if self.state == TtState.LEG1_OPEN:
            return _NONE  # 等待开仓腿成交确认

        if self.state == TtState.LEG2_PENDING:
            if self.params.strict_eod and ctx.bar_time.time() >= self._eod_time:
                return EngineDecision(
                    TtAction.FORCE_CLOSE, reason="eod_force"
                )
            if self._exit_signal(ctx):
                return EngineDecision(TtAction.CLOSE_T, reason="exit_signal")
            return _NONE

        # IDLE：入场判定
        if self._trades_today >= self.params.max_trades_per_day:
            return _NONE
        direction = self._entry_direction(ctx)
        if direction is None:
            return _NONE
        units = self.params.units_per_trade
        shares = self._planned_shares(ctx.close, units)
        if shares < self.params.lot_size or shares > ctx.available_to_sell:
            return _NONE
        self.state = TtState.LEG1_OPEN
        self._pending_direction = direction
        return EngineDecision(
            TtAction.OPEN_T, direction=direction, units=units, shares=shares,
            reason="core_entry",
        )

    # ---- 成交确认（由编排方在执行成交后调用）----

    def on_fill_leg(self) -> None:
        """单腿成交完成：开仓腿确认 → LEG2_PENDING；对冲腿确认 → IDLE 计数。"""
        if self.state == TtState.LEG1_OPEN:
            self.state = TtState.LEG2_PENDING
        elif self.state == TtState.LEG2_PENDING:
            self.state = TtState.IDLE
            self._trades_today += 1
            self._pending_direction = None

    # ---- 内部 ----

    def _entry_direction(self, ctx: BarContext) -> Optional[str]:
        p = self.params
        t = ctx.primary_trend
        if t == Trend.UNKNOWN:
            return None
        if t in (Trend.UP, Trend.SIDEWAYS) and ctx.rsi <= p.rsi_buy_th:
            return "up"
        if t in (Trend.DOWN, Trend.SIDEWAYS) and ctx.rsi >= p.rsi_sell_th:
            return "down"
        return None

    def _exit_signal(self, ctx: BarContext) -> bool:
        p = self.params
        if self._pending_direction == "up":
            return ctx.rsi >= p.rsi_sell_th
        if self._pending_direction == "down":
            return ctx.rsi <= p.rsi_buy_th
        return False

    def _planned_shares(self, price: float, units: int) -> int:
        raw = self.unit_cash * units / price
        return int(raw // self.params.lot_size) * self.params.lot_size

    def _roll_day(self, bar_time: datetime) -> None:
        day = bar_time.date()
        if day != self._current_day:
            self._current_day = day
            self._trades_today = 0
