"""
quant-etf CLI: unified command-line interface for all quant-etf operations.

Usage:
    uv run quant-etf --help
    uv run quant-etf <command> [options]

Commands:
    daily-run         运行每日选股任务
    dashboard         启动 Dashboard 监控系统
    minute-collect    启动分钟级K线数据采集器
    backfill          批量补跑历史日期任务
    restart-dashboard 一键重启 Dashboard 服务
    run               运行单个选股任务
    list-tasks        列出所有可用选股任务
    check             Dashboard 健康检查
"""
import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path


def cmd_daily_run(args):
    from loguru import logger
    from quant_etf.tasks import TaskRegistry
    from quant_etf.tasks import ETFTask, ShortTermStockTask, MidTermReboundTask
    from quant_etf.comparison import ResultComparator

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    logger.add(log_dir / "daily_run_{time:YYYY-MM-DD}.log", rotation="10 MB", encoding="utf-8")

    if args.date:
        dates = [args.date]
    else:
        today = datetime.now()
        dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(args.days)]
        dates.reverse()

    logger.info(f"Running daily tasks for dates: {dates}")
    for date_str in dates:
        logger.info(f"\n{'='*50}")
        logger.info(f"Processing date: {date_str}")
        logger.info(f"{'='*50}\n")

        task_names = ["etf", "short", "mid"]
        for task_name in task_names:
            logger.info(f"Running task: {task_name} for date {date_str}")
            task = TaskRegistry.get_task(task_name, target_date=date_str)
            if task:
                task.run()
            else:
                logger.error(f"Task not found: {task_name}")

        comparator = ResultComparator()
        all_reports = []
        for task_name in task_names:
            report = comparator.compare(task_name, date_str)
            print("\n" + report + "\n")
            all_reports.append(report)

        report_path = Path("data") / "results" / date_str / "daily_summary.txt"
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text("\n\n".join(all_reports), encoding="utf-8")
            logger.info(f"Daily summary saved to {report_path}")
        except Exception as e:
            logger.error(f"Failed to save daily summary: {e}")


def cmd_dashboard(args):
    import os
    from quant_etf.dashboard.app import main as dashboard_main

    if args.port:
        os.environ["DASHBOARD_PORT"] = str(args.port)
    if args.host:
        os.environ["DASHBOARD_HOST"] = args.host
    if args.no_reload:
        os.environ["DASHBOARD_RELOAD"] = "false"

    dashboard_main()


def cmd_minute_collect(args):
    import signal
    import time
    from loguru import logger
    from quant_etf.conf import ALL_POOL
    from quant_etf.minute_collector import (
        is_trading_time,
        wait_until_trading_start,
        collect_minute_data_for_all,
    )

    running = True

    def signal_handler(signum, frame):
        nonlocal running
        logger.info("Received interrupt signal, shutting down gracefully...")
        running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    logger.add(log_dir / "minute_collector_{time:YYYY-MM-DD}.log", rotation="100 MB", encoding="utf-8")

    logger.info("=" * 60)
    logger.info("Minute Data Collector Started")
    logger.info(f"Total securities in ALL_POOL: {len(ALL_POOL)}")
    logger.info("=" * 60)

    while running:
        try:
            if not is_trading_time():
                logger.info(f"Outside trading hours, waiting... ({datetime.now()})")
                if not wait_until_trading_start(check_interval=60, should_stop=lambda: not running):
                    break

            current_time = datetime.now().strftime("%H:%M:%S")
            logger.info(f"Starting collection at {datetime.now().strftime('%Y-%m-%d')} {current_time}")

            result = collect_minute_data_for_all(codes=ALL_POOL, count=500)
            logger.info(f"Collection completed: {result}")

            # 等待约 60 秒再采集下一轮，每秒检查一次中断
            for _ in range(60):
                if not running:
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received, shutting down...")
            break
        except Exception as e:
            logger.exception(f"Error in main loop: {e}")
            time.sleep(60)

    logger.info("Minute Data Collector Stopped")


def cmd_backfill(args):
    from loguru import logger
    from quant_etf.tasks import TaskRegistry
    from quant_etf.tasks import ETFTask, ShortTermStockTask, MidTermReboundTask
    from quant_etf.comparison import ResultComparator
    from quant_etf.trading_day import get_trading_dates_between

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    logger.add(log_dir / "backfill_{time:YYYY-MM-DD}.log", rotation="10 MB", encoding="utf-8")

    trading_dates = get_trading_dates_between(args.start_date, args.end_date)
    if not trading_dates:
        logger.warning(f"No trading dates found between {args.start_date} and {args.end_date}")
        return

    for date_obj in trading_dates:
        date_str = date_obj.strftime("%Y-%m-%d")
        logger.info(f"=== Starting backfill for {date_str} ===")

        for task_name in ["etf", "short", "mid"]:
            logger.info(f"Running task: {task_name} for date {date_str}")
            task = TaskRegistry.get_task(task_name, target_date=date_str)
            if task:
                task.run()

        comparator = ResultComparator()
        all_reports = []
        for task_name in ["etf", "short", "mid"]:
            report = comparator.compare(task_name, date_str)
            print(f"\n--- {date_str} {task_name.upper()} Report ---")
            print(report)
            all_reports.append(report)

        report_path = Path("data") / "results" / date_str / "daily_summary.txt"
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text("\n\n".join(all_reports), encoding="utf-8")
            logger.info(f"Daily summary saved to {report_path}")
        except Exception as e:
            logger.error(f"Failed to save daily summary: {e}")

    logger.info("=== Backfill completed ===")


def cmd_restart_dashboard(args):
    import os
    import signal
    import subprocess
    import time

    DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "8522"))
    DASHBOARD_HOST = os.environ.get("DASHBOARD_HOST", "127.0.0.1")

    def find_processes_on_port(port):
        processes = []
        try:
            result = subprocess.run(
                ["netstat", "-aon", "-p", "TCP"],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.splitlines():
                if "LISTENING" not in line:
                    continue
                parts = line.split()
                if len(parts) < 5:
                    continue
                local_addr = parts[1]
                if local_addr.endswith(f":{port}"):
                    pid = int(parts[4])
                    processes.append({"pid": pid, "addr": local_addr})
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            print(f"  [warning] Error querying port: {e}")
        return processes

    print(f"[1/3] Finding old services on port {DASHBOARD_PORT}...")
    procs = find_processes_on_port(DASHBOARD_PORT)
    if not procs:
        print(f"  No service running on port {DASHBOARD_PORT}.")
    else:
        for proc in procs:
            pid = proc["pid"]
            try:
                os.kill(pid, signal.SIGTERM)
                print(f"  Terminated PID {pid}")
            except Exception as e:
                print(f"  [error] Failed to kill PID {pid}: {e}")

        print(f"[2/3] Waiting for port {DASHBOARD_PORT} to release...")
        for i in range(10):
            time.sleep(0.5)
            if not find_processes_on_port(DASHBOARD_PORT):
                print(f"  Port released. ({(i+1)*0.5:.1f}s)")
                break

    print(f"[3/3] Starting new dashboard service...")
    project_root = Path(__file__).resolve().parent.parent.parent
    proc = subprocess.Popen(
        ["uv", "run", "quant-etf", "dashboard"],
        cwd=str(project_root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    time.sleep(1)
    if proc.poll() is not None:
        print(f"  [error] Failed to start dashboard (exit: {proc.returncode})")
        sys.exit(1)

    url = f"http://{DASHBOARD_HOST}:{DASHBOARD_PORT}"
    print(f"\n  Dashboard started at {url} (PID: {proc.pid})")


def cmd_run(args):
    from loguru import logger
    from quant_etf.conf import LOG_DIR
    from quant_etf.tasks import TaskRegistry

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

    logger.info("Quant ETF System Starting...")
    task_name = args.task.lower()

    if task_name not in ("etf", "short", "mid"):
        logger.error(f"Unknown task: {task_name}")
        sys.exit(1)

    task = TaskRegistry.get_task(task_name, target_date=args.date)
    if task is None:
        logger.error(f"Failed to load task: {task_name}")
        sys.exit(1)

    try:
        task.run()
        logger.info("System finished successfully.")
    except Exception as e:
        logger.exception(f"System execution failed: {e}")
        sys.exit(1)


def cmd_list_tasks(args):
    from loguru import logger
    from quant_etf.tasks import TaskRegistry

    tasks = TaskRegistry.list_tasks()
    print("=" * 40)
    print("Available tasks:")
    print("=" * 40)
    for task in tasks:
        print(f"  {task['name']:10} - {task['description']}")
    print("=" * 40)


def cmd_check(args):
    import urllib.request

    pages = [
        "/pages/overview", "/pages/portfolio", "/pages/strategy",
        "/pages/monitor", "/pages/alerts", "/pages/settings",
    ]
    for p in pages:
        try:
            r = urllib.request.urlopen(f"http://127.0.0.1:{args.port}{p}", timeout=5)
            print(f"{p}: Status={r.status} Len={len(r.read())}")
        except Exception as e:
            print(f"{p}: ERROR {e}")


def build_parser():
    parser = argparse.ArgumentParser(
        description="quant-etf: 基于动量策略的 ETF/股票选股工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", help="可用子命令")

    p = sub.add_parser("daily-run", help="运行每日选股任务 (etf/short/mid)")
    p.add_argument("--days", "-d", type=int, default=1, help="运行最近N天 (默认: 1)")
    p.add_argument("--date", type=str, help="指定特定日期 (格式: YYYY-MM-DD)")

    p = sub.add_parser("dashboard", help="启动 Dashboard 监控系统")
    p.add_argument("--port", "-p", type=int, default=8522, help="监听端口 (默认: 8522)")
    p.add_argument("--host", type=str, default="127.0.0.1", help="监听地址 (默认: 127.0.0.1)")
    p.add_argument("--no-reload", action="store_true", help="禁用热重载")

    sub.add_parser("minute-collect", help="启动分钟级K线数据采集器")

    p = sub.add_parser("backfill", help="批量补跑历史日期任务")
    p.add_argument("start_date", type=str, help="开始日期 (格式: YYYY-MM-DD)")
    p.add_argument("end_date", type=str, help="结束日期 (格式: YYYY-MM-DD)")

    sub.add_parser("restart-dashboard", help="一键重启 Dashboard 服务")

    p = sub.add_parser("run", help="运行单个选股任务")
    p.add_argument("task", nargs="?", default="etf", help="任务名称: etf/short/mid (默认: etf)")
    p.add_argument("--date", type=str, help="指定日期 (格式: YYYY-MM-DD)")

    sub.add_parser("list-tasks", help="列出所有可用选股任务")

    p = sub.add_parser("check", help="Dashboard 健康检查")
    p.add_argument("--port", type=int, default=8080, help="Dashboard 端口")

    sub.add_parser("backfill-stock-names", help="补齐 stock_code_name.json 中缺失的股票代码名称")

    p = sub.add_parser(
        "refresh-stock-names",
        help="强制全量重建 stock_code_name.json（覆盖错误条目）",
    )
    p.add_argument("--dry-run", action="store_true", help="只打印差异，不写文件")
    p.add_argument("--target", type=str, default=None, help="自定义目标文件路径")

    return parser


def cmd_backfill_stock_names(args):
    from loguru import logger
    from quant_etf.data_source import ETFDataSource

    ds = ETFDataSource()
    result = ds.backfill_stock_names()
    logger.info(f"补齐完成: {result}")
    print(f"Backfill completed: {result}")


def cmd_refresh_stock_names(args):
    from loguru import logger
    from quant_etf.data_source import ETFDataSource

    ds = ETFDataSource()
    result = ds.refresh_stock_names(target_file=args.target, dry_run=args.dry_run)
    summary = (
        f"new={len(result['new'])} "
        f"updated={len(result['updated'])} "
        f"unchanged={len(result['unchanged'])} "
        f"failed={len(result['failed'])}"
    )
    logger.info(f"刷新完成: {summary}")
    print(f"Refresh completed: {summary}")
    if result["updated"]:
        print("\n[Updated entries]")
        for u in result["updated"]:
            print(
                f"  {u['code']}  '{u['old_name']}' -> '{u['new_name']}'"
                f"  market '{u['old_market']}' -> '{u['new_market']}'"
            )
    if result["failed"]:
        print(f"\n[Failed codes] (kept old entries): {result['failed']}")


COMMANDS = {
    "daily-run": cmd_daily_run,
    "dashboard": cmd_dashboard,
    "minute-collect": cmd_minute_collect,
    "backfill": cmd_backfill,
    "restart-dashboard": cmd_restart_dashboard,
    "run": cmd_run,
    "list-tasks": cmd_list_tasks,
    "check": cmd_check,
    "backfill-stock-names": cmd_backfill_stock_names,
    "refresh-stock-names": cmd_refresh_stock_names,
}


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    handler = COMMANDS.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
