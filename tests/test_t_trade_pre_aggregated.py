"""pre_aggregated 快路径：跳过主级别重采样，但结果必须逐笔一致。

背景：run_backtest 在 primary_level="15m" 时会对 bars 做一次
resample_intraday(df, "15min")。该实现对 2 年数据要先生成 71,633 个 bin
（63,732 个空 bin），单只标的 ~10 秒——cProfile 显示它占 run_backtest 总耗时
46%。外部用 DuckDB time_bucket 预聚合出 15m 后，这一步是等价重复计算。

本测试锁死两件事：
  1. pre_aggregated=True 时不改变任何回测结果
  2. 外部预聚合的 15m 数据能被正确识别为 drive（而不是被再聚合一次）
"""

import dataclasses

import numpy as np
import pandas as pd
import pytest

from quant_etf.t_trade.backtest import resample_intraday, run_backtest
from quant_etf.t_trade.params import TTradeParams


def _synthetic_bars(n_days: int = 40) -> pd.DataFrame:
    """造一段规整的 A 股 5m 日线（48 根/日，含午休空档）。"""
    stamps = []
    for d in pd.bdate_range("2025-01-06", periods=n_days):
        am = pd.date_range(d + pd.Timedelta(hours=9, minutes=35),
                           d + pd.Timedelta(hours=11, minutes=30), freq="5min")
        pm = pd.date_range(d + pd.Timedelta(hours=13),
                           d + pd.Timedelta(hours=15), freq="5min")
        stamps.extend(list(am) + list(pm))
    t = pd.Series(stamps)
    rng = np.random.default_rng(7)
    walk = np.cumsum(rng.normal(0, 0.002, len(t))) + 2.0
    return pd.DataFrame({
        "time": t,
        "open": walk,
        "high": walk + 0.004,
        "low": walk - 0.004,
        "close": walk + 0.001,
        "volume": rng.integers(1000, 5000, len(t)).astype(float),
    })


def _params15(**kw) -> TTradeParams:
    p = TTradeParams(ma_len=20, slope_n=4, rsi_len=6)
    return dataclasses.replace(p, primary_level="15m", **kw)


@pytest.fixture(scope="module")
def df5():
    return _synthetic_bars()


def _fingerprint(res) -> tuple:
    return (round(res.final_equity, 6), round(res.final_bh_equity, 6),
            len(res.trades), res.win_count)


def test_pre_aggregated_matches_pandas_path(df5):
    """核心：预聚合 15m + pre_aggregated=True == 原 pandas 重采样路径。"""
    params = _params15()

    base = run_backtest(["X"], {"X": df5}, params, 100_000)

    agg = resample_intraday(df5, "15min").reset_index()
    pre = run_backtest(["X"], {"X": agg}, params, 100_000, pre_aggregated=True)

    assert _fingerprint(pre) == _fingerprint(base), (
        f"预聚合路径与原路径不一致: {pre.trades} 笔 "
        f"{round(pre.final_equity, 2)} vs {base.trades} 笔 "
        f"{round(base.final_equity, 2)}"
    )


def test_pre_aggregated_noop_on_5m(df5):
    """5m 主级别下 pre_aggregated 是 no-op：本来就不做主级别重采样。"""
    params = TTradeParams(ma_len=20, slope_n=4, rsi_len=6)
    assert _fingerprint(
        run_backtest(["X"], {"X": df5}, params, 100_000, pre_aggregated=True)
    ) == _fingerprint(run_backtest(["X"], {"X": df5}, params, 100_000))


def test_pre_aggregated_actually_skips_resample(df5, monkeypatch):
    """确认快路径真的没调用 resample_intraday，而不是碰巧结果相同。"""
    import quant_etf.t_trade.backtest as bt

    calls = []
    real = bt.resample_intraday

    def counting(df, rule="15min"):
        calls.append(rule)
        return real(df, rule)

    monkeypatch.setattr(bt, "resample_intraday", counting)
    agg = real(df5, "15min").reset_index()

    bt.run_backtest(["X"], {"X": agg}, _params15(), 100_000,
                    pre_aggregated=True)
    # 只允许高级别（60m）那一次，主级别 15min 那次必须没有
    assert "15min" not in calls, f"主级别重采样仍被调用: {calls}"

    calls.clear()
    bt.run_backtest(["X"], {"X": df5}, _params15(), 100_000)
    assert "15min" in calls, "原路径应仍然调用主级别重采样"


def test_insufficient_pre_aggregated_is_skipped_not_crashed(df5):
    """预聚合数据太短时应优雅跳过，而不是抛异常。"""
    agg = resample_intraday(df5, "15min").reset_index().head(5)
    res = run_backtest(["X"], {"X": agg}, _params15(), 100_000,
                       pre_aggregated=True)
    assert res.codes == [] or res.final_equity == 0.0
