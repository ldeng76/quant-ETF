"""
初始化15分钟数据脚本

从通达信获取历史数据并生成15分钟K线
"""

import sys
from pathlib import Path
from argparse import ArgumentParser
from datetime import datetime, timedelta
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent))

from quant_etf.minute_collector import get_minute_bars, save_minute_data_from_dicts
from quant_etf.minute_data_manager import generate_15min_for_pool
from quant_etf.conf import ETF_POOL


def fetch_and_save_1min_data(code: str, total_bars: int = 2000) -> int:
    """
    获取并保存1分钟数据
    :param code: ETF代码
    :param total_bars: 总条数
    :return: 保存的条数
    """
    logger.info(f"Fetching 1min data for {code}...")

    all_bars = []
    batch_size = 500
    offset = 0

    while offset < total_bars:
        bars = get_minute_bars(code, count=batch_size)
        if not bars:
            break

        all_bars.extend(bars)
        offset += len(bars)

        if len(bars) < batch_size:
            break

        logger.debug(f"Fetched {len(all_bars)} bars for {code}")

    if all_bars:
        success = save_minute_data_from_dicts(code, all_bars)
        if success:
            logger.info(f"Saved {len(all_bars)} 1min bars for {code}")
            return len(all_bars)
        else:
            logger.error(f"Failed to save 1min data for {code}")
            return 0

    return 0


def init_data(codes: list[str], days: int = 90, skip_1min: bool = False) -> dict:
    """
    初始化数据
    :param codes: ETF代码列表
    :param days: 回溯天数
    :param skip_1min: 是否跳过1分钟数据获取
    :return: 统计信息
    """
    stats = {
        "total_codes": len(codes),
        "1min_success": 0,
        "15min_success": 0,
        "1min_bars": 0,
        "15min_bars": 0,
    }

    logger.info(f"Starting initialization for {len(codes)} ETFs ({days} days)")

    if not skip_1min:
        logger.info("Step 1: Fetching 1-minute data...")

        for i, code in enumerate(codes, 1):
            logger.info(f"[{i}/{len(codes)}] Processing {code}...")

            try:
                bars = fetch_and_save_1min_data(code, total_bars=2000)
                if bars > 0:
                    stats["1min_success"] += 1
                    stats["1min_bars"] += bars
                else:
                    logger.warning(f"No 1min data fetched for {code}")
            except Exception as e:
                logger.error(f"Failed to fetch 1min data for {code}: {e}")

        logger.info(
            f"Step 1 completed: {stats['1min_success']}/{len(codes)} codes, "
            f"{stats['1min_bars']} bars"
        )

    logger.info("Step 2: Generating 15-minute data...")

    start_date = datetime.now() - timedelta(days=days)
    total_15min = generate_15min_for_pool(codes, start_date)

    stats["15min_success"] = len(codes)
    stats["15min_bars"] = total_15min

    logger.info(f"Step 2 completed: Generated {total_15min} 15-minute bars")

    return stats


def main():
    """
    主函数
    """
    parser = ArgumentParser(description="初始化ETF短线策略数据")
    parser.add_argument("--days", type=int, default=90, help="回溯天数，默认90天")
    parser.add_argument(
        "--pool-size",
        type=int,
        default=len(ETF_POOL),
        help=f"ETF池大小，默认{len(ETF_POOL)}",
    )
    parser.add_argument(
        "--skip-1min", action="store_true", help="跳过1分钟数据获取（假设已有数据）"
    )

    args = parser.parse_args()

    pool = ETF_POOL[: args.pool_size]

    print(f"\n{'=' * 60}")
    print(f"ETF短线策略数据初始化")
    print(f"{'=' * 60}")
    print(f"ETF池大小: {len(pool)}")
    print(f"回溯天数: {args.days}")
    print(f"跳过1分钟数据: {args.skip_1min}")
    print(f"{'=' * 60}\n")

    stats = init_data(pool, days=args.days, skip_1min=args.skip_1min)

    print(f"\n{'=' * 60}")
    print(f"初始化完成！")
    print(f"{'=' * 60}")
    print(f"总代码数: {stats['total_codes']}")
    print(f"1分钟数据: {stats['1min_success']} 成功, {stats['1min_bars']} 条")
    print(f"15分钟数据: {stats['15min_success']} 成功, {stats['15min_bars']} 条")
    print(f"{'=' * 60}\n")

    logger.info("Initialization completed successfully!")


if __name__ == "__main__":
    main()
