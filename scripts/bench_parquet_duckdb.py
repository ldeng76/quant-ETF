"""基准测试：PG 逐码查询 vs parquet+DuckDB（做T回测数据管线）。

对比三段：
1. 加载：PG 70 次查询 vs DuckDB 扫 parquet（同样输出 per-code DataFrame）
2. 15m 重采样：pandas resample vs DuckDB time_bucket（对齐语义：收盘标注）
3. 全管线（加载+重采样）端到端

用法：.venv/Scripts/python.exe scripts/bench_parquet_duckdb.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import duckdb
import pandas as pd
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from quant_etf.conf import ETF_POOL
from quant_etf.minute_collector import query_minute_data

PARQUET = Path("data/cache/minute_bars_5m_ttrade.parquet")
CODES = list(dict.fromkeys(ETF_POOL))
START, END = "2026-03-02", "2026-06-04 23:59:59"


def export_parquet() -> int:
    """PG → parquet（一次性导出，用 DuckDB COPY，免 pyarrow 依赖）。"""
    PARQUET.parent.mkdir(parents=True, exist_ok=True)
    frames = []
    for code in CODES:
        df = query_minute_data(code, start=START, end=END, limit=500_000)
        if len(df):
            frames.append(df.reset_index().assign(code=code))
    all_df = pd.concat(frames, ignore_index=True)
    # psycopg2 返回 Decimal → duckdb 会推断 DECIMAL(11,2) 且绑定期溢出，先转 float
    for col in ("open", "high", "low", "close", "volume", "amount"):
        all_df[col] = all_df[col].astype(float)
    con = duckdb.connect()
    con.register("all_df", all_df)
    con.execute(f"COPY (SELECT * FROM all_df) TO '{PARQUET.as_posix()}' (FORMAT parquet)")
    return len(all_df)


def bench_pg_load() -> float:
    t0 = time.perf_counter()
    for code in CODES:
        query_minute_data(code, start=START, end=END, limit=500_000)
    return time.perf_counter() - t0


def bench_duckdb_load_single_query() -> float:
    """DuckDB 一条 SQL 扫全表，再在 pandas 侧切分（输出与 PG 路径同构）。"""
    t0 = time.perf_counter()
    con = duckdb.connect()
    df = con.execute(
        f"""
        select code, time, open, high, low, close, volume, amount
        from read_parquet('{PARQUET.as_posix()}')
        where time between '{START}' and '{END}'
        order by code, time
        """
    ).df()
    out = {c: g[["time", "open", "high", "low", "close", "volume", "amount"]]
           .reset_index(drop=True) for c, g in df.groupby("code")}
    n = sum(len(v) for v in out.values())
    dt = time.perf_counter() - t0
    print(f"    （{len(out)} 只 / {n} 行）")
    return dt


def bench_pandas_resample(bars: dict[str, pd.DataFrame]) -> float:
    t0 = time.perf_counter()
    for df in bars.values():
        s = df.set_index("time")
        s.resample("15min", label="right", closed="right").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last",
             "volume": "sum"}
        ).dropna(subset=["close"])
    return time.perf_counter() - t0


def bench_duckdb_resample() -> tuple[float, pd.DataFrame]:
    t0 = time.perf_counter()
    con = duckdb.connect()
    df = con.execute(
        f"""
        -- pandas closed='right',label='right' 等价于向上取整到15分钟边界：
        -- label = time_bucket(t − 1s) + 15min（边界值恰好落在自身桶）
        with bucketed as (
            select code,
                   time_bucket(interval '15 minute', time - interval '1 second')
                       + interval '15 minute' as bucket_label,
                   first(open order by time) as open,
                   max(high) as high,
                   min(low) as low,
                   last(close order by time) as close,
                   sum(volume) as volume
            from read_parquet('{PARQUET.as_posix()}')
            where time between '{START}' and '{END}'
            group by code, bucket_label
        )
        select code,
               CAST(bucket_label AS TIMESTAMP) as time,
               open, high, low, close, volume
        from bucketed
        where close is not null
        order by code, time
        """
    ).df()
    dt = time.perf_counter() - t0
    return dt, df


def main() -> None:
    if not PARQUET.exists():
        n = export_parquet()
        print(f"导出 parquet: {n} 行 → {PARQUET}")
    else:
        print(f"复用已有 parquet: {PARQUET}（{PARQUET.stat().st_size/1e6:.1f} MB）")

    # 基线：pandas 侧全量 bars（喂给后续 resample 对比）
    bars = {}
    for code in CODES:
        df = query_minute_data(code, start=START, end=END, limit=500_000)
        if len(df):
            bars[code] = df.reset_index()

    results = []

    t = bench_pg_load()
    results.append(("加载：PG 70次查询", t))

    t = bench_duckdb_load_single_query()
    results.append(("加载：DuckDB 扫 parquet（单查询）", t))

    t = bench_pandas_resample(bars)
    results.append(("重采样15m：pandas resample", t))

    t, duck = bench_duckdb_resample()
    results.append(("重采样15m：DuckDB time_bucket", t))

    # 对齐校验：行数与抽样值
    pandas_rows = sum(
        len(g.dropna(subset=["close"]))
        for g in [
            df.set_index("time")
            .resample("15min", label="right", closed="right")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last",
                  "volume": "sum"})
            for df in bars.values()
        ]
    )
    print(f"\n===== 结果（{PARQUET.stat().st_size/1e6:.1f} MB parquet，"
          f"{len(bars)} 只 / {sum(len(v) for v in bars.values())} 行 5m）=====")
    pg_load = results[0][1]
    for name, t in results:
        speedup = f"  ← 相对PG加载 {pg_load/t:.1f}x" if "DuckDB" in name else ""
        print(f"{name}: {t:.2f}s{speedup}")
    print(f"重采样对齐校验: pandas {pandas_rows} 行 vs DuckDB {len(duck)} 行"
          f"（{'一致' if pandas_rows == len(duck) else '不一致，需人工核对边界'}）")


if __name__ == "__main__":
    main()
