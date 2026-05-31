# 多用户策略定时调度系统 - 设计方案

> **状态：** 已批准
> **日期：** 2026-05-31

## 1. 目标

为量化 ETF 系统增加多用户支持 + 180 秒定时重算能力。所有用户共享统一策略（ETF 组合选股、中期反弹选股、短线选股），每个用户通过不同的证券池组合自己的"投资组合"。

## 2. 核心架构

```
┌──────────────────────────────────────────────────────────┐
│                    APScheduler                            │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐     │
│  │Job 1d  │  │Job 60m │  │Job 30m │  │Job 15m │     │
│  │@180s  │  │@180s   │  │@180s   │  │@180s   │     │
│  └────┬───┘  └────┬───┘  └────┬───┘  └────┬───┘     │
└───────┼───────────┼───────────┼───────────┼──────────┘
        │           │           │           │
        └───────────┴─────┬─────┴───────────┘
                          ▼
              ┌──────────────────────┐
              │   Data Fetcher        │
              │   (全局证券并集去重)   │
              │   + 共享内存缓存       │
              └──────────┬───────────┘
                         │ 数据就绪
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
    ┌──────────┐  ┌──────────┐  ┌──────────┐
    │ETF策略  │  │短线策略 │  │中期反弹  │
    │(并行用户)│  │(并行用户)│  │(并行用户)│
    └────┬─────┘  └────┬─────┘  └────┬─────┘
         │             │             │
         └─────────────┼─────────────┘
                       ▼
              ┌──────────────────┐
              │  结果写入 PostgreSQL │
              └──────────────────┘
```

## 3. 证券池模型

### 3.1 池类型

| 类型 | 来源 | 合并方式 |
|------|------|----------|
| 公共池 | `conf.py` 中的 `ETF_POOL`、`STOCK_POOL`、`MID_TERM_STOCK_POOL` | 所有用户共享 |
| 用户私有池 | 数据库 `user_pools` 表 | 追加到公共池 |

### 3.2 合并规则

```python
def get_user_codes(user_id: int, pool_type: str, interval: str) -> set[str]:
    # 1. 公共池
    codes = set(PUBLIC_POOLS[pool_type][interval])
    # 2. 用户私有池（追加模式）
    user_private = db.get_user_pool(user_id, pool_type)
    if user_private:
        codes.update(user_private.codes)
    return codes
```

## 4. 数据层设计

### 4.1 全局证券并集

```python
def get_all_codes_for_interval(interval: str) -> set[str]:
    codes = set(PUBLIC_POOLS[interval])
    for user_pool in db.get_all_user_pools(interval):
        codes.update(user_pool.codes)
    return codes
```

### 4.2 共享数据缓存

- `SharedDataCache`：进程内字典，按 `(code, interval)` 缓存 K 线 DataFrame
- TTL = 300s，超时重新拉取
- 同一轮 Job 内多次访问命中缓存

## 5. 调度策略

- 4 个独立 APScheduler Job，每个按 180 秒间隔执行
- Job 之间互不干扰，独立调度
- 每轮 Job 执行超时：150 秒

## 6. 执行流程

每轮 Job 的执行流：

```
1. 触发 Job(周期)
2. 获取该周期的全局证券并集
3. 批量抓取数据 → 写入共享缓存
4. 获取所有用户列表
5. ThreadPoolExecutor(max_workers=N) 并行：
   for user in users:
       run_etf_task(user, interval)
       run_short_task(user, interval)
       run_midterm_task(user, interval)
6. 所有结果写入 DB
7. 记录本轮运行日志
```

## 7. 数据库设计

### 7.1 新增表

**`users`**

```sql
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    name VARCHAR(64) UNIQUE NOT NULL,
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);
```

**`user_pools`**

```sql
CREATE TABLE user_pools (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id),
    pool_type VARCHAR(32),  -- 'etf' / 'stock' / 'mid_term'
    codes JSONB NOT NULL,    -- 用户私有 codes
    enabled BOOLEAN DEFAULT TRUE,
    updated_at TIMESTAMP DEFAULT NOW()
);
```

**`strategy_rankings`**

```sql
CREATE TABLE strategy_rankings (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id),
    interval VARCHAR(8),     -- '1d' / '60m' / '30m' / '15m'
    task_type VARCHAR(32),  -- 'etf' / 'short' / 'mid_term'
    code VARCHAR(16),
    score DECIMAL(12,6),
    rank_pos INT,
    p60 DECIMAL(12,6),
    p20 DECIMAL(12,6),
    p10 DECIMAL(12,6),
    p5 DECIMAL(12,6),
    volume_ratio DECIMAL(10,4),
    trend_ok BOOLEAN,
    computed_at TIMESTAMP DEFAULT NOW()
);
```

**`job_runs`**

```sql
CREATE TABLE job_runs (
    id SERIAL PRIMARY KEY,
    interval VARCHAR(8),
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    status VARCHAR(16),      -- 'success' / 'partial' / 'failed'
    user_count INT,
    security_count INT,
    error_msg TEXT
);
```

### 7.2 索引

```sql
CREATE INDEX idx_rankings_user_interval_task ON strategy_rankings(user_id, interval, task_type, computed_at DESC);
CREATE INDEX idx_rankings_code_time ON strategy_rankings(code, computed_at DESC);
```

## 8. 错误处理

| 场景 | 处理方式 |
|------|----------|
| 数据抓取失败 | 本轮跳过，标记 `job_runs.status='failed'`，不写入排名 |
| 单用户策略异常 | 跳过该用户，其他用户继续 |
| 单支证券数据异常 | 跳过该证券，不影响同批次其他证券 |
| Job 执行超时 | 设置 150s 超时，超时则强制结束 |

## 9. 监控

- 每轮 Job 运行记录写入 `job_runs` 表
- `loguru` 记录：触发时间、采集耗时、策略耗时、总耗时

## 10. 复用清单

| 现有组件 | 复用方式 |
|----------|----------|
| `ETFTask`、`ShortTermStockTask`、`MidTermReboundTask` | 直接调用，传入用户证券池 |
| `data_source.py`、`minute_collector.py` | 数据拉取，新增缓存层包装 |
| `conf.py` 公共池配置 | 直接引用 |
| PostgreSQL 连接 | 复用现有连接基础设施 |
| CLI 入口 | 新增 APScheduler 服务启动命令 |