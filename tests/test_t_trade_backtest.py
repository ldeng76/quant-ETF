"""做T回测合成数据冒烟：箱体/单边上涨/恒定价三例 + 产物完整性。"""

import numpy as np
import pandas as pd
import pytest

from quant_etf.t_trade.backtest import run_backtest, write_outputs
from quant_etf.t_trade.params import TTradeParams

PARAMS = TTradeParams()


def _bars(prices: list[float], start="2026-03-02") -> pd.DataFrame:
    """价格序列 → 交易日对齐的 5m bars：每天 48 根，真实交易时段
    （上午 09:35→11:30 共 24 根 + 下午 13:05→15:00 共 24 根，收盘标注）。"""
    p = np.asarray(prices, dtype=float)
    pad = (-len(p)) % 48
    if pad:
        p = np.append(p, np.full(pad, p[-1]))
    n_days = len(p) // 48
    days = pd.bdate_range(start, periods=n_days)
    frames = []
    for d in range(n_days):
        date_str = f"{days[d].date()}"
        morning = pd.date_range(f"{date_str} 09:35", periods=24, freq="5min")
        afternoon = pd.date_range(f"{date_str} 13:05", periods=24, freq="5min")
        day_times = morning.append(afternoon)
        seg = p[d * 48:(d + 1) * 48]
        frames.append(pd.DataFrame({
            "time": day_times, "open": seg, "high": seg + 0.001,
            "low": seg - 0.001, "close": seg, "volume": 1000.0,
            "amount": seg * 1000.0,
        }))
    return pd.concat(frames, ignore_index=True)


def _sideways_with_dip_and_spike() -> list[float]:
    """箱体（双向）：200 根平价暖机；急跌→收回触发正T；急拉→回落触发反T。
    横盘趋势下两个方向都被许可。"""
    base = [10.0] * 192
    dip = [10.0 - 0.08 * (i + 1) for i in range(12)]       # 正T入场
    recover = [9.04 + 0.08 * (i + 1) for i in range(12)]
    gap = [10.0] * 24
    spike = [10.0 + 0.08 * (i + 1) for i in range(12)]     # 反T入场（RSI→超买）
    drop = [10.96 - 0.08 * (i + 1) for i in range(12)]
    tail = [10.0] * 36
    return base + dip + recover + gap + spike + drop + tail


def _uptrend_with_dip() -> list[float]:
    """单边上涨：稳步上行（斜率>阈值→上行趋势），中段一次急跌
    12 根（RSI→超卖）→ 只许正T（上行禁止反T）。"""
    rise = [10.0 + 0.02 * i for i in range(150)]
    dip = [13.0 - 0.10 * (i + 1) for i in range(12)]
    resume = [11.8 + 0.05 * (i + 1) for i in range(80)]
    return rise + dip + resume + [17.8 + 0.02 * i for i in range(60)]


def _flat() -> list[float]:
    """恒定价：无差价空间（RSI 恒定无信号、振幅≈0）→ 零交易。"""
    return [10.0] * 320


class TestSyntheticSmoke:
    def test_sidewise_box_generates_both_directions(self):
        bars = _bars(_sideways_with_dip_and_spike())
        result = run_backtest(["TEST"], {"TEST": bars}, PARAMS, total_cash=100_000.0)
        closed = [t for t in result.trades if np.isfinite(t.get("profit", np.nan))]
        directions = {t["direction"] for t in closed}
        assert len(closed) >= 1, f"箱体应产生T单，实际 {result.trades}"
        assert directions >= {"正T", "反T"}, f"箱体应双向T，实际 {directions}"

    def test_uptrend_only_forward_t(self):
        bars = _bars(_uptrend_with_dip())
        result = run_backtest(["TEST"], {"TEST": bars}, PARAMS, total_cash=100_000.0)
        directions = {t["direction"] for t in result.trades if "direction" in t}
        assert "反T" not in directions, f"上行趋势禁止反T，实际 {directions}"
        closed = [t for t in result.trades if np.isfinite(t.get("profit", np.nan))]
        assert len(closed) >= 1

    def test_flat_price_zero_trades(self):
        bars = _bars(_flat())
        result = run_backtest(["TEST"], {"TEST": bars}, PARAMS, total_cash=100_000.0)
        closed = [t for t in result.trades if np.isfinite(t.get("profit", np.nan))]
        assert len(closed) == 0
        # 没有任何交易 → 做T与纯持有完全一致
        assert result.final_equity == pytest.approx(result.final_bh_equity)

    def test_outputs_complete(self, tmp_path):
        bars = _bars(_sideways_with_dip_and_spike())
        result = run_backtest(["TEST"], {"TEST": bars}, PARAMS, total_cash=100_000.0)
        paths = write_outputs(result, PARAMS, tmp_path)
        for name in ("trades", "equity", "summary", "params"):
            assert name in paths and paths[name].exists()
        summary = paths["summary"].read_text(encoding="utf-8")
        assert "样本区间" in summary and "做T vs 纯持有" in summary
        assert "平均差价" in summary

    def test_same_params_rerun_identical(self):
        bars = _bars(_sideways_with_dip_and_spike())
        r1 = run_backtest(["TEST"], {"TEST": bars}, PARAMS, total_cash=100_000.0)
        r2 = run_backtest(["TEST"], {"TEST": bars}, PARAMS, total_cash=100_000.0)
        assert r1.trades == r2.trades
        assert r1.final_equity == r2.final_equity
        assert r1.total_profit == r2.total_profit
