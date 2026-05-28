# 统一 pN 为"N 根 K 线涨幅"

## 背景

当前 `calculate_returns()` 使用 `bars_for_days(N, interval)` 将"交易日"转换为 K 线根数。对 30m 周期，`p5` 实际计算的是 `5 * 8 = 40` 根 K 线的涨幅，而非 5 根。

用户期望：p5 就是最后 5 根 K 线，不论日线还是分钟线。

## 语义定义

p5/p10/p20/p60 统一含义：**最后 N 根 K 线的涨幅**。

- 日线：p5 = 5 根日 K = 5 日涨幅（与原行为一致）
- 分钟线：p5 = 5 根分钟 K 线

## 标签格式

`unit_label(N)` 统一返回 `f"{N}根"`，如 `5根`、`20根`、`60根`。

不再区分日线/分钟线，不再显示天数换算。

## 改动文件

### 1. `src/quant_etf/strategy.py` — `calculate_returns()`

```python
# 现在
b60 = bars_for_days(60, self._bar_interval)  # 30m: 480
min_bars = bars_for_days(60, self._bar_interval) + 1

# 改为
b60, b20, b10, b5 = 60, 20, 10, 5
min_bars = 61
```

不再调用 `bars_for_days`。

### 2. `src/quant_etf/bar_interval.py` — `unit_label()`

```python
# 现在
if self.is_daily:
    return f"{n_bars}日"
days = n_bars / self.bars_per_day
return f"{n_bars}根({days:.1f}天)"

# 改为
return f"{n_bars}根"
```

### 3. `src/quant_etf/dashboard/services/strategy_runner.py` — `get_drilldown_data()`

```python
# 现在
n_bars = bars_for_days(days, bi)

# 改为
n_bars = days
```

`get_interval()` 调用仍保留（label 需要）。

## 不变部分

- `_build_column_labels()` — 仍调用 `unit_label()`，无需改
- drilldown 弹窗前端 — 不变
- `risk.py` 中的 `bars_for_days` 用法 — 属于独立的风控模块，不在本次范围

## 验证

- 日线策略结果数值不变（5 根日 K = 5 日）
- 分钟线策略 p5 = 最后 5 根 K 线涨幅（而非 40/80/240 根）
- 表头标签统一显示 `5根` / `10根` / `20根` / `60根`
