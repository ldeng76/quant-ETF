"""M4a 批量实验的结构性测试：消融/矩阵/成本网格/隔夜对照。"""

import numpy as np
import pandas as pd

from quant_etf.t_trade.metrics import (
    run_ablation,
    run_cost_grid,
    run_eod_compare,
    run_level_rsi_matrix,
)
from quant_etf.t_trade.params import TTradeParams


def _bars(prices: list[float], start="2026-03-02") -> pd.DataFrame:
    p = np.asarray(prices, dtype=float)
    pad = (-len(p)) % 48
    if pad:
        p = np.append(p, np.full(pad, p[-1]))
    n_days = len(p) // 48
    days = pd.bdate_range(start, periods=n_days)
    frames = []
    for d in range(n_days):
        date_str = f"{days[d].date()}"
        times = pd.date_range(f"{date_str} 09:35", periods=24, freq="5min").append(
            pd.date_range(f"{date_str} 13:05", periods=24, freq="5min")
        )
        seg = p[d * 48:(d + 1) * 48]
        vol = 2000.0 - 30.0 * np.arange(48)
        frames.append(pd.DataFrame({
            "time": times, "open": seg, "high": seg + 0.001,
            "low": seg - 0.001, "close": seg, "volume": vol,
            "amount": seg * vol,
        }))
    return pd.concat(frames, ignore_index=True)


def _box() -> list[float]:
    base = [10.0] * 192
    dip = [10.0 - 0.08 * (i + 1) for i in range(12)]
    recover = [9.04 + 0.08 * (i + 1) for i in range(12)]
    gap = [10.0] * 24
    spike = [10.0 + 0.08 * (i + 1) for i in range(12)]
    drop = [10.96 - 0.08 * (i + 1) for i in range(12)]
    tail = [10.0] * 36
    return base + dip + recover + gap + spike + drop + tail


PARAMS = TTradeParams()
BARS = {"TEST": _bars(_box())}


def test_ablation_shape_and_baseline():
    df = run_ablation(["TEST"], BARS, PARAMS, 100_000.0)
    assert len(df) == 8  # 基线 + 6 开关 + 逆势T
    assert df.iloc[0]["config"] == "baseline(全开)"
    assert {"marginal_excess", "excess", "trades"} <= set(df.columns)


def test_cost_grid_rows():
    df = run_cost_grid(["TEST"], BARS, PARAMS, 100_000.0)
    assert len(df) == 3 * 3 * 2
    assert set(df["slippage_bp"]) == {0.0, 5.0, 10.0}


def test_level_rsi_matrix_rows():
    df = run_level_rsi_matrix(["TEST"], BARS, PARAMS, 100_000.0)
    assert len(df) == 4
    assert set(df["primary_level"]) == {"5m", "15m"}


def test_eod_compare_rows():
    df = run_eod_compare(["TEST"], BARS, PARAMS, 100_000.0)
    assert len(df) == 2
    assert set(df["strict_eod"]) == {True, False}
