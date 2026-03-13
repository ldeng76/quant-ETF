"""
运行每日任务脚本

Usage:
    uv run run_daily.py --days 3      # 运行最近3天
    uv run run_daily.py                # 运行最近1天（默认）
    uv run run_daily.py --date 2026-03-03  # 运行指定日期
"""
import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path

# Add src to sys.path to ensure modules can be imported
sys.path.append(str(Path(__file__).parent / "src"))

from loguru import logger
from quant_etf.tasks import TaskRegistry
# Ensure tasks are registered (imported)
from quant_etf.tasks import ETFTask, ShortTermStockTask, MidTermReboundTask
from quant_etf.comparison import ResultComparator


def validate_date(date_str: str) -> bool:
    """验证日期格式是否正确 (YYYY-MM-DD)"""
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def run_task(task_name: str, target_date: str | None = None):
    logger.info(f"Running task: {task_name}" + (f" for date {target_date}" if target_date else ""))
    try:
        task = TaskRegistry.get_task(task_name, target_date=target_date)
        if not task:
            logger.error(f"Task not found: {task_name}")
            return
        task.run()
    except Exception as e:
        logger.exception(f"Error running task {task_name}: {e}")


def run_for_dates(dates: list[str]):
    """
    运行指定日期列表的任务
    :param dates: 日期列表 ["2026-03-03", "2026-03-04", ...]
    """
    for date_str in dates:
        logger.info(f"\n{'='*50}")
        logger.info(f"Processing date: {date_str}")
        logger.info(f"{'='*50}\n")

        # Run Tasks for this date
        tasks = ["etf", "short", "mid"]
        for task_name in tasks:
            run_task(task_name, target_date=date_str)

        # Compare Results
        logger.info(f"Generating comparison reports for {date_str}...")
        comparator = ResultComparator()

        all_reports = []
        for task_name in tasks:
            report = comparator.compare(task_name, date_str)
            print("\n" + report + "\n")
            logger.info(f"Comparison report for {task_name}:\n{report}")
            all_reports.append(report)

        # Save daily report summary
        report_path = Path("data") / "results" / date_str / "daily_summary.txt"
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text("\n\n".join(all_reports), encoding="utf-8")
            logger.info(f"Daily summary saved to {report_path}")
        except Exception as e:
            logger.error(f"Failed to save daily summary: {e}")

def main():
    # Setup logging
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    logger.add(log_dir / "daily_run_{time:YYYY-MM-DD}.log", rotation="10 MB", encoding="utf-8")

    # Parse arguments
    parser = argparse.ArgumentParser(description="运行每日任务")
    parser.add_argument("--days", "-d", type=int, default=1, help="运行最近N天 (默认: 1)")
    parser.add_argument("--date", type=str, help="指定特定日期 (格式: YYYY-MM-DD)")
    args = parser.parse_args()

    if args.date:
        # 验证日期格式
        if not validate_date(args.date):
            logger.error(f"Invalid date format: {args.date}. Use YYYY-MM-DD")
            sys.exit(1)
        # 运行指定日期
        dates = [args.date]
    else:
        # 运行最近 N 天
        today = datetime.now()
        dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(args.days)]
        dates.reverse()  # 按时间顺序排列

    logger.info(f"Running tasks for dates: {dates}")
    run_for_dates(dates)


if __name__ == "__main__":
    main()
