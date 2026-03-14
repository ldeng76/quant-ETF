"""
获取ETF_POOL各票的最近10个交易日1分钟K线数据，保存到DuckDB
"""

import sys
from pathlib import Path
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from quant_etf.minute_collector import get_minute_bars, save_minute_data_from_dicts
from quant_etf.conf import ETF_POOL


def fetch_10days_1min():
    """
    获取并保存最近10个交易日的1分钟数据
    """
    # 10个交易日约需要2500条数据（250条/天）
    total_bars = 2500
    
    logger.info(f"开始采集 ETF_POOL ({len(ETF_POOL)} 只) 的最近10个交易日1分钟数据")
    logger.info(f"预计每只ETF获取 {total_bars} 条数据")
    
    stats = {
        "total": len(ETF_POOL),
        "success": 0,
        "failed": 0,
        "total_bars": 0,
    }
    
    for i, code in enumerate(ETF_POOL, 1):
        logger.info(f"[{i}/{len(ETF_POOL)}] 采集 {code} ...")
        
        try:
            bars = get_minute_bars(code, count=total_bars)
            if bars:
                success = save_minute_data_from_dicts(code, bars)
                if success:
                    stats["success"] += 1
                    stats["total_bars"] += len(bars)
                    logger.info(f"  成功: {len(bars)} 条")
                else:
                    stats["failed"] += 1
                    logger.warning(f"  保存失败")
            else:
                stats["failed"] += 1
                logger.warning(f"  无数据")
        except Exception as e:
            stats["failed"] += 1
            logger.error(f"  错误: {e}")
    
    logger.info(f"\n采集完成!")
    logger.info(f"  成功: {stats['success']}/{stats['total']}")
    logger.info(f"  失败: {stats['failed']}/{stats['total']}")
    logger.info(f"  总数据: {stats['total_bars']} 条")
    
    return stats


if __name__ == "__main__":
    fetch_10days_1min()
