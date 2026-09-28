"""15m 路径数据等价性核查：两版日志的 bar 数换算不上比例，必须查清。

原版：5 只 IS 段 5m bar = 25,456（每只 5,091）
DuckDB：5 只 IS 段 15m bar = 15,152（每只 3,030）→ 折 5m 应为 45,456（每只 9,090）

若两版数据不同却给出相同交易结果，说明要么数字口径不同，要么存在更深的问题。
"""
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import duckdb
import pandas as pd

CODES = ["159869", "159560", "159780", "159567", "561700"]
IS_S, IS_E = "2024-09-09", "2025-12-31"

con = duckdb.connect()
p5 = (ROOT / "data/cache/ttrade_minute_bars_5m.parquet").as_posix()
p15 = (ROOT / "data/cache/ttrade_minute_bars_15m.parquet").as_posix()

print("=" * 72)
print("A. 原版路径：pickle 全池 → pandas 时间切片（_slice_period 复刻）")
print("=" * 72)
with open(ROOT / "data/cache/tbars_full2y.pkl", "rb") as f:
    full = pickle.load(f)
tot = 0
for c in CODES:
    df = full[c]
    sub = df[(df["time"] >= IS_S) & (df["time"] <= IS_E)]
    tot += len(sub)
    print(f"  {c}: 5m {len(sub):>6} 根 | {sub['time'].min()} → {sub['time'].max()}")
print(f"  合计 {tot:,} 根 5m")

print()
print("=" * 72)
print("B. DuckDB 5m parquet 同窗口 SQL 切片")
print("=" * 72)
tot5 = 0
for c in CODES:
    n = con.execute(
        f"SELECT count(*) n FROM read_parquet('{p5}') WHERE code = ? "
        f"AND time >= CAST('{IS_S} 00:00:00' AS TIMESTAMP) "
        f"AND time <= CAST('{IS_E} 00:00:00' AS TIMESTAMP)", [c]
    ).fetchdf()["n"].iloc[0]
    rng = con.execute(
        f"SELECT min(time) a, max(time) b FROM read_parquet('{p5}') WHERE code = ? "
        f"AND time >= CAST('{IS_S} 00:00:00' AS TIMESTAMP) "
        f"AND time <= CAST('{IS_E} 00:00:00' AS TIMESTAMP)", [c]
    ).fetchdf().iloc[0]
    tot5 += int(n)
    print(f"  {c}: 5m {n:>6} 根 | {rng['a']} → {rng['b']}")
print(f"  合计 {tot5:,} 根 5m")

print()
print("=" * 72)
print("C. DuckDB 15m parquet（预聚合产物）")
print("=" * 72)
tot15 = 0
for c in CODES:
    n = con.execute(
        f"SELECT count(*) n FROM read_parquet('{p15}') WHERE code = ? "
        f"AND time >= CAST('{IS_S} 00:00:00' AS TIMESTAMP) "
        f"AND time <= CAST('{IS_E} 00:00:00' AS TIMESTAMP)", [c]
    ).fetchdf()["n"].iloc[0]
    rng = con.execute(
        f"SELECT min(time) a, max(time) b FROM read_parquet('{p15}') WHERE code = ? "
        f"AND time >= CAST('{IS_S} 00:00:00' AS TIMESTAMP) "
        f"AND time <= CAST('{IS_E} 00:00:00' AS TIMESTAMP)", [c]
    ).fetchdf().iloc[0]
    tot15 += int(n)
    print(f"  {c}: 15m {n:>6} 根 | {rng['a']} → {rng['b']}")
print(f"  合计 {tot15:,} 根 15m")

print()
print("=" * 72)
print("D. 对账")
print("=" * 72)
print(f"  原版 5m 合计      : {tot:,}")
print(f"  DuckDB 5m 合计    : {tot5:,}   {'一致' if tot==tot5 else '★不一致'}")
print(f"  DuckDB 15m 合计   : {tot15:,}")
print(f"  15m × 3 应等于    : {tot15*3:,}")
print(f"  DuckDB 5m 合计    : {tot5:,}   "
      f"{'一致' if tot5==tot15*3 else f'★差 {tot15*3-tot5:,} 根（聚合丢数据？）'}")
print()
print(f"  日均 5m bar/只    : {tot5/len(CODES)/320:.1f}  （理论 48）")
