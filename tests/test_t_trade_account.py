"""做T虚拟账户测试：底仓建仓 / 正反T两腿 / T+1 / 份耗尽 / 摊薄成本。"""

import pytest

from quant_etf.t_trade.account import SubAccount, TOrderRejected


class TestOpenBasePosition:
    def test_base_position_uses_half_cash_in_lots(self):
        # 总资金 100_000：底仓预算 50_000；价 10.00 → 5_000 股（整百）
        acc = SubAccount(total_cash=100_000.0)
        fill = acc.open_base_position(price=10.0)
        assert fill.shares == 5_000
        assert acc.base_shares == 5_000
        assert acc.shares_held == 5_000

    def test_cash_after_open_reflects_fees(self):
        acc = SubAccount(total_cash=100_000.0, commission_rate=1e-4, slippage=0.0)
        fill = acc.open_base_position(price=10.0)
        # 买入价 10.0（无滑点），成交额 50_000，佣金 5.0
        assert fill.price == pytest.approx(10.0)
        assert fill.commission == pytest.approx(5.0)
        assert acc.cash == pytest.approx(100_000.0 - 50_000.0 - 5.0)

    def test_lot_rounding_down(self):
        # 价 10.03 → 50_000/10.03 = 4985.04… → 取整 4900 股
        acc = SubAccount(total_cash=100_000.0, slippage=0.0)
        fill = acc.open_base_position(price=10.03)
        assert fill.shares == 4_900
        assert acc.base_shares == 4_900


class TestFeeModel:
    def test_slippage_worse_price_on_both_sides(self):
        acc = SubAccount(total_cash=1_000_000.0, commission_rate=0.0, slippage=0.001)
        acc.open_base_position(price=10.0)
        buy_fill = acc.buy(shares=1_000, price=10.0)
        sell_fill = acc.sell(shares=1_000, price=10.0)
        assert buy_fill.price == pytest.approx(10.01)
        assert sell_fill.price == pytest.approx(9.99)

    def test_min_commission_floor(self):
        acc = SubAccount(total_cash=1_000_000.0, commission_rate=1e-4, min_commission=5.0, slippage=0.0)
        acc.open_base_position(price=10.0)
        buy_fill = acc.buy(shares=100, price=10.0)
        # 成交额 1000 → 佣金 0.1 → 取最低 5.0
        assert buy_fill.commission == pytest.approx(5.0)


class TestTPlusOne:
    def test_sell_beyond_yesterday_position_rejected(self):
        acc = SubAccount(total_cash=1_000_000.0, slippage=0.0)
        acc.open_base_position(price=10.0)  # 底仓 50_000 股? 不，10万资金一半=5万/10=5000股
        with pytest.raises(TOrderRejected, match="T\\+1"):
            acc.sell(shares=acc.base_shares + 100, price=10.0)

    def test_available_restored_next_day(self):
        acc = SubAccount(total_cash=100_000.0, slippage=0.0)
        acc.open_base_position(price=10.0)  # 底仓 5000 股
        acc.sell(shares=1000, price=10.0)  # 反T开仓腿：卖底仓
        assert acc.available_to_sell == 4000
        with pytest.raises(TOrderRejected, match="T\\+1"):
            acc.sell(shares=4100, price=10.0)  # 超过当日剩余可卖量
        acc.on_day_start()
        assert acc.available_to_sell == 4000  # 昨仓=4000，当日清零


class TestTSettlement:
    def test_t_profit_dilutes_base_cost(self):
        acc = SubAccount(total_cash=100_000.0, slippage=0.0, commission_rate=0.0)
        acc.open_base_position(price=10.0)  # 5000 股，成本 50_000
        acc.settle_t_trade(t_profit=200.0)
        assert acc.diluted_cost_total == pytest.approx(49_800.0)
        assert acc.diluted_cost_per_share == pytest.approx(9.96)

    def test_t_loss_raises_base_cost(self):
        acc = SubAccount(total_cash=100_000.0, slippage=0.0, commission_rate=0.0)
        acc.open_base_position(price=10.0)
        acc.settle_t_trade(t_profit=-100.0)
        assert acc.diluted_cost_per_share == pytest.approx(10.02)

    def test_diluted_cost_can_go_negative(self):
        # 文章目标：成本趋近 0 甚至负数——累计差价超过建仓成本
        acc = SubAccount(total_cash=100_000.0, slippage=0.0, commission_rate=0.0)
        acc.open_base_position(price=10.0)
        acc.settle_t_trade(t_profit=60_000.0)
        assert acc.diluted_cost_per_share < 0


class TestTTradeLegs:
    def test_forward_t_full_cycle(self):
        # 正T：10 元买入 1000 股（机动仓）→ 10.2 卖出等量底仓
        acc = SubAccount(total_cash=100_000.0, slippage=0.0, commission_rate=0.0)
        acc.open_base_position(price=10.0)
        open_fill = acc.open_t(direction="up", shares=1000, price=10.0)
        assert open_fill.action == "buy"
        assert acc.pending_t_shares == 1000
        shares_before = acc.shares_held  # 6000（底仓5000+机动1000）
        close_fill, profit = acc.close_t(price=10.2)
        assert close_fill.action == "sell"
        assert close_fill.shares == 1000  # 等量铁律
        assert acc.shares_held == shares_before - 1000 == acc.base_shares  # 底仓复原
        assert acc.pending_t_shares is None
        assert profit == pytest.approx(200.0)
        assert acc.diluted_cost_total == pytest.approx(50_000.0 - 200.0)

    def test_reverse_t_full_cycle(self):
        # 反T：10.2 先卖 1000 股底仓 → 10.0 买回等量
        acc = SubAccount(total_cash=100_000.0, slippage=0.0, commission_rate=0.0)
        acc.open_base_position(price=10.0)
        open_fill = acc.open_t(direction="down", shares=1000, price=10.2)
        assert open_fill.action == "sell"
        assert acc.shares_held == 4000
        close_fill, profit = acc.close_t(price=10.0)
        assert close_fill.action == "buy"
        assert close_fill.shares == 1000
        assert acc.shares_held == acc.base_shares
        assert profit == pytest.approx(200.0)

    def test_base_intact_invariant(self):
        acc = SubAccount(total_cash=100_000.0, slippage=0.0, commission_rate=0.0)
        acc.open_base_position(price=10.0)
        acc.open_t(direction="up", shares=1000, price=10.0)
        acc.close_t(price=10.1)
        assert acc.base_intact()

    def test_cannot_open_second_t_while_one_pending(self):
        acc = SubAccount(total_cash=100_000.0, slippage=0.0, commission_rate=0.0)
        acc.open_base_position(price=10.0)
        acc.open_t(direction="up", shares=1000, price=10.0)
        with pytest.raises(TOrderRejected, match="pending"):
            acc.open_t(direction="up", shares=1000, price=10.0)

    def test_cannot_settle_while_pending(self):
        acc = SubAccount(total_cash=100_000.0, slippage=0.0, commission_rate=0.0)
        acc.open_base_position(price=10.0)
        acc.open_t(direction="up", shares=1000, price=10.0)
        with pytest.raises(TOrderRejected):
            acc.settle_t_trade(t_profit=0.0)


class TestParamsSnapshot:
    def test_json_roundtrip_preserves_all_fields(self):
        from quant_etf.t_trade.params import TTradeParams

        p = TTradeParams()
        p2 = TTradeParams.from_json(p.to_json())
        assert p == p2

    def test_modified_params_roundtrip(self):
        from quant_etf.t_trade.params import TTradeParams

        p = TTradeParams(rsi_buy_th=30.0, primary_level="15m", strict_eod=False)
        p2 = TTradeParams.from_json(p.to_json())
        assert p2.rsi_buy_th == 30.0
        assert p2.primary_level == "15m"
        assert p2.strict_eod is False
        assert p2.buy_window == ("09:35", "11:00")
