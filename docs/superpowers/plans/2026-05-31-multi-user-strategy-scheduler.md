# 多用户策略定时调度系统 - 实施计划

> **状态：** 进行中
> **日期：** 2026-05-31
> **分支：** `feat/multi-user-scheduler`
> **设计稿：** `docs/superpowers/specs/2026-05-31-multi-user-strategy-scheduler-design.md`

---

## 任务分解

### 阶段一：基础设施（无依赖，可并行）

- [T1] **DB Schema 迁移脚本**：`scripts/migrate_multiuser_schema.sql`
  - 创建 `users`、`user_pools`、`strategy_rankings`、`job_runs` 四张表及索引
  - 创建默认管理员用户（id=1）
  - SQL 可幂等重复执行（`CREATE TABLE IF NOT EXISTS` + `DO $$ BEGIN EXCEPTION$$`）

- [T2] **数据库操作层**：`src/quant_etf/scheduler_db.py`
  - `get_all_users()` → List[dict]
  - `get_user_pool(user_id, pool_type)` → list[str] | None
  - `get_all_user_pools()` → Dict[(pool_type, user_id), list[str]]
  - `upsert_strategy_rankings(rankings: list[dict])` → None
  - `insert_job_run(...)` → int (返回 run_id)
  - `update_job_run(run_id, status, error_msg)` → None
  - 复用 `dashboard/db.py` 已有 `get_pg_conn()`（同步 psycopg2）
  - 所有 SQL 参数化防注入

- [T3] **共享数据缓存**：`src/quant_etf/scheduler_cache.py`
  - `SharedDataCache` 类：进程内 dict，key = `(code, interval)`，value = DataFrame
  - TTL = 300s（全局常量 `CACHE_TTL = 300`）
  - `get(code, interval)` → DataFrame | None（过期返回 None）
  - `set(code, interval, df)` → None
  - `prefetch(codes: set[str], interval)` → None（批量抓取所有证券的 K 线数据）
  - 数据来源：复用 `data_source.py` 的 `load_dataframe` / `get_tdx_bars` 方法
  - prefetch 异常：单个证券失败不影响其他证券，记录 warning 后继续

### 阶段二：调度核心（依赖 T2 + T3）

- [T4] **调度引擎**：`src/quant_etf/scheduler_engine.py`
  - `PUBLIC_POOLS` 常量（构建自 `conf.py` 的 `ETF_POOL`/`STOCK_POOL`/`MID_TERM_STOCK_POOL`，三层 dict：pool_type → user_id → codes）
  - `get_user_codes(user_id, pool_type)` → set[str]
  - `get_all_codes(interval)` → set[str]（所有用户 + 公共池的并集）
  - `run_job_for_interval(interval, run_id)` → None
    1. 获取全局证券并集
    2. `cache.prefetch(codes, interval)` 预热缓存
    3. `get_all_users()` 获取所有用户
    4. `ThreadPoolExecutor(max_workers=len(users))` 并行执行三个策略
    5. 每个策略调用现有 Task 类，传入用户证券池
    6. 收集所有 ranking 结果
    7. `upsert_strategy_rankings()` 批量写入 DB
  - `run_single_user_strategy(user, interval)` → list[dict] rankings
    - 调用 ETFTask / ShortTermStockTask / MidTermReboundTask
    - 注入 `conf.ETF_POOL = user_etf_codes` 等（通过 monkey-patch 或 Task 参数）
    - 实际方案：Task 构造函数接受 `codes` 参数覆盖池子（需要修改 Task 签名）

- [T5] **APScheduler 入口**：`src/quant_etf/scheduler.py`
  - `start_scheduler()` 函数
  - 使用 `APScheduler` + `BackgroundScheduler`（非阻塞）
  - 注册 4 个 Job：`1d`、`60m`、`30m`、`15m`，间隔均为 180 秒
  - 每个 Job 设置 `misfire_grace_time=30`
  - 超时控制：`signal.signal(signal.SIGALRM, handler)` 在 150s 后强制退出（Windows 不支持 SIGALRM，改用 `threading.Timer`）
  - 日志：Job 触发时记录 info，异常时记录 error

### 阶段三：CLI 集成与 Task 改造（依赖 T4）

- [T6] **Task 池子注入改造**：`src/quant_etf/tasks.py` — `BaseTask.__init__` 增加 `codes: list[str] | None = None` 参数；`run()` 方法中若 `self._codes` 非空则用它替换 conf 中的池子（通过临时 patch `conf.ETF_POOL` 等）

- [T7] **CLI 命令**：`src/quant_etf/cli.py` 增加 `scheduler` 子命令
  - `quant-etf scheduler start`：启动调度服务
  - `quant-etf scheduler status`：查询当前调度状态（job_runs 表最新记录）
  - 注册命令：`subparsers.add_parser("scheduler", ...).set_defaults(func=cmd_scheduler)`

### 阶段四：验证

- [T8] **单元测试**（如测试框架已就绪）
  - `tests/test_scheduler_cache.py` — TTL 过期、并发 prefetch
  - `tests/test_scheduler_db.py` — CRUD 操作
  - `tests/test_scheduler_engine.py` — 并行执行逻辑

---

## 执行顺序

```
T1 (DB迁移) ─┐
             ├──→ T2 (scheduler_db.py) ─┐
T3 (cache.py) ────────────────────────────┼──→ T4 (scheduler_engine.py) ─→ T6 (Task改造) ─→ T7 (CLI) ─→ T8 (tests)
                                           │
                                           └──→ T5 (scheduler.py)
```

**并行策略：** T1 和 T3 可并行；T2 完成后 T4 可并行启动；T6 在 T4 后执行。

---

## 验收标准

1. `scripts/migrate_multiuser_schema.sql` 执行后四张表创建成功，无报错
2. `scheduler_db.py` 的 CRUD 函数正确执行（验证 INSERT + SELECT）
3. `scheduler_cache.py` 的 prefetch + get + TTL 逻辑正确（单元测试覆盖）
4. `scheduler_engine.py` 单独运行一次 1d Job 能产出 rankings 结果
5. `scheduler.py` 启动后 180s 内四个 Job 各触发一次（观察日志）
6. CLI `scheduler start` 命令可正常启动服务

---

## 技术决策

1. **Task 池子注入**：用 monkey-patch 方案（临时替换 `conf.ETF_POOL` 等），避免修改 Task 构造函数签名
2. **超时控制**：Windows 平台使用 `threading.Timer` 替代 SIGALRM
3. **PostgreSQL 连接**：同步 `psycopg2`（已有 `get_pg_conn()`），不引入 asyncpg 以保持模块简洁
4. **缓存失效**：基于时间戳判断，每次 `get()` 时检查 `time.time() - cached_at > CACHE_TTL`
5. **池子模型**：所有 K 线周期共用同一套公共池（用户确认）