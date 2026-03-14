import os
import sys
from pathlib import Path

# 将 src 目录添加到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

db_path = 'data/minute/minute_data.duckdb'
if os.path.exists(db_path):
    os.remove(db_path)
    print('已删除旧数据库')

from quant_etf.minute_collector import (
    get_minute_bars,
    save_minute_data_from_dicts,
    init_minute_db,
    query_minute_data,
)
from quant_etf.conf import ETF_POOL

init_minute_db()

code = ETF_POOL[0]
print(f'测试代码: {code}')

data = get_minute_bars(code, count=500)
print(f'获取数据: {len(data)} 条')
print(f'数据类型: {type(data)}')
if data:
    print(f'第一条: {data[0]}')

result = save_minute_data_from_dicts(code, data)
print(f'保存结果: {result}')

if result:
    count = query_minute_data(f"SELECT COUNT(*) as cnt FROM minute_bars WHERE code='{code}'")
    print(f'数据库中 {code} 记录数: {count.iloc[0]["cnt"]}')

print('\n--- 测试多批次 ---')
data2 = get_minute_bars(code, count=500)
all_data = data + data2
print(f'合并后数据: {len(all_data)} 条')

result2 = save_minute_data_from_dicts(code, all_data)
print(f'第二次保存结果: {result2}')

if result2:
    count = query_minute_data(f"SELECT COUNT(*) as cnt FROM minute_bars WHERE code='{code}'")
    print(f'数据库中 {code} 记录数: {count.iloc[0]["cnt"]}')
