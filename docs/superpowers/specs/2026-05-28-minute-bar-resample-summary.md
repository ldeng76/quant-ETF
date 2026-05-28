# DuckDB 分钟K线动态重采样 — 实施总结

## 问题

`minute_bars` 表存储1分钟K线数据，5m/15m/30m/60m 需基于它动态生成。旧实现存在两个问题：

1. **性能差**：`resample_to_interval` 用 pandas 逐股票 `resample()`，批量场景（20-100只）慢
2. **聚合错误**：不处理 A 股交易时段边界（午休 11:30-13:00），跨时段聚合结果不正确

## 方案

DuckDB 内存聚合 + 交易时段分组键。不持久化，纯计算引擎。

### 核心算法

A 股每天两个 session：早盘 09:30-11:30（120分钟）、午盘 13:00-15:00（120分钟）。

每根1分钟 bar 计算 `bar_seq`（session 内从0开始的序号），用 `bar_seq // N` 整数除法作为分组键。session 变化时分组键自然断裂，不跨午休聚合。

```sql
WITH ordered AS (
    SELECT *,
        CASE WHEN EXTRACT(HOUR FROM time) < 12 THEN 0 ELSE 1 END AS session,
        ROW_NUMBER() OVER (
            PARTITION BY code, DATE(time), session
            ORDER BY time
        ) - 1 AS bar_seq
    FROM minute_bars_1m
)
SELECT
    code,
    MAX(time) AS time,
    FIRST(open ORDER BY time) AS open,
    MAX(high) AS high,
    MIN(low) AS low,
    LAST(close ORDER BY time) AS close,
    SUM(volume) AS volume,
    SUM(amount) AS amount
FROM ordered
GROUP BY code, DATE(time), session, bar_seq // {interval_minutes}
```

**关键细节：** DuckDB 的 `/` 是浮点除法（`1/5=0.2`），必须用 `//` 做整数除法（`1//5=0`）。

### 数据流

```
PG minute_bars → 批量拉取(单次SQL) → DuckDB 内存表 → SQL聚合 → DataFrame
```

### 文件变更

| 文件 | 操作 |
|---|---|
| `src/quant_etf/minute_resampler.py` | **新增** — DuckDB 聚合引擎 |
| `src/quant_etf/data_source.py` | `_load_minute_data_resampled` 切换到 DuckDB |
| `src/quant_etf/minute_data_manager.py` | `get_minute_bars_for_interval` 标记 deprecated |

### 接口

```python
# 单股票
def resample_bars(code: str, interval: BarInterval, count: int = 200) -> pd.DataFrame

# 批量
def resample_bars_batch(
    codes: list[str], interval: BarInterval, start_time: datetime | None = None
) -> dict[str, pd.DataFrame]
```

## 性能对比

合成数据（每只股票每天 240 根1分钟 bar），5次取平均：

| 场景 | 周期 | Pandas | DuckDB | 加速比 |
|---|---|---|---|---|
| 5只×30天 (2.6万行) | 5m | 19ms | 24ms | 0.8x |
| | 15m | 17ms | 21ms | 0.8x |
| | 60m | 17ms | 14ms | 1.2x |
| 20只×60天 (21万行) | 5m | 305ms | 139ms | 2.2x |
| | 15m | 676ms | 102ms | 6.6x |
| | 60m | 645ms | 91ms | 7.1x |
| 50只×90天 (78万行) | 5m | 5086ms | 323ms | 15.7x |
| | 15m | 4423ms | 136ms | **32.5x** |
| | 60m | 5066ms | 228ms | 22.3x |
| 100只×120天 (206万行) | 5m | 9711ms | 968ms | 10.0x |

**结论：**
- 小数据量（<3万行）两者持平，DuckDB register 开销抵消了聚合优势
- **目标场景（20-100只）加速比 2x~32x**
- 数据量越大优势越明显，pandas 逐股票 resample 是 O(N×M)，DuckDB 列式聚合一次扫描

## 正确性验证

| 周期 | 预期 bar 数 | 实际 | 状态 |
|---|---|---|---|
| 5m | 48 (24早盘+24午盘) | 48 | ✓ |
| 15m | 16 (8+8) | 16 | ✓ |
| 30m | 8 (4+4) | 8 | ✓ |
| 60m | 4 (2+2) | 4 | ✓ |

- OHLC 聚合正确（open=首根, close=末根, high=max, low=min）
- 最后早盘 bar 11:29，首个午盘 bar 13:04，无跨时段聚合

## 设计文档

`docs/superpowers/specs/2026-05-28-minute-bar-resample-design.md`
