"""做T引擎状态机：纯决策、不碰钱、不碰存储（ADR-0002）。

状态：IDLE → LEG1_SUBMITTED(开仓腿已决策待成交确认) → LEG2_PENDING(持仓待对冲) → IDLE。
输入：BarContext（bar 快照 + 指标 + 账户查询快照）；输出：EngineDecision。
回测与实时共用本模块——第二期只换数据驱动方式，不改决策逻辑。

当日闭环契约（strict_eod=True）：
- eod_force_time 之后的 bar 不再开新 T 单（否则当日无法闭环）；
- LEG2_PENDING 在 eod 强制平仓，无论盈亏；
- LEG1_SUBMITTED 在 eod 发出 FORCE_CLOSE——编排方须核对账户：
  开仓腿已成交则照常对冲，未成交则调用 `resolve_stale_order()` 复位。

unit_cash 由编排方从 SubAccount.unit_cash 接线，勿另行计算（单一口径）。
"""

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum
from typing import Optional
from .classifier import Direction, Trend, index_permits
from .params import TTradeParams


def _opposite(trend: Trend) -> Trend:
    """方向的反面：上行↔下行；横盘无反面。"""
    if trend == Trend.UP:
        return Trend.DOWN
    if trend == Trend.DOWN:
        return Trend.UP
    return Trend.SIDEWAYS


class TtState(Enum):
    IDLE = "idle"
    LEG1_SUBMITTED = "leg1_submitted"  # 开仓腿已决策，待成交确认
    LEG2_PENDING = "leg2_pending"  # 开仓腿已成交，待对冲


class TtAction(Enum):
    NONE = "none"
    OPEN_T = "open_t"
    CLOSE_T = "close_t"
    FORCE_CLOSE = "force_close"


@dataclass(frozen=True)
class BarContext:
    """单根 bar 的决策输入快照。

    增强开关的判定材料由编排方预计算注入；值为 None 表示该信号不可算
    （暖机期/数据缺失），对应门控放行、不阻断交易。
    """

    bar_time: datetime  # bar 收盘时间
    close: float
    rsi: float
    primary_trend: Trend
    available_to_sell: int  # 账户 T+1 可卖量快照
    day_amp_ok: bool = True  # 当日已实现振幅达标（amp_dead 门，做T"没振幅不做"）
    # ---- 增强开关判定材料（None=信号未计算：入场门放行、对冲腿不触发）----
    fractal_buy_ok: bool | None = None  # 底分型（近2bar内确认）或下影线企稳
    fractal_sell_ok: bool | None = None  # 顶分型或上影线承压
    volume_shrink_ok: bool = True  # 量能收缩（当前bar < 2bar前）
    higher_trend: Optional[Trend] = None  # 高级别（15m/60m）三分类
    index_trend: Optional[Trend] = None  # 大盘（沪深300 主级别）三分类
    in_buy_window: bool = True  # 处于买入时段 09:35–11:00
    in_sell_window: bool = True  # 处于卖出偏好时段 13:30–14:30
    dev_above_ma: bool = False  # 价格高于 MA96 超 dev_th（正T对冲附加触发）
    dev_below_ma: bool = False  # 价格低于 MA96 超 dev_th（反T对冲附加触发）


def _in_window(t: time, window: tuple[str, str]) -> bool:
    lo, hi = (time.fromisoformat(x) for x in window)
    return lo <= t <= hi


@dataclass(frozen=True)
class EngineDecision:
    action: TtAction
    direction: Optional[Direction] = None
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
        self._pending_direction: Optional[Direction] = None
        self._trades_today = 0
        self._current_day: Optional[date] = None
        self._eod_time = time.fromisoformat(params.eod_force_time)

    # ---- 决策 ----

    def on_bar(self, ctx: BarContext) -> EngineDecision:
        self._roll_day(ctx.bar_time)
        is_eod = ctx.bar_time.time() >= self._eod_time

        if self.state == TtState.LEG1_SUBMITTED:
            if self.params.strict_eod and is_eod:
                return EngineDecision(
                    TtAction.FORCE_CLOSE,
                    direction=self._pending_direction,
                    reason="eod_force_leg1_unconfirmed",
                )
            return _NONE  # 等待开仓腿成交确认

        if self.state == TtState.LEG2_PENDING:
            if self.params.strict_eod and is_eod:
                return EngineDecision(
                    TtAction.FORCE_CLOSE,
                    direction=self._pending_direction,
                    reason="eod_force",
                )
            if self._exit_signal(ctx):
                return EngineDecision(
                    TtAction.CLOSE_T,
                    direction=self._pending_direction,
                    reason="exit_signal",
                )
            return _NONE

        # IDLE：入场判定
        if self.params.strict_eod and is_eod:
            return _NONE  # 尾盘不开新T单——当日闭环无法保证
        if not ctx.day_amp_ok:
            return _NONE  # 当日振幅不足，差价覆盖不了摩擦成本，停手
        if self._trades_today >= self.params.max_trades_per_day:
            return _NONE
        direction = self._entry_direction(ctx)
        if direction is None:
            return _NONE
        gates, ok = self._entry_gates(ctx, direction)
        if not ok:
            return _NONE
        units = self._units_for(ctx, direction)
        shares = self._planned_shares(ctx.close, units)
        if shares < self.params.lot_size or shares > ctx.available_to_sell:
            return _NONE
        self.state = TtState.LEG1_SUBMITTED
        self._pending_direction = direction
        return EngineDecision(
            TtAction.OPEN_T, direction=direction, units=units, shares=shares,
            reason="entry:" + "+".join(gates),
        )

    # ---- 成交确认（由编排方在执行成交后调用）----

    def on_fill_leg(self) -> None:
        """单腿成交完成：开仓腿确认 → LEG2_PENDING；对冲腿确认 → IDLE 计数。"""
        if self.state == TtState.LEG1_SUBMITTED:
            self.state = TtState.LEG2_PENDING
        elif self.state == TtState.LEG2_PENDING:
            self.state = TtState.IDLE
            self._trades_today += 1
            self._pending_direction = None

    def resolve_stale_order(self) -> None:
        """开仓腿确认永久丢失（未成交且不再会成交）时复位状态机。"""
        if self.state == TtState.LEG1_SUBMITTED:
            self.state = TtState.IDLE
            self._pending_direction = None

    # ---- 内部 ----

    def _entry_direction(self, ctx: BarContext) -> Optional[Direction]:
        """方向许可（三完全分类）+ RSI 触发；allow_offside_t 开启逆势T。"""
        p = self.params
        t = ctx.primary_trend
        if t == Trend.UNKNOWN:
            return None
        fwd_ok = t in (Trend.UP, Trend.SIDEWAYS) or (
            p.allow_offside_t and t == Trend.DOWN
        )
        rev_ok = t in (Trend.DOWN, Trend.SIDEWAYS) or (
            p.allow_offside_t and t == Trend.UP
        )
        if fwd_ok and ctx.rsi <= p.rsi_buy_th:
            return Direction.FORWARD
        if rev_ok and ctx.rsi >= p.rsi_sell_th:
            return Direction.REVERSE
        return None

    def _entry_gates(self, ctx: BarContext, direction: Direction) -> tuple[list[str], bool]:
        """增强开关门控（默认全开）。返回 (通过的判定标签, 是否全部放行)。"""
        p = self.params
        passed: list[str] = []
        up = direction == Direction.FORWARD

        fractal_ok = ctx.fractal_buy_ok if up else ctx.fractal_sell_ok
        if p.confirm_fractal and fractal_ok is False:
            return [], False
        if p.confirm_fractal and fractal_ok:
            passed.append("fractal")
        if p.confirm_volume and not ctx.volume_shrink_ok:
            return [], False
        if p.confirm_volume:
            passed.append("volume")

        dir_trend = Trend.UP if up else Trend.DOWN
        if p.resonance:
            # "走势不一致→忍住不做"：仅高级别方向相反时拦；横盘为中性放行，
            # 完全同向才计共振标签（并触发重拳份数）
            if ctx.higher_trend is not None:
                if ctx.higher_trend == _opposite(dir_trend):
                    return [], False
                if ctx.higher_trend == dir_trend:
                    passed.append("resonance")
        if p.index_filter:
            if ctx.index_trend is not None and not index_permits(ctx.index_trend, dir_trend):
                return [], False
            if ctx.index_trend == dir_trend:
                passed.append("index")
        if p.time_window:
            in_window = _in_window(
                ctx.bar_time.time(), p.buy_window if up else p.sell_window
            )
            if not in_window:
                return [], False
            passed.append("window")
        return passed, True

    def _units_for(self, ctx: BarContext, direction: Direction) -> int:
        """共振窗口（主级别+高级别+大盘三者同向）→ 重拳出击多份。"""
        p = self.params
        dir_trend = Trend.UP if direction == Direction.FORWARD else Trend.DOWN
        if (
            p.resonance
            and ctx.primary_trend == dir_trend
            and ctx.higher_trend == dir_trend
            and ctx.index_trend == dir_trend
        ):
            return p.resonance_units
        return p.units_per_trade

    def _exit_signal(self, ctx: BarContext) -> bool:
        """对冲腿触发（§2.4）：RSI 反向阈值 ∨ 分型/影线 ∨ 偏离度超阈。"""
        p = self.params
        if self._pending_direction == Direction.FORWARD:
            return (
                ctx.rsi >= p.rsi_sell_th
                or (p.confirm_fractal and ctx.fractal_sell_ok)
                or (p.dev_th > 0 and ctx.dev_above_ma)
            )
        if self._pending_direction == Direction.REVERSE:
            return (
                ctx.rsi <= p.rsi_buy_th
                or (p.confirm_fractal and ctx.fractal_buy_ok)
                or (p.dev_th > 0 and ctx.dev_below_ma)
            )
        return False

    def _planned_shares(self, price: float, units: int) -> int:
        # 含滑点口径与账户扣款一致，防止临界资金下"决策成立、成交被拒"
        raw = self.unit_cash * units / (price * (1.0 + self.params.slippage))
        return int(raw // self.params.lot_size) * self.params.lot_size

    def _roll_day(self, bar_time: datetime) -> None:
        day = bar_time.date()
        if day != self._current_day:
            self._current_day = day
            self._trades_today = 0
            # 跨日死单清理：严格闭环模式下不允许隔夜挂单；
            # 宽松模式（strict_eod=False）下 LEG1_SUBMITTED 顺延到次日成交
            if self.params.strict_eod and self.state == TtState.LEG1_SUBMITTED:
                self.resolve_stale_order()
