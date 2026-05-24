"""导出159516前复权数据用于人工校对"""
import pandas as pd
from pathlib import Path
from loguru import logger
from quant_etf.data_source import ETFDataSource

def export_qfq_data():
    """导出159516的前复权数据"""
    
    # 加载数据（默认启用前复权）
    ds = ETFDataSource()
    df = ds.load_data("159516", adjust_qfq=True)
    
    if df.empty:
        logger.error("Failed to load data for 159516")
        return
    
    # 筛选2026-03-01至今的数据
    start_date = pd.Timestamp("2026-03-01")
    df_filtered = df[df.index >= start_date].copy()
    
    if df_filtered.empty:
        logger.error(f"No data found after {start_date}")
        return
    
    # 重置索引，将date变为列
    df_filtered = df_filtered.reset_index()
    df_filtered.rename(columns={"index": "date", "date": "date"}, inplace=True)
    
    # 格式化日期
    df_filtered["date"] = df_filtered["date"].dt.strftime("%Y-%m-%d")
    
    # 选择需要的列
    output_columns = ["date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]
    df_output = df_filtered[output_columns].copy()
    
    # 格式化数值（保留3位小数）
    for col in ["open", "high", "low", "close"]:
        df_output[col] = df_output[col].round(3)
    
    df_output["pct_chg"] = df_output["pct_chg"].round(2)
    df_output["volume"] = df_output["volume"].astype(int)
    df_output["amount"] = df_output["amount"].round(0).astype(int)
    
    # 导出到临时CSV文件
    output_path = Path("data/etf/159516_qfq_temp.csv")
    df_output.to_csv(output_path, index=False, encoding="utf-8-sig")
    
    logger.info(f"✓ 已导出 {len(df_output)} 条记录到: {output_path}")
    logger.info(f"✓ 日期范围: {df_output['date'].iloc[0]} 至 {df_output['date'].iloc[-1]}")
    logger.info(f"\n前5行数据:")
    print(df_output.head().to_string(index=False))
    logger.info(f"\n后5行数据:")
    print(df_output.tail().to_string(index=False))
    
    # 同时导出原始数据（未复权）用于对比
    df_raw = ds.load_data("159516", adjust_qfq=False)
    if not df_raw.empty:
        df_raw_filtered = df_raw[df_raw.index >= start_date].copy()
        df_raw_filtered = df_raw_filtered.reset_index()
        df_raw_filtered["date"] = df_raw_filtered["date"].dt.strftime("%Y-%m-%d")
        df_raw_output = df_raw_filtered[output_columns].copy()
        
        for col in ["open", "high", "low", "close"]:
            df_raw_output[col] = df_raw_output[col].round(3)
        df_raw_output["pct_chg"] = df_raw_output["pct_chg"].round(2)
        df_raw_output["volume"] = df_raw_output["volume"].astype(int)
        df_raw_output["amount"] = df_raw_output["amount"].round(0).astype(int)
        
        raw_path = Path("data/etf/159516_raw_temp.csv")
        df_raw_output.to_csv(raw_path, index=False, encoding="utf-8-sig")
        logger.info(f"\n✓ 已导出原始数据（未复权）到: {raw_path}")
    
    return output_path

if __name__ == "__main__":
    export_qfq_data()
