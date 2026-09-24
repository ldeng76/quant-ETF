"""做T虚拟账户：底仓/机动仓份制核算，全部成交的唯一入口。

铁律在账户层强制：等量对倒断言、T+1 可卖量、机动份余量。
费用模型：佣金 = max(成交额 × rate, min_commission)，ETF 免印花税；
滑点在成交价上体现——买入劣化（×(1+slippage)），卖出劣化（×(1−slippage)）。
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Fill:
    """一笔成交。"""

    action: str  # "buy" / "sell"
    shares: int
    price: float  # 含滑点的成交价
    commission: float
    fee_total: float  # ETF 仅佣金


class TOrderRejected(Exception):
    """账户层拒绝：T+1 可卖量不足 / 现金不足 / 数量非法。"""


class SubAccount:
    """单标的独立等权子账户。

    底仓（base_shares）期初一次建满、数量恒定，是做T的"根据地"；
    机动资金均分为 mobile_units 份，每份金额 = 初始机动资金 / 份数。
    """

    def __init__(
        self,
        total_cash: float,
        base_units: int = 10,
        mobile_units: int = 10,
        commission_rate: float = 1e-4,
        min_commission: float = 0.0,
        slippage: float = 5e-4,
        lot_size: int = 100,
    ):
        if total_cash <= 0:
            raise ValueError("total_cash must be positive")
        self.initial_cash = float(total_cash)
        self.base_units = base_units
        self.mobile_units = mobile_units
        self.unit_cash = self.initial_cash / 2.0 / mobile_units
        self.commission_rate = commission_rate
        self.min_commission = min_commission
        self.slippage = slippage
        self.lot_size = lot_size

        self.cash = float(total_cash)
        self.shares_held = 0
        self.base_shares = 0
        self.sold_today = 0
        self.shares_at_day_start = 0
        self.diluted_cost_total = 0.0  # 底仓摊薄总成本
        self._pending_t_shares: Optional[int] = None  # 开仓腿数量，未对冲
        self._pending_direction: Optional[str] = None
        self._open_leg_net = 0.0  # 开仓腿净现金流（买入为负、卖出为正，含佣金）

    # ---- 查询 ----

    @property
    def available_to_sell(self) -> int:
        """T+1 可卖量：昨仓 − 当日已卖。"""
        return self.shares_at_day_start - self.sold_today

    @property
    def pending_t_shares(self) -> Optional[int]:
        """当前未平 T 单的开仓腿数量；无在场 T 单时为 None。"""
        return self._pending_t_shares

    def unit_shares(self, price: float, units: int = 1) -> int:
        """units 份机动资金按现价可买的股数（整百取整）。"""
        raw = self.unit_cash * units / price
        return int(raw // self.lot_size) * self.lot_size

    # ---- 成交 ----

    def _commission(self, value: float) -> float:
        return max(value * self.commission_rate, self.min_commission)

    def open_base_position(self, price: float) -> Fill:
        """期初建满底仓：底仓预算（总资金一半）按整百买入。"""
        if self.base_shares > 0:
            raise TOrderRejected("base position already opened")
        budget = self.initial_cash / 2.0
        shares = int(budget / price // self.lot_size) * self.lot_size
        if shares <= 0:
            raise TOrderRejected("budget too small for one lot")
        buy_price = price * (1.0 + self.slippage)
        value = shares * buy_price
        commission = self._commission(value)
        self.shares_held += shares
        self.base_shares = shares
        self.shares_at_day_start = shares
        self.cash -= value + commission
        self.diluted_cost_total = value + commission
        return Fill("buy", shares, buy_price, commission, commission)

    def buy(self, shares: int, price: float) -> Fill:
        """机动资金买入（正T开仓腿 / 反T对冲腿）。"""
        if shares <= 0 or shares % self.lot_size != 0:
            raise TOrderRejected("shares must be positive lot multiples")
        buy_price = price * (1.0 + self.slippage)
        value = shares * buy_price
        commission = self._commission(value)
        if value + commission > self.cash:
            raise TOrderRejected("insufficient mobile cash")
        self.shares_held += shares
        self.cash -= value + commission
        return Fill("buy", shares, buy_price, commission, commission)

    def sell(self, shares: int, price: float) -> Fill:
        """卖出（反T开仓腿卖底仓 / 正T对冲腿）。T+1 约束在此强制。"""
        if shares <= 0 or shares % self.lot_size != 0:
            raise TOrderRejected("shares must be positive lot multiples")
        if shares > self.available_to_sell:
            raise TOrderRejected(
                f"T+1: available {self.available_to_sell} < requested {shares}"
            )
        sell_price = price * (1.0 - self.slippage)
        value = shares * sell_price
        commission = self._commission(value)
        self.shares_held -= shares
        self.sold_today += shares
        self.cash += value - commission
        return Fill("sell", shares, sell_price, commission, commission)

    def open_t(self, direction: str, shares: int, price: float) -> Fill:
        """开仓腿：正T（direction="up"，先买）或反T（direction="down"，先卖底仓）。

        同时在场 T 单唯一（等量铁律的前置）。
        """
        if self._pending_t_shares is not None:
            raise TOrderRejected("pending T trade exists, cannot open another")
        if direction == "up":
            fill = self.buy(shares, price)
            self._open_leg_net = -(fill.price * shares + fill.commission)
        elif direction == "down":
            fill = self.sell(shares, price)
            self._open_leg_net = fill.price * shares - fill.commission
        else:
            raise ValueError(f"direction must be 'up'/'down', got {direction!r}")
        self._pending_t_shares = shares
        self._pending_direction = direction
        return fill

    def close_t(self, price: float) -> tuple[Fill, float]:
        """对冲腿：与开仓腿反向、**等量**，闭环即结算摊薄。返回 (成交, 差价)。"""
        if self._pending_t_shares is None:
            raise TOrderRejected("no pending T trade to close")
        shares = self._pending_t_shares
        if self._pending_direction == "up":
            fill = self.sell(shares, price)
            close_net = fill.price * shares - fill.commission
        else:
            fill = self.buy(shares, price)
            close_net = -(fill.price * shares + fill.commission)
        profit = self._open_leg_net + close_net
        self._pending_t_shares = None
        self._pending_direction = None
        self.diluted_cost_total -= profit
        return fill, profit

    def base_intact(self) -> bool:
        """底仓对账断言：收盘时总持仓必须等于底仓（等量铁律）。"""
        return self.shares_held == self.base_shares

    # ---- 日切与核算 ----

    def settle_t_trade(self, t_profit: float) -> None:
        """T 单闭环结算：差价（正为盈）摊入底仓，目标成本趋近 0 甚至负数。

        t_profit = 开仓腿与对冲腿的净差价（已含两边佣金）。
        """
        if self._pending_t_shares is not None:
            raise TOrderRejected("cannot settle: T trade still open")
        self.diluted_cost_total -= t_profit

    @property
    def diluted_cost_per_share(self) -> float:
        if self.base_shares == 0:
            return 0.0
        return self.diluted_cost_total / self.base_shares

    def on_day_start(self) -> None:
        """日切：昨仓刷新、当日已卖清零。"""
        self.sold_today = 0
        self.shares_at_day_start = self.shares_held
