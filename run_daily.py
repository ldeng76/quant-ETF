import sys
from datetime import datetime
from pathlib import Path

# Add src to sys.path to ensure modules can be imported
sys.path.append(str(Path(__file__).parent / "src"))

from loguru import logger
from quant_etf.tasks import TaskRegistry
# Ensure tasks are registered (imported)
from quant_etf.tasks import ETFTask, ShortTermStockTask, MidTermReboundTask
from quant_etf.comparison import ResultComparator

def run_task(task_name: str):
    logger.info(f"Running task: {task_name}")
    try:
        task = TaskRegistry.get_task(task_name)
        if not task:
            logger.error(f"Task not found: {task_name}")
            return
        task.run()
    except Exception as e:
        logger.exception(f"Error running task {task_name}: {e}")

def main():
    # Setup logging
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    logger.add(log_dir / "daily_run_{time:YYYY-MM-DD}.log", rotation="10 MB", encoding="utf-8")
    
    current_date = datetime.now().strftime("%Y-%m-%d")
    logger.info(f"Starting daily run for {current_date}")

    # 1. Run Tasks
    tasks = ["etf", "short", "mid"]
    for task_name in tasks:
        run_task(task_name)
        
    # 2. Compare Results
    logger.info("Generating comparison reports...")
    comparator = ResultComparator()
    
    all_reports = []
    for task_name in tasks:
        report = comparator.compare(task_name, current_date)
        print("\n" + report + "\n")
        logger.info(f"Comparison report for {task_name}:\n{report}")
        all_reports.append(report)
        
    # Save daily report summary
    report_path = Path("data") / "results" / current_date / "daily_summary.txt"
    try:
        if report_path.parent.exists():
            report_path.write_text("\n\n".join(all_reports), encoding="utf-8")
            logger.info(f"Daily summary saved to {report_path}")
    except Exception as e:
        logger.error(f"Failed to save daily summary: {e}")

if __name__ == "__main__":
    main()
