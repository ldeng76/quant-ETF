import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from quant_etf.data_source import ETFDataSource

ds = ETFDataSource()
df = ds.load_data('159516', check_freshness=False, adjust_qfq=False)

print("=" * 80)
print("原始数据（未复权）")
print("=" * 80)
print("\n2026-03-27 至 2026-04-02 数据:")
mask = (df.index >= "2026-03-27") & (df.index <= "2026-04-02")
print(df.loc[mask][["open", "high", "low", "close", "pct_chg"]])

print(f"\n最新收盘价 (2026-05-22): {df.iloc[-1]['close']}")
print(f"60天前收盘价: {df.iloc[-61]['close']}")
r60_original = (df.iloc[-1]['close'] - df.iloc[-61]['close']) / df.iloc[-61]['close']
print(f"r60 (未复权) = {r60_original:.4f}")

print("\n" + "=" * 80)
print("前复权后的数据")
print("=" * 80)

df_qfq = ds.load_data('159516', check_freshness=False, adjust_qfq=True)
print("\n2026-03-27 至 2026-04-02 数据:")
mask = (df_qfq.index >= "2026-03-27") & (df_qfq.index <= "2026-04-02")
print(df_qfq.loc[mask][["open", "high", "low", "close", "pct_chg"]])

print(f"\n最新收盘价 (2026-05-22): {df_qfq.iloc[-1]['close']}")
print(f"60天前收盘价: {df_qfq.iloc[-61]['close']}")
r60_qfq = (df_qfq.iloc[-1]['close'] - df_qfq.iloc[-61]['close']) / df_qfq.iloc[-61]['close']
print(f"r60 (前复权) = {r60_qfq:.4f}")
