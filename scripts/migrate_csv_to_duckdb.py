"""
CSV -> DuckDB 迁移脚本

将 data/etf/ 和 data/stocks/ 目录下的 CSV 文件导入到 data/market.duckdb。

用法:
    # dry-run 模式（默认），仅检查不写入
    python scripts/migrate_csv_to_duckdb.py

    # 正式迁移（不删CSV）
    python scripts/migrate_csv_to_duckdb.py --no-dry-run

    # 迁移并验证后删除CSV
    python scripts/migrate_csv_to_duckdb.py --no-dry-run --delete-csv
"""
import argparse
import sys
from pathlib import Path

import pandas as pd
from loguru import logger

# 添加 src 到 path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from quant_etf.conf import DATA_DIR
from quant_etf.market_db import (
    init_market_db,
    get_market_db_path,
    save_daily_to_db,
    load_daily_from_db,
    close_all_market_db_connections,
)


def migrate_csv_dir(csv_dir: Path, table: str, dry_run: bool) -> tuple[int, int, list[str]]:
    """
    迁移一个目录下的所有 CSV 到 DuckDB
    :return: (成功数, 失败数, 失败文件列表)
    """
    if not csv_dir.exists():
        logger.warning(f"Directory not found: {csv_dir}")
        return 0, 0, []

    csv_files = sorted(csv_dir.glob("*.csv"))
    # 排除临时文件
    csv_files = [f for f in csv_files if "_temp" not in f.name]
    logger.info(f"Found {len(csv_files)} CSV files in {csv_dir}")

    success = 0
    failed = 0
    failed_files = []

    for csv_path in csv_files:
        code = csv_path.stem
        try:
            df = pd.read_csv(csv_path, index_col="date", parse_dates=True)
            if df.empty:
                logger.warning(f"  Empty CSV: {csv_path.name}")
                success += 1
                continue

            if not dry_run:
                save_daily_to_db(table, code, df, data_dir=DATA_DIR)

            logger.info(f"  {'[DRY-RUN] ' if dry_run else ''}{table}/{code}: {len(df)} rows")
            success += 1
        except Exception as e:
            logger.error(f"  FAILED {csv_path.name}: {e}")
            failed += 1
            failed_files.append(csv_path.name)

    return success, failed, failed_files


def verify_migration(csv_dir: Path, table: str) -> tuple[int, int]:
    """
    验证迁移后的数据完整性
    :return: (匹配数, 不匹配数)
    """
    if not csv_dir.exists():
        return 0, 0

    csv_files = sorted(csv_dir.glob("*.csv"))
    csv_files = [f for f in csv_files if "_temp" not in f.name]

    matched = 0
    mismatched = 0

    for csv_path in csv_files:
        code = csv_path.stem
        try:
            csv_df = pd.read_csv(csv_path, index_col="date", parse_dates=True)
            csv_rows = len(csv_df)

            db_df = load_daily_from_db(table, code, data_dir=DATA_DIR)
            db_rows = len(db_df)

            if csv_rows == db_rows:
                matched += 1
            else:
                mismatched += 1
                logger.warning(f"  MISMATCH {code}: CSV={csv_rows} rows, DuckDB={db_rows} rows")
        except Exception as e:
            mismatched += 1
            logger.error(f"  VERIFY FAILED {code}: {e}")

    return matched, mismatched


def delete_csv_files(csv_dir: Path) -> int:
    """删除目录下的 CSV 文件（排除 _temp 文件）"""
    if not csv_dir.exists():
        return 0

    csv_files = [f for f in csv_dir.glob("*.csv") if "_temp" not in f.name]
    for f in csv_files:
        f.unlink()
    logger.info(f"Deleted {len(csv_files)} CSV files from {csv_dir}")
    return len(csv_files)


def main():
    parser = argparse.ArgumentParser(description="Migrate CSV market data to DuckDB")
    parser.add_argument("--no-dry-run", action="store_true", help="Actually write to DuckDB (default is dry-run)")
    parser.add_argument("--delete-csv", action="store_true", help="Delete CSV files after successful migration")
    args = parser.parse_args()

    dry_run = not args.no_dry_run

    logger.info("=" * 60)
    logger.info(f"CSV -> DuckDB Migration {'(DRY-RUN)' if dry_run else ''}")
    logger.info("=" * 60)

    db_path = get_market_db_path(DATA_DIR)
    etf_dir = DATA_DIR / "etf"
    stocks_dir = DATA_DIR / "stocks"

    # 初始化数据库
    if not dry_run:
        init_market_db(db_path)
        logger.info(f"Database initialized: {db_path}")
    else:
        logger.info(f"[DRY-RUN] Would initialize: {db_path}")

    # 迁移 ETF
    logger.info(f"\n--- Migrating ETF data from {etf_dir} ---")
    etf_success, etf_failed, etf_failed_files = migrate_csv_dir(etf_dir, "etf_daily", dry_run)

    # 迁移 Stocks
    logger.info(f"\n--- Migrating Stock data from {stocks_dir} ---")
    stock_success, stock_failed, stock_failed_files = migrate_csv_dir(stocks_dir, "stock_daily", dry_run)

    # 汇总
    logger.info("\n" + "=" * 60)
    logger.info("MIGRATION SUMMARY")
    logger.info("=" * 60)
    logger.info(f"ETF:    {etf_success} succeeded, {etf_failed} failed")
    logger.info(f"Stocks: {stock_success} succeeded, {stock_failed} failed")

    if etf_failed_files:
        logger.info(f"Failed ETF files: {etf_failed_files}")
    if stock_failed_files:
        logger.info(f"Failed Stock files: {stock_failed_files}")

    if dry_run:
        logger.info("\n[DRY-RUN] No data was written. Re-run with --no-dry-run to migrate.")
        return

    # 验证
    logger.info("\n--- Verifying data integrity ---")
    etf_matched, etf_mismatch = verify_migration(etf_dir, "etf_daily")
    stock_matched, stock_mismatch = verify_migration(stocks_dir, "stock_daily")

    logger.info(f"ETF verify:    {etf_matched} matched, {etf_mismatch} mismatched")
    logger.info(f"Stock verify:  {stock_matched} matched, {stock_mismatch} mismatched")

    total_mismatch = etf_mismatch + stock_mismatch
    if total_mismatch > 0:
        logger.error(f"\nVERIFICATION FAILED: {total_mismatch} files have mismatched row counts. NOT deleting CSV files.")
        return

    # 删除 CSV
    if args.delete_csv:
        logger.info("\n--- Deleting CSV files ---")
        deleted_etf = delete_csv_files(etf_dir)
        deleted_stock = delete_csv_files(stocks_dir)
        logger.info(f"Total deleted: {deleted_etf + deleted_stock} CSV files")
    else:
        logger.info("\nCSV files preserved. Use --delete-csv to remove after verification.")

    close_all_market_db_connections()
    logger.info("\nMigration complete.")


if __name__ == "__main__":
    main()
