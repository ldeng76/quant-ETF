"""
SQLite数据库管理
看板业务数据：账户、持仓、告警规则、调度配置
"""
import sqlite3
from pathlib import Path
from typing import Any
from loguru import logger
from .config import DASHBOARD_DB_PATH

_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS accounts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    broker      TEXT DEFAULT '',
    cash        REAL DEFAULT 0.0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS holdings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER NOT NULL,
    code        TEXT NOT NULL,
    name        TEXT DEFAULT '',
    quantity    INTEGER NOT NULL,
    cost_price  REAL NOT NULL,
    current_price REAL DEFAULT NULL,
    strategy    TEXT DEFAULT '',
    notes       TEXT DEFAULT '',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS alert_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    rule_type   TEXT NOT NULL,
    config      TEXT NOT NULL,
    enabled     BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alerts_dashboard (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id     INTEGER,
    alert_type  TEXT NOT NULL,
    severity    TEXT NOT NULL,
    title       TEXT NOT NULL,
    message     TEXT,
    data        TEXT,
    status      TEXT DEFAULT 'active',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS schedules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy    TEXT NOT NULL,
    interval    INTEGER NOT NULL,
    enabled     BOOLEAN DEFAULT TRUE,
    last_run_at TIMESTAMP,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def get_connection() -> sqlite3.Connection:
    """获取数据库连接"""
    DASHBOARD_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DASHBOARD_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """初始化数据库表"""
    conn = get_connection()
    try:
        conn.executescript(_TABLES_SQL)
        conn.commit()
        logger.info(f"Dashboard database initialized: {DASHBOARD_DB_PATH}")
    except Exception as e:
        logger.error(f"Failed to initialize dashboard database: {e}")
        raise
    finally:
        conn.close()


def query(sql: str, params: list | None = None) -> list[dict]:
    """查询返回字典列表"""
    conn = get_connection()
    try:
        cur = conn.execute(sql, params or [])
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def query_one(sql: str, params: list | None = None) -> dict | None:
    """查询返回单行"""
    conn = get_connection()
    try:
        cur = conn.execute(sql, params or [])
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def execute(sql: str, params: list | None = None) -> int:
    """执行写操作，返回 lastrowid"""
    conn = get_connection()
    try:
        cur = conn.execute(sql, params or [])
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def execute_many(sql: str, params_list: list[list]):
    """批量执行"""
    conn = get_connection()
    try:
        conn.executemany(sql, params_list)
        conn.commit()
    finally:
        conn.close()
