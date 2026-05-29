"""
将 DuckDB 数据迁移到 PostgreSQL

迁移范围:
- minute_data.duckdb -> minute_bars (分钟K线)
- market.duckdb -> market_daily (日线行情)
- alerts.duckdb -> monitor_alerts (监控告警) [如存在]
"""
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

import duckdb
import psycopg2
import psycopg2.extras

# PostgreSQL 连接参数
PG_HOST = "localhost"
PG_PORT = 5432
PG_USER = "postgres"
PG_PASSWORD = "admin@pwd"
PG_DB = "quant_etf"


def get_pg_conn():
    return psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        user=PG_USER,
        password=PG_PASSWORD,
        database=PG_DB,
    )


def migrate_minute_bars(pg_conn):
    """迁移 minute_data.duckdb -> minute_bars"""
    db_path = Path("data/minute/minute_data.duckdb")
    if not db_path.exists():
        print(f"  [跳过] {db_path} 不存在")
        return

    print(f"  从 {db_path} 迁移...")
    duck_conn = duckdb.connect(str(db_path), read_only=True)

    # 读取数据
    df = duck_conn.execute("SELECT * FROM minute_bars").df()
    duck_conn.close()

    if df.empty:
        print(f"    0 rows")
        return

    print(f"    读取 {len(df)} rows")

    cur = pg_conn.cursor()
    cur.executemany("""
        INSERT INTO minute_bars (code, time, open, high, low, close, volume, amount, year, month, day, hour, minute)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (code, time) DO NOTHING
    """, [
        (str(r.code), r.time, r.open, r.high, r.low, r.close,
         int(r.volume) if r.volume else 0,
         float(r.amount) if r.amount else 0.0,
         int(r.year) if r.year else None,
         int(r.month) if r.month else None,
         int(r.day) if r.day else None,
         int(r.hour) if r.hour else None,
         int(r.minute) if r.minute else None)
        for r in df.itertuples()
    ])
    pg_conn.commit()
    print(f"    写入 {len(df)} rows")


def migrate_market_daily(pg_conn):
    """迁移 market.duckdb -> market_daily"""
    db_path = Path("data/market.duckdb")
    if not db_path.exists():
        print(f"  [跳过] {db_path} 不存在")
        return

    print(f"  从 {db_path} 迁移...")

    for table, col_code in [("etf_daily", "etf"), ("stock_daily", "stock")]:
        duck_conn = duckdb.connect(str(db_path), read_only=True)
        try:
            df = duck_conn.execute(f"SELECT * FROM {table}").df()
        except Exception:
            print(f"    表 {table} 不存在，跳过")
            duck_conn.close()
            continue
        duck_conn.close()

        if df.empty:
            print(f"    {table}: 0 rows")
            continue

        print(f"    {table}: 读取 {len(df)} rows")

        cur = pg_conn.cursor()
        cur.executemany("""
            INSERT INTO market_daily (code, date, open, high, low, close, amount, volume, pct_chg)
            VALUES (%s, %s::date, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (code, date) DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                amount = EXCLUDED.amount,
                volume = EXCLUDED.volume,
                pct_chg = EXCLUDED.pct_chg
        """, [
            (str(r.code), r.date, r.open, r.high, r.low, r.close,
             float(r.amount) if r.amount else 0.0,
             int(r.volume) if r.volume else 0,
             float(r.pct_chg) if r.pct_chg else 0.0)
            for r in df.itertuples()
        ])
        pg_conn.commit()
        print(f"    {table}: 写入 {len(df)} rows")


def migrate_monitor_alerts(pg_conn):
    """迁移 alerts.duckdb -> monitor_alerts"""
    # alerts 可能在 data/alerts/ 或其他位置
    possible_paths = [
        Path("data/alerts/alerts.duckdb"),
        Path("data/alerts.duckdb"),
    ]
    db_path = None
    for p in possible_paths:
        if p.exists():
            db_path = p
            break

    if not db_path:
        print(f"  [跳过] alerts.duckdb 不存在")
        return

    print(f"  从 {db_path} 迁移...")
    duck_conn = duckdb.connect(str(db_path), read_only=True)

    try:
        df = duck_conn.execute("SELECT * FROM alerts").df()
    except Exception:
        print(f"    alerts 表不存在，跳过")
        duck_conn.close()
        return
    duck_conn.close()

    if df.empty:
        print(f"    0 rows")
        return

    print(f"    读取 {len(df)} rows")

    cur = pg_conn.cursor()
    cur.executemany("""
        INSERT INTO monitor_alerts (time, code, strategy_name, signal_type, direction,
            score, entry_price, stop_loss, take_profit, reason, market_state,
            market_return, market_volatility, ma10, ma20, ma30)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, [
        (r.time, str(r.code) if r.code else None, r.strategy_name, r.signal_type, r.direction,
         float(r.score) if r.score else None,
         float(r.entry_price) if r.entry_price else None,
         float(r.stop_loss) if r.stop_loss else None,
         float(r.take_profit) if r.take_profit else None,
         r.reason, r.market_state,
         float(r.market_return) if r.market_return else None,
         float(r.market_volatility) if r.market_volatility else None,
         float(r.ma10) if r.ma10 else None,
         float(r.ma20) if r.ma20 else None,
         float(r.ma30) if r.ma30 else None)
        for r in df.itertuples()
    ])
    pg_conn.commit()
    print(f"    写入 {len(df)} rows")


def verify_tables(pg_conn):
    """验证迁移结果"""
    cur = pg_conn.cursor()

    tables = ["minute_bars", "minute_bars_15m", "market_daily", "monitor_alerts"]
    print("\n=== 验证迁移结果 ===")
    for table in tables:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        count = cur.fetchone()[0]
        print(f"  {table}: {count} rows")


def main():
    print("迁移 DuckDB 数据到 PostgreSQL...")
    print("=" * 50)

    pg_conn = get_pg_conn()

    print("\n--- 迁移 minute_bars ---")
    migrate_minute_bars(pg_conn)

    print("\n--- 迁移 market_daily ---")
    migrate_market_daily(pg_conn)

    print("\n--- 迁移 monitor_alerts ---")
    migrate_monitor_alerts(pg_conn)

    verify_tables(pg_conn)

    pg_conn.close()
    print("\n完成!")


if __name__ == "__main__":
    main()