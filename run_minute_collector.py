"""
分钟级K线数据采集器运行脚本

在开市期间持续运行，每分钟获取 ALL_POOL 中所有证券的分钟级K线数据。

Usage:
    uv run run_minute_collector.py
"""
import sys
import signal
from pathlib import Path
from datetime import datetime
import time

sys.path.append(str(Path(__file__).parent / "src"))

from loguru import logger
from quant_etf.conf import ALL_POOL
from quant_etf.minute_collector import (
    is_trading_time,
    wait_until_trading_start,
    collect_minute_data_for_all,
)


RUNNING = True


def signal_handler(signum, frame):
    """
    处理 Ctrl+C 信号，优雅退出
    """
    global RUNNING
    logger.info("Received interrupt signal, shutting down gracefully...")
    RUNNING = False


def setup_logging():
    """
    配置日志输出
    """
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    logger.add(
        log_dir / "minute_collector_{time:YYYY-MM-DD}.log",
        rotation="100 MB",
        encoding="utf-8",
        level="INFO",
    )
    logger.add(sys.stderr, level="INFO")


def main():
    """
    主函数：持续运行分钟数据采集
    """
    global RUNNING

    setup_logging()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info("=" * 60)
    logger.info("Minute Data Collector Started")
    logger.info(f"Total securities in ALL_POOL: {len(ALL_POOL)}")
    logger.info("=" * 60)

    while RUNNING:
        try:
            if not is_trading_time():
                logger.info(f"Outside trading hours, waiting... ({datetime.now()})")
                wait_until_trading_start(check_interval=60)
                if not RUNNING:
                    break

            current_date = datetime.now().strftime("%Y-%m-%d")
            current_time = datetime.now().strftime("%H:%M:%S")
            logger.info(f"=" * 60)
            logger.info(f"Starting collection at {current_date} {current_time}")

            result = collect_minute_data_for_all(
                codes=ALL_POOL,
                count=500,
            )

            logger.info(f"Collection completed: {result}")

            for i in range(60):
                if not RUNNING:
                    break
                time.sleep(1)

        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received, shutting down...")
            break
        except Exception as e:
            logger.exception(f"Error in main loop: {e}")
            time.sleep(60)

    logger.info("Minute Data Collector Stopped")


if __name__ == "__main__":
    main()
