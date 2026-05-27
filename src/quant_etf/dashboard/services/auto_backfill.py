"""
自动补算服务
为Dashboard提供后台自动检测并补算缺失CSV的功能
"""
import threading
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger

from quant_etf.conf import PROJECT_ROOT
from quant_etf.tasks import TaskRegistry
from quant_etf.trading_day import get_trading_dates_between


def _get_trading_dates_for_range(days: int) -> list[datetime]:
    """获取最近N个交易日的datetime列表"""
    today = datetime.now()
    # 60个日历日足够覆盖30个交易日（约42天）
    start = today - timedelta(days=60)

    trading_dates = get_trading_dates_between(start, today, "510310")
    trading_dates.sort()

    # 取最近n个
    return trading_dates[-days:] if len(trading_dates) > days else trading_dates


def _scan_and_backfill(strategy_name: str, days: int) -> None:
    """在后台线程中执行检测和补算"""
    try:
        logger.info(f"Auto-backfill started for {strategy_name} (last {days} days)")

        # 1. 获取交易日
        trading_dates = _get_trading_dates_for_range(days)
        if not trading_dates:
            logger.warning("No trading dates found for auto-backfill")
            return

        # 2. 扫描缺失的CSV
        results_dir = PROJECT_ROOT / "data" / "results"
        missing_dates = []

        for dt in trading_dates:
            date_str = dt.strftime("%Y-%m-%d")
            csv_path = results_dir / date_str / f"{strategy_name}.csv"
            if not csv_path.exists():
                missing_dates.append((date_str, dt))

        if not missing_dates:
            logger.info(f"No missing CSVs for {strategy_name}")
            return

        logger.info(f"Found {len(missing_dates)} missing dates for {strategy_name}")

        # 3. 执行补算
        success_count = 0
        for date_str, dt in missing_dates:
            csv_path = results_dir / date_str / f"{strategy_name}.csv"

            # TOCTOU防护：补算前再次检查
            if csv_path.exists():
                logger.info(f"  Skipped (already exists): {date_str}")
                continue

            try:
                task = TaskRegistry.get_task(strategy_name, target_date=date_str, intraday=False)
                if not task:
                    raise ValueError(f"Unknown strategy: {strategy_name}")

                task.run()

                if csv_path.exists():
                    logger.info(f"  SUCCESS: {date_str} {strategy_name}")
                    success_count += 1
                else:
                    logger.warning(f"  Task ran but CSV not created: {date_str}")
            except Exception as e:
                logger.warning(f"  FAILED: {date_str} {strategy_name} - {e}")

        logger.info(f"Auto-backfill completed: {success_count}/{len(missing_dates)} succeeded")

    except Exception as e:
        logger.error(f"Auto-backfill failed for {strategy_name}: {e}")


def auto_backfill_history(
    strategy_name: str = "etf",
    days: int = 30,
    blocking: bool = False
) -> None:
    """自动检测并补算缺失的历史CSV文件
    
    此函数会在获取历史汇总前被调用，在后台异步检查并补算缺失的CSV。
    
    Args:
        strategy_name: 策略名称
        days: 检查最近的天数
        blocking: 是否阻塞等待补算完成（默认False，后台异步执行）
    """
    if blocking:
        # 同步模式：阻塞等待补算完成
        _scan_and_backfill(strategy_name, days)
    else:
        # 异步模式：在后台线程中执行，不阻塞主线程
        thread = threading.Thread(
            target=_scan_and_backfill,
            args=(strategy_name, days),
            daemon=True,
            name=f"auto-backfill-{strategy_name}"
        )
        thread.start()
