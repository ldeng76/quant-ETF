"""量化 15m 重采样的替代实现收益。

profile 已证明 run_backtest 有 46% 时间花在 resample_intraday（pandas
resample+groupby）。这里对比三种实现的速度与一致性：

  A. 现状 backtest.resample_intraday（pandas resample.agg）
  B. numpy reduceat（session 分桶 + argsort，无 pandas groupby 开销）
  C. DuckDB time_bucket（与 optimize_t_trade_duckdb.py 的思路一致）

并检查 A/B/C 与原实现是否逐 bar 一致 —— 重采样错了等于策略全错。
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import duckdb
import numpy as np
import pandas as pd

from quant_etf.t_trade.backtest import resample_intraday


def _cumcount(a: np.ndarray) -> np.ndarray:
    """组内序号（numpy 版 groupby().cumcount()）。"""
    starts = np.flatnonzero(np.r_[True, a[1:] != a[:-1]])
    counts = np.diff(np.r_[starts, len(a)])
    return np.repeat(np.arange(len(starts)), counts)


def numpy_resample_15m(df: pd.DataFrame) -> pd.DataFrame:
    """AM/PM 各自按 3 根 5m bar 一桶，用 reduceat 聚合，避开 pandas groupby。"""
    t = pd.to_datetime(df["time"])
    key = ((t.dt.normalize().astype("int64") // 86_400_000_000_000) * 2
           + (t.dt.hour >= 13).astype(np.int64)).to_numpy()
    bucket = key * 64 + (_cumcount(key) // 3)
    order = np.argsort(bucket, kind="stable")
    b = bucket[order]
    starts = np.flatnonzero(np.r_[True, b[1:] != b[:-1]])
    ends = np.r_[starts[1:], len(b) - 1]          # 每组最后一根（与 starts 等长）
    g = lambda col: df[col].to_numpy()[order]  # noqa: E731
    o, h, lo, c, v = g("open"), g("high"), g("low"), g("close"), g("volume")
    return pd.DataFrame({
        "time": t.to_numpy()[order][ends],
        "open": o[starts],
        "high": np.maximum.reduceat(h, starts),
        "low": np.minimum.reduceat(lo, starts),
        "close": c[ends],
        "volume": np.add.reduceat(v, starts),
    })


def duckdb_resample_15m(con, code: str) -> pd.DataFrame:
    p = (ROOT / "data/cache/ttrade_minute_bars_5m.parquet").as_posix()
    sql = f"""
    WITH o AS (
        SELECT time, open, high, low, close, volume,
               CASE WHEN EXTRACT(HOUR FROM time) < 12 THEN 0 ELSE 1 END AS sess,
               ROW_NUMBER() OVER (PARTITION BY DATE(time),
                 CASE WHEN EXTRACT(HOUR FROM time) < 12 THEN 0 ELSE 1 END
                 ORDER BY time) - 1 AS seq
        FROM read_parquet('{p}') WHERE code = '{code}'
    )
    SELECT MAX(time) AS time, FIRST(open ORDER BY time) AS open,
           MAX(high) AS high, MIN(low) AS low,
           LAST(close ORDER BY time) AS close, SUM(volume) AS volume
    FROM o GROUP BY DATE(time), sess, seq // 3 ORDER BY time
    """
    df = con.execute(sql).df()
    df["time"] = pd.to_datetime(df["time"])
    return df


def compare(a: pd.DataFrame, b: pd.DataFrame, name: str) -> None:
    if len(a) != len(b):
        print(f"  {name}: ★ bar 数不同 {len(a)} vs {len(b)}")
        return
    cols = ["open", "high", "low", "close", "volume"]
    diffs = {c: float(np.abs(a[c].to_numpy() - b[c].to_numpy()).max()) for c in cols}
    tol = 1e-6 if name == "duckdb" else 1e-4
    bad = {k: v for k, v in diffs.items() if v > tol}
    # 时间戳对齐检查
    ta = pd.to_datetime(a["time"]).to_numpy()
    tb = pd.to_datetime(b["time"]).to_numpy()
    tdiff = int((ta != tb).sum())
    print(f"  {name}: bar 数一致({len(a)})，时间戳差异 {tdiff}，"
          f"数值最大偏差 {max(diffs.values()):.2e}"
          + (f" ★ 超差 {bad}" if bad else " → 一致"))


if __name__ == "__main__":
    import pickle
    with open(ROOT / "data/cache/tbars_full2y.pkl", "rb") as f:
        bars = pickle.load(f)
    code = "159869"
    df = bars[code].reset_index(drop=True)
    print(f"样本：{code}，{len(df):,} 根 5m bar\n")

    print("=" * 66)
    print("1. 速度对比（3 轮取最优）")
    print("=" * 66)
    con = duckdb.connect()

    def best(fn, n=3):
        out = []
        for _ in range(n):
            t0 = time.perf_counter()
            fn()
            out.append(time.perf_counter() - t0)
        return min(out)

    t_pandas = best(lambda: resample_intraday(df, "15min"))
    t_numpy = best(lambda: numpy_resample_15m(df))
    t_ddb = best(lambda: duckdb_resample_15m(con, code))
    print(f"  A pandas resample.agg : {t_pandas*1000:8.1f} ms   (现状)")
    print(f"  B numpy reduceat      : {t_numpy*1000:8.1f} ms   "
          f"({t_pandas/t_numpy:.1f}x)")
    print(f"  C DuckDB time_bucket  : {t_ddb*1000:8.1f} ms   "
          f"({t_pandas/t_ddb:.1f}x)")

    print()
    print("=" * 66)
    print("2. 与现状逐 bar 一致性")
    print("=" * 66)
    a = resample_intraday(df, "15min").reset_index(drop=True)
    compare(a, numpy_resample_15m(df).reset_index(drop=True), "numpy   ")
    compare(a, duckdb_resample_15m(con, code).reset_index(drop=True), "duckdb ")

    print()
    print("=" * 66)
    print("3. 折算到 run_backtest 层面（单只 16.1s，resample 占 46%）")
    print("=" * 66)
    t_loop = 16.1 - t_pandas          # 去掉 resample 后的回测循环时间
    print(f"  回测循环(其余)约 {t_loop:.1f}s，resample 约 {t_pandas:.1f}s")
    for nm, t in (("现状", t_pandas), ("numpy", t_numpy), ("duckdb", t_ddb)):
        print(f"  {nm:7s}: 单只 {t_loop + t:5.2f}s → 69 只单线程 "
              f"{(t_loop + t)*69/60:5.1f} 分钟，"
              f"8 核并行约 {((t_loop + t)*69/8)/60:4.1f} 分钟")
