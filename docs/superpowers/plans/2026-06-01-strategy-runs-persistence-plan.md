# strategy_runs 持久化实现计划

## 步骤 1：创建 Schema 迁移 SQL

**文件**：`scripts/migrate_strategy_runs.sql`

```sql
-- strategy_runs：执行记录表
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

-- strategy_run_results：结果明细表
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

-- 外键（可选，ON DELETE CASCADE 便于清理）
ALTER TABLE strategy_run_results DROP CONSTRAINT IF EXISTS fk_results_run_id;
ALTER TABLE strategy_run_results ADD CONSTRAINT fk_results_run_id
    FOREIGN KEY (run_id) REFERENCES strategy_runs(run_id) ON DELETE CASCADE;
```

**执行**：`psql $DATABASE_URL -f scripts/migrate_strategy_runs.sql`

---

## 步骤 2：修改 `strategy_runner.py`

### 2.1 新增导入

文件：`src/quant_etf/dashboard/services/strategy_runner.py`

```python
from ..db import execute, execute_many
```

### 2.2 新增数据库写入函数

在文件末尾（约第 550 行后）新增：

```python
def _save_run_record(run_id: str, strategy: str, bar_interval: str, status: str,
                     result: list[dict] | None = None, market_regime: dict | None = None,
                     error_msg: str | None = None, finished_at: str | None = None):
    """保存执行记录到数据库"""
    from ..db import execute, execute_many
    from datetime import datetime

    # UPDATE 状态
    result_count = len(result) if result else 0
    execute("""
        UPDATE strategy_runs
        SET status = %s,
            finished_at = %s,
            result_count = %s,
            market_regime = %s,
            error_msg = %s
        WHERE run_id = %s
    """, [status, finished_at, result_count,
          json.dumps(market_regime) if market_regime else None,
          error_msg, run_id])

    # 批量写入结果
    if result and status == 'complete':
        params = [
            [run_id, r.get('code', ''), r.get('name', ''),
             r.get('score'), r.get('signal', ''), r.get('unit_label', '')]
            for r in result
        ]
        execute_many("""
            INSERT INTO strategy_run_results (run_id, code, name, score, signal, unit_label)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, params)
```

### 2.3 修改 `run_strategy()` 执行前插入记录

约第 89-96 行，在设置 `_running_tasks[run_id]` 后新增：

```python
    # 插入数据库记录
    execute("""
        INSERT INTO strategy_runs (run_id, strategy, bar_interval, status, started_at, created_by)
        VALUES (%s, %s, %s, 'running', %s, 'scheduler')
    """, [run_id, strategy_name, bar_interval, datetime.now().isoformat()])
```

### 2.4 修改 `_execute()` 成功后写入结果

约第 153-157 行，`_running_tasks[run_id]["status"] = "complete"` 后新增：

```python
    # 持久化到数据库
    _save_run_record(
        run_id=run_id,
        strategy=strategy_name,
        bar_interval=bar_interval,
        status='complete',
        result=records,
        market_regime=_running_tasks[run_id].get("market_regime"),
        finished_at=_running_tasks[run_id].get("finished_at")
    )
```

### 2.5 修改 `_execute()` 异常处理时更新状态

约第 222-224 行，在设置状态后新增：

```python
    _save_run_record(
        run_id=run_id,
        strategy=strategy_name,
        bar_interval=bar_interval,
        status='error',
        error_msg=error_msg,
        finished_at=datetime.now().isoformat()
    )
```

---

## 步骤 3：运行迁移

```bash
psql $DATABASE_URL -f scripts/migrate_strategy_runs.sql
```

或通过 dashboard 的迁移机制（如果有的话）。

---

## 步骤 4：验证

### 4.1 语法检查
```bash
uv run python -c "from quant_etf.dashboard.services.strategy_runner import run_strategy; print('ok')"
```

### 4.2 启动 dashboard
```bash
uv run quant-etf dashboard --no-reload
```

观察日志中策略执行后是否写入数据库。

### 4.3 查询验证
```sql
SELECT * FROM strategy_runs ORDER BY started_at DESC LIMIT 5;
SELECT * FROM strategy_run_results WHERE run_id = 'xxx';
```

---

## 文件清单

| 文件 | 操作 |
|------|------|
| `scripts/migrate_strategy_runs.sql` | 新建 |
| `src/quant_etf/dashboard/services/strategy_runner.py` | 修改 |

---

## 回滚计划

如需回滚，执行：
```sql
DROP TABLE IF EXISTS strategy_run_results;
DROP TABLE IF EXISTS strategy_runs;
```