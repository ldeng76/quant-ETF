# ETF卖出信号功能设计

## 概述

基于ETF组合选股策略结果，自动检测"今日掉榜"的ETF并在Dashboard策略页面显示卖出信号提示，供用户手动操作卖出。

## 核心逻辑

### 卖出信号规则（严格模式）

- 对比相邻两次策略运行结果（最新日期 vs 前一日期的 `etf.csv`）
- 昨日在结果中但今日不在的ETF → **卖出信号**
- 无前一交易日数据时不生成信号（首次运行场景）

### 数据来源

复用现有 `strategy_runner.py` 中 `get_history_summary()` 的逻辑：
- `is_active == False` 表示已掉榜
- `off_date == 最新结果日期` 表示**今天刚掉榜** → 即为卖出信号

## 实现方案

### 1. 后端：`src/quant_etf/dashboard/services/strategy_runner.py`

新增函数 `get_sell_signals()`:

```python
def get_sell_signals(strategy_name: str = "etf") -> list[dict]:
    """检测今日掉榜的ETF，返回卖出信号列表"""
    summary = get_history_summary(strategy_name=strategy_name, days=30, auto_backfill=False)
    # 找到最新结果日期
    latest_date = max((d["last_on_date"] for d in summary), default=None)
    if not latest_date:
        return []
    # 筛选：今天刚掉榜的（off_date == latest_date 或 is_active==False 且 off_date非"-"）
    # 严格模式：off_date 等于最新日期的才卖出
    signals = []
    for item in summary:
        if not item["is_active"] and item["off_date"] == latest_date:
            signals.append({
                "code": item["code"],
                "name": item["name"],
                "last_on_date": item["last_on_date"],
                "on_days": item["on_days"],
            })
    return signals
```

### 2. API端点：`src/quant_etf/dashboard/routes/strategy.py`

新增端点：

```python
@router.get("/sell-signals", response_class=HTMLResponse)
async def get_sell_signals_fragment(request: Request, strategy: str = "etf"):
    """渲染卖出信号区块"""
    from ..services.strategy_runner import get_sell_signals
    signals = get_sell_signals(strategy_name=strategy)
    return templates.TemplateResponse(
        request, "strategy/_sell_signals.html",
        {"signals": signals}
    )
```

### 3. 前端模板：`src/quant_etf/dashboard/templates/strategy/_sell_signals.html`

新建模板文件：

- 红色警告样式（Bootstrap `alert-danger`）
- 显示：代码、名称、最后在榜日期、连续在榜天数
- 无信号时显示绿色"今日无卖出信号"提示

### 4. 集成到策略结果页：`src/quant_etf/dashboard/templates/strategy/_results.html`

在策略执行完成（`status == 'complete'`）后，在历史汇总表格上方插入卖出信号区块：

```html
{% if status.status == 'complete' %}
<div id="sell-signals-container"
     hx-get="/api/strategy/sell-signals?strategy=etf"
     hx-trigger="load"
     hx-indicator="#sell-spinner">
    <div id="sell-spinner" class="htmx-indicator text-center py-2">
        <span class="spinner-border spinner-border-sm"></span>
        <span class="small text-muted">检测卖出信号...</span>
    </div>
</div>
{% endif %}
```

## 边界情况

| 场景 | 处理方式 |
|------|---------|
| 首次运行（无历史数据） | 不生成信号，静默返回空列表 |
| 非交易日（无当日结果） | 不生成信号，等待下一交易日 |
| 策略结果文件缺失 | 静默跳过，记录INFO日志 |
| 非ETF策略（short/mid） | 同样支持，通过 `strategy` 参数指定 |

## 不做的事情

- 不自动执行卖出操作
- 不生成通达信卖出指令文件
- 不发送推送通知（后续可扩展）
