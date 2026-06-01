"""
PostgreSQL Schema 迁移框架
幂等迁移：记录当前版本，每次启动自动执行缺失的迁移
"""
import asyncio
from loguru import logger
from .db import _get_pool, query, query_one, execute

# PostgreSQL 迁移（按版本顺序）
_MIGRATIONS: list[tuple[int, str]] = [
    # Version 1: 初始 schema（已在 db.py _SCHEMA_SQL 中定义）
    (1, """
        INSERT INTO schema_version (version)
        VALUES (1)
        ON CONFLICT (version) DO NOTHING;
    """),
    # Version 2: 多租户支持 (user_id 列已通过 db.py 初始化，这里只记录版本)
    (2, """
        INSERT INTO schema_version (version)
        VALUES (2)
        ON CONFLICT (version) DO NOTHING;
    """),
    # Version 3: 事件日志 + 任务运行记录
    (3, """
        -- event_log 和 task_runs 已在 db.py 的 _SCHEMA_SQL 中定义
        INSERT INTO schema_version (version)
        VALUES (3)
        ON CONFLICT (version) DO NOTHING;
    """),
    # Version 4: 用户有效期 + 自选关注表（拆为多条独立迁移）
    (4, """
        ALTER TABLE users ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP DEFAULT NULL;
    """),
    (5, """
        ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMP DEFAULT NULL;
    """),
    (6, """
        CREATE TABLE IF NOT EXISTS watchlist (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            code VARCHAR(20) NOT NULL,
            name VARCHAR(100) DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, code)
        );
    """),
    (7, """
        CREATE INDEX IF NOT EXISTS idx_watchlist_user_id ON watchlist(user_id);
    """),
    (8, """
        INSERT INTO schema_version (version)
        VALUES (8)
        ON CONFLICT (version) DO NOTHING;
    """),
    # Version 9: 多周期支持 - schedules 表增加 bar_interval 列
    (9, """
        ALTER TABLE schedules ADD COLUMN IF NOT EXISTS bar_interval VARCHAR(4) DEFAULT '1d';
    """),
    (10, """
        INSERT INTO schema_version (version)
        VALUES (10)
        ON CONFLICT (version) DO NOTHING;
    """),
    # Version 11: strategy_runs 执行记录表
    (11, """
        CREATE TABLE IF NOT EXISTS strategy_runs (
            id              SERIAL PRIMARY KEY,
            run_id          VARCHAR(64) UNIQUE NOT NULL,
            strategy        VARCHAR(32) NOT NULL,
            bar_interval    VARCHAR(8) NOT NULL,
            status          VARCHAR(16) NOT NULL DEFAULT 'running',
            started_at      TIMESTAMP NOT NULL,
            finished_at     TIMESTAMP,
            result_count    INTEGER DEFAULT 0,
            market_regime   JSONB,
            error_msg       TEXT,
            created_by      VARCHAR(16) DEFAULT 'scheduler'
        );
        CREATE INDEX IF NOT EXISTS idx_runs_run_id ON strategy_runs(run_id);
        CREATE INDEX IF NOT EXISTS idx_runs_strategy_time ON strategy_runs(strategy, bar_interval, started_at DESC);
        CREATE TABLE IF NOT EXISTS strategy_run_results (
            id              SERIAL PRIMARY KEY,
            run_id          VARCHAR(64) NOT NULL,
            code            VARCHAR(16) NOT NULL,
            name            VARCHAR(64),
            p60             VARCHAR(32),
            p20             VARCHAR(32),
            p10             VARCHAR(32),
            p5              VARCHAR(32),
            target_weight   REAL,
            interval_       VARCHAR(8),
            date_           VARCHAR(16),
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_results_run_id ON strategy_run_results(run_id);
    """),
]


def _get_current_version() -> int:
    """获取当前 schema 版本"""
    try:
        row = query_one("SELECT MAX(version) as v FROM schema_version")
        return row["v"] if row and row["v"] else 0
    except Exception as e:
        logger.debug(f"schema_version check: {e}")
        return 0


def run_migrations():
    """执行所有未应用的迁移（同步接口）"""
    current = _get_current_version()
    logger.info(f"Current schema version: {current}")

    for version, sql in _MIGRATIONS:
        if version <= current:
            continue
        logger.info(f"Applying migration v{version}...")
        try:
            execute(sql.strip())
            logger.info(f"Migration v{version} applied successfully")
        except Exception as e:
            err_str = str(e).lower()
            # ON CONFLICT DO NOTHING 忽略版本已存在错误
            if "duplicate" in err_str and "key" in err_str:
                logger.debug(f"Migration v{version}: already applied")
            else:
                logger.error(f"Migration v{version} failed: {e}")
                execute(f"INSERT INTO schema_version (version) VALUES ({version}) ON CONFLICT DO NOTHING")

    new_version = _get_current_version()
    logger.info(f"Schema migration complete. Now at version: {new_version}")