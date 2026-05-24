
# 量化ETF看板设计文档

> 基于 FastAPI + HTMX + Alpine.js + Bootstrap 5
> 版本: v1.0

---

## 目录

- [1. 设计概要](#1-设计概要)
- [2. 技术选型](#2-技术选型)
- [3. 架构设计](#3-架构设计)
- [4. 页面布局与导航](#4-页面布局与导航)
- [5. 数据模型](#5-数据模型)
- [6. 核心模块设计](#6-核心模块设计)
- [7. 数据流](#7-数据流)
- [8. 与现有模块的集成](#8-与现有模块的集成)
- [9. 部署方案](#9-部署方案)
- [10. MVP范围与迭代路线](#10-mvp范围与迭代路线)

---

## 1. 设计概要

### 1.1 目标

为本量化ETF项目构建一个基于Web的交互式看板，提供：

1. **持仓管理**：多账户ETF持仓的增删改查
2. **策略执行与结果展示**：一键执行现有策略（etf/short/mid），图表化展示结果
3. **定时自动运行与实时监控**：预设策略自动执行，实时更新页面
4. **智能告警**：页面内可视告警，展示触发条件和历史记录

### 1.2 已确定的决策

| 项目 | 决策 | 说明 |
|------|------|------|
| 部署模式 | 本地 + 服务器兼顾 | 本地开发运行，同时方便部署到服务器 |
| 多账户 | 账户 + 策略组合两级 | 账户下分策略组合独立管理持仓 |
| 告警策略 | MVP仅页面内告警 | 外部通知放第二阶段 |
| CSS框架 | Bootstrap 5 (仅CSS) | CDN引入，不引入Bootstrap JS |
| 前端架构 | FastAPI + HTMX + Alpine.js | 纯Python后端，前端零JS框架 |

---

## 2. 技术选型

### 2.1 前端

| 组件 | 选型 | 引入方式 | 用途 |
|------|------|----------|------|
| **CSS框架** | Bootstrap 5.3 | CDN (`bootstrap.min.css`) | 布局、表格、表单、告警组件 |
| **交互框架** | HTMX 2.x | CDN | 所有AJAX交互，页面片段替换 |
| **前端状态** | Alpine.js 3.x | CDN | 前端响应式状态（表单联动、告警弹窗显隐） |
| **图表** | Chart.js 4.x | CDN | 策略结果可视化（柱状图、雷达图、折线图） |
| **图标** | Bootstrap Icons | CDN | 导航栏、按钮、状态标识 |

### 2.2 后端

| 组件 | 选型 | 说明 |
|------|------|------|
| **Web框架** | FastAPI | 异步路由、自动OpenAPI文档 |
| **模板引擎** | Jinja2 | HTML模板渲染（HTMX返回HTML片段） |
| **ASGI服务器** | Uvicorn | 生产运行 |
| **进程管理** | gunicorn + uvicorn workers | 服务部署（可选） |

### 2.3 数据层

| 数据 | 存储 | 说明 |
|------|------|------|
| **看板业务数据** | SQLite (`data/dashboard.db`) | 账户、持仓、告警规则、调度配置 |
| **分钟K线数据** | DuckDB `data/minute/minute_data.duckdb` | 已有，只读 |
| **选股结果** | DuckDB `data/results/results.duckdb` | 结构化存储，支持按日期/策略/代码SQL查询。CSV仅作为通达信等外部工具的导出格式 |
| **告警记录** | DuckDB `data/alerts/alerts.duckdb` | 已有，与选股结果一致的DuckDB存储模式 |

---

## 3. 架构设计

### 3.1 整体架构图

```
┌────────────────────────────────────────────────┐
│                  Browser                        │
│  HTMX  +  Alpine.js  +  Chart.js  +  Bootstrap  │
└────────────────────┬───────────────────────────┘
                     │  HTTP / SSE
┌────────────────────▼───────────────────────────┐
│             FastAPI (Uvicorn)                    │
│                                                  │
│  ┌─────────┐  ┌──────────┐  ┌───────────────┐   │
│  │ 页面路由  │  │ JSON API  │  │ SSE事件推送    │   │
│  │ /pages   │  │ /api/*   │  │ /events       │   │
│  └────┬─────┘  └────┬─────┘  └──────┬────────┘   │
│       └─────────────┼──────────────┘             │
│                     ▼                            │
│  ┌───────────────────────────────────────────┐   │
│  │             Services 层                     │   │
│  │  strategy_runner │ scheduler │ alert_engine │  │
│  │  sse_manager     │ portfolio_manager        │  │
│  └────────────────────┬──────────────────────┘   │
│                       │                          │
└───────────────────────┼──────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────┐
│            现有模块集成层                          │
│                                                   │
│  TaskRegistry  →  现有策略执行流程                  │
│  AlertRecorder  →  现有告警数据库                  │
│  ETFMonitor     →  实时监控逻辑                    │
│  StrategyEngine →  策略计算引擎                    │
│  ResultComparator → 结果对比                       │
│  data_source.py  →  数据源获取                     │
└───────────────────────┬──────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────┐
│                数据层                              │
│                                                   │
│  SQLite (看板业务)  │  DuckDB (分钟K线 + 选股结果 + 告警)  │
└──────────────────────────────────────────────────┘
```

### 3.2 模块职责

#### dashboard/ 新增模块

| 文件 | 职责 |
|------|------|
| `app.py` | FastAPI应用入口、启动配置、路由挂载、模板注册 |
| `config.py` | 看板专属配置（监听地址、端口、数据路径、告警阈值） |
| `db.py` | SQLite数据库管理（建表、迁移、连接） |
| `models.py` | Pydantic模型 + SQLite表映射 |
| `routes/pages.py` | 页面渲染路由（HTML片段） |
| `routes/portfolio.py` | 持仓管理API |
| `routes/strategy.py` | 策略执行API |
| `routes/alerts.py` | 告警管理API |
| `routes/market.py` | 市场状态API |
| `services/strategy_runner.py` | 异步策略执行（后台任务线程） |
| `services/scheduler.py` | 定时任务调度管理 |
| `services/alert_engine.py` | 告警条件检测引擎 |
| `services/sse_manager.py` | SSE连接管理与事件广播 |

---

## 4. 页面布局与导航

### 4.1 全局布局

```
┌─────────────────────────────────────────────────────┐
│  🔍 [Logo]  quant-ETF看板      [账户: 我的主账户 ▼] │ ← 顶部导航栏
├──────────┬──────────────────────────────────────────┤
│          │                                          │
│  📊 总览  │  ← 主要内容区 (content)                 │
│  📁 持仓  │    HTMX局部渲染，不整页刷新              │
│  📈 策略  │                                          │
│  👁 监控  │                                          │
│  🔔 告警  │                                          │
│          │                                          │
│  ⚙ 设置  │                                          │
├──────────┴──────────────────────────────────────────┤
│  © quant-ETF Dashboard | 最后更新: 2026-05-21 ...   │ ← 页脚
└─────────────────────────────────────────────────────┘
```

### 4.2 页面导航结构

| 菜单 | 子页面 | 核心内容 |
|------|--------|----------|
| 📊 总览 | `index.html` | 账户总资产概览、今日告警数、最新策略排名摘要、市场状态卡片 |
| 📁 持仓管理 | 账户列表 → 持仓编辑 | 左侧账户树切换，右侧持仓表格（增删改） |
| 📈 策略执行 | 策略选择 → 执行 → 结果 | 策略勾选框、执行按钮+进度、排名表格+图表 |
| 👁 实时监控 | 监控状态 → 定时配置 | 运行状态指示器、定时策略配置、最新信号流 |
| 🔔 告警中心 | 活跃告警 → 历史记录 → 规则配置 | 告警列表（可标记已处理）、告警规则CRUD |
| ⚙ 设置 | 系统配置 | ETF池管理、数据源配置、通知配置（占位） |

### 4.3 交互方式

所有页面间的导航和数据更新通过HTMX实现：
- **页面切换** → `hx-get="/pages/portfolio"` 替换主内容区
- **表单提交** → `hx-post="/api/portfolio/holdings"` + `hx-target="#table-area"`
- **表格编辑** → 行内 `hx-put` 或 Alpine表单弹窗
- **定时刷新** → `hx-trigger="every 30s"` 更新状态面板
- **告警推送** → SSE `/events` 实时推送到页面

---

## 5. 数据模型

### 5.1 看板数据库（SQLite）

#### 账户表 (accounts)

```sql
CREATE TABLE accounts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,             -- 账户名称
    broker      TEXT DEFAULT '',           -- 券商名称
    cash        REAL DEFAULT 0.0,         -- 可用资金
    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);
```

#### 持仓表 (holdings)

```sql
CREATE TABLE holdings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER NOT NULL,          -- 关联账户
    code        TEXT NOT NULL,             -- ETF代码
    name        TEXT DEFAULT '',           -- ETF名称（自动填充）
    quantity    INTEGER NOT NULL,          -- 持有数量
    cost_price  REAL NOT NULL,            -- 成本价
    current_price REAL DEFAULT NULL,       -- 当前价（可能从DuckDB自动更新）
    strategy    TEXT DEFAULT '',           -- 所属策略组合
    notes       TEXT DEFAULT '',
    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW(),
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);
```

#### 告警规则表 (alert_rules)

```sql
CREATE TABLE alert_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,             -- 规则名称
    rule_type   TEXT NOT NULL,             -- 类型: top3_entry / momentum_shock / position_deviation
    config      TEXT NOT NULL,             -- JSON配置 {threshold, ...}
    enabled     BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMP DEFAULT NOW()
);
```

#### 告警记录表 (alerts_dashboard)

```sql
CREATE TABLE alerts_dashboard (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id     INTEGER,                   -- 关联规则
    alert_type  TEXT NOT NULL,             -- 类型
    severity    TEXT NOT NULL,             -- info / warning / danger
    title       TEXT NOT NULL,             -- 告警标题
    message     TEXT,                      -- 详情描述
    data        TEXT,                      -- JSON附带数据
    status      TEXT DEFAULT 'active',     -- active / acknowledged / resolved
    created_at  TIMESTAMP DEFAULT NOW(),
    resolved_at TIMESTAMP
);
```

#### 调度配置表 (schedules)

```sql
CREATE TABLE schedules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy    TEXT NOT NULL,             -- 策略名称
    interval    INTEGER NOT NULL,          -- 间隔秒数
    enabled     BOOLEAN DEFAULT TRUE,
    last_run_at TIMESTAMP,
    created_at  TIMESTAMP DEFAULT NOW()
);
```

### 5.2 已有数据（只读）

看板从以下已有数据源读取内容（不修改）：

| 数据源 | 路径 | 用途 |
|--------|------|------|
| 选股结果 | `data/results/results.duckdb`（表: `strategy_results`） | 按日期/策略分类存储，支持SQL过滤查询。CSV仅作为通达信导出格式 |
| 告警记录 | `data/alerts/alerts.duckdb`（表: `alerts`） | 由 `ETFMonitor` 产生的监控信号，看板仅读取展示 |
| 分钟K线 | `data/minute/minute_data.duckdb` | 实时数据获取 |
| 名称映射 | `data/meta/stock_code_name.json` | ETF/股票名称显示 |

> **说明**：看板新增的 `alerts_dashboard` 表（SQLite）与已有 `alerts.duckdb` 分工不同 —— 前者记录看板告警引擎触发的业务告警（如动量突变、评分前三变化），后者记录 `ETFMonitor` 监控系统产生的交易信号。看板页面可以整合两个来源展示。

#### DuckDB Schema

**选股结果表**（`data/results/results.duckdb`）：
> 策略执行时由 `tasks.py` 的 `save_results_to_csv()` 方法写入 CSV，看板通过 DuckDB 读取展示。**实际表结构由 CSV 字段决定**——各策略导出字段不同：

| 策略 | 关键字段 | 说明 |
|------|----------|------|
| `etf` | code, name, score(→target_weight), r60, r20, r10, r5 | ETF动量评分 |
| `short` | code, name, score, r5, r10, r20, volume_ratio_1d_20d, trend_ok | 短线强势股 |
| `mid` | code, name, score, drawdown_from_120d_high, bounce_from_20d_low, r5, r10, r20, volume_ratio_1d_20d | 中期反弹股 |

> ⚠️ **注意**：当前 `results.duckdb` 可能不存在实际表定义（策略直接写 CSV），看板实现时应按需建表，或直接查询 CSV 文件所在目录。

**告警记录表**（`data/alerts/alerts.duckdb`）：

```sql
CREATE TABLE IF NOT EXISTS alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    time            TIMESTAMP,                    -- 告警时间
    code            VARCHAR,                      -- ETF/股票代码
    strategy_name   VARCHAR,                      -- 策略名称
    signal_type     VARCHAR,                      -- 信号类型
    direction       VARCHAR,                      -- buy / sell
    score           DOUBLE,                       -- 评分
    entry_price     DOUBLE,                       -- 建议入场价
    stop_loss       DOUBLE,                       -- 止损价
    take_profit     DOUBLE,                       -- 止盈价
    reason          TEXT,                         -- 原因描述
    market_state    VARCHAR,                      -- 市场状态
    market_return   DOUBLE,                       -- ETF池收益率
    market_volatility DOUBLE,                     -- 市场波动率
    ma10            DOUBLE,
    ma20            DOUBLE,
    ma30            DOUBLE,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_time ON alerts(time);
CREATE INDEX IF NOT EXISTS idx_code ON alerts(code);
```

---

## 6. 核心模块设计

### 6.1 持仓管理（routes/portfolio.py）

**API端点：**

| 方法 | 路径 | 用途 | HTMX交互 |
|------|------|------|----------|
| GET | `/pages/portfolio` | 渲染持仓管理页面 | 页面切换 |
| GET | `/pages/portfolio/accounts` | 账户列表（侧边栏） | `hx-target="#account-list"` |
| POST | `/api/portfolio/accounts` | 新增账户 | 刷新侧边栏 |
| PUT | `/api/portfolio/accounts/{id}` | 编辑账户 | 局部刷新 |
| DELETE | `/api/portfolio/accounts/{id}` | 删除账户 | 局部刷新 |
| GET | `/pages/portfolio/account/{id}/holdings` | 某账户持仓表格 | `hx-target="#holdings-table"` |
| POST | `/api/portfolio/holdings` | 新增/更新持仓 | 刷新表格行 |
| PUT | `/api/portfolio/holdings/{id}` | 编辑持仓（行内） | 刷新表格行 |
| DELETE | `/api/portfolio/holdings/{id}` | 删除持仓 | 移除表格行 |

**界面流程：**

1. 左侧账户列表，点击切换 `hx-get`
2. 右侧持仓表格 `table.table-striped`
3. 点击"新增"弹Alpine模态框，`hx-post`提交后 `hx-target` 刷新表格
4. 表格行内 `hx-put` 保存行编辑
5. 删除确认通过Alpine的 `x-confirm`

**核心逻辑：**

```python
# routes/portfolio.py (示意)
@router.get("/pages/portfolio/account/{id}/holdings")
async def get_account_holdings(request: Request, id: int):
    holdings = db.query("SELECT * FROM holdings WHERE account_id = ?", [id])
    names = load_etf_name_map()
    # 尝试从DuckDB获取最新行情填充current_price
    return templates.TemplateResponse(
        "portfolio/_holdings_table.html",
        {"request": request, "holdings": holdings, "names": names}
    )
```

### 6.2 策略执行（routes/strategy.py）

**API端点：**

| 方法 | 路径 | 用途 | HTMX交互 |
|------|------|------|----------|
| GET | `/pages/strategy` | 策略执行页面 | 页面切换 |
| POST | `/api/strategy/run` | 执行选定的策略 | `hx-post` + 轮询进度 |
| GET | `/api/strategy/status/{run_id}` | 查询执行进度 | `hx-trigger="every 2s"` |
| GET | `/pages/strategy/results/{run_id}` | 渲染结果表格+图表 | `hx-target="#result-area"` |

**执行流程：**

```
用户勾选策略 → 点击执行
    ↓
POST /api/strategy/run → 创建run_id
    ↓
后台 asyncio.to_thread 异步执行 TaskRegistry.get_task().run()
    ↓
前端 hx-trigger="every 2s" 轮询 /api/strategy/status/{run_id}
    ↓
完成后 hx-get="/pages/strategy/results/{run_id}" 替换结果区域
```

**异步执行核心逻辑：**

```python
# services/strategy_runner.py (示意)
import asyncio
from concurrent.futures import ThreadPoolExecutor

executor = ThreadPoolExecutor(max_workers=2)
running_tasks: dict[str, dict] = {}

async def run_strategy(strategy_name: str, run_id: str):
    def _run():
        task = TaskRegistry.get_task(strategy_name)
        task.run()
        return task  # 获取结果
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(executor, _run)
    running_tasks[run_id] = {"status": "complete", "result": result}
```

### 6.3 实时监控与定时调度（services/scheduler.py）

**定时调度核心方案：**

使用 `asyncio` 内置的 `asyncio.create_task` 实现轻量调度器，不引入APScheduler等外部依赖（MVP阶段）。

```python
# services/scheduler.py (示意)
import asyncio
from datetime import datetime
from loguru import logger

class Scheduler:
    def __init__(self):
        self._tasks: dict[int, asyncio.Task] = {}
    
    async def start_loop(self, schedule_id: int, strategy: str, interval: int):
        """启动定时循环"""
        while True:
            logger.info(f"Scheduled run: {strategy} (every {interval}s)")
            run_id = f"sched_{schedule_id}_{datetime.now().timestamp()}"
            await strategy_runner.run_strategy(strategy, run_id)
            # SSE广播新结果
            await sse_manager.broadcast({
                "type": "strategy_result",
                "schedule_id": schedule_id,
                "strategy": strategy,
                "run_id": run_id
            })
            await asyncio.sleep(interval)
```

**SSE推送（实时更新）：**

```python
# services/sse_manager.py (示意)
import asyncio
from typing import AsyncGenerator

class SSEManager:
    def __init__(self):
        self._queues: set[asyncio.Queue] = set()
    
    async def subscribe(self) -> AsyncGenerator[str, None]:
        queue: asyncio.Queue = asyncio.Queue()
        self._queues.add(queue)
        try:
            while True:
                data = await queue.get()
                yield f"data: {json.dumps(data)}\n\n"
        finally:
            self._queues.discard(queue)
    
    async def broadcast(self, data: dict):
        for queue in self._queues:
            await queue.put(data)
```

**监控页面设计：**

```
┌──────────────────────────────────────────┐
│  👁 实时监控                              │
├──────────────────────────────────────────┤
│  ┌────────────┐ ┌────────────┐ ┌───────┐ │
│  │ 运行状态: 🟢 │ 最近执行: 3s │ 策略: 2 │ │
│  │ 活跃(2个)   │            │ 启用  │ │
│  └────────────┘ └────────────┘ └───────┘ │
│                                          │
│  定时策略配置表                            │
│  ┌──────────┬────────┬──────┬──────────┐ │
│  │ 策略      │ 间隔    │ 状态  │ 操作     │ │
│  ├──────────┼────────┼──────┼──────────┤ │
│  │ etf      │ 300s   │ 🟢   │ 停止/编辑 │ │
│  │ short    │ 600s   │ ⚪   │ 启动/编辑 │ │
│  └──────────┴────────┴──────┴──────────┘ │
│                                          │
│  最新信号流（SSE实时推送）                 │
│  [15:30:01] etf 执行完成 → 15条结果      │
│  [15:25:01] short 执行完成 → 5条结果     │
│  ...                                     │
└──────────────────────────────────────────┘
```
┌──────────────────────────────────────────┐
│  👁 实时监控                              │
├──────────────────────────────────────────┤
│  ┌────────────┐ ┌────────────┐ ┌───────┐ │
│  │ 运行状态: 🟢 │ 最近执行: 3s │ 策略: 2 │ │
│  │ 活跃(2个)   │ 前    │ 启用  │ │
│  └────────────┘ └────────────┘ └───────┘ │
│                                          │
│  定时策略配置表                            │
│  ┌──────────┬────────┬──────┬──────────┐ │
│  │ 策略      │ 间隔    │ 状态  │ 操作     │ │
│  ├──────────┼────────┼──────┼──────────┤ │
│  │ etf      │ 300s   │ 🟢   │ 停止/编辑 │ │
│  │ short    │ 600s   │ ⚪   │ 启动/编辑 │ │
│  └──────────┴────────┴──────┴──────────┘ │
│                                          │
│  最新信号流（SSE实时推送）                 │
│  [15:30:01] etf 执行完成 → 15条结果      │
│  [15:25:01] short 执行完成 → 5条结果     │
│  ...                                     │
└──────────────────────────────────────────┘
```

### 6.4 告警引擎（services/alert_engine.py）

**规则预设（MVP阶段硬编码，后续可配置化）：**

```python
# services/alert_engine.py (示意)
from dataclasses import dataclass

@dataclass
class AlertRule:
    name: str
    check_fn: callable  # 检查函数
    severity: str       # info/warning/danger

class AlertEngine:
    def __init__(self):
        self.rules: list[AlertRule] = [
            AlertRule("评分进入前三", self._check_top3_entry, "warning"),
            AlertRule("动量得分突变", self._check_momentum_shock, "danger"),
            AlertRule("持仓偏离目标", self._check_position_deviation, "info"),
        ]
    
    def _check_top3_entry(self, latest_result, prev_result) -> Optional[dict]:
        """检查是否有标的首次进入前3"""
        curr_top3 = set(item.code for item in latest_result[:3])
        prev_top3 = set(item.code for item in prev_result[:3]) if prev_result else set()
        new_entries = curr_top3 - prev_top3
        if new_entries:
            entries_str = ", ".join(sorted(new_entries))
            return {"title": "新标的进入前三", "message": f"{entries_str} 首次进入评分前3"}
        return None
    
    def _check_momentum_shock(self, latest_result, prev_result) -> Optional[dict]:
        """检查标的得分是否发生剧烈变化"""
        if not prev_result:
            return None
        prev_map = {item.code: item.score for item in prev_result}
        for item in latest_result:
            if item.code in prev_map:
                change = abs(item.score - prev_map[item.code])
                if change > 0.15:  # 波动超过15%
                    return {"title": f"{item.code} 动量突变", "message": f"得分变化 {change:.2%}"}
        return None
    
    def check(self, latest_result, prev_result, portfolio_data) -> list[dict]:
        """执行所有规则检查"""
        alerts = []
        for rule in self.rules:
            result = rule.check_fn(latest_result, prev_result)
            if result:
                alerts.append({"type": rule.name, "severity": rule.severity, **result})
        return alerts
```

**页面展示：**

告警按 severity 着色：
- 🟢 **info** → Bootstrap `badge bg-info`
- 🟡 **warning** → Bootstrap `badge bg-warning text-dark`
- 🔴 **danger** → Bootstrap `badge bg-danger`

---

## 7. 数据流

### 7.1 手动策略执行

```
用户 → 勾选策略 → 点击执行
                  ↓
             POST /api/strategy/run
                  ↓
             创建 run_id, 返回 202 Accepted
                  ↓
         ┌────────┴────────┐
         │ 后台异步执行     │
         │ (ThreadPool)    │
         │                 │
         │ 1. 加载数据     │
         │ 2. 执行策略     │
         │ 3. 导出CSV      │
         │ 4. 存入结果      │
         └────────┬────────┘
                  ↓
         状态更新为 complete
                  ↓
         SSE 广播 "strategy_complete"
                  ↓
         ┌────────┴────────┐
         │ 前端轮询停止     │
         │ hx-get 加载结果  │
         │ → 表格 + 图表    │
         └─────────────────┘
```

### 7.2 定时自动执行

```
Scheduler 启动定时循环 (asyncio)
         ↓
   每隔 interval 秒:
         ↓
   run_strategy() 后台执行
         ↓
   SSE broadcast("strategy_result", data)
         ↓
   前端 SSE /events 收到消息
         ↓
   Alpine.js 更新监控状态
   HTMX 刷新结果片段
   告警引擎检查规则 → 触发告警 → 实时推送
```

### 7.3 持仓价值同步

```
定时任务（或手动触发）:
   读取 holdings 中的 code 列表
         ↓
   从 DuckDB minute_data 获取最新收盘价
         ↓
   UPDATE holdings SET current_price = ? WHERE id = ?
         ↓
   计算账户总市值 = ∑(quantity * current_price) + cash
         ↓
   SSE 推送 "portfolio_update"
         ↓
   前端更新总览卡片
```

---

## 8. 与现有模块的集成

### 8.1 集成策略

| 现有模块 | 集成方式 | 修改量 |
|----------|----------|--------|
| `tasks.py` | 直接调用 `TaskRegistry.get_task().run()` | **不修改** |
| `strategy.py` | 复用 `StrategyEngine` 类 | **不修改** |
| `monitor.py` | 借鉴 `ETFMonitor.run_cycle()` 逻辑到定时任务 | 最小（提取核心逻辑） |
| `alert_recorder.py` | `AlertRecorder` 的数据库作为告警历史数据源（只读） | **不修改** |
| `comparison.py` | `ResultComparator` 读取CSV结果做对比展示 | **不修改** |
| `data_source.py` | `ETFDataSource` 用于获取名称映射 | **不修改** |
| `conf.py` | `ETF_POOL` 等配置可被看板读取展示 | **不修改** |

### 8.2 策略执行集成（核心）

看板不重复实现策略逻辑，而是直接调用已有的 `TaskRegistry`：

```python
# services/strategy_runner.py
from quant_etf.tasks import TaskRegistry, BaseTask
from quant_etf.data_source import ETFDataSource

class StrategyRunner:
    async def run(self, strategy_name: str) -> dict:
        # 用已有TaskRegistry获取任务实例
        task = TaskRegistry.get_task(strategy_name)
        if not task:
            return {"error": f"Unknown strategy: {strategy_name}"}

        # 在后台线程中同步执行
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, task.run)

        # 读取刚刚生成的CSV结果
        today = datetime.now().strftime("%Y-%m-%d")
        csv_path = PROJECT_ROOT / "data" / "results" / today / f"{strategy_name}.csv"
        if csv_path.exists():
            df = pd.read_csv(csv_path)
            return {"status": "ok", "count": len(df), "data": df.to_dict("records")}
        return {"status": "ok", "count": 0}
```

> ⚠️ **TaskRegistry 初始化开销**：`get_task()` 每次返回新实例，`run()` 内部会调用 `initialize()` 重新初始化 `ETFDataSource`、创建 `StrategyEngine` 和 `RiskManager`。对于定时调度高频执行场景，这是可接受的开销；如需极致优化，可考虑在 `StrategyRunner` 中缓存任务实例。

### 8.3 实时监控集成

看板的定时功能复用 `monitor.py` 中的核心逻辑（`update_15min_data`、`signal_generator.generate_signals`），但通过定时调度器驱动，而非 `ETFMonitor` 的 `while True` 循环：

```python
# services/scheduler.py
from quant_etf.minute_data_manager import get_pool_15min_bars, update_15min_data
from quant_etf.signal_generator import SignalGenerator
from quant_etf.market_analyzer import MarketAnalyzer

async def run_monitor_cycle(etf_pool):
    # 更新15分钟数据
    for code in etf_pool:
        update_15min_data(code)

    # 获取数据
    pool_data = get_pool_15min_bars(etf_pool)

    # 分析市场
    analyzer = MarketAnalyzer()
    market_state = analyzer.analyze_market(etf_pool)

    # 生成信号
    generator = SignalGenerator()
    signals = generator.generate_signals(pool_data, market_state)

    return {"market_state": market_state, "signals": signals}
```

> ✅ **已验证存在的模块**：`signal_generator.py`、`market_analyzer.py`、`minute_data_manager.py` 均位于 `src/quant_etf/` 目录，接口可用。建议实现前确认 `SignalGenerator.generate_signals()` 和 `MarketAnalyzer.analyze_market()` 的签名。

---

## 9. 部署方案

### 9.1 本地运行

```bash
# 从项目根目录
uv run uvicorn src.quant_etf.dashboard.app:app \
    --host 127.0.0.1 \
    --port 8080 \
    --reload
# 访问 http://localhost:8080
```

### 9.2 服务部署

**通过环境变量控制监听地址：**

```bash
# 部署到服务器（对外访问）
export DASHBOARD_HOST="0.0.0.0"
export DASHBOARD_PORT="8080"
uv run uvicorn src.quant_etf.dashboard.app:app \
    --host $DASHBOARD_HOST \
    --port $DASHBOARD_PORT
```

**systemd 服务示例：**

```ini
[Unit]
Description=quant-etf Dashboard
After=network.target

[Service]
Type=simple
User=quant
WorkingDirectory=/opt/quant-etf
Environment="DASHBOARD_HOST=0.0.0.0"
Environment="DASHBOARD_PORT=8080"
ExecStart=/opt/quant-etf/.venv/bin/uvicorn src.quant_etf.dashboard.app:app --host $DASHBOARD_HOST --port $DASHBOARD_PORT
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### 9.3 Pyproject配置

在 `pyproject.toml` 添加看板启动脚本：

```toml
[project.scripts]
quant-dashboard = "quant_etf.dashboard.app:main"

[project.optional-dependencies]
dashboard = [
    "fastapi>=0.110.0",
    "uvicorn[standard]>=0.27.0",
    "jinja2>=3.1.0",
    "python-multipart>=0.0.9",
    "httpx>=0.27.0",
]
```

---

## 10. MVP范围与迭代路线

### 第一阶段：MVP（2-3周）

| 功能 | 优先级 | 预计工时 |
|------|--------|----------|
| 项目结构搭建 + FastAPI基础框架 | P0 | 1天 |
| SQLite数据库设计与初始化 | P0 | 1天 |
| 持仓管理CRUD（账户+持仓表格） | P0 | 2天 |
| 策略执行（触发现有TaskRegistry） | P0 | 2天 |
| 结果展示（排名表格 + Chart.js柱状图） | P0 | 2天 |
| 基础页面布局（侧边栏导航 + HTMX切换） | P0 | 1天 |
| 告警中心页面（规则预设+告警列表） | P1 | 1天 |
| 定时策略调度（基础版asyncio定时器） | P1 | 2天 |
| SSE实时状态推送 | P1 | 1天 |
| 监控页面（运行状态+信号流） | P1 | 1天 |
| 部署文档 + systemd示例 | P2 | 0.5天 |

### 第二阶段：增强（3-4周）

| 功能 | 说明 |
|------|------|
| 外部告警通知 | 企业微信/钉钉Webhook + 邮件 |
| 告警规则可配置 | 页面内CRUD告警规则 |
| 策略参数临时调整 | 页面内修改权重、TOP_N等 |
| 历史结果趋势图 | 多日对比折线图 |
| ETF池管理 | 页面内编辑ETF池 |
| 账户持仓自动估值 | 定时同步DuckDB行情 |

### 第三阶段：扩展（5-8周）

| 功能 | 说明 |
|------|------|
| 回测功能集成 | 对接回测引擎，看板展示回测报告 |
| 权限管理 | 简单用户登录、多用户隔离 |
| Docker部署 | 提供Docker Compose一键部署 |
| 移动端适配 | 响应式优化 |
| 数据导出 | Excel/PDF报告导出 |

### 6.5 错误处理与降级设计

#### 设计原则

**数据异常直接报错，不容忍。** 任何数据源异常（缺少数据、数据为空、数据格式错误）都立即终止当前操作并返回错误信息，绝不以错误数据继续执行。

#### 策略执行异常处理

```python
# services/strategy_runner.py
def _execute():
    try:
        task = TaskRegistry.get_task(strategy_name)
        if not task:
            raise ValueError(f"Unknown strategy: {strategy_name}")

        task.initialize()          # 数据源初始化失败 → 抛出异常
        task.run()                  # 策略执行失败 → 抛出异常
        # ... 读取结果 ...
        _running_tasks[run_id]["status"] = "complete"

    except Exception as e:
        logger.error(f"Strategy {strategy_name} failed: {e}")
        _running_tasks[run_id]["status"] = "error"
        _running_tasks[run_id]["error"] = str(e)
        _running_tasks[run_id]["progress"] = -1
        # SSE 广播 error 事件，前端展示错误提示
```

**run_id 状态机：**

| 状态 | 含义 | 前端处理 |
|------|------|----------|
| `running` | 执行中 | 显示进度条，轮询 status |
| `complete` | 成功完成 | 渲染结果表格+图表 |
| `error` | 执行失败 | 显示错误信息（`error` 字段内容） |

#### 数据源异常处理

| 场景 | 处理方式 |
|------|----------|
| DuckDB 连接失败 | 抛出 `ConnectionError`，页面显示"数据源不可用" |
| 行情数据为空 | 策略执行时检测到 `empty DataFrame` → 抛出 `ValueError` |
| CSV 结果文件不存在 | 策略执行完成但无输出 → `count: 0`，前端提示"本次无结果" |
| ETF/股票名称映射缺失 | 用代码本身作为显示名，不影响流程 |

#### 定时任务异常

定时调度任务执行异常时：
1. 记录错误日志（`logger.error`）
2. **不重试**（避免死循环）
3. SSE 广播异常事件，前端监控页面显示失败状态
4. 下次调度周期正常继续执行

---

## 关键设计约束

1. **不修改现有模块**：看板通过调用现有API/类的方式集成，不改动`tasks.py`、`strategy.py`等
2. **零JS框架**：不用React/Vue/Svelte，HTMX处理所有AJAX交互
3. **CDN优先**：前端库全部CDN引入，不引入npm
4. **单进程部署 + SSE限制**：MVP阶段使用单 worker，`SSEManager` 在进程内广播。多 worker 部署时需外部消息队列（如 Redis Pub/Sub）
5. **异步优先**：策略执行等耗时操作通过后台线程+SSE通知结果
6. **数据异常直接报错**：数据源异常不容忍、不降级，立即终止并返回错误

---

## 附录：HTMX使用示例

```html
<!-- 持仓表格行：每次更新局部刷新 -->
<tr hx-get="/pages/portfolio/account/1/holdings" 
    hx-trigger="every 60s" 
    hx-swap="outerHTML">
    <td>510050</td>
    <td>华夏上证50ETF</td>
    <td>1000</td>
    <td>2.850</td>
    <td>2.890</td>
    <td>+1.40%</td>
</tr>

<!-- 策略执行按钮：显示加载状态 -->
<button hx-post="/api/strategy/run" 
        hx-target="#result-area"
        hx-indicator="#spinner">
    ▶ 执行选中策略
</button>
<div id="spinner" class="htmx-indicator">
    <span class="spinner-border spinner-border-sm"></span> 执行中...
</div>

<!-- 告警列表：SSE实时追加 -->
<div id="alert-list" hx-ext="sse" sse-connect="/events" sse-swap="message">
    <!-- SSE推送新告警HTML片段自动插入 -->
</div>
```

---

*文档版本: v1.0*
*更新时间: 2026-05-21*
*决策记录: [docs/user_improvements.md](file://E:\mw3\wspy\2025\quant-ETF\docs\user_improvements.md)*
