import sys
import os
import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from quant_etf.pool_loader import get_stock_pool
from quant_etf.dashboard.config import (
    POSTGRES_HOST,
    POSTGRES_PORT,
    POSTGRES_USER,
    POSTGRES_PASSWORD,
    POSTGRES_DB,
)


def main():
    # 动态获取所有池的标的（ETF + 短线股票 + 中期反弹），去重
    all_codes = sorted(set(
        get_stock_pool("etf")
        + get_stock_pool("stock")
        + get_stock_pool("mid_term")
    ))
    print(f"Pool size (deduplicated): {len(all_codes)}")
    print(f"Connecting to PostgreSQL at {POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB} ...")

    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            dbname=POSTGRES_DB,
        )
    except psycopg2.OperationalError as e:
        print(f"\nConnection failed: {e}")
        if "password" in str(e).lower() or "authentication" in str(e).lower():
            print("Hint: set POSTGRES_PASSWORD env var, or configure pg_hba.conf for trust auth.")
        return

    with conn:
        with conn.cursor() as cur:
            # 获取全局最新采集日期
            cur.execute("SELECT MAX(time) FROM minute_bars")
            row = cur.fetchone()
            latest_time = row[0]

            if latest_time is None:
                print("No data in minute_bars table at all.")
                return

            latest_date = latest_time.date()
            print(f"Latest overall collection time: {latest_time}")
            print(f"Latest collection date: {latest_date}")

            # 查询 DB 中所有有数据的 code 及其最新时间
            cur.execute(
                "SELECT code, MAX(time) FROM minute_bars GROUP BY code ORDER BY code"
            )
            db_rows = cur.fetchall()
            db_code_map: dict[str, object] = {r[0]: r[1] for r in db_rows}
            db_codes = set(db_code_map.keys())
            pool_codes = set(all_codes)

            # ── 1. 池中代码的数据状况 ──
            missing_no_latest = []   # 有数据但不在最新日期
            missing_no_data = []     # 完全无数据
            has_latest_count = 0

            for code in all_codes:
                max_time = db_code_map.get(code)
                if max_time is None:
                    missing_no_data.append(code)
                elif max_time.date() < latest_date:
                    missing_no_latest.append((code, max_time))
                else:
                    has_latest_count += 1

            print(f"\n{'='*60}")
            print(f"[Pool] Codes with data on latest date ({latest_date}): {has_latest_count} / {len(all_codes)}")
            print(f"{'='*60}")

            if missing_no_latest:
                print(f"\n[Pool] Codes MISSING on latest date ({len(missing_no_latest)}):")
                for code, last_time in missing_no_latest:
                    print(f"  {code}  last data: {last_time}")

            if missing_no_data:
                print(f"\n[Pool] Codes with NO data at all ({len(missing_no_data)}):")
                for code in missing_no_data:
                    print(f"  {code}")

            all_missing = len(missing_no_data) + len(missing_no_latest)
            print(f"\nTotal pool missing (no data or not on latest date): {all_missing}")

            # ── 2. DB 中有数据但不在当前池中的孤立代码 ──
            orphan_codes = db_codes - pool_codes
            if orphan_codes:
                print(f"\n{'='*60}")
                print(f"[Orphan] Codes in DB but NOT in current pool: {len(orphan_codes)}")
                print(f"{'='*60}")
                for code in sorted(orphan_codes):
                    print(f"  {code}  last data: {db_code_map[code]}")


if __name__ == "__main__":
    main()
