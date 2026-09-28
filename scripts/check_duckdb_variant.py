"""DuckDB 版评估：parquet 完整性 + 两版结果一致性 + 计时对比。"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import duckdb
import pandas as pd

P = (ROOT / "data/cache/ttrade_minute_bars_5m.parquet").as_posix()

print("=" * 70)
print("1. parquet 内容体检")
print("=" * 70)
con = duckdb.connect()
r = con.execute(
    f"SELECT count(*) AS n, count(DISTINCT code) AS codes, "
    f"min(time) AS t0, max(time) AS t1 FROM read_parquet('{P}')"
).fetchdf()
print(r.to_string(index=False))
print("\ncolumns:", con.execute(
    f"DESCRIBE SELECT * FROM read_parquet('{P}')").fetchdf()["column_name"].tolist())
print("\ndtypes:")
print(con.execute(f"SELECT * FROM read_parquet('{P}') LIMIT 1").df().dtypes.to_string())
print("\n每只 bar 数（分位）:")
cnt = con.execute(
    f"SELECT code, count(*) AS n FROM read_parquet('{P}') GROUP BY code"
).fetchdf()["n"]
print(cnt.describe().round(0).to_dict())
print("\n深度不足(<2000 根)的 code:",
      sorted(con.execute(
          f"SELECT code, count(*) n FROM read_parquet('{P}') GROUP BY code "
          f"HAVING n < 2000").fetchdf()["code"].astype(str).tolist()))

print()
print("=" * 70)
print("2. 与 PG 现查逐只比对（抽样 3 只，验证 parquet 没有静默截断）")
print("=" * 70)
from quant_etf.minute_collector import query_minute_data
for code in ["510050", "588000", "159869"]:
    df = query_minute_data(code, limit=500_000)
    pq = con.execute(
        f"SELECT count(*) n FROM read_parquet('{P}') WHERE code = '{code}'"
    ).fetchdf()["n"].iloc[0]
    same = (len(df) == pq) if df is not None else (pq == 0)
    print(f"  {code}: PG {len(df) if df is not None else 0} 根 vs parquet {pq} 根 "
          f"→ {'一致' if same else '★不一致'}")

print()
print("=" * 70)
print("3. DuckDB 版 nulltest numpy 标量 bug 复现")
print("=" * 70)
import dataclasses
import numpy as np
from quant_etf.t_trade.params import TTradeParams
GRID = {"rsi_len": [2, 3, 6, 9, 14], "resonance": [True, False]}
rng = np.random.default_rng(42)
p = TTradeParams()
for dim, vals in GRID.items():
    v = rng.choice(vals)
    p = dataclasses.replace(p, **{dim: v})
    print(f"  rng.choice({dim}) → {v!r}  type={type(v).__name__}")
try:
    s = p.to_json()
    print("  to_json() → OK")
except TypeError as e:
    print(f"  to_json() → ★ TypeError: {e}")

print()
print("=" * 70)
print("4. 15m 聚合 SQL 拼接实测（死代码，但语法是否成立）")
print("=" * 70)
import importlib.util
spec = importlib.util.spec_from_file_location(
    "ddb", ROOT / "scripts" / "optimize_t_trade_duckdb.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

codes, start, end = ["510050"], "2024-09-09", "2025-12-31"
clauses = ["code = ANY(?)"]
params = [list(codes)]
clauses.append("time >= CAST(? AS TIMESTAMP)")
params.append(m._to_ts(start))
clauses.append("time <= CAST(? AS TIMESTAMP)")
params.append(m._to_ts(end))
time_clause = " AND " + " AND ".join(clauses[1:])
src = (f"read_parquet('{m.BARS_PARQUET.as_posix()}') WHERE code = ANY(?) "
       f"{time_clause}")
sql = m._AGG_15M_SQL.format(src=src, time_clause="")
print("拼接结果的关键两行：")
for line in sql.splitlines():
    if "FROM read_parquet" in line or "WHERE code = ANY" in line:
        print("   ", line.strip())
con2 = duckdb.connect()
try:
    con2.execute(sql, params).fetchdf()
    print("  执行 → OK")
except Exception as e:
    print(f"  执行 → ★ {type(e).__name__}: {str(e).splitlines()[0]}")

print()
print("=" * 70)
print("5. 死代码 / CLI 差异清单")
print("=" * 70)
src_text = (ROOT / "scripts" / "optimize_t_trade_duckdb.py").read_text(encoding="utf-8")
for fn in ("ensure_15m_bars", "build_15m_parquet"):
    calls = src_text.count(fn) - src_text.count(f"def {fn}")
    print(f"  {fn}: 定义 1 处，调用 {calls} 处")
grid_block = src_text.split("PARAM_GRID")[1].split("\n}")[0]
print("  PARAM_GRID 含 long_t_only:", "long_t_only" in grid_block)
print("  CLI 含 --resume:", "--resume" in src_text)
print("  stage_final 时间窗:", "OOS段" if "oos_start or args.start" in src_text else "全区间")
