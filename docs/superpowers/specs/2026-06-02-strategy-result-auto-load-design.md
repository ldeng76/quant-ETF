# 策略结果自动加载与时效标注设计

## 背景

当前"策略执行"页面的工作流：选择 K 线周期和策略 → 点击"执行选中策略" → 后台计算 → 显示结果。

**问题**：每次必须手动触发才能看到结果，无法快速查看上次计算的历史结果。

**目标**：选择 K 线周期后，自动展示每个策略的最后一次计算结果，并标注结果时效性。

## 需求

1. **自动加载上次结果**：选择 K 线周期后，自动从数据库加载每个已勾选策略的最后一次成功执行结果
2. **Tab 切换**：多策略时以 Tab 形式展示，每个策略一个 Tab，点击切换
3. **时效标注**：
   - 显示结果生成时间（`finished_at`）
   - 判断标准：结果时间 < 最新数据采集时间 → 红色字体标注"已过期"
   - 结果时间 >= 最新数据采集时间 → 绿色字体标注"已是最新"
4. **执行按钮保留**：手动触发后，SSE 通知前端刷新当前结果

## 方案设计

### 方案选择：DB 查询 API（方案 A）

利用已有的 `strategy_runs` + `strategy_run_results` PG 表（目前只写不读），新建查询 API。

**选择理由**：DB 已有完善的持久化数据，只需补上读取通道。相比 SSE 推送+前端缓存方案，更可靠、更简单。

## 详细设计

### 1. 后端：新增 API 端点

**`GET /api/strategy/last-result`**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `bar_interval` | string | 是 | K线周期：`1d`, `5m`, `15m`, `30m`, `60m` |
| `strategies` | string | 否 | 逗号分隔的策略名，如 `etf,short`。不传则返回所有策略的最新结果 |

**返回 JSON**：

```json
{
  "latest_collect_time": "2025-06-03T14:55:00",
  "results": [
    {
      "strategy": "etf",
      "title": "ETF 组合",
      "run_id": "run_20250603_143000_abc123",
      "finished_at": "2025-06-03T14:30:00",
      "is_stale": true,
      "result_count": 10,
      "market_regime": {
        "market_score": 75.5,
        "median_score": 3.2,
        "mode": "normal",
        "top_n": 10,
        "risk_discount": 1.0,
        "index_scores": {"000001": 1.5}
      },
      "items": [
        {
          "code": "159516",
          "name": "纳斯达克ETF",
          "p60": 5.9,
          "p20": 3.2,
          "p10": 1.5,
          "p5": 0.8,
          "target_weight": 0.15,
          "interval_": "1d",
          "date_": "2025-06-03"
        }
      ]
    }
  ]
}
```

**时效判断逻辑**：

- `latest_collect_time` = `SELECT MAX(time) FROM minute_bars`（K线时间戳，近似等于最新采集完成时间）
  - 注：`time` 是 K线时间窗口标记（如 14:55 表示 14:50~14:55 的K线），与实际采集时刻相差约 5 秒（`COLLECT_OFFSET_SECONDS`），可忽略
- `is_stale` = `finished_at < latest_collect_time`（结果生成时间早于最新数据 → 过期）

**SQL 查询**：

```sql
-- 1. 获取最新数据采集时间
SELECT MAX(time) AS latest_collect_time FROM minute_bars;

-- 2. 对每个策略查最近一次成功执行的 run
SELECT DISTINCT ON (strategy) *
FROM strategy_runs
WHERE bar_interval = %s
  AND status = 'complete'
  AND strategy = ANY(%s)
ORDER BY strategy, finished_at DESC;

-- 3. 根据 run_id 查结果明细（p60 为 VARCHAR，需 cast 为数值排序）
SELECT * FROM strategy_run_results
WHERE run_id = %s
ORDER BY p60::FLOAT DESC NULLS LAST;
```

### 2. 后端：路由实现位置

在 `src/quant_etf/dashboard/routes/strategy.py` 中新增端点。

```python
@router.get("/last-result")
async def get_last_result(bar_interval: str, strategies: str = ""):
    """获取指定K线周期下各策略的最后一次执行结果"""
    # 1. 查询最新数据采集时间
    # 2. 查询每个策略的最新成功执行记录
    # 3. 查询结果明细
    # 4. 组装返回 JSON
```

### 3. 前端改造

#### 3.1 交互流程

```
页面加载
  │
  ├─→ GET /api/strategy/strategies → 渲染策略 checkbox 列表（默认全选）
  │
  └─→ 用户选择/切换 K 线周期
          │
          └─→ GET /api/strategy/last-result?bar_interval=X&strategies=a,b
                  │
                  ├─→ 渲染 Tab 栏（每个策略一个 Tab）
                  │
                  └─→ 渲染当前 Tab 的结果表格 + 时效标注
```

#### 3.2 Tab 切换 UI

- 多策略时显示 Tab 栏，每个 Tab 显示策略中文名
- Tab 上可附加小圆点指示时效状态（红=过期，绿=最新）
- 单策略时隐藏 Tab 栏，直接显示结果
- 当前选中 Tab 高亮

#### 3.3 时效标注

每个策略结果上方显示：

```
结果生成时间：2025-06-03 14:30:00  [已过期 - 最新数据: 14:55]   ← 红色
结果生成时间：2025-06-03 15:00:00  [已是最新]                    ← 绿色
```

#### 3.4 结果表格

新增 `_last_result.html` 模板，从 JSON 数据渲染结果表格。表格结构与现有 `_results.html` 一致（列名、百分比格式、drilldown 等），但数据来源从内存 `_running_tasks` 改为 API 返回的 JSON `items` 数组。

drilldown 功能保留：点击 p5/p10/p20/p60 单元格仍可弹出 ECharts 折线图，调用现有 `GET /api/strategy/drilldown/{run_id}` 接口。

#### 3.5 执行按钮行为不变

- "执行选中策略"仍然 POST `/api/strategy/run`
- 执行完成后 SSE 推送 `strategy_result` 事件
- 前端收到后重新调用 `last-result` API 刷新显示

### 4. 文件改动范围

| 文件 | 改动 |
|------|------|
| `routes/strategy.py` | 新增 `GET /last-result` 端点 |
| `templates/strategy/index.html` | 添加 Tab UI + 自动加载逻辑 + 时效标注 |
| `templates/strategy/_content.html` | 同上（HTMX 片段版本） |
| `templates/strategy/_results.html` | 不修改（保留给手动执行后的实时结果渲染） |
| `templates/strategy/_last_result.html` | **新增**：从 JSON 数据渲染结果表格的模板 |

### 5. 不涉及的部分

- 不修改 `strategy_runs` / `strategy_run_results` 表结构
- 不修改 `strategy_runner.py` 的执行逻辑
- 不修改定时调度逻辑（`scheduler.py`）
- 不修改采集服务（`minute_collector_service.py`）

### 6. DB 存储字段局限

`strategy_run_results` 表仅持久化了以下字段：`code, name, p60, p20, p10, p5, target_weight, interval_, date_`。

以下字段**未持久化**，自动加载结果表格中**不会显示**：

| 缺失字段 | 影响策略 | 说明 |
|----------|----------|------|
| `score` | 所有策略 | 综合评分 |
| `volume_ratio_1d_20d` | short | 成交量比率 |
| `trend_ok` | short | 趋势达标 |
| `drawdown_from_120d_high` | mid | 距120日高点回撤 |
| `bounce_from_20d_low` | mid | 距20日低点反弹 |
| `stabilization_ok` | mid | 企稳达标 |
| `rebound_ok` | mid | 反弹达标 |

手动执行策略后（通过内存 `_running_tasks` 渲染 `_results.html`）仍可显示完整字段。`_last_result.html` 仅渲染 DB 已存储的列。

## 数据流全景

```
[页面加载 / 切换K线周期]
        │
        ▼
GET /api/strategy/last-result?bar_interval=5m&strategies=etf,short
        │
        ├─→ SELECT MAX(time) FROM minute_bars  →  latest_collect_time
        ├─→ SELECT DISTINCT ON (strategy) FROM strategy_runs  →  每个策略最新 run
        └─→ SELECT * FROM strategy_run_results WHERE run_id = X  →  结果明细
        │
        ▼
返回 JSON {latest_collect_time, results: [{strategy, is_stale, items, ...}]}
        │
        ▼
前端渲染 Tab 栏 + 结果表格 + 时效标注
        │
        ├─→ is_stale = true  →  红色 "已过期"
        └─→ is_stale = false →  绿色 "已是最新"

[手动执行策略]
        │
        ▼
POST /api/strategy/run → 执行 → SSE broadcast strategy_result
        │
        ▼
前端收到 SSE → 重新调用 last-result API → 刷新显示
```
