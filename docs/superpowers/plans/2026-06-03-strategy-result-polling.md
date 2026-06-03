# 实现计划：策略结果轮询刷新

spec: `docs/superpowers/specs/2026-06-03-strategy-result-polling-design.md`

## 步骤

### 1. 后端：新增 result-timestamps 端点

**文件：** `src/quant_etf/dashboard/routes/strategy.py`

在 `get_last_result` 端点之前添加新端点 `GET /result-timestamps`：
- 参数：`bar_interval: str`（必填），`strategies: str`（可选，逗号分隔）
- SQL：`SELECT DISTINCT ON (strategy) strategy, finished_at, run_id, status FROM strategy_runs WHERE bar_interval = %s AND status IN ('complete', 'error') AND strategy = ANY(%s) ORDER BY strategy, finished_at DESC`
- 返回 `{strategy_name: {finished_at, run_id, status}}` 的 dict
- 复用现有 `query()` 和 `list_available_strategies()` 的模式

### 2. 前端 strategy/index.html：轮询替换 SSE

**文件：** `src/quant_etf/dashboard/templates/strategy/index.html`

改动点：
1. `init()`: 删 `this.initSSE()`，加 `this._pollTimer = setInterval(() => this.pollResultTimestamps(), 60000)`
2. 删除整个 `initSSE()` 方法
3. 新增 `pollResultTimestamps()`: 调 `/api/strategy/result-timestamps`，对比 `this.lastResultData` 中各策略的 `finished_at`，有变化调 `this.loadLastResult()`
4. 新增 `destroy()`: 清除 `_pollTimer` 和 `_fastPollTimer`
5. 改 `runStrategies()`: POST 返回后启动 5 秒短轮询 `_fastPollTimer`，检测到 `finished_at` 或 `run_id` 变化后调 `loadLastResult()` 并清除短轮询，90 秒安全超时

### 3. 前端 strategy/_content.html：同步改动

**文件：** `src/quant_etf/dashboard/templates/strategy/_content.html`

与步骤 2 完全相同的改动（两个模板是策略页面的两种布局，JS 逻辑一致）。

### 4. 验证

- 启动 dashboard，打开策略页面
- DevTools Network 面板确认 `result-timestamps` 每 60 秒调用一次
- 手动执行策略，确认 5 秒短轮询触发结果刷新
- 切换 barInterval，确认轮询参数正确
- 打开 Monitor 页面确认 SSE 信号流不受影响
- 离开策略页面后 DevTools Timers 无泄漏
