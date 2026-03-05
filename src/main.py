import sys
import argparse
from pathlib import Path

from loguru import logger

from quant_etf.conf import LOG_DIR, PROJECT_ROOT
from quant_etf.tasks import TaskRegistry


def setup_logger():
    """
    配置日志
    """
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level="INFO"
    )
    logger.add(
        LOG_DIR / "quant_etf_{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention="30 days",
        level="DEBUG",
        encoding="utf-8"
    )


def parse_args():
    """
    解析命令行参数
    """
    parser = argparse.ArgumentParser(
        description="Quant ETF 选股系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
可用任务:
  etf     ETF 组合选股任务 (默认)
  short   短线股票选股任务
  mid     中期反弹股票选股任务

示例:
  uv run python src/main.py etf
  uv run python src/main.py short
  uv run python src/main.py mid
  uv run python src/main.py --list
        """
    )

    parser.add_argument(
        "task",
        nargs="?",
        default="etf",
        help="要执行的任务类型 (默认: etf)"
    )

    parser.add_argument(
        "--list",
        "-l",
        action="store_true",
        help="列出所有可用任务"
    )

    parser.add_argument(
        "--update",
        "-u",
        action="store_true",
        help="强制更新数据源"
    )

    parser.add_argument(
        "--backfill-stock-code-name",
        "-b",
        action="store_true",
        help="补齐 stock_code_name.json 中缺失的股票代码名称"
    )

    return parser.parse_args()


def list_tasks():
    """
    列出所有可用任务
    """
    tasks = TaskRegistry.list_tasks()
    logger.info("=" * 40)
    logger.info("可用任务列表:")
    logger.info("=" * 40)
    for task in tasks:
        logger.info(f"  {task['name']:10} - {task['description']}")
    logger.info("=" * 40)


def main():
    """
    主入口函数
    """
    setup_logger()
    logger.info("Quant ETF System Starting...")

    args = parse_args()

    if args.list:
        list_tasks()
        return

    if args.backfill_stock_code_name:
        from quant_etf.data_source import ETFDataSource
        ds = ETFDataSource()
        result = ds.backfill_stock_names()
        logger.info(f"补齐完成: {result}")
        return

    task_name = args.task.lower()

    if task_name not in ("etf", "short", "mid"):
        logger.error(f"未知任务: {task_name}")
        logger.info("使用 --list 查看可用任务")
        sys.exit(1)

    task = TaskRegistry.get_task(task_name)
    if task is None:
        logger.error(f"无法加载任务: {task_name}")
        sys.exit(1)

    try:
        task.run()
        logger.info("System finished successfully.")
    except Exception as e:
        logger.exception(f"System execution failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()


# 使用说明
#
# 运行 ETF 选股任务:
#   uv run python src/main.py etf
#
# 运行短线股票选股任务:
#   uv run python src/main.py short
#
# 运行中期反弹股票选股任务:
#   uv run python src/main.py mid
#
# 列出所有可用任务:
#   uv run python src/main.py --list
#   uv run python src/main.py -l
#
# 补齐缺失的股票代码名称:
#   uv run python src/main.py --backfill-stock-code-name
#   uv run python src/main.py -b
