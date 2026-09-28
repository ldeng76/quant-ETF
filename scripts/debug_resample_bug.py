"""排查 resample_intraday 只产出 988 根（应为 7900）的原因。

这不是性能问题而是正确性问题：如果 backtest.higher_trend_series 的高级别
趋势只基于 12% 的区间算出来，那么 resonance / index_filter 门控在绝大部分
时间上是残废的 —— 整个策略的「高级别共振」能力实际未生效。
"""
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import numpy as np
import pandas as pd

from quant_etf.t_trade.backtest import resample_intraday

with open(ROOT / "data/cache/tbars_full2y.pkl", "rb") as f:
    bars = pickle.load(f)
code = "159869"
df = bars[code].reset_index(drop=True)

print(f"原始 df：{len(df):,} 行")
print("  dtypes:\n", df.dtypes.to_string())
print(f"  time 单调递增: {df['time'].is_monotonic_increasing}")
print(f"  time 重复数  : {df['time'].duplicated().sum()}")
print(f"  time 范围    : {df['time'].min()} → {df['time'].max()}")
print(f"  不同交易日数 : {df['time'].dt.normalize().nunique()}")

s = df.set_index("time")
print(f"\nset_index 后 index 类型: {type(s.index).__name__}")
print(f"  index 单调: {s.index.is_monotonic_increasing}")
print(f"  index 唯一: {s.index.is_unique}")

r = s.resample("15min", label="right", closed="right")
agg = r.agg({"open": "first", "high": "max", "low": "min",
             "close": "last", "volume": "sum"})
print(f"\nresample 后 bin 总数     : {len(agg):,}")
print(f"  dropna(close) 后剩余    : {agg.dropna(subset=['close']).shape[0]:,}")
print(f"  唯一 bar 数（期望）      : {len(df)//3:,}")

nn = agg["close"].notna()
print(f"\n非空 bin {int(nn.sum()):,} / 空 bin {int((~nn).sum()):,}")
if int(nn.sum()):
    idx = agg.index[nn]
    print(f"  非空 bin 时间跨度: {idx.min()} → {idx.max()}")
    gaps = idx.to_series().diff().dropna()
    print(f"  相邻非空 bin 的间隔分布(前5): "
          f"{gaps.value_counts().head(5).to_dict()}")
    # 每天有多少非空 bin
    per_day = pd.Series(1, index=idx).groupby(idx.normalize()).sum()
    print(f"  每天非空 bin 数: {per_day.describe()[['min','50%','max']].to_dict()}")
    print(f"  完整(16根/天)的交易日数: {int((per_day==16).sum())}")
    print(f"  不完整的交易日数     : {int((per_day!=16).sum())}")

print()
print("=" * 66)
print("对照：5m bar 的实际日内分布")
print("=" * 66)
one_day = df[df["time"].dt.normalize() == df["time"].dt.normalize().iloc[300]]
print(f"样本日 {one_day['time'].dt.normalize().iloc[0]}，{len(one_day)} 根 5m bar")
print(one_day[["time", "close"]].head(6).to_string(index=False))
print("...")
print(one_day[["time", "close"]].tail(4).to_string(index=False))
hours = one_day["time"].dt.hour.value_counts().sort_index()
print(f"\n日内按小时分布: {hours.to_dict()}")
