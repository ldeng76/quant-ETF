"""
缺失CSV检测与补算服务
检查最近N个交易日内策略结果CSV的缺失情况，并支持自动补算
"""
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger

from quant_etf.conf import PROJECT_ROOT
from quant_etf.tasks import TaskRegistry
from quant_etf.trading_day import get_trading_dates_between


def get_recent_trading_dates(n: int = 30, code: str = "510310") -> list[datetime]:
    """获取最近N个交易日的datetime列表"""
    today = datetime.now()
    # 60个日历日足够覆盖30个交易日（约42天）
    start = today - timedelta(days=60)

    trading_dates = get_trading_dates_between(start, today, code)
    trading_dates.sort()

    # 取最近n个
    return trading_dates[-n:] if len(trading_dates) > n else trading_dates


def scan_missing_csvs(
    trading_dates: list[datetime],
    strategies: list[str] = None
) -> dict:
    """扫描缺失的CSV文件，返回缺失报告"""
    if strategies is None:
        strategies = ["etf"]

    results_dir = PROJECT_ROOT / "data" / "results"
    missing = {}  # date_str -> [strategies]
    existing = {}  # date_str -> [strategies]

    for dt in trading_dates:
        date_str = dt.strftime("%Y-%m-%d") if isinstance(dt, datetime) else str(dt)[:10]
        date_dir = results_dir / date_str

        missing_for_date = []
        existing_for_date = []

        for strategy in strategies:
            csv_path = date_dir / f"{strategy}.csv"
            if csv_path.exists():
                existing_for_date.append(strategy)
            else:
                missing_for_date.append(strategy)

        if missing_for_date:
            missing[date_str] = missing_for_date
        if existing_for_date:
            existing[date_str] = existing_for_date

    return {
        "trading_dates": [
            dt.strftime("%Y-%m-%d") if isinstance(dt, datetime) else str(dt)[:10]
            for dt in trading_dates
        ],
        "missing": missing,
        "existing": existing,
        "summary": {
            "total_trading_days": len(trading_dates),
            "days_with_missing": len(missing),
            "total_missing_csvs": sum(len(v) for v in missing.values()),
        }
    }


def backfill_missing(
    missing_report: dict,
    strategies: list[str] = None
) -> dict:
    """执行缺失CSV的补算"""
    if strategies is None:
        strategies = ["etf"]

    success = []
    failed = []
    skipped = []

    # 构建待补算列表
    tasks_to_run = []
    for date_str, missing_strategies in missing_report["missing"].items():
        for strategy in missing_strategies:
            if strategy in strategies:
                tasks_to_run.append((date_str, strategy))

    total = len(tasks_to_run)
    for i, (date_str, strategy) in enumerate(tasks_to_run, 1):
        logger.info(f"[{i}/{total}] Backfilling {date_str} {strategy}...")

        # TOCTOU防护：补算前再次检查
        csv_path = PROJECT_ROOT / "data" / "results" / date_str / f"{strategy}.csv"
        if csv_path.exists():
            logger.info(f"  Skipped (already exists): {date_str} {strategy}")
            skipped.append((date_str, strategy))
            continue

        try:
            task = TaskRegistry.get_task(strategy, target_date=date_str, intraday=False)
            if not task:
                raise ValueError(f"Unknown strategy: {strategy}")

            task.run()

            # 验证CSV是否生成
            if csv_path.exists():
                logger.info(f"  SUCCESS: {date_str} {strategy}")
                success.append((date_str, strategy))
            else:
                logger.warning(f"  Task ran but CSV not created: {date_str} {strategy}")
                failed.append((date_str, strategy, "CSV not created after task.run()"))
        except Exception as e:
            logger.warning(f"  FAILED: {date_str} {strategy} - {e}")
            failed.append((date_str, strategy, str(e)))

    return {
        "success": success,
        "failed": failed,
        "skipped": skipped,
        "summary": {
            "total": total,
            "succeeded": len(success),
            "failed": len(failed),
            "skipped": len(skipped),
        }
    }


def check_and_backfill(
    trading_days: int = 30,
    strategies: list[str] = None,
    dry_run: bool = False
) -> dict:
    """一键检测+补算，CLI和Dashboard的共同入口"""
    if strategies is None:
        strategies = ["etf"]

    logger.info(f"=== CSV Missing Check & Backfill ===")
    logger.info(f"Strategies: {strategies}")
    logger.info(f"Looking back {trading_days} trading days...")

    # 1. 获取交易日
    trading_dates = get_recent_trading_dates(n=trading_days)
    if not trading_dates:
        logger.warning("No trading dates found!")
        return {"error": "No trading dates found"}

    date_range = f"{trading_dates[0].strftime('%Y-%m-%d')} ~ {trading_dates[-1].strftime('%Y-%m-%d')}"
    logger.info(f"Trading dates found: {len(trading_dates)} ({date_range})")

    # 2. 扫描缺失
    logger.info("Scanning for missing CSVs...")
    missing_report = scan_missing_csvs(trading_dates, strategies)

    summary = missing_report["summary"]
    logger.info(
        f"Missing: {summary['total_missing_csvs']} CSVs across "
        f"{summary['days_with_missing']} days"
    )

    if missing_report["missing"]:
        for date_str, missing_strategies in sorted(missing_report["missing"].items()):
            logger.info(f"  {date_str}: {', '.join(missing_strategies)}")

    # 3. 补算（非dry-run模式）
    if dry_run:
        logger.info("[dry-run mode] No backfill performed.")
        return {
            "missing_report": missing_report,
            "backfill_result": None,
            "dry_run": True,
        }

    if summary["total_missing_csvs"] == 0:
        logger.info("No missing CSVs. All good!")
        return {
            "missing_report": missing_report,
            "backfill_result": None,
        }

    logger.info("Starting backfill...")
    backfill_result = backfill_missing(missing_report, strategies)

    b_summary = backfill_result["summary"]
    logger.info(
        f"Backfill complete: {b_summary['succeeded']} succeeded, "
        f"{b_summary['failed']} failed, {b_summary['skipped']} skipped"
    )

    return {
        "missing_report": missing_report,
        "backfill_result": backfill_result,
        "dry_run": False,
    }
