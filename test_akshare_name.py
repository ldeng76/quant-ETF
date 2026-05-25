#!/usr/bin/env python
"""测试 akshare 获取股票名称功能"""

import akshare as ak

# 测试股票代码
test_codes = [
    "688981", "000063", "000547", "000554", "000968",
    "002050", "002131", "002195", "002202", "002261",
    "600028", "600030", "600118", "600498", "600879",
]

print("正在获取A股实时行情数据...")
try:
    df = ak.stock_zh_a_spot_em()
    print(f"成功获取 {len(df)} 只股票数据")
    
    # 筛选测试股票
    result = df[df["代码"].isin(test_codes)][["代码", "名称"]].reset_index(drop=True)
    
    print(f"\n找到 {len(result)} 只股票：")
    print("=" * 40)
    for _, row in result.iterrows():
        print(f"{row['代码']}  {row['名称']}")
    
    found = set(result["代码"])
    missing = set(test_codes) - found
    if missing:
        print(f"\n未找到 {len(missing)} 只股票：")
        print(sorted(missing))
        
except Exception as e:
    print(f"错误：{e}")
    import traceback
    traceback.print_exc()
