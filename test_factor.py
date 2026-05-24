"""调试前复权因子计算"""
import pandas as pd

# 原始数据
data = {
    'date': ['2026-03-26', '2026-03-27', '2026-03-30', '2026-03-31'],
    'open': [1.642, 1.59, 0.812, 0.845],
    'close': [1.621, 1.66, 0.852, 0.819]
}

df = pd.DataFrame(data)
df['date'] = pd.to_datetime(df['date'])
df.set_index('date', inplace=True)

print("原始数据:")
print(df)

# 除权日
xdxr_date = pd.Timestamp('2026-03-30')

# 除权日前一天收盘价
mask_before = df.index < xdxr_date
last_close_before = df.loc[mask_before, 'close'].iloc[-1]
print(f"\n除权日前一天(2026-03-27)收盘价: {last_close_before}")

# 除权日收盘价
close_on_xdxr = df.loc[xdxr_date, 'close']
print(f"除权日(2026-03-30)收盘价: {close_on_xdxr}")

# 前复权因子（历史价格需要乘以这个因子）
# 目标：让除权日前的价格与除权日后的价格连续
# 所以：1.66 * factor = 0.852
# factor = 0.852 / 1.66
factor = close_on_xdxr / last_close_before
print(f"\n前复权因子 = {close_on_xdxr} / {last_close_before} = {factor:.6f}")

# 应用前复权
print(f"\n前复权后的数据:")
for idx, row in df.iterrows():
    if idx < xdxr_date:
        adjusted_open = row['open'] * factor
        adjusted_close = row['close'] * factor
        print(f"{idx.strftime('%Y-%m-%d')}: 开盘={adjusted_open:.3f}, 收盘={adjusted_close:.3f} (调整后)")
    else:
        print(f"{idx.strftime('%Y-%m-%d')}: 开盘={row['open']:.3f}, 收盘={row['close']:.3f} (原始)")
