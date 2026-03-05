import sys
from pathlib import Path

# Add src to sys.path to ensure modules can be imported
sys.path.append(str(Path(__file__).parent / "src"))

from loguru import logger
from quant_etf.tasks import TaskRegistry
# Ensure tasks are registered (imported)
from quant_etf.tasks import ETFTask, ShortTermStockTask, MidTermReboundTask
from quant_etf.comparison import ResultComparator
from quant_etf.trading_day import get_trading_dates_between


def run_task(task_name: str, target_date: str):
    """
    运行指定任务并保存结果到指定日期目录
    """
    logger.info(f"Running task: {task_name} for date {target_date}")
    try:
        task = TaskRegistry.get_task(task_name, target_date=target_date)
        if not task:
            logger.error(f"Task not found: {task_name}")
            return False
        task.run()
        return True
    except Exception as e:
        logger.exception(f"Error running task {task_name}: {e}")
        return False


def run_backfill(start_date: str, end_date: str):
    """
    批量补跑指定日期范围内的任务（自动跳过非交易日）
    """
    tasks = ["etf", "short", "mid"]

    # Setup logging
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    logger.add(log_dir / "backfill_{time:YYYY-MM-DD}.log", rotation="10 MB", encoding="utf-8")

    # 获取有效交易日列表
    trading_dates = get_trading_dates_between(start_date, end_date)

    if not trading_dates:
        logger.warning(f"No trading dates found between {start_date} and {end_date}")
        return

    for date_obj in trading_dates:
        date_str = date_obj.strftime("%Y-%m-%d")
        logger.info(f"=== Starting backfill for {date_str} ===")

        # Run all tasks
        for task_name in tasks:
            run_task(task_name, date_str)

        # Generate comparison report for this date
        logger.info(f"Generating comparison report for {date_str}...")
        comparator = ResultComparator()

        all_reports = []
        for task_name in tasks:
            report = comparator.compare(task_name, date_str)
            print(f"\n--- {date_str} {task_name.upper()} Report ---")
            print(report)
            all_reports.append(report)

        # Save daily report summary
        report_path = Path("data") / "results" / date_str / "daily_summary.txt"
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text("\n\n".join(all_reports), encoding="utf-8")
            logger.info(f"Daily summary saved to {report_path}")
        except Exception as e:
            logger.error(f"Failed to save daily summary: {e}")

    logger.info("=== Backfill completed ===")


if __name__ == "__main__":
    # 指定补跑日期范围（会自动跳过非交易日）
    run_backfill("2026-03-02", "2026-03-05")
