# 策略结果轮询刷新

## 背景

策略执行页面当前通过 SSE (`EventSource('/events')`) 监听 `strategy_result` 事件来触发结果刷新。实践中 SSE 存在连接断开、重复连接等问题，导致结果不能可靠地自动更新。

> 注：`base.html` 已有一个全局 `EventSource('/events')` 连接（用于告警 badge），策略页面又创建了第二个。本方案消除策略页面的冗余连接，`base.html` 的连接保留不动。

## 方案

用**轻量级轮询**替代策略页面中的 SSE 结果刷新：

- 每 60 秒查询一个只返回时间戳的轻量接口
- 前端对比当前显示的时间戳，有更新才拉全量结果
- 手动执行策略后启动**短轮询**（5 秒间隔），检测到结果变化立即刷新
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
  "etf": {"finished_at": "2026-06-03T14:30:00", "run_id": "abc123", "status": "complete"},
  "momentum": {"finished_at": "2026-06-03T14:31:05", "run_id": "def456", "status": "error"}
}
```

**SQL：** 复用 `strategy_runs` 表的现有查询模式：

```sql
SELECT DISTINCT ON (strategy) strategy, finished_at, run_id, status
FROM strategy_runs
WHERE bar_interval = %s
  AND status IN ('complete', 'error')
  AND strategy = ANY(%s)
ORDER BY strategy, finished_at DESC
```

> 包含 `error` 状态，确保策略执行失败时前端也能检测到变化并刷新（展示旧结果或错误提示）。不返回 items、market_regime 等大字段。

## 前端：轮询逻辑

**改动范围：** 仅 `strategy/index.html` 和 `strategy/_content.html`（两个模板有重复的策略页面逻辑）。

### 变更

1. **删除 `initSSE()` 方法** — 不再创建 `EventSource` 监听 `strategy_result`
2. **新增 `pollResultTimestamps()` 方法：**
   - 调用 `result-timestamps` 接口（传当前 `this.barInterval`）
   - 遍历返回的时间戳，与 `this.lastResultData.results` 中对应策略的 `finished_at` 对比
   - 任一策略 `finished_at` 或 `status` 有变化 → 调 `this.loadLastResult()`
3. **`init()` 中启动轮询：**
   ```js
   this._pollTimer = setInterval(() => this.pollResultTimestamps(), 60000);
   ```
4. **新增 `destroy()` 清理定时器：**
   ```js
   destroy() {
       if (this._pollTimer) clearInterval(this._pollTimer);
       if (this._fastPollTimer) clearInterval(this._fastPollTimer);
   }
   ```
5. **`runStrategies()` 改动：** POST 返回后启动**短轮询**（5 秒间隔），检测到 `finished_at` 或 `run_id` 变化后调 `loadLastResult()` 并停止短轮询。不调 `loadLastResult()` 本身，因为此时策略尚未执行完毕，DB 中仍为旧数据。
   ```js
   // POST /api/strategy/run 返回后
   const baseline = this._snapshotTimestamps();  // 记录当前各策略的 finished_at
   this._fastPollTimer = setInterval(async () => {
       const changed = await this._checkTimestampsChanged(baseline);
       if (changed) {
           clearInterval(this._fastPollTimer);
           this._fastPollTimer = null;
           this.loadLastResult();
       }
   }, 5000);
   // 安全超时：90 秒后自动停止短轮询
   setTimeout(() => {
       if (this._fastPollTimer) { clearInterval(this._fastPollTimer); this._fastPollTimer = null; }
   }, 90000);
   ```
6. **`lastResultData` 基准更新：** `loadLastResult()` 成功后自然更新，下次轮询自动使用新基准

### 不变

- `loadLastResult()` — 不变
- `renderResult()` — 不变
- `barInterval` 切换和策略勾选 — 不变，`loadLastResult()` 已在切换时调用（`pollResultTimestamps()` 内部读取 `this.barInterval`，Alpine 响应式数据天然为最新值）

## SSE 处理

- **策略页面的 `EventSource('/events')` 连接删除**（消除与 `base.html` 的重复连接）
- **SSE 基础设施保留：** `sse_manager.py`、`strategy_runner.py` 中的广播、`app.py` 中的 `/events` 端点、`_SuppressCancelMiddleware`
- **其他页面 SSE 连接保留：** `base.html`（告警 badge）、`monitor/`（实时信号流）

## 改动文件

| 文件 | 改动 |
|------|------|
| `dashboard/routes/strategy.py` | 新增 `result-timestamps` 端点（含 error 状态） |
| `dashboard/templates/strategy/index.html` | 删 `initSSE()`，加轮询 + destroy 清理 + runStrategies 短轮询 |
| `dashboard/templates/strategy/_content.html` | 同上 |

共 3 个文件，核心是 1 个新接口 + 前端轮询替换 SSE 监听。

## 验证

1. 策略页面加载后，确认 `result-timestamps` 每 60 秒被调用一次
2. 后台执行策略完成后，下一个轮询周期内页面自动刷新
3. 手动点击"执行选中策略"后，5 秒短轮询检测到完成即刷新（不等 60 秒长轮询）
4. 策略执行失败时，前端也能检测到 `status=error` 并刷新
5. Monitor 页面 SSE 信号流不受影响
6. 切换 `barInterval` 后轮询参数正确更新
7. 离开策略页面后无定时器泄漏（DevTools → Timers 验证）
