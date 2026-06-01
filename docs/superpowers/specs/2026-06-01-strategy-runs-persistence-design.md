# strategy_runs 持久化设计方案

## 背景

Scheduler 执行策略后，结果存在 `_running_tasks` 内存字典（`strategy_runner.py`），没有持久化到数据库。每次重启或下次执行，历史结果不可查。需要用 PostgreSQL 统一存储，弃用 CSV。

## 目标

1. 用 `strategy_runs` + `strategy_run_results` 两张表记录每次执行的输入输出
2. 在 `strategy_runner.py` 的 `run_strategy()` 中写入数据库
3. 保留 CSV 作为备份/导出用，但不作为主要数据源

## 表结构

### strategy_runs — 执行记录表

```sql
CREATE TABLE strategy_runs (
    id              SERIAL PRIMARY KEY,
    run_id          VARCHAR(64) UNIQUE NOT NULL,   -- run_20250601_143000_abc123
    strategy        VARCHAR(32) NOT NULL,           -- 'etf'
    bar_interval    VARCHAR(8) NOT NULL,            -- '1d' / '60m' / '30m' / '15m'
    status          VARCHAR(16) NOT NULL,           -- 'running' / 'complete' / 'error'
    started_at      TIMESTAMP NOT NULL,
    finished_at     TIMESTAMP,
    result_count    INTEGER DEFAULT 0,
    market_regime   JSONB,                          -- 大盘评分 {market_score, median_score, mode, ...}
    error_msg       TEXT,
    created_by      VARCHAR(16) DEFAULT 'scheduler' -- 'scheduler' / 'manual'
);

CREATE INDEX idx_runs_strategy_time ON strategy_runs(strategy, bar_interval, started_at DESC);
```

### strategy_run_results — 结果明细表

```sql
CREATE TABLE strategy_run_results (
    id              SERIAL PRIMARY KEY,
    run_id          VARCHAR(64) NOT NULL REFERENCES strategy_runs(run_id) ON DELETE CASCADE,
    code            VARCHAR(16) NOT NULL,
    name            VARCHAR(64),
    score           REAL,
    signal          VARCHAR(8),                     -- BUY / SELL / HOLD
    unit_label      VARCHAR(32),                    -- p90 / p80 / ...
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_results_run_id ON strategy_run_results(run_id);
```

## 改动范围

### 1. Schema 迁移
- 文件：`scripts/migrate_strategy_runs.sql`
- 创建上述两张表及索引

### 2. strategy_runner.py 修改
- `run_strategy()` 执行前：INSERT `strategy_runs`
- `_execute()` 执行后（成功）：UPDATE `strategy_runs`，批量 INSERT `strategy_run_results`
- `_execute()` 异常处理（失败）：UPDATE `strategy_runs` SET status='error'

### 3. 查询接口（可选）
- `/api/strategy/history` — 查询最近 N 次执行记录
- `/api/strategy/results/{run_id}` — 已有，改为从数据库读取

### 4. CSV 保留
- CSV 继续按原逻辑写入（备份/导出用途）
- 不依赖 CSV 作为数据源

## 执行顺序

1. 创建迁移 SQL 文件
2. 修改 `strategy_runner.py` 添加数据库写入
3. 运行迁移创建表
4. 测试 `uv run quant-etf dashboard` 启动后策略执行正常
5. 验证结果写入数据库

## 不在范围内

- 修改现有 CSV 写入逻辑（保留）
- API 接口大幅调整
- 历史 CSV 数据导入数据库