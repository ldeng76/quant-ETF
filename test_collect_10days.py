"""
测试脚本：获取 ETF_POOL 中所有票的最近10个交易日的分钟级K线数据
"""
import pandas as pd
from datetime import datetime, timedelta
import sys
from pathlib import Path
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent / "src"))

from quant_etf.minute_collector import (
    get_minute_bars,
    save_minute_data,
    load_minute_data,
    query_minute_data,
    init_minute_db,
    get_minute_db_path,
    get_db_connection,
)
from quant_etf.conf import ETF_POOL


def get_trading_days(n: int = 10) -> list[datetime]:
    """
    获取过去 n 个交易日的日期列表（排除周末）
    """
    trading_days = []
    current = datetime.now()

    while len(trading_days) < n:
        if current.weekday() < 5:
            trading_days.append(current)
        current -= timedelta(days=1)

    return sorted(trading_days)


def collect_10_day_minute_data():
    """
    采集最近10个交易日的分钟级数据
    """
    logger.info(f"开始采集 ETF_POOL ({len(ETF_POOL)} 只) 的最近10个交易日分钟数据")
    logger.info(f"ETF_POOL: {ETF_POOL}")

    trading_days = get_trading_days(10)
    logger.info(f"目标交易日: {[d.strftime('%Y-%m-%d') for d in trading_days]}")

    init_minute_db()
    db_path = get_minute_db_path()
    logger.info(f"数据库路径: {db_path}")

    total_success = 0
    total_failed = 0
    total_bars = 0

    for i, code in enumerate(ETF_POOL):
        logger.info(f"[{i+1}/{len(ETF_POOL)}] 采集 {code} 的分钟数据...")

        all_data = pd.DataFrame()
        batch_count = 0
        max_batches = 10
        is_first_batch = True

        while batch_count < max_batches:
            offset = batch_count * 500
            df = get_minute_bars(code, count=500)

            if df.empty:
                logger.warning(f"  {code} 第{batch_count+1}批数据为空，停止采集")
                break

            if is_first_batch:
                all_data = df
                is_first_batch = False
            else:
                all_data = pd.concat([all_data, df], ignore_index=True)

            batch_count += 1
            logger.info(f"  第{batch_count}批: 获取 {len(df)} 条记录")

            if len(df) < 500:
                break

        if not all_data.empty:
            try:
                if save_minute_data(code, all_data):
                    total_success += 1
                    total_bars += len(all_data)
                    date_range = f"{all_data.index.min().strftime('%Y-%m-%d')} ~ {all_data.index.max().strftime('%Y-%m-%d')}"
                    logger.info(f"  ✓ {code} 保存成功: {len(all_data)} 条 ({date_range})")
                else:
                    total_failed += 1
                    logger.error(f"  ✗ {code} 保存失败 (返回False)")
            except Exception as e:
                total_failed += 1
                logger.error(f"  ✗ {code} 保存异常: {e}")
        else:
            total_failed += 1
            logger.error(f"  ✗ {code} 无数据")

    logger.info(f"\n采集完成!")
    logger.info(f"  成功: {total_success}/{len(ETF_POOL)}")
    logger.info(f"  失败: {total_failed}/{len(ETF_POOL)}")
    logger.info(f"  总数据条数: {total_bars}")

    return total_success, total_failed, total_bars


def verify_data():
    """
    验证数据存储
    """
    logger.info("\n=== 验证数据存储 ===")

    conn = get_db_connection()

    total_count = conn.execute("SELECT COUNT(*) as cnt FROM minute_bars").fetchone()[0]
    logger.info(f"数据库总记录数: {total_count}")

    code_count = conn.execute("SELECT COUNT(DISTINCT code) as cnt FROM minute_bars").fetchone()[0]
    logger.info(f"证券数量: {code_count}")

    date_range = conn.execute("""
        SELECT MIN(time) as earliest, MAX(time) as latest
        FROM minute_bars
    """).fetchone()
    logger.info(f"时间范围: {date_range[0]} ~ {date_range[1]}")

    logger.info("\n各证券数据量统计 (前10):")
    code_stats = conn.execute("""
        SELECT code, COUNT(*) as cnt, MIN(time) as earliest, MAX(time) as latest
        FROM minute_bars
        GROUP BY code
        ORDER BY cnt DESC
        LIMIT 10
    """).df()
    logger.info(code_stats.to_string())

    logger.info("\n最近10条数据:")
    recent = query_minute_data("SELECT * FROM minute_bars ORDER BY time DESC LIMIT 10")
    logger.info(recent.to_string())


if __name__ == "__main__":
    collect_10_day_minute_data()
    verify_data()
