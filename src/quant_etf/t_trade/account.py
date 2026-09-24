"""做T虚拟账户：底仓/机动仓份制核算，全部成交的唯一入口。

铁律在账户层强制：等量对倒断言、T+1 可卖量、机动份余量。
费用模型：佣金 = max(成交额 × rate, min_commission)，ETF 免印花税；
滑点在成交价上体现——买入劣化（×(1+slippage)），卖出劣化（×(1−slippage)）。

注意：正T对冲腿卖出的是底仓昨仓（当日买入的机动份额当日不可卖），
close_t 的 T+1 校验依赖此约定——单量不得超过底仓规模。
"""

from dataclasses import dataclass

from .classifier import Direction


@dataclass(frozen=True)
class Fill:
    """一笔成交。"""

    action: str  # "buy" / "sell"
    shares: int
    price: float  # 含滑点的成交价
    commission: float


class TOrderRejected(Exception):
    """账户层拒绝：T+1 可卖量不足 / 现金不足 / 已有在场T单 / 数量非法。"""


class SubAccount:
    """单标的独立等权子账户。

    底仓（base_shares）期初一次建满、数量恒定，是做T的"根据地"；
    资金按 base_units : mobile_units 拆分（默认 10:10 即五五开），
    每份机动资金 unit_cash = 机动总资金 / mobile_units。
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
        # "10份底仓 + 10份资金" = 20 等份；每份机动 = 总资金 / 总份数
        total_units = base_units + mobile_units
        self.unit_cash = self.initial_cash / total_units
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
        self._pending_t_shares: int | None = None  # 开仓腿数量，未对冲
        self._pending_direction: Direction | None = None
        self._open_leg_net = 0.0  # 开仓腿净现金流（买入为负、卖出为正，含佣金）

    # ---- 查询 ----

    @property
    def available_to_sell(self) -> int:
        """T+1 可卖量：昨仓 − 当日已卖。"""
        return self.shares_at_day_start - self.sold_today

    @property
    def pending_t_shares(self) -> int | None:
        """当前未平 T 单的开仓腿数量；无在场 T 单时为 None。"""
        return self._pending_t_shares

    # ---- 成交 ----

    def _commission(self, value: float) -> float:
        return max(value * self.commission_rate, self.min_commission)

    def open_base_position(self, price: float) -> Fill:
        """期初建满底仓：底仓预算按整百买入。"""
        if self.base_shares > 0:
            raise TOrderRejected("base position already opened")
        total_units = self.base_units + self.mobile_units
        budget = self.initial_cash * self.base_units / total_units
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
        return Fill("buy", shares, buy_price, commission)

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
        return Fill("buy", shares, buy_price, commission)

    def _buy_unchecked(self, shares: int, price: float) -> Fill:
        """强平专用买入：跳过现金校验（现金可小幅透支），其余校验照常。"""
        if shares <= 0 or shares % self.lot_size != 0:
            raise TOrderRejected("shares must be positive lot multiples")
        buy_price = price * (1.0 + self.slippage)
        value = shares * buy_price
        commission = self._commission(value)
        self.shares_held += shares
        self.cash -= value + commission
        return Fill("buy", shares, buy_price, commission)

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
        return Fill("sell", shares, sell_price, commission)

    def open_t(self, direction: Direction, shares: int, price: float) -> Fill:
        """开仓腿：正T（先买）或反T（先卖底仓）。同时在场 T 单唯一。"""
        if self._pending_t_shares is not None:
            raise TOrderRejected("pending T trade exists, cannot open another")
        if direction == Direction.FORWARD:
            fill = self.buy(shares, price)
            self._open_leg_net = -(fill.price * shares + fill.commission)
        else:
            fill = self.sell(shares, price)
            self._open_leg_net = fill.price * shares - fill.commission
        self._pending_t_shares = shares
        self._pending_direction = direction
        return fill

    def close_t(self, price: float, force: bool = False) -> tuple[Fill, float]:
        """对冲腿：与开仓腿反向、**等量**，闭环即结算摊薄。返回 (成交, 差价)。

        差价（正为盈）已含两腿佣金，结算时摊入底仓成本。
        force=True 用于 14:55 强平：对冲腿是铁律、必须成交，现金不足时
        允许透支（现金可为小幅负值，等价于当日融资），不因余额拒绝。
        """
        if self._pending_t_shares is None:
            raise TOrderRejected("no pending T trade to close")
        shares = self._pending_t_shares
        if self._pending_direction == Direction.FORWARD:
            fill = self.sell(shares, price)
            close_net = fill.price * shares - fill.commission
        else:
            fill = self._buy_unchecked(shares, price) if force else self.buy(shares, price)
            close_net = -(fill.price * shares + fill.commission)
        profit = self._open_leg_net + close_net
        self._pending_t_shares = None
        self._pending_direction = None
        self.diluted_cost_total -= profit
        return fill, profit

    def base_intact(self) -> bool:
        """底仓对账断言：收盘时总持仓必须等于底仓（等量铁律）。"""
        return self.shares_held == self.base_shares

    @property
    def diluted_cost_per_share(self) -> float:
        """摊薄成本/股：差价累计摊入底仓后的持有成本，可趋近 0 甚至负数。"""
        if self.base_shares == 0:
            return 0.0
        return self.diluted_cost_total / self.base_shares

    # ---- 日切 ----

    def on_day_start(self) -> None:
        """日切：昨仓刷新、当日已卖清零。"""
        self.sold_today = 0
        self.shares_at_day_start = self.shares_held
