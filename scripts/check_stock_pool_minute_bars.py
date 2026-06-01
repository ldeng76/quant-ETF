import sys
import os
import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from quant_etf.conf import STOCK_POOL
from quant_etf.dashboard.config import (
    POSTGRES_HOST,
    POSTGRES_PORT,
    POSTGRES_USER,
    POSTGRES_PASSWORD,
    POSTGRES_DB,
)


def main():
    print(f"Stock pool size: {len(STOCK_POOL)}")
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
            cur.execute("SELECT MAX(time) FROM minute_bars")
            row = cur.fetchone()
            latest_time = row[0]

            if latest_time is None:
                print("No data in minute_bars table at all.")
                return

            print(f"Latest overall collection time: {latest_time}")
            latest_date = latest_time.date()
            print(f"Latest collection date: {latest_date}")

            missing_no_latest = []
            missing_no_data = []
            has_latest_count = 0

            for code in STOCK_POOL:
                cur.execute(
                    "SELECT MAX(time) FROM minute_bars WHERE code = %s", (code,)
                )
                row = cur.fetchone()
                max_time = row[0]

                if max_time is None:
                    missing_no_data.append(code)
                elif max_time.date() < latest_date:
                    missing_no_latest.append((code, max_time))
                else:
                    has_latest_count += 1

            print(f"\n{'='*60}")
            print(f"Codes with data on latest date ({latest_date}): {has_latest_count} / {len(STOCK_POOL)}")
            print(f"{'='*60}")

            if missing_no_latest:
                print(f"\nCodes MISSING on latest date ({len(missing_no_latest)}):")
                for code, last_time in missing_no_latest:
                    print(f"  {code}  last data: {last_time}")

            if missing_no_data:
                print(f"\nCodes with NO data at all ({len(missing_no_data)}):")
                for code in missing_no_data:
                    print(f"  {code}")

            all_missing = len(missing_no_data) + len(missing_no_latest)
            print(f"\nTotal missing (no data or not on latest date): {all_missing}")


if __name__ == "__main__":
    main()
