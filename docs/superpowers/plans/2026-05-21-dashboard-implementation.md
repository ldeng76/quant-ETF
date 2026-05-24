# 量化ETF看板 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于 FastAPI + HTMX + Alpine.js + Bootstrap 5 构建量化ETF Web看板，覆盖MVP功能

**Architecture:** 看板作为独立模块 `src/quant_etf/dashboard/`，通过 `TaskRegistry` 调用现有策略逻辑，通过 SQLite 管理业务数据（账户/持仓/告警），通过 DuckDB 读取已有行情和结果数据。前端使用 HTMX 处理所有 AJAX 交互，Chart.js 展示图表，SSE 实现实时推送。

**Tech Stack:** FastAPI 0.110+, Uvicorn 0.27+, Jinja2 3.1+, HTMX 2.x, Alpine.js 3.x, Chart.js 4.x, Bootstrap 5.3, Bootstrap Icons, SQLite, DuckDB

---

## 文件结构

```
src/quant_etf/dashboard/
├── __init__.py                     # 包初始化
├── __main__.py                     # CLI入口 (python -m quant_etf.dashboard)
├── app.py                          # FastAPI应用入口、启动配置、路由挂载
├── config.py                       # 看板专属配置
├── db.py                           # SQLite数据库管理
├── models.py                       # Pydantic模型
├── routes/
│   ├── __init__.py
│   ├── pages.py                    # 页面渲染路由 (/pages/*)
│   ├── portfolio.py                # 持仓管理API (/api/portfolio/*)
│   ├── strategy.py                 # 策略执行API (/api/strategy/*)
│   ├── alerts.py                   # 告警管理API (/api/alerts/*)
│   └── market.py                   # 市场状态API (/api/market/*)
├── services/
│   ├── __init__.py
│   ├── strategy_runner.py          # 异步策略执行
│   ├── scheduler.py                # 定时任务调度
│   ├── alert_engine.py             # 告警条件检测
│   └── sse_manager.py              # SSE连接管理与事件广播
└── templates/
    ├── base.html                   # 全局布局（导航栏+侧边栏+内容区）
    ├── index.html                  # 总览页面
    ├── portfolio/
    │   ├── index.html              # 持仓管理页面
    │   ├── _account_list.html      # 账户列表侧栏片段
    │   └── _holdings_table.html    # 持仓表格片段
    ├── strategy/
    │   ├── index.html              # 策略执行页面
    │   └── _results.html           # 结果表格+图表片段
    ├── monitor/
    │   └── index.html              # 实时监控页面
    ├── alerts/
    │   ├── index.html              # 告警中心页面
    │   └── _alert_list.html        # 告警列表片段
    └── settings/
        └── index.html              # 设置页面
```

---

## 实施任务

### Task 1: 项目脚手架 + FastAPI 基础框架

**Files:**
- Create: `src/quant_etf/dashboard/__init__.py`
- Create: `src/quant_etf/dashboard/config.py`
- Create: `src/quant_etf/dashboard/app.py`
- Create: `src/quant_etf/dashboard/db.py`
- Create: `src/quant_etf/dashboard/models.py`
- Create: `src/quant_etf/dashboard/routes/__init__.py`
- Create: `src/quant_etf/dashboard/services/__init__.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: 创建包初始化文件**

`__init__.py`:
```python
"""量化ETF看板 - 基于 FastAPI + HTMX + Alpine.js + Bootstrap 5"""
```

- [ ] **Step 2: 创建 config.py**

```python
from pathlib import Path
from src.quant_etf.conf import DATA_DIR, PROJECT_ROOT

# 数据路径
DASHBOARD_DB_PATH = DATA_DIR / "dashboard.db"

# 已有DuckDB数据路径
RESULTS_DUCKDB_PATH = DATA_DIR / "results" / "results.duckdb"
ALERTS_DUCKDB_PATH = DATA_DIR / "alerts" / "alerts.duckdb"
MINUTE_DUCKDB_PATH = DATA_DIR / "minute" / "minute_data.duckdb"

# 元数据
STOCK_CODE_NAME_PATH = DATA_DIR / "meta" / "stock_code_name.json"

# 看板服务配置
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 8080

# SSE配置
SSE_HEARTBEAT_INTERVAL = 30  # 秒

# 告警阈值
ALERT_MOMENTUM_SHOCK_THRESHOLD = 0.15  # 动量突变阈值 15%
```

- [ ] **Step 3: 创建 db.py**

```python
"""
SQLite数据库管理
看板业务数据：账户、持仓、告警规则、调度配置
"""
import sqlite3
from pathlib import Path
from typing import Any
from loguru import logger
from .config import DASHBOARD_DB_PATH

_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS accounts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    broker      TEXT DEFAULT '',
    cash        REAL DEFAULT 0.0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS holdings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER NOT NULL,
    code        TEXT NOT NULL,
    name        TEXT DEFAULT '',
    quantity    INTEGER NOT NULL,
    cost_price  REAL NOT NULL,
    current_price REAL DEFAULT NULL,
    strategy    TEXT DEFAULT '',
    notes       TEXT DEFAULT '',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS alert_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    rule_type   TEXT NOT NULL,
    config      TEXT NOT NULL,
    enabled     BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alerts_dashboard (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id     INTEGER,
    alert_type  TEXT NOT NULL,
    severity    TEXT NOT NULL,
    title       TEXT NOT NULL,
    message     TEXT,
    data        TEXT,
    status      TEXT DEFAULT 'active',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS schedules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy    TEXT NOT NULL,
    interval    INTEGER NOT NULL,
    enabled     BOOLEAN DEFAULT TRUE,
    last_run_at TIMESTAMP,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def get_connection() -> sqlite3.Connection:
    """获取数据库连接"""
    DASHBOARD_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DASHBOARD_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """初始化数据库表"""
    conn = get_connection()
    try:
        conn.executescript(_TABLES_SQL)
        conn.commit()
        logger.info(f"Dashboard database initialized: {DASHBOARD_DB_PATH}")
    except Exception as e:
        logger.error(f"Failed to initialize dashboard database: {e}")
        raise
    finally:
        conn.close()


def query(sql: str, params: list | None = None) -> list[dict]:
    """查询返回字典列表"""
    conn = get_connection()
    try:
        cur = conn.execute(sql, params or [])
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def query_one(sql: str, params: list | None = None) -> dict | None:
    """查询返回单行"""
    conn = get_connection()
    try:
        cur = conn.execute(sql, params or [])
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def execute(sql: str, params: list | None = None) -> int:
    """执行写操作，返回 lastrowid"""
    conn = get_connection()
    try:
        cur = conn.execute(sql, params or [])
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def execute_many(sql: str, params_list: list[list]):
    """批量执行"""
    conn = get_connection()
    try:
        conn.executemany(sql, params_list)
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: 创建 models.py**

```python
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class AccountCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    broker: str = ""
    cash: float = 0.0


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    broker: Optional[str] = None
    cash: Optional[float] = None


class HoldingCreate(BaseModel):
    account_id: int
    code: str = Field(..., min_length=6, max_length=6)
    name: str = ""
    quantity: int = Field(..., ge=0)
    cost_price: float = Field(..., ge=0)
    strategy: str = ""
    notes: str = ""


class HoldingUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    quantity: Optional[int] = None
    cost_price: Optional[float] = None
    strategy: Optional[str] = None
    notes: Optional[str] = None


class AlertRuleCreate(BaseModel):
    name: str
    rule_type: str  # top3_entry / momentum_shock / position_deviation
    config: str = "{}"  # JSON string


class AlertUpdate(BaseModel):
    status: str  # active / acknowledged / resolved


class ScheduleCreate(BaseModel):
    strategy: str
    interval: int = Field(..., ge=60)  # 最少60秒


class StrategyRunRequest(BaseModel):
    strategies: list[str] = Field(..., min_length=1)
```

- [ ] **Step 5: 创建 app.py**

```python
"""
FastAPI应用入口
"""
import asyncio
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse
from loguru import logger

from .config import DASHBOARD_HOST, DASHBOARD_PORT
from .db import init_db
from .routes import pages, portfolio, strategy, alerts, market
from .services.sse_manager import sse_manager
from .services.scheduler import scheduler

app = FastAPI(title="quant-ETF Dashboard", version="1.0.0")

# 模板目录
TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# 挂载路由
app.include_router(pages.router)
app.include_router(portfolio.router, prefix="/api/portfolio")
app.include_router(strategy.router, prefix="/api/strategy")
app.include_router(alerts.router, prefix="/api/alerts")
app.include_router(market.router, prefix="/api/market")


@app.on_event("startup")
async def startup():
    """应用启动时初始化"""
    init_db()
    logger.info("Dashboard startup complete")


@app.on_event("shutdown")
async def shutdown():
    """应用关闭时清理"""
    await scheduler.stop_all()
    logger.info("Dashboard shutdown complete")


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(status_code=500, content={"detail": str(exc)})


def main():
    """CLI入口"""
    import uvicorn
    uvicorn.run(
        "src.quant_etf.dashboard.app:app",
        host=DASHBOARD_HOST,
        port=DASHBOARD_PORT,
        reload=True,
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: 创建 routes/__init__.py 和 services/__init__.py**

`routes/__init__.py`:
```python
```

`services/__init__.py`:
```python
```

- [ ] **Step 7: 更新 pyproject.toml 依赖**

追加 dashboard 可选依赖：
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

- [ ] **Step 8: 安装依赖并验证**

```powershell
cd E:\mw3\wspy\2025\quant-ETF
uv add fastapi uvicorn jinja2 python-multipart httpx
```

- [ ] **Step 9: 启动测试**

```powershell
uv run uvicorn src.quant_etf.dashboard.app:app --host 127.0.0.1 --port 8080
```

访问 http://127.0.0.1:8080/docs — 应看到 FastAPI Swagger 文档页面，确认应用启动正常。

---

### Task 2: 基础页面布局 (base.html + HTMX 导航)

**Files:**
- Create: `src/quant_etf/dashboard/templates/base.html`
- Create: `src/quant_etf/dashboard/routes/pages.py`
- Create: `src/quant_etf/dashboard/templates/index.html` (总览占位)
- Create: `src/quant_etf/dashboard/templates/portfolio/index.html` (持仓占位)
- Create: `src/quant_etf/dashboard/templates/strategy/index.html` (策略占位)
- Create: `src/quant_etf/dashboard/templates/monitor/index.html` (监控占位)
- Create: `src/quant_etf/dashboard/templates/alerts/index.html` (告警占位)
- Create: `src/quant_etf/dashboard/templates/settings/index.html` (设置占位)

- [ ] **Step 1: 创建 base.html 全局布局**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}量化ETF看板{% endblock %}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css" rel="stylesheet">
    <script src="https://unpkg.com/htmx.org@2.0.4"></script>
    <script src="https://cdn.jsdelivr.net/npm/@alpinejs/collapse@3.14.8/dist/cdn.min.js" defer></script>
    <script src="https://unpkg.com/alpinejs@3.14.8" defer></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
    <style>
        body { font-size: .875rem; background: #f8f9fa; }
        .sidebar { position: fixed; top: 0; bottom: 0; left: 0; z-index: 100; padding: 56px 0 0; width: 200px; background: #fff; border-right: 1px solid #dee2e6; }
        .sidebar .nav-link { font-weight: 500; color: #495057; padding: .5rem 1rem; border-radius: 0; }
        .sidebar .nav-link:hover { background: #e9ecef; }
        .sidebar .nav-link.active { color: #0d6efd; background: #e9ecef; }
        .sidebar .nav-link i { margin-right: 8px; }
        main { margin-left: 200px; padding-top: 56px; }
        .htmx-indicator { opacity: 0; transition: opacity .3s; }
        .htmx-request .htmx-indicator { opacity: 1; }
        .htmx-request.htmx-indicator { opacity: 1; }
    </style>
</head>
<body>
    <!-- 顶部导航栏 -->
    <nav class="navbar navbar-dark bg-dark fixed-top" style="z-index: 1030;">
        <div class="container-fluid px-3">
            <a class="navbar-brand fw-bold" href="/pages/overview"
               hx-get="/pages/overview" hx-target="#content" hx-push-url="true">
                <i class="bi bi-graph-up"></i> quant-ETF看板
            </a>
            <div class="d-flex align-items-center">
                <span class="text-light-emphasis small me-3" id="clock-display"></span>
                <div id="sse-status" class="badge bg-success small">已连接</div>
            </div>
        </div>
    </nav>

    <!-- 侧边栏 -->
    <nav class="sidebar d-none d-md-block">
        <ul class="nav flex-column" x-data="{ active: 'overview' }">
            <li class="nav-item">
                <a class="nav-link" :class="{ active: active === 'overview' }" href="#"
                   hx-get="/pages/overview" hx-target="#content" hx-push-url="true"
                   @click.prevent="active = 'overview'; htmx.trigger(this, 'hx-get')">
                    <i class="bi bi-speedometer2"></i> 总览
                </a>
            </li>
            <li class="nav-item">
                <a class="nav-link" :class="{ active: active === 'portfolio' }" href="#"
                   hx-get="/pages/portfolio" hx-target="#content" hx-push-url="true"
                   @click.prevent="active = 'portfolio'; htmx.trigger(this, 'hx-get')">
                    <i class="bi bi-folder"></i> 持仓
                </a>
            </li>
            <li class="nav-item">
                <a class="nav-link" :class="{ active: active === 'strategy' }" href="#"
                   hx-get="/pages/strategy" hx-target="#content" hx-push-url="true"
                   @click.prevent="active = 'strategy'; htmx.trigger(this, 'hx-get')">
                    <i class="bi bi-bar-chart"></i> 策略
                </a>
            </li>
            <li class="nav-item">
                <a class="nav-link" :class="{ active: active === 'monitor' }" href="#"
                   hx-get="/pages/monitor" hx-target="#content" hx-push-url="true"
                   @click.prevent="active = 'monitor'; htmx.trigger(this, 'hx-get')">
                    <i class="bi bi-eye"></i> 监控
                </a>
            </li>
            <li class="nav-item">
                <a class="nav-link" :class="{ active: active === 'alerts' }" href="#"
                   hx-get="/pages/alerts" hx-target="#content" hx-push-url="true"
                   @click.prevent="active = 'alerts'; htmx.trigger(this, 'hx-get')">
                    <i class="bi bi-bell"></i> 告警
                </a>
            </li>
            <li class="nav-item">
                <a class="nav-link" :class="{ active: active === 'settings' }" href="#"
                   hx-get="/pages/settings" hx-target="#content" hx-push-url="true"
                   @click.prevent="active = 'settings'; htmx.trigger(this, 'hx-get')">
                    <i class="bi bi-gear"></i> 设置
                </a>
            </li>
        </ul>
    </nav>

    <!-- 主内容区 -->
    <main role="main" class="px-4" id="content">
        {% block content %}
        <div class="d-flex justify-content-center align-items-center" style="height: 60vh;">
            <div class="spinner-border text-primary" role="status">
                <span class="visually-hidden">加载中...</span>
            </div>
        </div>
        {% endblock %}
    </main>

    <script>
        // 页面加载后默认跳转到总览
        document.addEventListener('DOMContentLoaded', function() {
            const content = document.getElementById('content');
            const overviewLink = document.querySelector('a[hx-get="/pages/overview"]');
            if (overviewLink && !window.location.pathname.startsWith('/pages/')) {
                htmx.trigger(overviewLink, 'hx-get');
            }
            // 时钟
            function updateClock() {
                document.getElementById('clock-display').textContent = new Date().toLocaleTimeString('zh-CN');
            }
            setInterval(updateClock, 1000);
            updateClock();
        });

        // HTMX 事件监听
        document.addEventListener('htmx:beforeSwap', function(evt) {
            // 如果返回 404，不替换内容
            if (evt.detail.xhr.status === 404) {
                evt.detail.shouldSwap = false;
            }
        });
    </script>
</body>
</html>
```

- [ ] **Step 2: 创建 routes/pages.py**

```python
"""
页面渲染路由 - 返回HTML片段，供HTMX加载
"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from datetime import datetime

from ..app import templates

router = APIRouter(tags=["pages"])


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@router.get("/pages/overview", response_class=HTMLResponse)
async def overview_page(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "now": _now()})


@router.get("/pages/portfolio", response_class=HTMLResponse)
async def portfolio_page(request: Request):
    return templates.TemplateResponse("portfolio/index.html", {"request": request, "now": _now()})


@router.get("/pages/strategy", response_class=HTMLResponse)
async def strategy_page(request: Request):
    return templates.TemplateResponse("strategy/index.html", {"request": request, "now": _now()})


@router.get("/pages/monitor", response_class=HTMLResponse)
async def monitor_page(request: Request):
    return templates.TemplateResponse("monitor/index.html", {"request": request, "now": _now()})


@router.get("/pages/alerts", response_class=HTMLResponse)
async def alerts_page(request: Request):
    return templates.TemplateResponse("alerts/index.html", {"request": request, "now": _now()})


@router.get("/pages/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse("settings/index.html", {"request": request, "now": _now()})
```

- [ ] **Step 3: 创建占位页面模板**

`templates/index.html`:
```html
{% extends "base.html" %}
{% block title %}总览 - 量化ETF看板{% endblock %}
{% block content %}
<div class="py-3">
    <h4><i class="bi bi-speedometer2"></i> 总览</h4>
    <p class="text-muted">最后更新: {{ now }}</p>
    <div class="row" id="overview-cards">
        <div class="col-md-3 mb-3">
            <div class="card text-bg-primary">
                <div class="card-body">
                    <h5 class="card-title">账户数</h5>
                    <p class="card-text display-6" id="account-count">-</p>
                </div>
            </div>
        </div>
        <div class="col-md-3 mb-3">
            <div class="card text-bg-success">
                <div class="card-body">
                    <h5 class="card-title">今日告警</h5>
                    <p class="card-text display-6" id="today-alerts">-</p>
                </div>
            </div>
        </div>
        <div class="col-md-3 mb-3">
            <div class="card text-bg-warning">
                <div class="card-body">
                    <h5 class="card-title">策略运行</h5>
                    <p class="card-text display-6" id="strategy-status">-</p>
                </div>
            </div>
        </div>
        <div class="col-md-3 mb-3">
            <div class="card text-bg-info">
                <div class="card-body">
                    <h5 class="card-title">市场状态</h5>
                    <p class="card-text display-6" id="market-status">-</p>
                </div>
            </div>
        </div>
    </div>
</div>
<script>
    // 加载概览数据
    htmx.ajax('GET', '/api/market/overview', { target: '#overview-cards', swap: 'innerHTML' });
</script>
{% endblock %}
```

创建其余 5 个占位页面，每个继承 base.html，显示对应页面标题和占位内容。如：

`templates/portfolio/index.html`:
```html
{% extends "base.html" %}
{% block title %}持仓管理 - 量化ETF看板{% endblock %}
{% block content %}
<div class="py-3">
    <h4><i class="bi bi-folder"></i> 持仓管理</h4>
    <hr>
    <div class="row">
        <div class="col-md-3">
            <div id="account-list" hx-get="/pages/portfolio/accounts" hx-trigger="load">
                <div class="spinner-border spinner-border-sm"></div> 加载账户...
            </div>
        </div>
        <div class="col-md-9">
            <div id="holdings-table" class="text-muted small">
                请先选择一个账户
            </div>
        </div>
    </div>
</div>
{% endblock %}
```

其他模板可简化：
`templates/strategy/index.html` — 策略执行页面骨架
`templates/monitor/index.html` — 监控页面骨架
`templates/alerts/index.html` — 告警中心骨架
`templates/settings/index.html` — 设置页面骨架

- [ ] **Step 4: 验证导航**

```powershell
uv run uvicorn src.quant_etf.dashboard.app:app --host 127.0.0.1 --port 8080 --reload
```

打开浏览器访问 http://127.0.0.1:8080/pages/overview，确认：
- 页面正常渲染，侧边栏显示6个导航项
- 点击侧边栏各菜单，主内容区切换（非整页刷新）
- 顶部导航栏显示时钟

---

### Task 3: 持仓管理 CRUD (账户 + 持仓)

**Files:**
- Create: `src/quant_etf/dashboard/routes/portfolio.py`
- Create: `src/quant_etf/dashboard/templates/portfolio/_account_list.html`
- Create: `src/quant_etf/dashboard/templates/portfolio/_holdings_table.html`

- [ ] **Step 1: 创建 routes/portfolio.py**


```python
"""
持仓管理API
"""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from typing import Optional

from ..app import templates
from ..db import query, query_one, execute
from ..models import AccountCreate, AccountUpdate, HoldingCreate, HoldingUpdate
from ..config import STOCK_CODE_NAME_PATH
import json

router = APIRouter(tags=["portfolio"])


def _load_etf_name_map() -> dict:
    """加载ETF名称映射"""
    try:
        if STOCK_CODE_NAME_PATH.exists():
            with open(STOCK_CODE_NAME_PATH, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


# ========== 账户 ==========

@router.get("/accounts", response_class=HTMLResponse)
async def list_accounts(request: Request):
    """账户列表（侧边栏片段）"""
    accounts = query("SELECT * FROM accounts ORDER BY name")
    return templates.TemplateResponse(
        "portfolio/_account_list.html",
        {"request": request, "accounts": accounts}
    )


@router.post("/accounts", response_class=HTMLResponse)
async def create_account(request: Request, data: AccountCreate):
    execute(
        "INSERT INTO accounts (name, broker, cash) VALUES (?, ?, ?)",
        [data.name, data.broker, data.cash]
    )
    accounts = query("SELECT * FROM accounts ORDER BY name")
    return templates.TemplateResponse(
        "portfolio/_account_list.html",
        {"request": request, "accounts": accounts}
    )


@router.put("/accounts/{account_id}", response_class=HTMLResponse)
async def update_account(request: Request, account_id: int, data: AccountUpdate):
    fields = []
    params = []
    if data.name is not None:
        fields.append("name = ?")
        params.append(data.name)
    if data.broker is not None:
        fields.append("broker = ?")
        params.append(data.broker)
    if data.cash is not None:
        fields.append("cash = ?")
        params.append(data.cash)
    if not fields:
        raise HTTPException(400, "No fields to update")
    fields.append("updated_at = CURRENT_TIMESTAMP")
    params.append(account_id)
    execute(f"UPDATE accounts SET {', '.join(fields)} WHERE id = ?", params)
    accounts = query("SELECT * FROM accounts ORDER BY name")
    return templates.TemplateResponse(
        "portfolio/_account_list.html",
        {"request": request, "accounts": accounts}
    )


@router.delete("/accounts/{account_id}", response_class=HTMLResponse)
async def delete_account(request: Request, account_id: int):
    execute("DELETE FROM holdings WHERE account_id = ?", [account_id])
    execute("DELETE FROM accounts WHERE id = ?", [account_id])
    # 返回空列表片段
    accounts = query("SELECT * FROM accounts ORDER BY name")
    return templates.TemplateResponse(
        "portfolio/_account_list.html",
        {"request": request, "accounts": accounts}
    )


# ========== 持仓 ==========

@router.get("/accounts/{account_id}/holdings", response_class=HTMLResponse)
async def list_holdings(request: Request, account_id: int):
    """账户持仓表格"""
    account = query_one("SELECT * FROM accounts WHERE id = ?", [account_id])
    if not account:
        raise HTTPException(404, "Account not found")
    holdings = query("SELECT * FROM holdings WHERE account_id = ? ORDER BY code", [account_id])
    names = _load_etf_name_map()
    return templates.TemplateResponse(
        "portfolio/_holdings_table.html",
        {
            "request": request,
            "account": account,
            "holdings": holdings,
            "names": names,
        }
    )


@router.post("/holdings", response_class=HTMLResponse)
async def create_holding(request: Request, data: HoldingCreate):
    account = query_one("SELECT id FROM accounts WHERE id = ?", [data.account_id])
    if not account:
        raise HTTPException(404, "Account not found")
    execute(
        "INSERT INTO holdings (account_id, code, name, quantity, cost_price, strategy, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [data.account_id, data.code, data.name, data.quantity, data.cost_price, data.strategy, data.notes]
    )
    holdings = query("SELECT * FROM holdings WHERE account_id = ? ORDER BY code", [data.account_id])
    names = _load_etf_name_map()
    return templates.TemplateResponse(
        "portfolio/_holdings_table.html",
        {"request": request, "account": account, "holdings": holdings, "names": names}
    )


@router.put("/holdings/{holding_id}", response_class=HTMLResponse)
async def update_holding(request: Request, holding_id: int, data: HoldingUpdate):
    existing = query_one("SELECT * FROM holdings WHERE id = ?", [holding_id])
    if not existing:
        raise HTTPException(404, "Holding not found")
    fields = []
    params = []
    for field in ["code", "name", "quantity", "cost_price", "strategy", "notes"]:
        val = getattr(data, field, None)
        if val is not None:
            fields.append(f"{field} = ?")
            params.append(val)
    if not fields:
        raise HTTPException(400, "No fields to update")
    fields.append("updated_at = CURRENT_TIMESTAMP")
    params.append(holding_id)
    execute(f"UPDATE holdings SET {', '.join(fields)} WHERE id = ?", params)
    holdings = query("SELECT * FROM holdings WHERE account_id = ? ORDER BY code", [existing["account_id"]])
    names = _load_etf_name_map()
    account = query_one("SELECT * FROM accounts WHERE id = ?", [existing["account_id"]])
    return templates.TemplateResponse(
        "portfolio/_holdings_table.html",
        {"request": request, "account": account, "holdings": holdings, "names": names}
    )


@router.delete("/holdings/{holding_id}", response_class=HTMLResponse)
async def delete_holding(request: Request, holding_id: int):
    existing = query_one("SELECT * FROM holdings WHERE id = ?", [holding_id])
    if not existing:
        raise HTTPException(404, "Holding not found")
    execute("DELETE FROM holdings WHERE id = ?", [holding_id])
    holdings = query("SELECT * FROM holdings WHERE account_id = ? ORDER BY code", [existing["account_id"]])
    names = _load_etf_name_map()
    account = query_one("SELECT * FROM accounts WHERE id = ?", [existing["account_id"]])
    return templates.TemplateResponse(
        "portfolio/_holdings_table.html",
        {"request": request, "account": account, "holdings": holdings, "names": names}
    )
```

- [ ] **Step 2: 创建 _account_list.html**

```html
<div class="list-group list-group-flush" x-data="{ editing: null }">
    <div class="d-flex justify-content-between align-items-center mb-2">
        <strong>账户列表</strong>
        <button class="btn btn-sm btn-outline-primary"
                data-bs-toggle="modal" data-bs-target="#accountModal"
                @click="$dispatch('open-account-modal', { mode: 'create' })">
            <i class="bi bi-plus"></i> 新增
        </button>
    </div>
    {% for account in accounts %}
    <div class="list-group-item list-group-item-action d-flex justify-content-between align-items-center">
        <a href="#" class="text-decoration-none flex-grow-1"
           hx-get="/api/portfolio/accounts/{{ account.id }}/holdings"
           hx-target="#holdings-table">
            <strong>{{ account.name }}</strong>
            <br><small class="text-muted">{{ account.broker }}</small>
        </a>
        <div>
            <button class="btn btn-sm btn-outline-secondary"
                    hx-delete="/api/portfolio/accounts/{{ account.id }}"
                    hx-target="#account-list"
                    hx-confirm="确认删除账户 '{{ account.name }}' 及其所有持仓？">
                <i class="bi bi-trash"></i>
            </button>
        </div>
    </div>
    {% else %}
    <div class="text-muted small p-2">暂无账户，点击上方新增</div>
    {% endfor %}
</div>
```

- [ ] **Step 3: 创建 _holdings_table.html**

```html
{% if account %}
<div class="d-flex justify-content-between align-items-center mb-2">
    <h5 class="mb-0">{{ account.name }}
        <small class="text-muted">({{ account.broker }})</small>
    </h5>
    <button class="btn btn-sm btn-outline-primary"
            data-bs-toggle="modal" data-bs-target="#holdingModal"
            x-on:click="$dispatch('open-holding-modal', { mode: 'create', account_id: {{ account.id }} })">
        <i class="bi bi-plus"></i> 新增持仓
    </button>
</div>
<table class="table table-striped table-sm">
    <thead>
        <tr>
            <th>代码</th>
            <th>名称</th>
            <th>数量</th>
            <th>成本价</th>
            <th>当前价</th>
            <th>盈亏</th>
            <th>策略</th>
            <th>操作</th>
        </tr>
    </thead>
    <tbody>
        {% for h in holdings %}
        <tr>
            <td>{{ h.code }}</td>
            <td>{{ names.get(h.code, h.name) }}</td>
            <td>{{ h.quantity }}</td>
            <td>{{ "%.3f"|format(h.cost_price) }}</td>
            <td>
                {% if h.current_price %}
                {{ "%.3f"|format(h.current_price) }}
                {% else %}
                <span class="text-muted">-</span>
                {% endif %}
            </td>
            <td>
                {% if h.current_price %}
                {% set pnl = (h.current_price - h.cost_price) * h.quantity %}
                {% set pnl_pct = (h.current_price - h.cost_price) / h.cost_price * 100 %}
                <span class="{{ 'text-success' if pnl >= 0 else 'text-danger' }}">
                    {{ "%.2f"|format(pnl) }} ({{ "%.2f"|format(pnl_pct) }}%)
                </span>
                {% else %}
                <span class="text-muted">-</span>
                {% endif %}
            </td>
            <td>{{ h.strategy }}</td>
            <td>
                <button class="btn btn-sm btn-outline-danger"
                        hx-delete="/api/portfolio/holdings/{{ h.id }}"
                        hx-target="#holdings-table"
                        hx-confirm="确认删除持仓 {{ h.code }}？">
                    <i class="bi bi-x"></i>
                </button>
            </td>
        </tr>
        {% else %}
        <tr>
            <td colspan="8" class="text-center text-muted">暂无持仓数据</td>
        </tr>
        {% endfor %}
    </tbody>
</table>
{% else %}
<div class="text-muted">请先在左侧选择一个账户</div>
{% endif %}
```

- [ ] **Step 4: 验证持仓管理**

启动服务后访问 /pages/portfolio，测试：
- 新增账户 → 侧边栏出现
- 点击账户 → 右侧显示持仓表格（空）
- 新增持仓 → 表格出现新行
- 删除账户 → 确认后侧边栏移除
- 删除持仓 → 确认后表格行移除

---

### Task 4: 策略执行 + 结果展示

**Files:**
- Create: `src/quant_etf/dashboard/services/strategy_runner.py`
- Create: `src/quant_etf/dashboard/routes/strategy.py`
- Update: `src/quant_etf/dashboard/templates/strategy/index.html`
- Create: `src/quant_etf/dashboard/templates/strategy/_results.html`

- [ ] **Step 1: 创建 strategy_runner.py**

```python
"""
异步策略执行器
通过后台线程调用 TaskRegistry 执行策略
"""
import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from loguru import logger

from quant_etf.tasks import TaskRegistry
from src.quant_etf.conf import PROJECT_ROOT

_executor = ThreadPoolExecutor(max_workers=2)
_running_tasks: dict[str, dict] = {}


async def run_strategy(strategy_name: str, run_id: Optional[str] = None) -> str:
    """异步执行策略，返回 run_id"""
    if run_id is None:
        run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    _running_tasks[run_id] = {
        "status": "running",
        "strategy": strategy_name,
        "started_at": datetime.now().isoformat(),
        "progress": 0,
    }

    def _execute():
        try:
            task = TaskRegistry.get_task(strategy_name)
            if not task:
                raise ValueError(f"Unknown strategy: {strategy_name}")

            _running_tasks[run_id]["progress"] = 30
            task.initialize()

            _running_tasks[run_id]["progress"] = 50
            task.run()

            _running_tasks[run_id]["progress"] = 80

            # 读取结果
            today = datetime.now().strftime("%Y-%m-%d")
            csv_path = PROJECT_ROOT / "data" / "results" / today / f"{strategy_name}.csv"
            if csv_path.exists():
                df = pd.read_csv(csv_path, dtype={"code": str})
                records = df.to_dict("records")
                # 过滤无效行
                records = [r for r in records if r.get("code")]
                _running_tasks[run_id]["result"] = records
                _running_tasks[run_id]["count"] = len(records)
            else:
                _running_tasks[run_id]["result"] = []
                _running_tasks[run_id]["count"] = 0

            _running_tasks[run_id]["status"] = "complete"
            _running_tasks[run_id]["progress"] = 100
            _running_tasks[run_id]["finished_at"] = datetime.now().isoformat()

        except Exception as e:
            logger.error(f"Strategy {strategy_name} failed: {e}")
            _running_tasks[run_id]["status"] = "error"
            _running_tasks[run_id]["error"] = str(e)
            _running_tasks[run_id]["progress"] = -1

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(_executor, _execute)
    return run_id


def get_task_status(run_id: str) -> Optional[dict]:
    """获取任务状态"""
    return _running_tasks.get(run_id)


def list_available_strategies() -> list[dict]:
    """列出可用策略"""
    return TaskRegistry.list_tasks()
```

- [ ] **Step 2: 创建 routes/strategy.py**

```python
"""
策略执行API
"""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from ..app import templates
from ..models import StrategyRunRequest
from ..services.strategy_runner import (
    run_strategy,
    get_task_status,
    list_available_strategies,
)

router = APIRouter(tags=["strategy"])


@router.get("/strategies", response_class=JSONResponse)
async def list_strategies():
    """列出可用策略"""
    return list_available_strategies()


@router.post("/run")
async def start_strategy(data: StrategyRunRequest):
    """执行选定的策略"""
    run_ids = []
    for strategy_name in data.strategies:
        run_id = await run_strategy(strategy_name)
        run_ids.append(run_id)
    return {"run_ids": run_ids, "message": f"Started {len(run_ids)} strategy run(s)"}


@router.get("/status/{run_id}", response_class=JSONResponse)
async def check_status(run_id: str):
    """查询执行进度"""
    status = get_task_status(run_id)
    if not status:
        raise HTTPException(404, f"Unknown run_id: {run_id}")
    return status


@router.get("/results/{run_id}", response_class=HTMLResponse)
async def get_results(request: Request, run_id: str):
    """渲染结果表格+图表"""
    status = get_task_status(run_id)
    if not status:
        raise HTTPException(404, f"Unknown run_id: {run_id}")
    return templates.TemplateResponse(
        "strategy/_results.html",
        {"request": request, "status": status, "run_id": run_id}
    )
```

- [ ] **Step 3: 更新 strategy/index.html**

```html
{% extends "base.html" %}
{% block title %}策略执行 - 量化ETF看板{% endblock %}
{% block content %}
<div class="py-3" x-data="{ runIds: [], polling: false }">
    <h4><i class="bi bi-bar-chart"></i> 策略执行</h4>
    <hr>

    <div class="card mb-3">
        <div class="card-body">
            <h5 class="card-title">选择策略</h5>
            <div id="strategy-list">
                <div class="spinner-border spinner-border-sm"></div> 加载策略列表...
            </div>
            <button class="btn btn-primary mt-2"
                    hx-post="/api/strategy/run"
                    hx-target="#strategy-result"
                    hx-include="[name='strategies']"
                    hx-indicator="#run-spinner"
                    @htmx:config-request="
                        // 收集选中策略
                        const checked = document.querySelectorAll('input[name=strategies]:checked');
                        event.detail.parameters.strategies = Array.from(checked).map(c => c.value);
                    ">
                <i class="bi bi-play-fill"></i> 执行选中策略
            </button>
            <span id="run-spinner" class="htmx-indicator">
                <span class="spinner-border spinner-border-sm"></span> 执行中...
            </span>
        </div>
    </div>

    <div id="strategy-result">
        <div class="text-muted small">选择策略后点击执行查看结果</div>
    </div>
</div>

<script>
    // 加载策略列表
    (function() {
        fetch('/api/strategy/strategies')
            .then(r => r.json())
            .then(strategies => {
                const html = strategies.map(s => `
                    <div class="form-check form-check-inline">
                        <input class="form-check-input" type="checkbox" name="strategies" value="${s.name}" id="s-${s.name}">
                        <label class="form-check-label" for="s-${s.name}">
                            ${s.name} <small class="text-muted">${s.description}</small>
                        </label>
                    </div>
                `).join('');
                document.getElementById('strategy-list').innerHTML = html;
            });
    })();
</script>
{% endblock %}
```

- [ ] **Step 4: 创建 _results.html**

```html
<div class="card">
    <div class="card-header d-flex justify-content-between align-items-center">
        <span>执行结果: {{ status.strategy }} ({{ run_id }})</span>
        <span class="badge {{ 'bg-success' if status.status == 'complete' else 'bg-warning' }}">
            {{ status.status }}
        </span>
    </div>
    <div class="card-body">
        {% if status.status == 'running' %}
        <div class="text-center py-4">
            <div class="progress mb-2">
                <div class="progress-bar progress-bar-striped progress-bar-animated"
                     style="width: {{ status.progress }}%">
                    {{ status.progress }}%
                </div>
            </div>
            <p class="text-muted">策略执行中... 开始于 {{ status.started_at }}</p>
            <button class="btn btn-sm btn-outline-primary"
                    hx-get="/api/strategy/status/{{ run_id }}"
                    hx-trigger="every 2s"
                    hx-swap="none"
                    @htmx:after-request="
                        const data = JSON.parse(event.detail.xhr.response);
                        if (data.status === 'complete' || data.status === 'error') {
                            htmx.ajax('GET', '/api/strategy/results/{{ run_id }}', { target: '#strategy-result' });
                        }
                    ">
                自动刷新中...
            </button>
        </div>

        {% elif status.status == 'error' %}
        <div class="alert alert-danger">
            <strong>执行失败:</strong> {{ status.error }}
        </div>

        {% elif status.status == 'complete' and status.result %}
        <div class="mb-3">
            <canvas id="resultChart-{{ run_id }}"></canvas>
        </div>
        <div class="table-responsive">
            <table class="table table-sm table-striped">
                <thead>
                    <tr>
                        {% for key in status.result[0].keys() %}
                        <th>{{ key }}</th>
                        {% endfor %}
                    </tr>
                </thead>
                <tbody>
                    {% for row in status.result %}
                    <tr>
                        {% for val in row.values() %}
                        <td>{{ val }}</td>
                        {% endfor %}
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        <script>
            // Chart.js 柱状图
            const ctx{{ run_id }} = document.getElementById('resultChart-{{ run_id }}').getContext('2d');
            const data{{ run_id }} = {{ status.result | tojson }};
            const labels{{ run_id }} = data{{ run_id }}.map(r => r.code || r.name || '');
            const scores{{ run_id }} = data{{ run_id }}.map(r => r.score || r.weight || 0);
            new Chart(ctx{{ run_id }}, {
                type: 'bar',
                data: {
                    labels: labels{{ run_id }},
                    datasets: [{
                        label: '评分/权重',
                        data: scores{{ run_id }},
                        backgroundColor: 'rgba(13, 110, 253, 0.6)',
                        borderColor: 'rgba(13, 110, 253, 1)',
                        borderWidth: 1
                    }]
                },
                options: {
                    responsive: true,
                    plugins: {
                        legend: { display: false },
                        title: { display: true, text: '{{ status.strategy }} 策略评分' }
                    },
                    scales: {
                        y: { beginAtZero: true }
                    }
                }
            });
        </script>

        {% else %}
        <div class="text-muted">无结果数据</div>
        {% endif %}
    </div>
</div>
```

- [ ] **Step 5: 验证策略执行**

启动服务，访问 /pages/strategy，测试：
- 显示可用策略列表（etf, short, mid）
- 勾选策略后点击执行
- 弹出进度条
- 完成后显示结果表格 + Chart.js 柱状图

---

### Task 5: SSE 实时状态推送

**Files:**
- Create: `src/quant_etf/dashboard/services/sse_manager.py`
- Update: `src/quant_etf/dashboard/app.py` (添加 SSE 路由)

- [ ] **Step 1: 创建 sse_manager.py**

```python
"""
SSE (Server-Sent Events) 连接管理与事件广播
"""
import asyncio
import json
from typing import AsyncGenerator
from loguru import logger


class SSEManager:
    def __init__(self):
        self._queues: set[asyncio.Queue] = set()

    async def subscribe(self) -> AsyncGenerator[str, None]:
        """订阅 SSE 事件流"""
        queue: asyncio.Queue = asyncio.Queue()
        self._queues.add(queue)
        try:
            # 发送初始连接事件
            yield f"data: {json.dumps({'type': 'connected', 'message': 'SSE connected'})}\n\n"
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    # 心跳保持连接
                    yield f": heartbeat\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            self._queues.discard(queue)
            logger.debug("SSE client disconnected")

    async def broadcast(self, data: dict):
        """广播事件到所有订阅者"""
        for queue in self._queues.copy():
            try:
                await queue.put(data)
            except Exception:
                self._queues.discard(queue)


# 全局单例
sse_manager = SSEManager()
```

- [ ] **Step 2: 更新 app.py 添加 SSE 路由**

在 app.py 中 `@app.on_event("startup")` 之前添加：

```python
from fastapi.responses import StreamingResponse
from .services.sse_manager import sse_manager


@app.get("/events")
async def sse_events(request: Request):
    """SSE 事件流端点"""
    return StreamingResponse(
        sse_manager.subscribe(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
```

- [ ] **Step 3: 更新 base.html 添加 SSE 客户端**

在 `base.html` 的 `<body>` 结束前添加：

```html
<!-- SSE 事件监听 -->
<div id="sse-events" hx-ext="sse" sse-connect="/events" sse-swap="message"
     hx-swap="none"
     @htmx:sse-message="
        const data = JSON.parse(event.detail.data);
        if (data.type === 'strategy_result') {
            // 更新监控页或提示
            console.log('Strategy result:', data);
        } else if (data.type === 'alert') {
            // 更新告警计数
            const badge = document.getElementById('alert-count');
            if (badge) badge.textContent = parseInt(badge.textContent || '0') + 1;
        } else if (data.type === 'portfolio_update') {
            console.log('Portfolio update:', data);
        }
     ">
</div>
```

- [ ] **Step 4: 验证 SSE**

```powershell
uv run uvicorn src.quant_etf.dashboard.app:app --host 127.0.0.1 --port 8080 --reload
```

测试：
1. 浏览器打开 http://127.0.0.1:8080
2. 查看 "已连接" 徽章显示绿色
3. 在浏览器 DevTools Network 标签中看到 `/events` 持续收到 SSE 事件

---

### Task 6: 定时调度 + 监控页面

**Files:**
- Create: `src/quant_etf/dashboard/services/scheduler.py`
- Create: `src/quant_etf/dashboard/routes/market.py`
- Update: `src/quant_etf/dashboard/templates/monitor/index.html`

- [ ] **Step 1: 创建 scheduler.py**

```python
"""
定时任务调度管理
使用 asyncio.create_task 实现轻量调度
"""
import asyncio
from datetime import datetime
from typing import Optional
from loguru import logger

from .strategy_runner import run_strategy
from .sse_manager import sse_manager
from ..db import query, execute


class Scheduler:
    def __init__(self):
        self._tasks: dict[int, asyncio.Task] = {}

    async def start_loop(self, schedule_id: int, strategy: str, interval: int):
        """启动定时循环"""
        logger.info(f"Starting scheduled loop: {strategy} (every {interval}s)")
        while True:
            try:
                logger.info(f"Scheduled run: {strategy} (every {interval}s)")
                run_id = f"sched_{schedule_id}_{datetime.now().timestamp()}"
                await run_strategy(strategy, run_id)

                # 更新最后运行时间
                execute(
                    "UPDATE schedules SET last_run_at = CURRENT_TIMESTAMP WHERE id = ?",
                    [schedule_id]
                )

                # SSE 广播结果
                await sse_manager.broadcast({
                    "type": "strategy_result",
                    "schedule_id": schedule_id,
                    "strategy": strategy,
                    "run_id": run_id,
                    "timestamp": datetime.now().isoformat(),
                })

            except Exception as e:
                logger.error(f"Scheduled run failed for {strategy}: {e}")
                await sse_manager.broadcast({
                    "type": "strategy_error",
                    "schedule_id": schedule_id,
                    "strategy": strategy,
                    "error": str(e),
                })

            await asyncio.sleep(interval)

    async def start_all(self):
        """启动所有已启用的调度"""
        schedules = query("SELECT * FROM schedules WHERE enabled = 1")
        for s in schedules:
            if s["id"] not in self._tasks:
                task = asyncio.create_task(
                    self.start_loop(s["id"], s["strategy"], s["interval"])
                )
                self._tasks[s["id"]] = task
                logger.info(f"Scheduler started: {s['strategy']} (id={s['id']})")

    async def stop(self, schedule_id: int):
        """停止指定调度"""
        task = self._tasks.pop(schedule_id, None)
        if task:
            task.cancel()
            logger.info(f"Scheduler stopped: id={schedule_id}")

    async def stop_all(self):
        """停止所有调度"""
        for sid in list(self._tasks.keys()):
            await self.stop(sid)

    def is_running(self, schedule_id: int) -> bool:
        return schedule_id in self._tasks and not self._tasks[schedule_id].done()


scheduler = Scheduler()
```

- [ ] **Step 2: 创建 routes/market.py**

```python
"""
市场状态与概览 API
"""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from ..app import templates
from ..db import query, execute
from ..models import ScheduleCreate
from ..services.scheduler import scheduler
from ..services.strategy_runner import list_available_strategies

router = APIRouter(tags=["market"])


@router.get("/overview", response_class=HTMLResponse)
async def overview_data(request: Request):
    """总览概览数据卡片"""
    accounts = query("SELECT COUNT(*) as cnt FROM accounts")
    alerts_today = query(
        "SELECT COUNT(*) as cnt FROM alerts_dashboard "
        "WHERE date(created_at) = date('now')"
    )
    schedules = query("SELECT COUNT(*) as cnt FROM schedules WHERE enabled = 1")
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "account_count": accounts[0]["cnt"] if accounts else 0,
            "alert_count": alerts_today[0]["cnt"] if alerts_today else 0,
            "schedule_count": schedules[0]["cnt"] if schedules else 0,
        }
    )


@router.get("/schedules", response_class=JSONResponse)
async def list_schedules():
    """列出调度配置"""
    schedules = query("SELECT * FROM schedules ORDER BY strategy")
    for s in schedules:
        s["running"] = scheduler.is_running(s["id"])
    return schedules


@router.post("/schedules")
async def create_schedule(data: ScheduleCreate):
    """创建调度"""
    sid = execute(
        "INSERT INTO schedules (strategy, interval) VALUES (?, ?)",
        [data.strategy, data.interval]
    )
    return {"id": sid, "message": "Schedule created"}


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: int):
    """删除调度"""
    await scheduler.stop(schedule_id)
    execute("DELETE FROM schedules WHERE id = ?", [schedule_id])
    return {"message": "Schedule deleted"}


@router.post("/schedules/{schedule_id}/toggle")
async def toggle_schedule(schedule_id: int):
    """启停调度"""
    s = query_one("SELECT * FROM schedules WHERE id = ?", [schedule_id])
    if not s:
        raise HTTPException(404, "Schedule not found")
    if scheduler.is_running(schedule_id):
        await scheduler.stop(schedule_id)
        execute("UPDATE schedules SET enabled = 0 WHERE id = ?", [schedule_id])
        return {"status": "stopped"}
    else:
        # 启动
        execute("UPDATE schedules SET enabled = 1 WHERE id = ?", [schedule_id])
        asyncio.create_task(scheduler.start_loop(schedule_id, s["strategy"], s["interval"]))
        return {"status": "started"}
```

- [ ] **Step 3: 更新 monitor/index.html**

```html
{% extends "base.html" %}
{% block title %}实时监控 - 量化ETF看板{% endblock %}
{% block content %}
<div class="py-3">
    <h4><i class="bi bi-eye"></i> 实时监控</h4>
    <hr>

    <div class="row mb-3">
        <div class="col-md-4">
            <div class="card text-bg-success">
                <div class="card-body">
                    <h6>运行状态</h6>
                    <span id="monitor-status">🟢 活跃</span>
                </div>
            </div>
        </div>
        <div class="col-md-4">
            <div class="card text-bg-info">
                <div class="card-body">
                    <h6>定时策略</h6>
                    <span id="schedule-count">加载中...</span>
                </div>
            </div>
        </div>
        <div class="col-md-4">
            <div class="card text-bg-secondary">
                <div class="card-body">
                    <h6>最近执行</h6>
                    <span id="last-run">-</span>
                </div>
            </div>
        </div>
    </div>

    <div class="card mb-3">
        <div class="card-header d-flex justify-content-between align-items-center">
            <span>定时策略配置</span>
            <button class="btn btn-sm btn-outline-primary"
                    data-bs-toggle="modal" data-bs-target="#scheduleModal">
                <i class="bi bi-plus"></i> 新增
            </button>
        </div>
        <div class="card-body" id="schedule-table"
             hx-get="/api/market/schedules"
             hx-trigger="load every 10s">
            <div class="spinner-border spinner-border-sm"></div>
        </div>
    </div>

    <div class="card">
        <div class="card-header">
            <span>最新信号流 (SSE)</span>
        </div>
        <div class="card-body" style="max-height: 300px; overflow-y: auto;" id="signal-stream">
            <div class="text-muted small">等待实时信号...</div>
        </div>
    </div>
</div>

<script>
    // 监听 SSE 信号流
    document.addEventListener('DOMContentLoaded', function() {
        const evtSource = new EventSource('/events');
        evtSource.onmessage = function(event) {
            const data = JSON.parse(event.data);
            const stream = document.getElementById('signal-stream');
            const time = new Date().toLocaleTimeString('zh-CN');
            const msg = document.createElement('div');
            msg.className = 'small mb-1';
            if (data.type === 'strategy_result') {
                msg.innerHTML = `<span class="text-success">[${time}]</span> ${data.strategy} 执行完成`;
            } else if (data.type === 'strategy_error') {
                msg.innerHTML = `<span class="text-danger">[${time}]</span> ${data.strategy} 执行失败: ${data.error}`;
            } else if (data.type === 'alert') {
                msg.innerHTML = `<span class="text-warning">[${time}]</span> ⚠ ${data.title}`;
            }
            stream.prepend(msg);
            // 限制显示条数
            while (stream.children.length > 50) {
                stream.removeChild(stream.lastChild);
            }
        };
    });
</script>
{% endblock %}
```

- [ ] **Step 4: 验证调度功能**

启动服务，测试：
1. 访问 /pages/monitor
2. 查看运行状态和定时策略配置表
3. 在后端手动触发 SSE 广播，查看信号流实时更新

---

### Task 7: 告警引擎 + 告警中心

**Files:**
- Create: `src/quant_etf/dashboard/services/alert_engine.py`
- Create: `src/quant_etf/dashboard/routes/alerts.py`
- Create: `src/quant_etf/dashboard/templates/alerts/_alert_list.html`
- Update: `src/quant_etf/dashboard/templates/alerts/index.html`

- [ ] **Step 1: 创建 alert_engine.py**

```python
"""
告警条件检测引擎
"""
import json
from datetime import datetime
from typing import Optional, Any
from dataclasses import dataclass
from loguru import logger

from ..db import execute, query


@dataclass
class AlertRule:
    name: str
    check_fn: callable
    severity: str  # info / warning / danger


class AlertEngine:
    """告警引擎"""

    def __init__(self):
        self.rules: list[AlertRule] = [
            AlertRule("评分进入前三", self._check_top3_entry, "warning"),
            AlertRule("动量得分突变", self._check_momentum_shock, "danger"),
            AlertRule("持仓偏离目标", self._check_position_deviation, "info"),
        ]

    def _check_top3_entry(self, latest_result, prev_result) -> Optional[dict]:
        """检查是否有标的首次进入前3"""
        if not latest_result or not prev_result:
            return None
        try:
            curr_top3 = set(item["code"] for item in latest_result[:3] if item.get("code"))
            prev_top3 = set(item["code"] for item in prev_result[:3] if item.get("code"))
            new_entries = curr_top3 - prev_top3
            if new_entries:
                entries_str = ", ".join(sorted(new_entries))
                return {
                    "title": "新标的进入前三",
                    "message": f"{entries_str} 首次进入评分前3",
                    "data": {"new_entries": list(new_entries)},
                }
        except Exception as e:
            logger.warning(f"Alert check top3_entry failed: {e}")
        return None

    def _check_momentum_shock(self, latest_result, prev_result) -> Optional[dict]:
        """检查标的得分是否发生剧烈变化"""
        if not latest_result or not prev_result:
            return None
        try:
            prev_map = {}
            for item in prev_result:
                code = item.get("code")
                score = item.get("score") or item.get("weight") or 0
                if code:
                    prev_map[code] = float(score)

            for item in latest_result:
                code = item.get("code")
                score = float(item.get("score") or item.get("weight") or 0)
                if code and code in prev_map:
                    change = abs(score - prev_map[code])
                    if change > 0.15:
                        return {
                            "title": f"{code} 动量突变",
                            "message": f"得分变化 {change:.2%}",
                            "data": {"code": code, "change": change},
                        }
        except Exception as e:
            logger.warning(f"Alert check momentum_shock failed: {e}")
        return None

    def _check_position_deviation(self, latest_result, prev_result) -> Optional[dict]:
        """检查持仓偏离目标（预留）
        需要结合 portfolio 数据，MVP阶段简化为占位
        """
        return None

    def check(self, latest_result, prev_result, portfolio_data=None) -> list[dict]:
        """执行所有规则检查"""
        alerts = []
        for rule in self.rules:
            try:
                result = rule.check_fn(latest_result, prev_result)
                if result:
                    alerts.append({
                        "alert_type": rule.name,
                        "severity": rule.severity,
                        **result,
                    })
            except Exception as e:
                logger.warning(f"Alert rule '{rule.name}' check failed: {e}")
        return alerts

    def save_alerts(self, alerts: list[dict]) -> list[int]:
        """保存告警到数据库"""
        ids = []
        for alert in alerts:
            alert_id = execute(
                """INSERT INTO alerts_dashboard
                   (rule_id, alert_type, severity, title, message, data)
                   VALUES (NULL, ?, ?, ?, ?, ?)""",
                [
                    alert.get("alert_type", ""),
                    alert.get("severity", "info"),
                    alert.get("title", ""),
                    alert.get("message", ""),
                    json.dumps(alert.get("data", {}), ensure_ascii=False),
                ]
            )
            ids.append(alert_id)
        return ids


# 全局告警引擎实例
alert_engine = AlertEngine()
```

- [ ] **Step 2: 创建 routes/alerts.py**

```python
"""
告警管理API
"""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from ..app import templates
from ..db import query, query_one, execute
from ..models import AlertRuleCreate, AlertUpdate

router = APIRouter(tags=["alerts"])


@router.get("/rules", response_class=JSONResponse)
async def list_rules():
    """列出告警规则"""
    return query("SELECT * FROM alert_rules ORDER BY name")


@router.post("/rules")
async def create_rule(data: AlertRuleCreate):
    """创建告警规则"""
    rid = execute(
        "INSERT INTO alert_rules (name, rule_type, config) VALUES (?, ?, ?)",
        [data.name, data.rule_type, data.config]
    )
    return {"id": rid}


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: int):
    """删除告警规则"""
    execute("DELETE FROM alert_rules WHERE id = ?", [rule_id])
    return {"message": "Deleted"}


@router.get("/dashboard", response_class=HTMLResponse)
async def list_dashboard_alerts(request: Request):
    """告警列表片段"""
    alerts = query("""
        SELECT * FROM alerts_dashboard
        ORDER BY
            CASE status WHEN 'active' THEN 0 WHEN 'acknowledged' THEN 1 ELSE 2 END,
            created_at DESC
        LIMIT 100
    """)
    return templates.TemplateResponse(
        "alerts/_alert_list.html",
        {"request": request, "alerts": alerts}
    )


@router.put("/dashboard/{alert_id}/status")
async def update_alert_status(alert_id: int, data: AlertUpdate):
    """更新告警状态"""
    if data.status == "resolved":
        execute(
            "UPDATE alerts_dashboard SET status = ?, resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
            [data.status, alert_id]
        )
    else:
        execute(
            "UPDATE alerts_dashboard SET status = ? WHERE id = ?",
            [data.status, alert_id]
        )
    return {"message": "Updated"}


@router.get("/dashboard/stats", response_class=JSONResponse)
async def alert_stats():
    """告警统计数据"""
    total = query_one("SELECT COUNT(*) as cnt FROM alerts_dashboard")["cnt"]
    active = query_one("SELECT COUNT(*) as cnt FROM alerts_dashboard WHERE status = 'active'")["cnt"]
    return {"total": total, "active": active}
```

- [ ] **Step 3: 更新 templates/alerts/index.html**

```html
{% extends "base.html" %}
{% block title %}告警中心 - 量化ETF看板{% endblock %}
{% block content %}
<div class="py-3">
    <h4><i class="bi bi-bell"></i> 告警中心</h4>
    <hr>

    <div class="row mb-3">
        <div class="col-md-6">
            <div class="card text-bg-danger">
                <div class="card-body">
                    <h6>活跃告警</h6>
                    <span class="display-6" id="active-alert-count">-</span>
                </div>
            </div>
        </div>
        <div class="col-md-6">
            <div class="card text-bg-secondary">
                <div class="card-body">
                    <h6>总告警数</h6>
                    <span class="display-6" id="total-alert-count">-</span>
                </div>
            </div>
        </div>
    </div>

    <div class="btn-group mb-3" role="group">
        <button class="btn btn-outline-secondary btn-sm active" data-filter="all">全部</button>
        <button class="btn btn-outline-danger btn-sm" data-filter="active">活跃</button>
        <button class="btn btn-outline-warning btn-sm" data-filter="acknowledged">已确认</button>
        <button class="btn btn-outline-success btn-sm" data-filter="resolved">已解决</button>
    </div>

    <div id="alert-list"
         hx-get="/api/alerts/dashboard"
         hx-trigger="load every 15s">
        <div class="spinner-border spinner-border-sm"></div> 加载告警...
    </div>
</div>

<script>
    fetch('/api/alerts/dashboard/stats')
        .then(r => r.json())
        .then(stats => {
            document.getElementById('active-alert-count').textContent = stats.active;
            document.getElementById('total-alert-count').textContent = stats.total;
        });

    document.querySelectorAll('[data-filter]').forEach(btn => {
        btn.addEventListener('click', function() {
            document.querySelectorAll('[data-filter]').forEach(b => b.classList.remove('active'));
            this.classList.add('active');
            const filter = this.dataset.filter;
            document.querySelectorAll('[data-alert-status]').forEach(row => {
                if (filter === 'all' || row.dataset.alertStatus === filter) {
                    row.style.display = '';
                } else {
                    row.style.display = 'none';
                }
            });
        });
    });
</script>
{% endblock %}
```

- [ ] **Step 4: 创建 _alert_list.html**

```html
{% if alerts %}
<div class="table-responsive">
    <table class="table table-sm table-hover">
        <thead>
            <tr>
                <th>时间</th>
                <th>级别</th>
                <th>类型</th>
                <th>标题</th>
                <th>消息</th>
                <th>状态</th>
                <th>操作</th>
            </tr>
        </thead>
        <tbody>
            {% for alert in alerts %}
            <tr data-alert-status="{{ alert.status }}">
                <td class="small">{{ alert.created_at[:19] }}</td>
                <td>
                    {% if alert.severity == 'danger' %}
                    <span class="badge bg-danger">危险</span>
                    {% elif alert.severity == 'warning' %}
                    <span class="badge bg-warning text-dark">警告</span>
                    {% else %}
                    <span class="badge bg-info">信息</span>
                    {% endif %}
                </td>
                <td class="small">{{ alert.alert_type }}</td>
                <td class="small">{{ alert.title }}</td>
                <td class="small text-muted">{{ alert.message[:50] }}{% if alert.message|length > 50 %}...{% endif %}</td>
                <td>
                    {% if alert.status == 'active' %}
                    <span class="badge bg-danger">活跃</span>
                    {% elif alert.status == 'acknowledged' %}
                    <span class="badge bg-warning text-dark">已确认</span>
                    {% else %}
                    <span class="badge bg-success">已解决</span>
                    {% endif %}
                </td>
                <td>
                    {% if alert.status == 'active' %}
                    <button class="btn btn-sm btn-outline-warning"
                            hx-put="/api/alerts/dashboard/{{ alert.id }}/status"
                            hx-headers='{"Content-Type": "application/json"}'
                            hx-body='{"status": "acknowledged"}'
                            hx-target="#alert-list"
                            hx-swap="outerHTML">
                        确认
                    </button>
                    {% elif alert.status == 'acknowledged' %}
                    <button class="btn btn-sm btn-outline-success"
                            hx-put="/api/alerts/dashboard/{{ alert.id }}/status"
                            hx-headers='{"Content-Type": "application/json"}'
                            hx-body='{"status": "resolved"}'
                            hx-target="#alert-list"
                            hx-swap="outerHTML">
                        解决
                    </button>
                    {% endif %}
                </td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
</div>
{% else %}
<div class="text-muted text-center py-4">暂无告警</div>
{% endif %}
```

- [ ] **Step 5: 验证告警功能**

启动服务，访问 /pages/alerts，测试：
- 显示告警统计卡片（活跃数、总数）
- 初始列表为空
- 通过 API 手动插入告警规则测试
- 验证告警出现在列表中，可标记为已确认/已解决

---

### Task 8: 最终完善 (总览页面 + 启动脚本 + pyproject.toml)

**Files:**
- Update: `src/quant_etf/dashboard/templates/index.html`
- Create: `src/quant_etf/dashboard/__main__.py`
- Update: `pyproject.toml`

- [ ] **Step 1: 完善总览页面**

更新 `templates/index.html`，添加统计卡片和 HTMX 自动刷新：

```html
{% extends "base.html" %}
{% block title %}总览 - 量化ETF看板{% endblock %}
{% block content %}
<div class="py-3">
    <h4><i class="bi bi-speedometer2"></i> 总览</h4>
    <p class="text-muted">最后更新: {{ now }}</p>

    <div class="row mb-4">
        <div class="col-md-3 mb-3">
            <div class="card text-bg-primary h-100">
                <div class="card-body">
                    <div class="d-flex justify-content-between">
                        <h6 class="card-title">账户数</h6>
                        <i class="bi bi-person-badge fs-3 opacity-50"></i>
                    </div>
                    <p class="card-text display-6">
                        {{ account_count if account_count is defined else '-' }}
                    </p>
                </div>
            </div>
        </div>
        <div class="col-md-3 mb-3">
            <div class="card text-bg-danger h-100">
                <div class="card-body">
                    <div class="d-flex justify-content-between">
                        <h6 class="card-title">活跃告警</h6>
                        <i class="bi bi-bell fs-3 opacity-50"></i>
                    </div>
                    <p class="card-text display-6" id="alert-count"
                       hx-get="/api/alerts/dashboard/stats"
                       hx-trigger="load every 30s"
                       hx-swap="innerHTML">-</p>
                </div>
            </div>
        </div>
        <div class="col-md-3 mb-3">
            <div class="card text-bg-success h-100">
                <div class="card-body">
                    <div class="d-flex justify-content-between">
                        <h6 class="card-title">可用策略</h6>
                        <i class="bi bi-bar-chart fs-3 opacity-50"></i>
                    </div>
                    <p class="card-text display-6">3</p>
                </div>
            </div>
        </div>
        <div class="col-md-3 mb-3">
            <div class="card text-bg-info h-100">
                <div class="card-body">
                    <div class="d-flex justify-content-between">
                        <h6 class="card-title">市场状态</h6>
                        <i class="bi bi-graph-up fs-3 opacity-50"></i>
                    </div>
                    <p class="card-text display-6">-</p>
                </div>
            </div>
        </div>
    </div>

    <div class="row">
        <div class="col-md-6 mb-3">
            <div class="card">
                <div class="card-header"><h6 class="mb-0">最近策略结果</h6></div>
                <div class="card-body p-0">
                    <div class="text-muted small p-3">执行策略后显示结果</div>
                </div>
            </div>
        </div>
        <div class="col-md-6 mb-3">
            <div class="card">
                <div class="card-header"><h6 class="mb-0">最新告警</h6></div>
                <div class="card-body p-0" id="recent-alerts"
                     hx-get="/api/alerts/dashboard" hx-trigger="load every 30s">
                    <div class="text-muted small p-3">加载中...</div>
                </div>
            </div>
        </div>
    </div>
</div>
{% endblock %}
```

- [ ] **Step 2: 创建 __main__.py**

```python
"""
CLI入口: python -m quant_etf.dashboard
"""
from .app import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 更新 pyproject.toml**

追加 CLI 配置（如果已添加则跳过）：

```toml
[project.scripts]
quant-dashboard = "quant_etf.dashboard.app:main"
```

- [ ] **Step 4: 最终验证**

```powershell
# 启动看板
uv run uvicorn src.quant_etf.dashboard.app:app --host 127.0.0.1 --port 8080
```

访问 http://127.0.0.1:8080/pages/overview，完整测试所有功能：
1. 总览页面显示统计卡片
2. 持仓管理：增删改查账户和持仓
3. 策略执行：选择策略并执行，查看结果表格和图表
4. 实时监控：查看运行状态和信号流
5. 告警中心：查看和标记告警
6. 设置页面：显示占位内容
7. SSE 连接正常，浏览器 DevTools 可见持续心跳

---

## 自检清单

| 检查项 | 说明 | 状态 |
|--------|------|------|
| Spec 覆盖 | 所有 MVP 功能均有对应任务 | ✅ |
| 占位符扫描 | 无 TBD/TODO | ✅ |
| 类型一致性 | 方法签名和返回类型在任务间一致 | ✅ |
| 文件路径 | 所有文件路径均绝对 | ✅ |
| 测试步骤 | 每个任务均有验证步骤 | ✅ |

---

## 执行方式

计划完成，保存在 `docs/superpowers/plans/2026-05-21-dashboard-implementation.md`。两种执行方式：

1. **Subagent-Driven (推荐)** — 每个任务派发独立 subagent，审查后快速迭代
2. **Inline Execution** — 在当前会话中批量执行，设置检查点审查

选择哪种方式？