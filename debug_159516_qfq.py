"""调试159516前复权计算过程"""
import pandas as pd
from quant_etf.data_source import ETFDataSource
from quant_etf.tdx import get_xdxr_info

def debug_qfq():
    ds = ETFDataSource()
    
    # 获取原始数据
    df_raw = ds.load_data("159516", adjust_qfq=False)
    
    # 获取xdxr信息
    xdxr_df = get_xdxr_info("159516")
    
    print("=" * 80)
    print("除权除息信息：")
    print("=" * 80)
    print(xdxr_df)
    
    # 查看除权日前后的数据
    print("\n" + "=" * 80)
    print("除权日前后的原始数据：")
    print("=" * 80)
    
    # 筛选2026-03-24到2026-04-02的数据
    start = pd.Timestamp("2026-03-24")
    end = pd.Timestamp("2026-04-02")
    df_period = df_raw[(df_raw.index >= start) & (df_raw.index <= end)]
    
    print(df_period[["open", "high", "low", "close", "pct_chg"]])
    
    # 手动计算复权因子
    print("\n" + "=" * 80)
    print("手动计算复权因子：")
    print("=" * 80)
    
    # 除权日是2026-03-30
    xdxr_date = pd.Timestamp("2026-03-30")
    
    # 除权日前一天的收盘价
    mask_before = df_raw.index < xdxr_date
    last_close_before = df_raw.loc[mask_before, "close"].iloc[-1]
    print(f"除权日前一天(2026-03-27)收盘价: {last_close_before}")
    
    # 除权日的收盘价
    close_on_xdxr = df_raw.loc[xdxr_date, "close"]
    print(f"除权日(2026-03-30)收盘价: {close_on_xdxr}")
    
    # 复权因子
    factor = last_close_before / close_on_xdxr
    print(f"复权因子 = {last_close_before} / {close_on_xdxr} = {factor:.6f}")
    
    # 验证：用复权因子调整除权日之前的价格
    print("\n" + "=" * 80)
    print("验证前复权计算：")
    print("=" * 80)
    
    test_dates = ["2026-03-26", "2026-03-27"]
    for date_str in test_dates:
        date = pd.Timestamp(date_str)
        original_close = df_raw.loc[date, "close"]
        adjusted_close = original_close * factor
        print(f"{date_str}: 原始收盘价={original_close:.3f}, 复权后={adjusted_close:.3f}")
    
    # 获取前复权后的数据
    print("\n" + "=" * 80)
    print("前复权后的数据（2026-03-24到2026-04-02）：")
    print("=" * 80)
    
    df_qfq = ds.load_data("159516", adjust_qfq=True)
    df_qfq_period = df_qfq[(df_qfq.index >= start) & (df_qfq.index <= end)]
    print(df_qfq_period[["open", "high", "low", "close", "pct_chg"]])

if __name__ == "__main__":
    debug_qfq()
