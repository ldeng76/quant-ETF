#!/usr/bin/env python3
"""
验证数据脚本：在跑选股之前输出ETF数据供人工验证

这个脚本会：
1. 加载ETF池中所有基金的最新数据
2. 输出到临时文件供用户验证
3. 显示每支基金的数据行数、最新日期、最新收盘价等关键信息
"""

import sys
import os
from pathlib import Path
from datetime import datetime

# 添加src到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from quant_etf.conf import ETF_POOL, PROJECT_ROOT
from quant_etf.data_source import ETFDataSource

def validate_etf_data():
    """验证ETF数据是否正确加载"""

    print(f"开始验证ETF数据...")
    print(f"ETF池数量: {len(ETF_POOL)}")
    print(f"项目根目录: {PROJECT_ROOT}")
    print("-" * 60)

    # 初始化数据源
    ds = ETFDataSource()

    # 验证结果
    results = []
    failed = []

    for code in ETF_POOL:
        try:
            df = ds.load_data(code)

            if df.empty:
                failed.append({
                    "code": code,
                    "error": "数据为空"
                })
                print(f"❌ {code}: 数据为空")
                continue

            # 获取关键信息
            latest_date = df.index[-1]
            latest_close = df["close"].iloc[-1]
            data_rows = len(df)
            data_start = df.index[0]
            data_end = df.index[-1]

            results.append({
                "code": code,
                "name": ds.get_etf_name_map().get(code, "Unknown"),
                "data_rows": data_rows,
                "data_start": str(data_start),
                "data_end": str(data_end),
                "latest_date": str(latest_date),
                "latest_close": float(latest_close)
            })

            print(f"✓ {code} ({ds.get_etf_name_map().get(code, 'Unknown')}): {data_rows}行, {data_start} ~ {data_end}, 最新收盘: {latest_close:.3f}")

        except Exception as e:
            failed.append({
                "code": code,
                "error": str(e)
            })
            print(f"❌ {code}: 加载失败 - {e}")

    print("-" * 60)
    print(f"\n验证完成:")
    print(f"  成功: {len(results)}/{len(ETF_POOL)}")
    print(f"  失败: {len(failed)}/{len(ETF_POOL)}")

    # 输出到临时文件
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = PROJECT_ROOT / "data" / "validation"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 保存成功结果
    success_file = output_dir / f"etf_validation_{timestamp}.csv"
    import pandas as pd
    pd.DataFrame(results).to_csv(success_file, index=False, encoding="utf-8-sig")
    print(f"\n成功结果已保存到: {success_file}")

    # 保存失败结果
    if failed:
        fail_file = output_dir / f"etf_validation_failed_{timestamp}.csv"
        pd.DataFrame(failed).to_csv(fail_file, index=False, encoding="utf-8-sig")
        print(f"失败结果已保存到: {fail_file}")

    # 保存详细数据预览（前5支基金的前10行数据）
    preview_file = output_dir / f"etf_data_preview_{timestamp}.txt"
    with open(preview_file, "w", encoding="utf-8") as f:
        f.write("ETF数据预览\n")
        f.write("=" * 80 + "\n\n")

        for i, item in enumerate(results[:5]):
            code = item["code"]
            df = ds.load_data(code)
            f.write(f"{code} ({item['name']}) - 最新{len(df)}行数据:\n")
            f.write("-" * 80 + "\n")
            f.write(df.tail(10).to_string())
            f.write("\n\n")

    print(f"数据预览已保存到: {preview_file}")

    return results, failed

if __name__ == "__main__":
    validate_etf_data()
