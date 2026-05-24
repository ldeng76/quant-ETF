# 看板模块审查改进清单

> 审查基准：`2026-05-21-dashboard-design.md` v1.0
> 审查时间：2026-05-21
> 审查范围：`src/quant_etf/dashboard/` 全部代码

---

## 一、模块完整性总览

设计文档定义了 13 个文件，当前已全部创建，**文件结构完整**：

| 文件 | 状态 | 说明 |
|------|------|------|
| `app.py` | ✅ 已实现 | FastAPI 入口、路由挂载、SSE 端点、启动/关闭事件 |
| `config.py` | ✅ 已实现 | 路径配置、告警阈值 |
| `db.py` | ✅ 已实现 | SQLite 建表、CRUD 工具函数 |
| `models.py` | ✅ 已实现 | Pydantic 请求模型 |
| `routes/pages.py` | ✅ 已实现 | 6 个页面渲染路由 |
| `routes/portfolio.py` | ✅ 已实现 | 账户+持仓 CRUD（9 个端点） |
| `routes/strategy.py` | ✅ 已实现 | 策略执行 API（4 个端点） |
| `routes/alerts.py` | ✅ 已实现 | 告警规则/记录管理（6 个端点） |
| `routes/market.py` | ✅ 已实现 | 市场状态+调度配置（6 个端点） |
| `services/strategy_runner.py` | ✅ 已实现 | 异步策略执行器 |
| `services/scheduler.py` | ✅ 已实现 | 定时调度管理 |
| `services/alert_engine.py` | ✅ 已实现 | 告警条件检测引擎 |
| `services/sse_manager.py` | ✅ 已实现 | SSE 连接管理与广播 |

---

## 二、数据模型对照（db.py vs 设计文档第 5 节）

**总体判定：✅ 基本一致，有一处增强**

| 表名 | 设计文档 | 当前实现 | 差异 |
|------|----------|----------|------|
| `accounts` | 5 列 | 5 列 | `CURRENT_TIMESTAMP` 替代 `NOW()`（SQLite 正确用法） ✅ |
| `holdings` | 10 列 + FK | 10 列 + FK | 增加了 `ON DELETE CASCADE`，优于设计 ✅ |
| `alert_rules` | 5 列 | 5 列 | 完全一致 ✅ |
| `alerts_dashboard` | 9 列 | 9 列 | 完全一致 ✅ |
| `schedules` | 5 列 | 5 列 | 完全一致 ✅ |

---

## 三、按优先级分类的改进清单

---

### P0 — 阻塞性缺陷（影响核心功能可用性）

#### P0-1：模态框 HTML 未定义，持仓/监控新增功能不可用

- **模块**：`templates/portfolio/_account_list.html`、`_holdings_table.html`、`templates/monitor/index.html`
- **设计要求**：第 6.1 节"点击'新增'弹 Alpine 模态框，`hx-post` 提交后刷新表格"
- **当前状态**：❌ 实现有误
- **具体问题**：模板中引用了 `data-bs-target="#accountModal"`、`data-bs-target="#holdingModal"`、`data-bs-target="#scheduleModal"`，但**三个模态框的 HTML 均未在任何模板中定义**。点击"新增"按钮不会弹出任何内容。
- **改进建议**：
  1. 在 `portfolio/index.html` 底部添加 `#accountModal` 和 `#holdingModal` 的 Alpine.js 模态框 HTML
  2. 在 `monitor/index.html` 底部添加 `#scheduleModal` 的模态框 HTML
  3. 或改用 Alpine.js `x-show` 实现自定义模态框（避免依赖 Bootstrap JS）

---

#### P0-2：config.py 未读取环境变量，部署方案不可用

- **模块**：`config.py`
- **设计要求**：第 9.2 节"通过环境变量控制监听地址：`DASHBOARD_HOST`、`DASHBOARD_PORT`"
- **当前状态**：❌ 未实现
- **具体问题**：`config.py` 硬编码 `DASHBOARD_HOST = "127.0.0.1"` 和 `DASHBOARD_PORT = 8080`，没有 `os.environ.get()` 读取环境变量
- **改进建议**：
  ```python
  import os
  DASHBOARD_HOST = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
  DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "8080"))
  ```

---

#### P0-3：scheduler.start_all() 未在启动时调用

- **模块**：`app.py` → `startup()`
- **设计要求**：第 7.2 节"Scheduler 启动定时循环"，第 9.1 节"应用启动后自动恢复已启用的调度"
- **当前状态**：❌ 未实现
- **具体问题**：`app.py` 的 `startup()` 只调用了 `init_db()`，没有调用 `scheduler.start_all()`。服务重启后所有已启用的定时任务不会自动恢复。
- **改进建议**：在 `startup()` 中增加 `await scheduler.start_all()`

---

#### P0-4：手动策略执行失败不广播 SSE 错误事件

- **模块**：`services/strategy_runner.py`
- **设计要求**：第 6.5 节"SSE 广播 error 事件，前端展示错误提示"
- **当前状态**：⚠️ 部分实现
- **具体问题**：`strategy_runner.py` 的 `_execute()` 在 `except` 中只更新了 `_running_tasks` 状态，**没有广播 SSE 错误事件**。只有 `scheduler.py` 在 `except` 中做了 SSE 广播。手动执行的策略失败后，前端只能通过轮询 `/status/{run_id}` 才能发现错误。
- **改进建议**：在 `_execute()` 的 `except` 块中增加 SSE 广播：
  ```python
  # 需要将 sse_manager 注入或在模块级调用
  asyncio.run_coroutine_threadsafe(
      sse_manager.broadcast({"type": "strategy_error", "run_id": run_id, "error": str(e)}),
      loop
  )
  ```

---

### P1 — 功能性缺陷（影响设计文档要求的功能完整性）

#### P1-1：Bootstrap JS 依赖矛盾

- **模块**：`templates/` 所有涉及模态框的文件
- **设计要求**：第 1.2 节决策表 "Bootstrap 5 (仅CSS) — CDN引入，不引入Bootstrap JS"
- **当前状态**：❌ 实现有误
- **具体问题**：模板使用 `data-bs-toggle="modal"` / `data-bs-target` 属性，这些是 Bootstrap JS 的 API，但 `base.html` 只引入了 `bootstrap.min.css`，没有引入 `bootstrap.bundle.min.js`
- **改进建议**：二选一：
  - 方案 A：在 base.html 中增加 `<script src="...bootstrap.bundle.min.js">` CDN（违反设计约束但最简单）
  - 方案 B：用 Alpine.js `x-data` + `x-show` 实现自定义模态框组件，彻底去除 Bootstrap JS 依赖（符合设计约束）

---

#### P1-2：告警引擎未与策略执行集成

- **模块**：`services/strategy_runner.py` / `services/alert_engine.py`
- **设计要求**：第 6.4 节"策略执行后检查告警规则 → 触发告警 → 实时推送"，第 7.2 节数据流
- **当前状态**：❌ 未实现
- **具体问题**：`alert_engine.py` 实现了三个告警规则和 `check()` / `save_alerts()` 方法，但**从未被任何地方调用**。策略执行完成后不会自动触发告警检查。
- **改进建议**：在 `strategy_runner.py` 的 `_execute()` 成功分支中，对比本次结果与上次结果，调用 `alert_engine.check()` 并 `save_alerts()`，然后 SSE 广播告警事件

---

#### P1-3：监控页面调度表返回 JSON 而非 HTML

- **模块**：`routes/market.py` → `list_schedules()` / `templates/monitor/index.html`
- **设计要求**：第 6.3 节监控页面"定时策略配置表"通过 HTMX 加载
- **当前状态**：⚠️ 部分实现
- **具体问题**：`monitor/index.html` 中 `#schedule-table` 使用 `hx-get="/api/market/schedules"` 加载调度列表，但 `market.py` 的 `list_schedules()` 返回 `JSONResponse`，不是 HTML 片段。页面会显示原始 JSON 数据。
- **改进建议**：创建 `templates/monitor/_schedule_table.html` 模板，`list_schedules()` 改为返回 `HTMLResponse`

---

#### P1-4：总览页告警计数卡片数据格式不匹配

- **模块**：`templates/index.html`
- **设计要求**：第 4.2 节"今日告警数"卡片
- **当前状态**：⚠️ 实现有误
- **具体问题**：`index.html` 第 30 行 `hx-get="/api/alerts/dashboard/stats"` 使用 `hx-swap="innerHTML"`，但该 API 返回 `{"total": N, "active": N}` JSON，不是纯数字文本。页面会显示原始 JSON 而非告警计数。
- **改进建议**：改为使用 Alpine.js `x-init` + `fetch()` 获取 JSON 后提取 `active` 字段，或新建一个只返回计数的 HTML 片段端点

---

#### P1-5：总览页市场状态卡片为空

- **模块**：`templates/index.html`
- **设计要求**：第 4.2 节"市场状态卡片"显示市场环境判断
- **当前状态**：❌ 未实现
- **具体问题**：市场状态卡片显示 `-` 占位符，没有绑定数据源。已有 `GET /api/market/status` 端点可用但未接入
- **改进建议**：使用 `x-init` + `fetch('/api/market/status')` 获取市场状态并填充

---

#### P1-6：持仓编辑（行内编辑）功能缺失

- **模块**：`templates/portfolio/_holdings_table.html`
- **设计要求**：第 6.1 节"表格行内 `hx-put` 保存行编辑"
- **当前状态**：❌ 未实现
- **具体问题**：`_holdings_table.html` 只有删除按钮，没有编辑按钮或行内编辑功能。`routes/portfolio.py` 中 `PUT /holdings/{id}` 端点已实现但前端未使用
- **改进建议**：在每行增加编辑按钮，点击后用 Alpine.js 切换到编辑模式（输入框替换静态文本），保存时 `hx-put`

---

#### P1-7：账户编辑功能缺失

- **模块**：`templates/portfolio/_account_list.html`
- **设计要求**：第 6.1 节 `PUT /api/portfolio/accounts/{id}` 编辑账户
- **当前状态**：❌ 未实现
- **具体问题**：`_account_list.html` 只有删除按钮，没有编辑按钮。`PUT /accounts/{id}` 端点已实现但前端未使用
- **改进建议**：在账户项旁增加编辑按钮，弹出模态框（Alpine.js）编辑后 `hx-put`

---

#### P1-8：DuckDB 告警历史数据未展示

- **模块**：`routes/alerts.py` / `templates/alerts/`
- **设计要求**：第 5.2 节"告警记录 (`alerts.duckdb`) 由 `ETFMonitor` 产生的监控信号，看板仅读取展示"，第 5.1 节说明"看板页面可以整合两个来源展示"
- **当前状态**：❌ 未实现
- **具体问题**：告警页面只读取 SQLite 的 `alerts_dashboard` 表，没有读取 `data/alerts/alerts.duckdb` 中 `ETFMonitor` 产生的告警记录
- **改进建议**：在 `alerts.py` 中增加一个端点，读取 DuckDB `alerts` 表最近的告警记录，模板中增加"监控信号"分区展示

---

#### P1-9：持仓当前价同步未实现

- **模块**：`services/` 层
- **设计要求**：第 7.3 节"持仓价值同步" — 从 DuckDB minute_data 获取最新收盘价，更新 holdings.current_price
- **当前状态**：❌ 未实现
- **具体问题**：持仓表有 `current_price` 字段但始终为 NULL，没有定时或手动同步逻辑
- **改进建议**：新增 `services/portfolio_sync.py`，定时或手动从 DuckDB 读取最新价更新持仓表，SSE 广播更新事件

---

### P2 — 设计偏差与优化建议

#### P2-1：URL 路径偏差

- **模块**：`routes/portfolio.py`、`routes/strategy.py`
- **设计要求**：第 6.1 节 HTML 片段端点使用 `/pages/portfolio/accounts`、`/pages/portfolio/account/{id}/holdings`；第 6.2 节 `/pages/strategy/results/{run_id}`
- **当前状态**：⚠️ 实现有误（偏差）
- **具体问题**：所有持仓和策略的 HTML 片段端点都挂载在 `/api/` 前缀下（如 `/api/portfolio/accounts` 而非 `/pages/portfolio/accounts`），与设计文档 URL 规范不一致。但内部模板引用自洽。
- **改进建议**：可保持现状（功能正常），或在后续重构时将 HTML 片段端点迁移到 pages.py 统一管理

---

#### P2-2：使用已废弃的 `@app.on_event()` API

- **模块**：`app.py`
- **设计要求**：无明确要求（最佳实践）
- **当前状态**：⚠️ 可优化
- **具体问题**：使用 `@app.on_event("startup")` 和 `@app.on_event("shutdown")`，这是 FastAPI 已标记为废弃的 API
- **改进建议**：改用 `lifespan` 上下文管理器

---

#### P2-3：依赖声明位置偏差

- **模块**：`pyproject.toml`
- **设计要求**：第 9.3 节 `dependencies should be in [project.optional-dependencies] dashboard = [...]`
- **当前状态**：⚠️ 偏差
- **具体问题**：dashboard 相关依赖（fastapi、uvicorn、jinja2、python-multipart、httpx）放在了主 `dependencies` 而非 `optional-dependencies`，这意味着不使用看板功能时也会安装这些包
- **改进建议**：将 dashboard 依赖移至 `[project.optional-dependencies]` 组，但保留在主依赖也可接受（简化安装）

---

#### P2-4：ETFDataSource 未被复用

- **模块**：`routes/portfolio.py`
- **设计要求**：第 8.1 节"data_source.py → ETFDataSource 用于获取名称映射（不修改）"
- **当前状态**：⚠️ 偏差
- **具体问题**：`portfolio.py` 直接读取 `stock_code_name.json` 文件，没有使用已有的 `ETFDataSource` 类
- **改进建议**：使用 `ETFDataSource.get_etf_name_map()` 保持与现有模块一致

---

#### P2-5：监控周期未集成 SignalGenerator

- **模块**：`services/scheduler.py`
- **设计要求**：第 8.3 节"看板的定时功能复用 monitor.py 中的核心逻辑（`update_15min_data`、`signal_generator.generate_signals`、`MarketAnalyzer`）"
- **当前状态**：❌ 未实现
- **具体问题**：调度器只通过 `TaskRegistry` 执行策略，没有实现基于 `SignalGenerator` + `MarketAnalyzer` 的实时监控周期
- **改进建议**：在 scheduler.py 中增加 `run_monitor_cycle()` 方法，集成 `minute_data_manager`、`SignalGenerator`、`MarketAnalyzer`

---

#### P2-6：设置页面纯占位

- **模块**：`templates/settings/index.html`
- **设计要求**：第 4.2 节"ETF池管理、数据源配置"
- **当前状态**：⚠️ 部分实现（占位）
- **具体问题**：设置页面只有两个 disabled 的输入框，无实际功能
- **改进建议**：第二阶段实现，当前可接受

---

#### P2-7：策略执行是同步等待而非真正的异步

- **模块**：`services/strategy_runner.py`
- **设计要求**：第 6.2 节"POST 返回 202 Accepted，前端轮询进度"
- **当前状态**：⚠️ 部分实现
- **具体问题**：`run_strategy()` 使用 `await loop.run_in_executor()` 等待执行完成，`POST /api/strategy/run` 会阻塞直到策略执行完毕才返回，而非立即返回 run_id 让前端轮询。设计文档明确要求返回 `202 Accepted`。
- **改进建议**：改为 `asyncio.create_task()` 包装执行，`POST /run` 立即返回 `{"run_ids": [...]}` 和 HTTP 202

---

## 四、总结统计

| 优先级 | 数量 | 说明 |
|--------|------|------|
| **P0** | 4 | 模态框缺失、环境变量、调度启动、SSE 广播 |
| **P1** | 9 | Bootstrap JS 矛盾、告警集成、调度表渲染、总览数据、编辑功能、DuckDB 读取、价格同步 |
| **P2** | 7 | URL 偏差、废弃 API、依赖位置、模块复用、监控周期、设置页、异步模型 |
| **合计** | **20** | |

**整体评估**：模块骨架和核心路由已完成约 **70%**，数据模型与设计文档完全一致。最大的缺口集中在 (1) 前端模态框 HTML 缺失导致新增功能不可用，(2) 告警引擎与策略执行未串联，(3) 监控页面的调度表渲染 JSON/HTML 不匹配。建议优先修复 P0 项使 MVP 可用。
