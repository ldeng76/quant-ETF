"""详细调试xdxr日期匹配"""
import pandas as pd
from quant_etf.tdx import get_xdxr_info
from quant_etf.data_source import ETFDataSource

ds = ETFDataSource()
df = ds.load_data('159516', adjust_qfq=False)
xdxr_df = get_xdxr_info('159516')

print("=" * 80)
print("df 索引类型和示例:")
print("=" * 80)
print(f"索引类型: {type(df.index)}")
print(f"索引示例: {df.index[:3]}")
print(f"索引dtype: {df.index.dtype}")

print("\n" + "=" * 80)
print("xdxr_df 列和示例:")
print("=" * 80)
print(f"列名: {xdxr_df.columns.tolist()}")
print(xdxr_df[['year', 'month', 'day']].head())

# 检查是否有date列
if 'date' in xdxr_df.columns:
    print(f"\ndate列: {xdxr_df['date']}")
elif 'datetime' in xdxr_df.columns:
    print(f"\ndatetime列: {xdxr_df['datetime']}")
else:
    print("\n没有date或datetime列，需要从year/month/day构建")
    
# 手动构建date
xdxr_test = xdxr_df.copy()
xdxr_test['date'] = pd.to_datetime(xdxr_test[['year', 'month', 'day']].rename(columns={
    'year': 'Y', 'month': 'M', 'day': 'D'
}).assign(D=1).replace({'D': {1: xdxr_test['day']}}))
# 更简单的方式
xdxr_test['date'] = pd.to_datetime(
    xdxr_test['year'].astype(str) + '-' + 
    xdxr_test['month'].astype(str).str.zfill(2) + '-' + 
    xdxr_test['day'].astype(str).str.zfill(2)
)
print(f"\n手动构建的date: {xdxr_test['date']}")

# 检查2026-03-30是否在df索引中
xdxr_date = pd.Timestamp('2026-03-30')
print(f"\nxdxr_date: {xdxr_date}")
print(f"xdxr_date in df.index: {xdxr_date in df.index}")
print(f"df.index中接近2026-03-30的日期:")
mask = df.index >= pd.Timestamp('2026-03-29')
mask2 = df.index <= pd.Timestamp('2026-03-31')
print(df.loc[mask & mask2].index)
