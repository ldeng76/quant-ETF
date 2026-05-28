# 周期感知的涨幅标签设计

## 背景

策略结果中的 r60/r20/r10/r5 字段表示"60/20/10/5 个单位周期前的价格 vs 当前价格"。在日线模式下这些是"60日/20日/10日/5日涨幅"，但在 30m 模式下 r5 实际是"5 × 8 = 40 根 30 分钟 K 线跨度"（约 0.6 天），不是"5 日"。

当前所有界面标签硬编码为"60日涨幅"等，导致用户在分钟周期下产生误解（如"最近 5 根 K 线在涨，但 r5 显示负值"）。

## 方案

### 1. BarInterval.unit_label(n_bars) -> str

在 `BarInterval` 上新增方法，集中管理标签生成：

```python
# bar_interval.py
def unit_label(self, n_bars: int) -> str:
    """返回 n_bars 根 K 线的中文描述"""
    if self.is_daily:
        return f"{n_bars}日"
    days = n_bars / self.bars_per_day
    if days == int(days):
        return f"{n_bars}根({int(days)}天)"
    return f"{n_bars}根({days:.1f}天)"
```

示例输出：

| 调用 | 结果 |
|---|---|
| `INTERVALS["1d"].unit_label(60)` | `"60日"` |
| `INTERVALS["1d"].unit_label(5)` | `"5日"` |
| `INTERVALS["30m"].unit_label(60)` | `"60根(7.5天)"` |
| `INTERVALS["30m"].unit_label(5)` | `"5根(0.6天)"` |
| `INTERVALS["60m"].unit_label(20)` | `"20根(5天)"` |
| `INTERVALS["15m"].unit_label(10)` | `"10根(0.6天)"` |

### 2. 字段名 r60 -> p60

将 `r` (return) 前缀改为 `p` (period)，消除"日"的隐含语义。

#### 涉及文件

| 文件 | 改动 |
|---|---|
| `strategy.py` | `ETFScore`、`StockScore` 字段名 r60->p60 等；`calculate_returns` 返回 dict key |
| `conf.py` | `MOMENTUM_WEIGHTS` key 名 |
| `export.py` | TDX 公式中的变量名 |
| `tasks.py` | `pct_cols` 列表 |
| `strategy_runner.py` | `_PERCENT_FIELDS` 列表 |

### 3. strategy_runner 预计算列标题

`strategy_runner.py` 的 `_execute()` 完成后，在 `_running_tasks[run_id]` 中注入 `column_labels`：

```python
from quant_etf.bar_interval import get_interval

bi = get_interval(bar_interval)
_running_tasks[run_id]["column_labels"] = {
    "p60": bi.unit_label(60),
    "p20": bi.unit_label(20),
    "p10": bi.unit_label(10),
    "p5": bi.unit_label(5),
}
```

### 4. 模板动态标签

`_results.html` 的 `col_map` 中 r60/r20/r10/r5 的硬编码标签替换为从 `status.column_labels` 取值：

```html
{% set col_map = {
    ...
    'p60': status.column_labels.get('p60', 'p60'),
    'p20': status.column_labels.get('p20', 'p20'),
    'p10': status.column_labels.get('p10', 'p10'),
    'p5': status.column_labels.get('p5', 'p5'),
    ...
} %}
```

### 5. CSV 兼容性

- CSV 已有 `interval` 列标识周期，新旧文件按日期目录隔离
- 字段名从 r60 改为 p60 是 breaking change，但小程序读取代码 `_parse_value` 按列名读值、解析百分比字符串，字段名变更不影响解析
- `get_today_results` 和 `get_sell_signals` 中引用 r60 的地方同步改为 p60

## 不改动的部分

- `StrategyEngine` 的计算逻辑（`bars_for_days`、bar 索引方式）不变
- `minute_resampler` 不变
- `ReboundStockScore` 的 `r20/r10/r5` 同样改为 `p20/p10/p5`
- monitor.py / signal_generator.py 不涉及结果显示，不改
