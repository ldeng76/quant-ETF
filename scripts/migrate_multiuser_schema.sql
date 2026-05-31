-- ============================================================
-- 多用户策略定时调度系统 - 数据库 Schema 迁移
-- 幂等脚本：可重复执行
-- ============================================================

BEGIN;

-- ----------------------------------------------------------
-- 1. scheduler_users 表（存储调度用户）
-- ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS scheduler_users (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(64) UNIQUE NOT NULL,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ----------------------------------------------------------
-- 2. user_pools 表（用户私有证券池）
-- ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_pools (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES scheduler_users(id) ON DELETE CASCADE,
    pool_type   VARCHAR(32) NOT NULL,  -- 'etf' / 'stock' / 'mid_term'
    codes       JSONB NOT NULL,         -- 用户私有证券代码列表，如 ["000001", "000002"]
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at  TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_user_pool_type UNIQUE (user_id, pool_type)
);

-- ----------------------------------------------------------
-- 3. strategy_rankings 表（策略计算结果）
-- 注意：interval 是 SQL 保留字，列名用 interval_
-- ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategy_rankings (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES scheduler_users(id) ON DELETE CASCADE,
    interval_   VARCHAR(8) NOT NULL,   -- '1d' / '60m' / '30m' / '15m'
    task_type   VARCHAR(32) NOT NULL,  -- 'etf' / 'short' / 'mid_term'
    code        VARCHAR(16) NOT NULL,
    score       NUMERIC(12, 6) NOT NULL,
    rank_pos    INTEGER NOT NULL,
    p60         NUMERIC(12, 6),
    p20         NUMERIC(12, 6),
    p10         NUMERIC(12, 6),
    p5          NUMERIC(12, 6),
    volume_ratio NUMERIC(10, 4),
    trend_ok    BOOLEAN,
    computed_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ----------------------------------------------------------
-- 4. job_runs 表（调度运行记录）
-- 注意：interval 是 SQL 保留字，列名用 interval_
-- ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS job_runs (
    id              SERIAL PRIMARY KEY,
    interval_       VARCHAR(8) NOT NULL,
    started_at      TIMESTAMP NOT NULL,
    finished_at     TIMESTAMP,
    status          VARCHAR(16) NOT NULL,  -- 'success' / 'partial' / 'failed'
    user_count      INTEGER,
    security_count  INTEGER,
    error_msg       TEXT
);

-- ----------------------------------------------------------
-- 5. 索引
-- ----------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_rankings_user_interval_task
    ON strategy_rankings(user_id, interval_, task_type, computed_at DESC);
CREATE INDEX IF NOT EXISTS idx_rankings_code_time
    ON strategy_rankings(code, computed_at DESC);
CREATE INDEX IF NOT EXISTS idx_job_runs_interval_time
    ON job_runs(interval_, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_pools_user_type
    ON user_pools(user_id, pool_type);

-- ----------------------------------------------------------
-- 6. 默认管理员用户（id=1）
-- ----------------------------------------------------------
INSERT INTO scheduler_users (id, name, enabled, created_at)
VALUES (1, 'admin', TRUE, NOW())
ON CONFLICT (id) DO NOTHING;

COMMIT;

-- 验证
SELECT 'scheduler_users'    AS tbl, COUNT(*) AS rows FROM scheduler_users;
SELECT 'user_pools'          AS tbl, COUNT(*) AS rows FROM user_pools;
SELECT 'strategy_rankings'   AS tbl, COUNT(*) AS rows FROM strategy_rankings;
SELECT 'job_runs'            AS tbl, COUNT(*) AS rows FROM job_runs;