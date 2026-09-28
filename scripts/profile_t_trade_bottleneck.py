"""定位回测瓶颈：证明真正的热点在 run_backtest 的逐 bar Python 循环，
而非数据加载层（pickle vs DuckDB 已实测 1.67x，收益有限）。

跑 cProfile：单只 ETF 全区间基线参数，输出累计耗时 top 12。
"""
import cProfile
import io
import pstats
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import pandas as pd

from quant_etf.t_trade.backtest import run_backtest
from quant_etf.t_trade.params import TTradeParams

PARAMS = TTradeParams.from_json(
    (ROOT / "data/results/2026-09-28/t_trade/params-realistic.json")
    .read_text(encoding="utf-8"))
CODE = "159869"

# ---- 数据加载耗时（两版对比的量级）----
t0 = time.time()
with open(ROOT / "data/cache/tbars_full2y.pkl", "rb") as f:
    import pickle
    bars = pickle.load(f)
t_pickle = time.time() - t0
df = bars[CODE].reset_index(drop=True)

# ---- 回测耗时（瓶颈）----
t0 = time.time()
run_backtest([CODE], {CODE: df}, PARAMS, 1_000_000, None)
t_bt = time.time() - t0

print(f"单只 {CODE} 全区间（{len(df):,} 根 5m bar）")
print(f"  pickle 反序列化全池 69 只 : {t_pickle:6.1f}s")
print(f"  run_backtest 单只        : {t_bt:6.1f}s")
print(f"  折算 69 只回测           : {t_bt*69:6.0f}s")
print(f"  worker 启动(原版8×pickle): {t_pickle*8/8:6.1f}s（并行摊薄后≈{t_pickle:.1f}s）")

print("\n" + "=" * 64)
print("cProfile：单只回测热点 top 12")
print("=" * 64)
pr = cProfile.Profile()
pr.enable()
run_backtest([CODE], {CODE: df}, PARAMS, 1_000_000, None)
pr.disable()
s = io.StringIO()
pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(12)
print("\n".join(s.getvalue().splitlines()[4:26]))
