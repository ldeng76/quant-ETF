#!/usr/bin/env python3
"""从 SQLite 迁移到 PostgreSQL"""
import os
import sys
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

import asyncpg

SQLITE_PATH = os.environ.get("SQLITE_PATH", str(PROJECT_ROOT / "data" / "dashboard.db"))


def get_sqlite_conn(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


async def _get_pg_pool():
    from quant_etf.dashboard.config import (
        POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
    )
    return await asyncpg.create_pool(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        database=POSTGRES_DB,
        min_size=1,
        max_size=4,
    )


def _migrate_accounts(sqlite_conn: sqlite3.Connection, pg_pool) -> tuple:
    rows = sqlite_conn.execute("SELECT * FROM accounts").fetchall()
    if not rows:
        return rows, lambda: None

    cols = [desc[0] for desc in sqlite_conn.execute("PRAGMA table_info(accounts)").fetchall()]
    has_user_id = "user_id" in cols

    async def _do():
        async with pg_pool.acquire() as conn:
            for row in rows:
                d = row_to_dict(row)
                user_id = d["user_id"] if has_user_id and d.get("user_id") else 1
                await conn.execute(
                    """INSERT INTO accounts (id, user_id, name, broker, cash, created_at, updated_at)
                       VALUES ($1, $2, $3, $4, $5, $6, $7)
                       ON CONFLICT (id) DO NOTHING""",
                    [d["id"], user_id, d["name"], d["broker"] or "", d["cash"] or 0.0,
                     d.get("created_at"), d.get("updated_at")]
                )
            max_id = max((row_to_dict(r)["id"] for r in rows), default=0)
            await conn.execute(f"SELECT setval('accounts_id_seq', {max_id}, true)")

    return rows, _do


def _migrate_holdings(sqlite_conn: sqlite3.Connection, pg_pool) -> tuple:
    rows = sqlite_conn.execute("SELECT * FROM holdings").fetchall()
    if not rows:
        return rows, lambda: None

    async def _do():
        async with pg_pool.acquire() as conn:
            for row in rows:
                d = row_to_dict(row)
                await conn.execute(
                    """INSERT INTO holdings
                       (id, account_id, code, name, quantity, cost_price, current_price, strategy, notes, created_at, updated_at)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                       ON CONFLICT (id) DO NOTHING""",
                    [d["id"], d["account_id"], d["code"], d["name"] or "",
                     d["quantity"], d["cost_price"], d.get("current_price"),
                     d.get("strategy", "") or "", d.get("notes", "") or "",
                     d.get("created_at"), d.get("updated_at")]
                )
            max_id = max((row_to_dict(r)["id"] for r in rows), default=0)
            await conn.execute(f"SELECT setval('holdings_id_seq', {max_id}, true)")

    return rows, _do


def _migrate_alert_rules(sqlite_conn: sqlite3.Connection, pg_pool) -> tuple:
    rows = sqlite_conn.execute("SELECT * FROM alert_rules").fetchall()
    if not rows:
        return rows, lambda: None

    cols = [desc[0] for desc in sqlite_conn.execute("PRAGMA table_info(alert_rules)").fetchall()]
    has_user_id = "user_id" in cols

    async def _do():
        async with pg_pool.acquire() as conn:
            for row in rows:
                d = row_to_dict(row)
                user_id = d["user_id"] if has_user_id and d.get("user_id") else None
                await conn.execute(
                    """INSERT INTO alert_rules (id, user_id, name, rule_type, config, enabled, created_at)
                       VALUES ($1, $2, $3, $4, $5, $6, $7)
                       ON CONFLICT (id) DO NOTHING""",
                    [d["id"], user_id, d["name"], d["rule_type"],
                     d["config"], d.get("enabled", True), d.get("created_at")]
                )
            max_id = max((row_to_dict(r)["id"] for r in rows), default=0)
            await conn.execute(f"SELECT setval('alert_rules_id_seq', {max_id}, true)")

    return rows, _do


def _migrate_alerts_dashboard(sqlite_conn: sqlite3.Connection, pg_pool) -> tuple:
    rows = sqlite_conn.execute("SELECT * FROM alerts_dashboard").fetchall()
    if not rows:
        return rows, lambda: None

    cols = [desc[0] for desc in sqlite_conn.execute("PRAGMA table_info(alerts_dashboard)").fetchall()]
    has_user_id = "user_id" in cols

    async def _do():
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                for row in rows:
                    d = row_to_dict(row)
                    user_id = d["user_id"] if has_user_id and d.get("user_id") else None
                    await conn.execute(
                        """INSERT INTO alerts_dashboard
                           (id, user_id, rule_id, alert_type, severity, title, message, data, status, created_at, resolved_at)
                           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                           ON CONFLICT (id) DO NOTHING""",
                        [d["id"], user_id, d.get("rule_id"),
                         d["alert_type"], d["severity"], d["title"],
                         d.get("message"), d.get("data"), d.get("status", "active"),
                         d.get("created_at"), d.get("resolved_at")]
                    )
            max_id = max((row_to_dict(r)["id"] for r in rows), default=0)
            await conn.execute(f"SELECT setval('alerts_dashboard_id_seq', {max_id}, true)")

    return rows, _do


def _migrate_schedules(sqlite_conn: sqlite3.Connection, pg_pool) -> tuple:
    rows = sqlite_conn.execute("SELECT * FROM schedules").fetchall()
    if not rows:
        return rows, lambda: None

    async def _do():
        async with pg_pool.acquire() as conn:
            for row in rows:
                d = row_to_dict(row)
                await conn.execute(
                    """INSERT INTO schedules (id, strategy, interval, enabled, last_run_at, created_at)
                       VALUES ($1, $2, $3, $4, $5, $6)
                       ON CONFLICT (id) DO NOTHING""",
                    [d["id"], d["strategy"], d["interval"],
                     d.get("enabled", True), d.get("last_run_at"), d.get("created_at")]
                )
            max_id = max((row_to_dict(r)["id"] for r in rows), default=0)
            await conn.execute(f"SELECT setval('schedules_id_seq', {max_id}, true)")

    return rows, _do


async def run_migration():
    MIGRATION_STEPS = [
        ("accounts", _migrate_accounts),
        ("holdings", _migrate_holdings),
        ("alert_rules", _migrate_alert_rules),
        ("alerts_dashboard", _migrate_alerts_dashboard),
        ("schedules", _migrate_schedules),
    ]
    print(f"Starting migration from SQLite ({SQLITE_PATH}) to PostgreSQL...")

    if not os.path.exists(SQLITE_PATH):
        print(f"ERROR: SQLite database not found at {SQLITE_PATH}")
        return

    sqlite_conn = get_sqlite_conn(SQLITE_PATH)

    try:
        pg_pool = await _get_pg_pool()
        print(f"Connected to PostgreSQL")

        for table, migration_fn in MIGRATION_STEPS:
            print(f"--- Migrating {table} ---")
            try:
                rows, _do = migration_fn(sqlite_conn, pg_pool)
                count = len(rows)
                if count > 0:
                    await _do()
                print(f"  {count} rows migrated")
            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"  ERROR: {e}")

        await pg_pool.close()

        # 验证数据
        from quant_etf.dashboard.db import query
        print("\nVerification:")
        for table in ["accounts", "holdings", "alert_rules", "alerts_dashboard", "schedules"]:
            rows = query(f"SELECT COUNT(*) as cnt FROM {table}")
            print(f"  {table}: {rows[0]['cnt']} rows")

        print("\nMigration complete!")

    finally:
        sqlite_conn.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_migration())