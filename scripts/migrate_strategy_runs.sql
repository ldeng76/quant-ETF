-- strategy_runs: 执行记录表
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

CREATE INDEX IF NOT EXISTS idx_runs_strategy_time ON strategy_runs(strategy, bar_interval, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_run_id ON strategy_runs(run_id);

-- strategy_run_results: 结果明细表
CREATE TABLE IF NOT EXISTS strategy_run_results (
    id              SERIAL PRIMARY KEY,
    run_id          VARCHAR(64) NOT NULL,
    code            VARCHAR(16) NOT NULL,
    name            VARCHAR(64),
    score           REAL,
    signal          VARCHAR(8),
    unit_label      VARCHAR(32),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_results_run_id ON strategy_run_results(run_id);