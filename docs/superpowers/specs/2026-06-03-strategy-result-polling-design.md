# 策略结果轮询刷新

## 背景

策略执行页面当前通过 SSE (`EventSource('/events')`) 监听 `strategy_result` 事件来触发结果刷新。实践中 SSE 存在连接断开、重复连接等问题，导致结果不能可靠地自动更新。

## 方案

用**轻量级轮询**替代策略页面中的 SSE 结果刷新：

- 每 60 秒查询一个只返回时间戳的接口
- 前端对比当前显示的时间戳，有更新才拉全量结果
- SSE 体系保留（Monitor 信号流、告警 badge 等仍使用）

## 后端：新鲜度接口

### 新增端点

`GET /api/strategy/result-timestamps`

**参数：**
- `bar_interval`: str（必填）— K 线周期
- `strategies`: str（可选，逗号分隔）— 策略列表，默认全部

**返回：**
```json
{
  "etf": {"finished_at": "2026-06-03T14:30:00", "run_id": "abc123"},
  "momentum": {"finished_at": "2026-06-03T14:31:05", "run_id": "def456"}
}
```

**SQL：** 复用 `strategy_runs` 表的现有查询模式（`DISTINCT ON (strategy)` + `status='complete'` + `ORDER BY finished_at DESC`），只返回 `strategy`、`finished_at`、`run_id` 三列。不返回 items、market_regime 等大字段。

## 前端：轮询逻辑

**改动范围：** 仅 `strategy/index.html` 和 `strategy/_content.html`（两个模板有重复的策略页面逻辑）。

### 变更

1. **删除 `initSSE()` 方法** — 不再创建 `EventSource` 监听 `strategy_result`
2. **新增 `pollResultTimestamps()` 方法：**
   - 调用 `result-timestamps` 接口
   - 遍历返回的时间戳，与 `this.lastResultData.results` 中对应策略的 `finished_at` 对比
   - 任一策略有更新 → 调 `this.loadLastResult()`
3. **`init()` 中启动轮询：** `this._pollTimer = setInterval(() => this.pollResultTimestamps(), 60000)`
4. **`runStrategies()` 改动：** 执行请求返回后主动调 `this.loadLastResult()` 一次，不等待轮询周期
5. **`lastResultData` 基准更新：** `loadLastResult()` 成功后自然更新，下次轮询自动使用新基准

### 不变

- `loadLastResult()` — 不变
- `renderResult()` — 不变
- `barInterval` 切换和策略勾选 — 不变，`loadLastResult()` 已在切换时调用

## SSE 处理

- **策略页面的 `EventSource('/events')` 连接删除**
- **SSE 基础设施保留：** `sse_manager.py`、`strategy_runner.py` 中的广播、`app.py` 中的 `/events` 端点、`_SuppressCancelMiddleware`
- **其他页面 SSE 连接保留：** `base.html`（告警 badge）、`monitor/`（实时信号流）

## 改动文件

| 文件 | 改动 |
|------|------|
| `dashboard/routes/strategy.py` | 新增 `result-timestamps` 端点 |
| `dashboard/templates/strategy/index.html` | 删 `initSSE()`，加轮询逻辑 |
| `dashboard/templates/strategy/_content.html` | 同上 |

共 3 个文件，核心是 1 个新接口 + 前端轮询替换 SSE 监听。

## 验证

1. 策略页面加载后，确认 `result-timestamps` 每 60 秒被调用一次
2. 后台执行策略完成后，下一个轮询周期内页面自动刷新
3. 手动点击"执行选中策略"后，结果立即刷新（不等轮询）
4. Monitor 页面 SSE 信号流不受影响
5. 切换 `barInterval` 后轮询参数正确更新
