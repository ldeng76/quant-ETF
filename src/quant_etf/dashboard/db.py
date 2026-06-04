"""
PostgreSQL 数据库管理 (asyncpg 连接池)
看板业务数据：账户、持仓、告警规则、调度配置、用户

多租户支持：
- accounts / alert_rules / alerts_dashboard 表通过 user_id 隔离
- users 表存储所有 OAuth 用户
"""
import asyncpg
import psycopg2
from typing import Any
from loguru import logger
from .config import (
    POSTGRES_HOST, POSTGRES_PORT,
    POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB,
    POSTGRES_POOL_SIZE, POSTGRES_MAX_OVERFLOW,
)

# 全局连接池
_pool: asyncpg.Pool | None = None


def get_pg_conn():
    """获取 psycopg2 连接（同步接口，用于 minute/minute_data/alert_recorder 等模块）"""
    return psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        database=POSTGRES_DB,
    )

_engine = None

def get_pg_engine():
    """获取 SQLAlchemy 引擎（用于 pandas read_sql），单例缓存"""
    global _engine
    if _engine is None:
        from urllib.parse import quote_plus
        from sqlalchemy import create_engine
        encoded_password = quote_plus(POSTGRES_PASSWORD)
        _engine = create_engine(
            f"postgresql+psycopg2://{POSTGRES_USER}:{encoded_password}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
        )
    return _engine


async def _get_pool() -> asyncpg.Pool:
    """获取或创建连接池"""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            database=POSTGRES_DB,
            min_size=2,
            max_size=POSTGRES_POOL_SIZE,
            max_queries=50000,
            command_timeout=60,
        )
        logger.info(
            f"PostgreSQL pool created: {POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
        )
    return _pool


async def close_pool():
    """关闭连接池（应用退出时调用）"""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("PostgreSQL pool closed")


# ============================================================
# 同步兼容层（用于已有代码）
# 注意：生产环境建议逐步迁移到 async 函数
# ============================================================

import asyncio
from concurrent.futures import ThreadPoolExecutor

_executor = ThreadPoolExecutor(max_workers=4)


def _run_sync(coro):
    """在同步上下文中运行 async 函数"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(coro)
        loop.close()
        return result
    # 如果已经在 async 上下文中
    import functools
    fut = asyncio.ensure_future(coro)
    return fut


async def _async_query(sql: str, params: list | None = None) -> list[dict]:
    """async 查询"""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        if params:
            rows = await conn.fetch(sql, params)
        else:
            rows = await conn.fetch(sql)
        return [dict(r) for r in rows]


async def _async_query_one(sql: str, params: list | None = None) -> dict | None:
    """async 查询单行"""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        if params:
            row = await conn.fetchrow(sql, params)
        else:
            row = await conn.fetchrow(sql)
        return dict(row) if row else None


async def _async_execute(sql: str, params: list | None = None) -> int:
    """async 执行写操作，返回 lastval (SERIAL)"""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        if params:
            row = await conn.fetchrow(sql + " RETURNING id", params)
        else:
            row = await conn.fetchrow(sql + " RETURNING id")
        return row["id"] if row else 0


def _convert_row(row: tuple, cols: list) -> dict:
    """转换行数据，将 Decimal 转为 float"""
    from decimal import Decimal
    result = {}
    for col, val in zip(cols, row):
        if isinstance(val, Decimal):
            result[col] = float(val)
        else:
            result[col] = val
    return result


# 兼容层：使用 psycopg2 同步连接
def query(sql: str, params: list | None = None) -> list[dict]:
    """查询返回字典列表（同步接口）"""
    with get_pg_conn() as conn:
        cur = conn.cursor()
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        cols = [desc[0] for desc in cur.description] if cur.description else []
        return [_convert_row(row, cols) for row in cur.fetchall()]


def query_one(sql: str, params: list | None = None) -> dict | None:
    """查询返回单行（同步接口）"""
    with get_pg_conn() as conn:
        cur = conn.cursor()
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        row = cur.fetchone()
        if row:
            cols = [desc[0] for desc in cur.description] if cur.description else []
            return _convert_row(row, cols)
        return None


def execute(sql: str, params: list | None = None) -> int:
    """执行写操作，返回 id（同步接口）"""
    with get_pg_conn() as conn:
        cur = conn.cursor()
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        conn.commit()
        return cur.lastrowid or 0


def execute_many(sql: str, params_list: list[list]):
    """批量执行（同步接口）"""
    with get_pg_conn() as conn:
        cur = conn.cursor()
        cur.executemany(sql, params_list)
        conn.commit()


# ============================================================
# Schema 定义 (PostgreSQL)
# ============================================================

_SCHEMA_SQL = """
-- accounts 表（按 user_id 隔离）
CREATE TABLE IF NOT EXISTS accounts (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL DEFAULT 1,
    name        VARCHAR(100) NOT NULL,
    broker      VARCHAR(100) DEFAULT '',
    cash        NUMERIC(18, 4) DEFAULT 0.0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_accounts_user_id ON accounts(user_id);

-- holdings 表（通过 account_id 间接隔离）
CREATE TABLE IF NOT EXISTS holdings (
    id          SERIAL PRIMARY KEY,
    account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    code        VARCHAR(20) NOT NULL,
    name        VARCHAR(100) DEFAULT '',
    quantity    INTEGER NOT NULL,
    cost_price  NUMERIC(18, 4) NOT NULL,
    current_price NUMERIC(18, 4),
    strategy    VARCHAR(100) DEFAULT '',
    notes       TEXT DEFAULT '',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_holdings_account_id ON holdings(account_id);

-- alert_rules 表（按 user_id 隔离）
CREATE TABLE IF NOT EXISTS alert_rules (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER,
    name        VARCHAR(100) NOT NULL,
    rule_type   VARCHAR(50) NOT NULL,
    config      TEXT NOT NULL,
    enabled     BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_alert_rules_user_id ON alert_rules(user_id);

-- alerts_dashboard 表（按 user_id 隔离）
CREATE TABLE IF NOT EXISTS alerts_dashboard (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER,
    rule_id     INTEGER,
    alert_type  VARCHAR(100) NOT NULL,
    severity    VARCHAR(20) NOT NULL,
    title       VARCHAR(200) NOT NULL,
    message     TEXT,
    data        TEXT,
    status      VARCHAR(20) DEFAULT 'active',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_alerts_dashboard_user_id ON alerts_dashboard(user_id);

-- schedules 表（全局，策略参数租户不可配置）
CREATE TABLE IF NOT EXISTS schedules (
    id          SERIAL PRIMARY KEY,
    strategy    VARCHAR(50) NOT NULL,
    interval    INTEGER NOT NULL,
    enabled     BOOLEAN DEFAULT TRUE,
    last_run_at TIMESTAMP,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- users 表（OAuth 用户）
CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    oauth_provider  VARCHAR(20) NOT NULL,
    oauth_id        VARCHAR(200) NOT NULL,
    username        VARCHAR(100) NOT NULL,
    display_name    VARCHAR(200) DEFAULT '',
    email           VARCHAR(200),
    avatar_url      VARCHAR(500),
    role            VARCHAR(20) NOT NULL DEFAULT 'user',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(oauth_provider, oauth_id)
);
CREATE INDEX IF NOT EXISTS idx_users_oauth ON users(oauth_provider, oauth_id);

-- watchlist 表（自选关注，按 user_id 隔离）
CREATE TABLE IF NOT EXISTS watchlist (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    code        VARCHAR(20) NOT NULL,
    name        VARCHAR(100) DEFAULT '',
    notes       TEXT DEFAULT '',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, code)
);
CREATE INDEX IF NOT EXISTS idx_watchlist_user_id ON watchlist(user_id);

-- schema_version 表（迁移追踪）
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- event_log 表（Phase 2 跨节点 SSE）
CREATE TABLE IF NOT EXISTS event_log (
    id          SERIAL PRIMARY KEY,
    event_type  VARCHAR(50) NOT NULL,
    event_data  TEXT NOT NULL,
    node_id     VARCHAR(50) DEFAULT '',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_event_log_created ON event_log(created_at);

-- local_users 表（本地账号密码登录）
CREATE TABLE IF NOT EXISTS local_users (
    id              SERIAL PRIMARY KEY,
    username        VARCHAR(50) UNIQUE NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,
    display_name    VARCHAR(100),
    role            VARCHAR(20) DEFAULT 'user',
    is_active       BOOLEAN DEFAULT true,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS task_runs (
    run_id      VARCHAR(100) PRIMARY KEY,
    strategy    VARCHAR(50) NOT NULL,
    status      VARCHAR(20) NOT NULL DEFAULT 'running',
    progress    INTEGER DEFAULT 0,
    result      TEXT,
    error       TEXT,
    started_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    node_id     VARCHAR(50) DEFAULT ''
);

-- ============================================================
-- 分钟级K线数据 (从 DuckDB 迁移)
-- ============================================================
CREATE TABLE IF NOT EXISTS minute_bars (
    code        VARCHAR(20) NOT NULL,
    time        TIMESTAMP NOT NULL,
    open        NUMERIC(18, 4),
    high        NUMERIC(18, 4),
    low         NUMERIC(18, 4),
    close       NUMERIC(18, 4),
    volume      BIGINT,
    amount      NUMERIC(18, 2),
    year        INTEGER,
    month       INTEGER,
    day         INTEGER,
    hour        INTEGER,
    minute      INTEGER,
    PRIMARY KEY (code, time)
);
CREATE INDEX IF NOT EXISTS idx_minute_bars_code ON minute_bars(code);
CREATE INDEX IF NOT EXISTS idx_minute_bars_time ON minute_bars(time DESC);

-- 15分钟K线
CREATE TABLE IF NOT EXISTS minute_bars_15m (
    code        VARCHAR(20) NOT NULL,
    time        TIMESTAMP NOT NULL,
    open        NUMERIC(18, 4),
    high        NUMERIC(18, 4),
    low         NUMERIC(18, 4),
    close       NUMERIC(18, 4),
    volume      BIGINT,
    amount      NUMERIC(18, 2),
    year        INTEGER,
    month       INTEGER,
    day         INTEGER,
    hour        INTEGER,
    minute      INTEGER,
    PRIMARY KEY (code, time)
);
CREATE INDEX IF NOT EXISTS idx_minute_15m_code ON minute_bars_15m(code);
CREATE INDEX IF NOT EXISTS idx_minute_15m_time ON minute_bars_15m(time DESC);

-- 日线行情（ETF + 股票统一表）
CREATE TABLE IF NOT EXISTS market_daily (
    id          SERIAL PRIMARY KEY,
    code        VARCHAR(20) NOT NULL,
    date        DATE NOT NULL,
    open        NUMERIC(18, 4),
    high        NUMERIC(18, 4),
    low         NUMERIC(18, 4),
    close       NUMERIC(18, 4),
    amount      NUMERIC(18, 2),
    volume      BIGINT,
    pct_chg     NUMERIC(10, 4),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (code, date)
);
CREATE INDEX IF NOT EXISTS idx_market_daily_code ON market_daily(code);
CREATE INDEX IF NOT EXISTS idx_market_daily_date ON market_daily(date DESC);

-- 监控告警记录（从 alerts.duckdb 迁移）
CREATE TABLE IF NOT EXISTS monitor_alerts (
    id              SERIAL PRIMARY KEY,
    time            TIMESTAMP,
    code            VARCHAR(20),
    strategy_name   VARCHAR(100),
    signal_type     VARCHAR(20),
    direction       VARCHAR(20),
    score           NUMERIC(10, 4),
    entry_price     NUMERIC(18, 4),
    stop_loss       NUMERIC(18, 4),
    take_profit     NUMERIC(18, 4),
    reason          TEXT,
    market_state    VARCHAR(20),
    market_return   NUMERIC(10, 4),
    market_volatility NUMERIC(10, 4),
    ma10            NUMERIC(18, 4),
    ma20            NUMERIC(18, 4),
    ma30            NUMERIC(18, 4),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_monitor_alerts_time ON monitor_alerts(time DESC);
CREATE INDEX IF NOT EXISTS idx_monitor_alerts_code ON monitor_alerts(code);

-- 市场状态快照表（预计算市场分析结果）
CREATE TABLE IF NOT EXISTS market_snapshot (
    id              SERIAL PRIMARY KEY,
    snapshot_time   TIMESTAMP NOT NULL,
    market_type     VARCHAR(20) NOT NULL,
    index_return    NUMERIC(10, 4),
    etf_pool_return NUMERIC(10, 4),
    volatility      NUMERIC(10, 4),
    trend_strength  NUMERIC(10, 4),
    index_ma_short  NUMERIC(18, 4),
    index_ma_long   NUMERIC(18, 4),
    etf_pool_ma_short NUMERIC(18, 4),
    etf_pool_ma_long NUMERIC(18, 4),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_market_snapshot_time ON market_snapshot(snapshot_time DESC);
"""


async def init_db_async():
    """异步初始化数据库表"""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        # 使用事务执行 schema
        async with conn.transaction():
            for stmt in _SCHEMA_SQL.strip().split(";"):
                s = stmt.strip()
                if s:
                    try:
                        await conn.execute(s)
                    except Exception as e:
                        # IF NOT EXISTS 忽略重复错误
                        if "already exists" not in str(e).lower():
                            logger.debug(f"Schema stmt skip: {e}")
    logger.info("PostgreSQL schema initialized")


def init_db():
    """同步初始化数据库表（启动时调用）"""
    with get_pg_conn() as conn:
        cur = conn.cursor()
        for stmt in _SCHEMA_SQL.strip().split(";"):
            s = stmt.strip()
            if s:
                try:
                    cur.execute(s)
                    conn.commit()
                except Exception as e:
                    # IF NOT EXISTS 忽略重复错误
                    if "already exists" not in str(e).lower():
                        logger.debug(f"Schema stmt skip: {e}")
                    conn.rollback()
    logger.info("PostgreSQL schema initialized")