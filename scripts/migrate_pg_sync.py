#!/usr/bin/env python3
"""从 SQLite 迁移到 PostgreSQL（纯 psycopg2 同步版）"""
import os
import sys
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

import psycopg2

SQLITE_PATH = os.environ.get("SQLITE_PATH", str(PROJECT_ROOT / "data" / "dashboard.db"))


def get_pg_conn():
    from quant_etf.dashboard.config import (
        POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
    )
    return psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        database=POSTGRES_DB,
    )


def get_sqlite_conn(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def migrate_accounts(sqlite_conn: sqlite3.Connection, pg_conn) -> int:
    rows = sqlite_conn.execute("SELECT * FROM accounts").fetchall()
    if not rows:
        return 0

    cols = [desc[0] for desc in sqlite_conn.execute("PRAGMA table_info(accounts)").fetchall()]
    has_user_id = "user_id" in cols

    cur = pg_conn.cursor()
    for row in rows:
        d = row_to_dict(row)
        user_id = d["user_id"] if has_user_id and d.get("user_id") else 1
        cur.execute(
            """INSERT INTO accounts (id, user_id, name, broker, cash, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (d["id"], user_id, d["name"], d["broker"] or "", d["cash"] or 0.0,
             d.get("created_at"), d.get("updated_at"))
        )
    if rows:
        max_id = max((row_to_dict(r)["id"] for r in rows), default=0)
        cur.execute(f"SELECT setval('accounts_id_seq', {max_id}, true)")
    pg_conn.commit()
    return len(rows)


def migrate_holdings(sqlite_conn: sqlite3.Connection, pg_conn) -> int:
    rows = sqlite_conn.execute("SELECT * FROM holdings").fetchall()
    if not rows:
        return 0

    cur = pg_conn.cursor()
    for row in rows:
        d = row_to_dict(row)
        cur.execute(
            """INSERT INTO holdings
               (id, account_id, code, name, quantity, cost_price, current_price, strategy, notes, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (d["id"], d["account_id"], d["code"], d["name"] or "",
             d["quantity"], d["cost_price"], d.get("current_price"),
             d.get("strategy", "") or "", d.get("notes", "") or "",
             d.get("created_at"), d.get("updated_at"))
        )
    if rows:
        max_id = max((row_to_dict(r)["id"] for r in rows), default=0)
        cur.execute(f"SELECT setval('holdings_id_seq', {max_id}, true)")
    pg_conn.commit()
    return len(rows)


def migrate_alert_rules(sqlite_conn: sqlite3.Connection, pg_conn) -> int:
    rows = sqlite_conn.execute("SELECT * FROM alert_rules").fetchall()
    if not rows:
        return 0

    cols = [desc[0] for desc in sqlite_conn.execute("PRAGMA table_info(alert_rules)").fetchall()]
    has_user_id = "user_id" in cols

    cur = pg_conn.cursor()
    for row in rows:
        d = row_to_dict(row)
        user_id = d["user_id"] if has_user_id and d.get("user_id") else None
        cur.execute(
            """INSERT INTO alert_rules (id, user_id, name, rule_type, config, enabled, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (d["id"], user_id, d["name"], d["rule_type"],
             d["config"], d.get("enabled", True), d.get("created_at"))
        )
    if rows:
        max_id = max((row_to_dict(r)["id"] for r in rows), default=0)
        cur.execute(f"SELECT setval('alert_rules_id_seq', {max_id}, true)")
    pg_conn.commit()
    return len(rows)


def migrate_alerts_dashboard(sqlite_conn: sqlite3.Connection, pg_conn) -> int:
    rows = sqlite_conn.execute("SELECT * FROM alerts_dashboard").fetchall()
    if not rows:
        return 0

    cols = [desc[0] for desc in sqlite_conn.execute("PRAGMA table_info(alerts_dashboard)").fetchall()]
    has_user_id = "user_id" in cols

    cur = pg_conn.cursor()
    for row in rows:
        d = row_to_dict(row)
        user_id = d["user_id"] if has_user_id and d.get("user_id") else None
        cur.execute(
            """INSERT INTO alerts_dashboard
               (id, user_id, rule_id, alert_type, severity, title, message, data, status, created_at, resolved_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (d["id"], user_id, d.get("rule_id"),
             d["alert_type"], d["severity"], d["title"],
             d.get("message"), d.get("data"), d.get("status", "active"),
             d.get("created_at"), d.get("resolved_at"))
        )
    if rows:
        max_id = max((row_to_dict(r)["id"] for r in rows), default=0)
        cur.execute(f"SELECT setval('alerts_dashboard_id_seq', {max_id}, true)")
    pg_conn.commit()
    return len(rows)


def migrate_schedules(sqlite_conn: sqlite3.Connection, pg_conn) -> int:
    rows = sqlite_conn.execute("SELECT * FROM schedules").fetchall()
    if not rows:
        return 0

    cur = pg_conn.cursor()
    for row in rows:
        d = row_to_dict(row)
        # SQLite stores boolean as 0/1, convert to Python bool
        enabled_val = d.get("enabled", 1)
        if isinstance(enabled_val, int):
            enabled_val = bool(enabled_val)
        cur.execute(
            """INSERT INTO schedules (id, strategy, interval, enabled, last_run_at, created_at)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (d["id"], d["strategy"], d["interval"],
             enabled_val, d.get("last_run_at"), d.get("created_at"))
        )
    if rows:
        max_id = max((row_to_dict(r)["id"] for r in rows), default=0)
        cur.execute(f"SELECT setval('schedules_id_seq', {max_id}, true)")
    pg_conn.commit()
    return len(rows)


def verify(pg_conn):
    cur = pg_conn.cursor()
    print("\nVerification:")
    for table in ["accounts", "holdings", "alert_rules", "alerts_dashboard", "schedules"]:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        print(f"  {table}: {cur.fetchone()[0]} rows")


def main():
    MIGRATIONS = [
        ("accounts", migrate_accounts),
        ("holdings", migrate_holdings),
        ("alert_rules", migrate_alert_rules),
        ("alerts_dashboard", migrate_alerts_dashboard),
        ("schedules", migrate_schedules),
    ]

    print(f"Migrating from SQLite ({SQLITE_PATH}) to PostgreSQL...")
    print()

    if not os.path.exists(SQLITE_PATH):
        print(f"ERROR: SQLite not found: {SQLITE_PATH}")
        return

    sqlite_conn = get_sqlite_conn(SQLITE_PATH)
    pg_conn = get_pg_conn()

    try:
        for name, migrate_fn in MIGRATIONS:
            print(f"--- Migrating {name} ---")
            try:
                count = migrate_fn(sqlite_conn, pg_conn)
                print(f"  {count} rows migrated")
            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"  ERROR: {e}")
                pg_conn.rollback()

        verify(pg_conn)
        print("\nDone!")

    finally:
        sqlite_conn.close()
        pg_conn.close()


if __name__ == "__main__":
    main()