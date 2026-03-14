import pandas as pd
import os
from quant_etf.minute_collector import get_minute_bars, save_minute_data, init_minute_db, query_minute_data
from quant_etf.conf import ETF_POOL

db_path = 'data/minute/minute_data.duckdb'
if os.path.exists(db_path):
    os.remove(db_path)
    print('已删除旧数据库')

init_minute_db()

code = ETF_POOL[0]
print(f'测试代码: {code}')

all_data = None
for i in range(3):
    df = get_minute_bars(code, count=500)
    if df.empty:
        break
    if all_data is None:
        all_data = df
    else:
        all_data = pd.concat([all_data, df], ignore_index=True)
    print(f'第{i+1}批: {len(df)} 条')
    if len(df) < 500:
        break

print(f'总数据: {len(all_data)} 条')

result = save_minute_data(code, all_data)
print(f'保存结果: {result}')

if result:
    count = query_minute_data(f"SELECT COUNT(*) as cnt FROM minute_bars WHERE code='{code}'")
    print(f'数据库中 {code} 记录数: {count.iloc[0]["cnt"]}')
