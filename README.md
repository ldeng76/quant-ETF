# quant-etf

基于动量策略的 ETF 选股工具，使用通达信(TDX)数据源。

## 功能特点

- **动量选股策略**: 基于 60/20/10/5 日收益率的加权动量排名
- **通达信数据集成**: 支持本地 TDX 数据文件和在线数据获取
- **自动导出**: 生成 TDX 导入文件和自定义公式
- **跨平台**: 支持 Windows 和 Linux

## 安装

### 前置要求

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (推荐) 或 pip

### 安装步骤

```bash
# 克隆仓库
git clone http://c202601:3000/dzy/quant-etf.git
cd quant-etf

# 使用 uv 安装 (推荐)
uv sync

# 或使用 pip
pip install -e .
```

## 配置

### 通达信数据源配置

项目支持两种数据源方式：

#### 1. 本地 TDX 数据文件

配置通达信数据目录路径（用于读取本地 `.day` 数据文件）：

**Windows**:
```python
# 编辑 src/quant_etf/conf.py
TDX_DIR = Path(r"C:\new_hxzq_hc")
```

**Linux**:
```bash
# 设置环境变量
export TDX_DATA_PATH="$HOME/.local/share/tdx"
```

或直接修改配置文件：
```python
TDX_DIR = Path.home() / ".local" / "share" / "tdx"
```

#### 2. 在线数据获取

项目使用 `pytdx` 库支持在线数据获取，当本地数据不可用时自动回退到在线数据源。

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `TDX_DATA_PATH` | 通达信数据目录 | Windows: `C:\new_hxzq_hc`<br>Linux: `~/.local/share/tdx` |

## CLI 子命令

项目提供统一的命令行入口 `quant-etf`，通过 `pyproject.toml` 注册：

```bash
uv run quant-etf --help
uv run quant-etf <command> [options]
```

### 子命令一览

| 命令 | 说明 |
|------|------|
| `daily-run` | 运行每日选股任务（ETF + 短线 + 中期反弹） |
| `run` | 运行单个选股任务 |
| `list-tasks` | 列出所有可用选股任务 |
| `dashboard` | 启动 Dashboard 监控系统 |
| `minute-collect` | 启动分钟级 K 线数据采集器 |
| `backfill` | 批量补跑历史日期任务 |
| `restart-dashboard` | 一键重启 Dashboard 服务 |
| `check` | Dashboard 健康检查 |
| `backfill-stock-names` | 补齐缺失的股票代码名称 |

### 命令详解

#### `daily-run` — 运行每日选股任务

同时运行 ETF / 短线股票 / 中期反弹三种选股策略，并生成汇总对比报告。

```bash
uv run quant-etf daily-run                     # 运行今天
uv run quant-etf daily-run --days 5            # 运行最近 5 个交易日
uv run quant-etf daily-run --date 2026-05-20   # 运行指定日期
```

| 参数 | 说明 | 默认 |
|------|------|------|
| `--days, -d` | 运行最近 N 天 | `1` |
| `--date` | 指定日期 (格式: YYYY-MM-DD) | — |

#### `run` — 运行单个选股任务

```bash
uv run quant-etf run etf                        # ETF 选股
uv run quant-etf run short                      # 短线股票选股
uv run quant-etf run mid                        # 中期反弹选股
uv run quant-etf run etf --date 2026-05-20      # 指定日期
```

| 参数 | 说明 | 默认 |
|------|------|------|
| `task` | 任务名称: `etf` / `short` / `mid` | `etf` |
| `--date` | 指定日期 (格式: YYYY-MM-DD) | — |

#### `list-tasks` — 列出所有可用任务

```bash
uv run quant-etf list-tasks
```

#### `dashboard` — 启动监控面板

启动 Web Dashboard (FastAPI + Uvicorn)，提供策略结果 / 持仓 / 市场概览等页面。

```bash
uv run quant-etf dashboard                        # 默认 127.0.0.1:8522
uv run quant-etf dashboard --port 8080            # 自定义端口
uv run quant-etf dashboard --host 0.0.0.0         # 开放网络访问
uv run quant-etf dashboard --no-reload            # 禁用热重载
```

| 参数 | 说明 | 默认 |
|------|------|------|
| `--port, -p` | 监听端口 | `8522` |
| `--host` | 监听地址 | `127.0.0.1` |
| `--no-reload` | 禁用热重载 | — |

#### `minute-collect` — 分钟级 K 线数据采集

持续运行，在交易时段内每分钟采集一次 ALL_POOL 标的的 1 分钟 K 线数据。

```bash
uv run quant-etf minute-collect
```

无参数。支持 `Ctrl+C` 优雅退出。

#### `backfill` — 批量补跑历史任务

```bash
uv run quant-etf backfill 2026-03-02 2026-03-05
```

| 参数 | 说明 |
|------|------|
| `start_date` | 开始日期 (必填，格式: YYYY-MM-DD) |
| `end_date` | 结束日期 (必填，格式: YYYY-MM-DD) |

#### `restart-dashboard` — 重启 Dashboard

查找并终止已有 Dashboard 进程，在相同端口重新启动：

```bash
uv run quant-etf restart-dashboard
```

#### `check` — Dashboard 健康检查

遍历 Dashboard 各页面路由，验证服务是否正常：

```bash
uv run quant-etf check                  # 默认端口 8080
uv run quant-etf check --port 8522      # 指定端口
```

| 参数 | 说明 | 默认 |
|------|------|------|
| `--port` | Dashboard 端口 | `8080` |

#### `backfill-stock-names` — 补齐股票代码名称

从在线数据源补齐 `stock_code_name.json` 中缺失的股票名称：

```bash
uv run quant-etf backfill-stock-names
```

### 与主流程的关系

所有功能模块均通过 `src/quant_etf/cli.py` 统一调度。模块入口关系如下：

```
uv run quant-etf <command>
        │
        └── cli.py:main()          ← 参数解析 + 命令分发
                │
                ├── daily-run   → tasks.py (TaskRegistry + ETFTask/ShortTermStockTask/MidTermReboundTask)
                ├── run         → tasks.py (单任务)
                ├── list-tasks  → tasks.py (列出)
                ├── dashboard   → dashboard/app.py (FastAPI)
                ├── minute-collect → minute_collector.py
                ├── backfill    → tasks.py + trading_day.py
                ├── restart-dashboard → 进程管理 + dashboard
                ├── check       → HTTP 健康检查
                └── backfill-stock-names → data_source.py
```

### 旧版入口脚本（向下兼容）

以下旧版脚本仍然可用，但内部均已重定向到统一 CLI：

| 旧脚本 | 对应 CLI 命令 |
|--------|-------------|
| `run_daily.py` | `quant-etf daily-run` |
| `run_dashboard.py` | `quant-etf dashboard` |
| `run_minute_collector.py` | `quant-etf minute-collect` |
| `restart_dashboard.py` | `quant-etf restart-dashboard` |
| `backfill_daily.py` | `quant-etf backfill` |
| `_check.py` | `quant-etf check` |
| `src/main.py` | `quant-etf run` / `quant-etf list-tasks` |

### 输出文件

运行后会在 `output/` 目录生成：

- `TDX_Strategy_Pick.txt` — TDX 导入文件
- `TDX_Formula_Momentum.txt` — TDX 自定义公式文件

将 `TDX_Strategy_Pick.txt` 内容复制到通达信的自定义板块中即可使用。

## 测试

```bash
# 运行所有测试
uv run pytest

# 运行特定测试文件
uv run pytest tests/test_tdx.py -v

# 跳过集成测试（需要真实 TDX 数据）
uv run pytest -m "not integration"
```

## 项目结构

```
quant-etf/
├── run_daily.py             # [旧] 每日选股 (→ CLI daily-run)
├── run_dashboard.py         # [旧] 启动看板 (→ CLI dashboard)
├── run_minute_collector.py  # [旧] K线采集 (→ CLI minute-collect)
├── restart_dashboard.py     # [旧] 重启看板 (→ CLI restart-dashboard)
├── backfill_daily.py        # [旧] 历史补跑 (→ CLI backfill)
├── _check.py                # [旧] 健康检查 (→ CLI check)
└── src/
    └── quant_etf/
        ├── cli.py           # ⬅ 统一 CLI 入口
        ├── conf.py          # 配置 (股票池、权重、数据目录)
        ├── data_source.py   # 数据源管理
        ├── tdx.py           # 通达信数据处理
        ├── tasks.py         # 选股任务注册与调度
        └── ...
├── tests/                   # 测试文件
├── output/                  # 输出目录
├── data/                    # 数据缓存目录
├── plan_*.md               # 计划文档
└── README.md
```

## 配置说明

### 动量权重调整

编辑 `src/quant_etf/conf.py`:

```python
MOMENTUM_WEIGHTS = {
    "r60": 0.1,  # 60日收益率权重
    "r20": 0.2,  # 20日收益率权重
    "r10": 0.3,  # 10日收益率权重
    "r5": 0.4    # 5日收益率权重
}
```

### 持仓数量

```python
TOP_N = 15  # 选出前 N 只标的
```

## 常见问题

### Q: Linux 下测试失败？

A: 部分测试需要真实的 TDX 数据。可以使用 `TDX_DATA_PATH` 环境变量指定数据目录，或跳过集成测试：

```bash
uv run pytest -m "not integration"
```

### Q: 在线数据获取失败？

A: 检查网络连接和 pytdx 库是否正确安装：

```bash
uv run pip show pytdx
```

---

## Dashboard 监控系统

### 系统架构

基于 **FastAPI + HTMX + SQLite/DuckDB + SSE** 的全栈监控看板，零 JavaScript 框架依赖（前端交互由 HTMX 驱动）。

```
┌──────────────┐     ┌──────────────────────────────────────┐
│  浏览器       │     │  FastAPI Server (uvicorn)            │
│  (HTMX+Alpine)│────▶│  ┌──────────┐ ┌──────────────────┐  │
│  SSE ←───────│     │  │ pages    │ │ API routes       │  │
│              │     │  │ (HTML)   │ │ /api/portfolio    │  │
│              │     │  │          │ │ /api/strategy     │  │
│              │     │  │          │ │ /api/alerts       │  │
│              │     │  │          │ │ /api/market       │  │
│              │     │  └──────────┘ └──────────────────┘  │
│              │     │  ┌──────────────────────────────┐    │
│              │     │  │ Services                     │    │
│              │     │  │  scheduler → strategy_runner  │    │
│              │     │  │  alert_engine → sse_manager   │    │
│              │     │  │  portfolio_sync              │    │
│              │     │  └──────────────────────────────┘    │
│              │     │  ┌──────────────────────────────┐    │
│              │     │  │ Data Stores                 │    │
│              │     │  │  dashboard.db (SQLite)      │    │
│              │     │  │  minute_data.duckdb          │    │
│              │     │  │  results.duckdb / alerts.*   │    │
│              │     │  └──────────────────────────────┘    │
└──────────────┘     └──────────────────────────────────────┘
```

- **模板引擎**: Jinja2，页面按模块拆分（`index.html` = 完整壳层，`_content.html` = HTMX 片段）
- **前端库**: Bootstrap 5.3 + HTMX 2.0 + Alpine.js 3.14 + Chart.js 4.4
- **后端**: FastAPI + uvicorn（热重载默认开启）
- **数据库**: 主库 SQLite (`data/dashboard.db`)，分钟K线/结果/告警存储在 DuckDB

### 启动方式

**方式一：统一 CLI（推荐）**
```bash
# 启动
uv run quant-etf dashboard --port 8522

# 重启（关旧进程 → 起新进程）
uv run quant-etf restart-dashboard

# 健康检查
uv run quant-etf check --port 8522
```

**方式二：Legacy 脚本（向后兼容）**
```bash
uv run python run_dashboard.py
uv run python restart_dashboard.py
```

**方式三：自定义参数**
```bash
uv run quant-etf dashboard --host 0.0.0.0 --port 8522 --no-reload
```

环境变量：`DASHBOARD_HOST`（默认 `127.0.0.1`）、`DASHBOARD_PORT`（默认 `8522`）。

### 核心页面

| 页面 | 路由 | 功能 |
|------|------|------|
| **总览** | `/pages/overview` | 账户数、今日告警、运行中调度数等关键指标卡片 |
| **持仓** | `/pages/portfolio` | 多账户管理（CRUD）、持仓表格（增删改）、手动价格同步 |
| **策略** | `/pages/strategy` | 列出可用策略、发起执行、查看运行进度与结果表格/图表 |
| **监控** | `/pages/monitor` | 定时调度配置（CRUD），支持手动启停循环任务 |
| **告警** | `/pages/alerts` | 告警规则管理、告警列表（按状态排序）、ETFMonitor 监控信号 |
| **设置** | `/pages/settings` | 系统设置（预留扩展） |

所有页面同时支持**全页加载**（直接浏览器访问）和 **HTMX 片段加载**（侧边栏导航），通过 `HX-Request` 请求头自动判断。

### API 路由结构

```
GET    /pages/overview            总览页
GET    /pages/portfolio           持仓页
GET    /pages/strategy            策略页
GET    /pages/monitor             监控页
GET    /pages/alerts              告警页
GET    /pages/settings            设置页

GET    /api/portfolio/accounts          账户列表（HTML片段）
POST   /api/portfolio/accounts          创建账户
PUT    /api/portfolio/accounts/{id}     更新账户
DELETE /api/portfolio/accounts/{id}     删除账户
GET    /api/portfolio/accounts/{id}/holdings  持仓列表
POST   /api/portfolio/holdings          创建持仓
PUT    /api/portfolio/holdings/{id}     更新持仓
DELETE /api/portfolio/holdings/{id}     删除持仓
POST   /api/portfolio/sync-prices       手动同步持仓价格

GET    /api/strategy/strategies         列出可用策略
POST   /api/strategy/run                执行选定策略
GET    /api/strategy/status/{run_id}    查看执行进度
GET    /api/strategy/results/{run_id}   渲染结果表格

GET    /api/alerts/rules                告警规则列表
POST   /api/alerts/rules                创建规则
DELETE /api/alerts/rules/{id}           删除规则
GET    /api/alerts/dashboard            告警列表片段
PUT    /api/alerts/dashboard/{id}/status 更新告警状态
GET    /api/alerts/dashboard/stats      告警统计
GET    /api/alerts/monitor-signals      ETFMonitor 监控信号

GET    /api/market/status               市场环境判断（JSON）
GET    /api/market/overview             总览数据卡片
GET    /api/market/schedules            调度配置列表
POST   /api/market/schedules            创建调度
DELETE /api/market/schedules/{id}       删除调度
POST   /api/market/schedules/{id}/toggle 启停调度

GET    /events                          SSE 事件流端点（实时推送）
```

### SSE 实时推送机制

系统使用 **Server-Sent Events (SSE)** 实现服务端到浏览器的实时事件推送，无需 WebSocket。

**架构：**
- `services/sse_manager.py` 维护 `asyncio.Queue` 集合，每个浏览器连接对应一个独立队列

**事件类型：**

| 事件类型 | 触发场景 | 推送方 |
|---------|---------|--------|
| `connected` | 客户端初次建立 SSE 连接 | sse_manager |
| `strategy_result` | 定时调度策略执行完成 | scheduler / strategy_runner |
| `strategy_error` | 策略执行失败 | scheduler / strategy_runner |
| `alert` | 告警引擎检测到新告警 | strategy_runner（告警引擎集成） |
| `portfolio_update` | 持仓价格同步完成 | portfolio_sync |

**连接管理：**
- 心跳探测：每 30 秒发送一次注释行（`heartbeat`）保活
- 自动断线清理：`CancelledError` 或异常时自动移除队列
- 浏览器端使用原生 `EventSource`（`base.html:120-143`），无需额外库

**典型推送流程（定时策略）：**

```
Scheduler.start_loop → run_strategy → 结果写入 CSV
    ↓
alert_engine.check (对比上次结果)
    ↓
sse_manager.broadcast({"type": "strategy_result", ...})
    ↓
浏览器 EventSource.onmessage 接收 → 更新 UI（无刷新）
```

### 数据流全景

```
TDX (pytdx) → minute_collector → DuckDB minute_bars
                                   ↓ resample
                              DuckDB minute_bars_15m
                                   ↓
            ┌──────────────────────┼────────────────────┐
            ▼                      ▼                    ▼
     ETFMonitor (monitor.py)   market_analyzer    strategy_runner
            │                      │                    │
            ▼                      ▼                    ▼
     alert_recorder          MarketState JSON     CSV results
     (alerts.duckdb)          ───────────────>    ──────────>
            │                    /api/market/status   │
            ▼                                         ▼
     Dashboard SQLite                         alert_engine.check()
     (alerts_dashboard)                              │
            │                                    SSE broadcast
            ▼                                         ▼
     Browser (HTMX swaps)                    Browser (EventSource)
```

### 监控指标

Dashboard 监控系统提供以下维度的指标数据：

#### 市场状态 (`/api/market/status`)

| 指标 | 来源 | 说明 |
|------|------|------|
| `market_type` | minute_bars | 牛市 / 熊市 / 震荡市 / 未知 |
| `index_return` | 沪深300 1min | 最近60分钟收益率 |
| `etf_pool_return` | ETF池平均 | 池内标的60分钟平均收益率 |
| `volatility` | 240周期 std × √240 | 年化波动率 |
| `trend_strength` | index + pool 均值 | 趋势强度 |
| `ma_short_vs_long` | MA60 vs MA240 | 均线排列方向 (bullish / bearish) |

#### 策略执行指标 (`/pages/strategy`)

每次策略运行生成每只标的的评分明细：

| 指标 | 说明 |
|------|------|
| `r60 / r20 / r10 / r5` | 60/20/10/5 日收益率 |
| `total_score` | 加权动量总分 |
| `target_weight` | 目标权重 |

#### ETFMonitor 监控信号 (`/api/alerts/monitor-signals`)

从 `data/alerts/alerts.duckdb` 读取的实时信号：

| 字段 | 说明 |
|------|------|
| `code` | ETF 6位代码 |
| `strategy_name` | 策略名称 |
| `signal_type` | buy / sell |
| `direction` | long / short |
| `score` | 综合评分 (0~1.0) |
| `entry_price / stop_loss / take_profit` | 风控价格（基于 ATR 计算） |
| `reason` | 信号触发原因 |
| `market_state` | 信号发生时的市场环境 |
| `ma10 / ma20 / ma30` | 15分钟 K 线的移动均线 |

### 告警规则

系统内置两级告警机制：

#### 1. Dashboard 告警引擎 (`services/alert_engine.py`)

每次策略执行后自动检查，告警记录写入 SQLite `alerts_dashboard` 表：

| 规则 | 类型 | 严重度 | 逻辑 |
|------|------|--------|------|
| **评分进入前三** | `top3_entry` | `warning` | 对比当次与上次排名，新进入前三的标的触发告警 |
| **动量得分突变** | `momentum_shock` | `danger` | 评分变动超过阈值（默认 15%），可配置 `ALERT_MOMENTUM_SHOCK_THRESHOLD` |
| **持仓偏离目标** | `position_deviation` | `info` | MVP 阶段预留，当前始终返回空 |

告警生命周期：`active` → `acknowledged` → `resolved`，在告警页面通过按钮切换。

#### 2. ETFMonitor 告警 (`alert_recorder.py`)

分钟级监控循环中，每次信号通过 `alert_recorder` 写入独立 DuckDB 数据库 (`data/alerts/alerts.duckdb`)，通过 `/api/alerts/monitor-signals` 在 Dashboard 中查询展示。

#### 3. SSE 实时推送

| 事件类型 | 触发场景 |
|---------|----------|
| `connected` | 客户端建立 SSE 连接 |
| `strategy_result` | 定时调度策略执行完成 |
| `strategy_error` | 策略执行失败 |
| `alert` | 告警引擎检测到新告警 |
| `portfolio_update` | 持仓价格同步完成 |

### 数据存储

| 数据库 | 位置 | 用途 |
|--------|------|------|
| SQLite | `data/dashboard.db` | 账户、持仓、告警规则、Dashboard 告警、调度配置 |
| DuckDB | `data/minute/minute_data.duckdb` | 1分钟 K 线数据 |
| DuckDB | `data/minute/minute_data_15m.duckdb` | 15分钟重采样 K 线 |
| DuckDB | `data/alerts/alerts.duckdb` | ETFMonitor 信号告警 |
| CSV | `data/results/{date}/*.csv` | 每日策略执行结果 |

### 配置参考 (`dashboard/config.py`)

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DASHBOARD_HOST` | `127.0.0.1` | HTTP 监听地址 |
| `DASHBOARD_PORT` | `8522` | HTTP 监听端口 |
| `ALERT_MOMENTUM_SHOCK_THRESHOLD` | `0.15` | 动量突变阈值（评分变动比例） |
| `SSE_HEARTBEAT_INTERVAL` | `30s` | SSE 心跳间隔 |

环境变量 `DASHBOARD_HOST`、`DASHBOARD_PORT` 可覆盖以上默认值。

## 许可证

MIT License
