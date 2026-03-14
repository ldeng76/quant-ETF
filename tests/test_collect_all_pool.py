"""
测试获取 ALL_POOL 中所有股票近10个交易日的分钟级K线数据
"""
from quant_etf.minute_collector import collect_minute_data_for_all, init_minute_db
from quant_etf.conf import ALL_POOL
from loguru import logger

TRADING_DAYS = 10
MINUTES_PER_DAY = 240
COUNT = TRADING_DAYS * MINUTES_PER_DAY

if __name__ == "__main__":
    logger.info(f"ALL_POOL 包含 {len(ALL_POOL)} 只证券")
    logger.info(f"准备获取每只证券最近 {TRADING_DAYS} 个交易日的数据")
    logger.info(f"预计每只证券获取 {COUNT} 条分钟数据")

    init_minute_db()

    result = collect_minute_data_for_all(ALL_POOL, count=COUNT)

    logger.info(f"采集完成! 成功: {result['success']}, 失败: {result['failed']}")
