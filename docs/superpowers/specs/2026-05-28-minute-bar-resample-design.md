# DuckDB 内存聚合：分钟K线动态重采样

## 背景

`minute_bars` 表存储1分钟K线数据。5m/15m/30m/60m 需要基于它动态生成。
当前实现 `resample_to_interval` 使用 pandas 时间重采样，存在两个问题：
1. 每次请求拉大量数据到 Python 内存做聚合，批量场景（20-100只股票）性能差
2. 不处理 A 股交易时段边界（午休 11:30-13:00），跨时段聚合结果错误

## 方案

DuckDB 作为纯计算引擎（不持久化），从 PG 批量拉取1分钟数据，用 SQL 聚合。

### 交易时段分组键算法

A 股每天两个 session：
- 早盘 09:30-11:30（120 分钟）
- 午盘 13:00-15:00（120 分钟）

每根 bar 计算 `bar_seq`（session 内从 0 开始的序号），`group_key = bar_seq // N`（N=5/15/30/60）。

```sql
WITH ordered AS (
  SELECT *,
    CASE WHEN EXTRACT(HOUR FROM time) < 12 THEN 0 ELSE 1 END AS session,
    ROW_NUMBER() OVER (PARTITION BY code, DATE(time), session ORDER BY time) - 1 AS bar_seq
  FROM minute_bars_1m
),
grouped AS (
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
  GROUP BY code, DATE(time), session, bar_seq / {interval_minutes}
)
SELECT * FROM grouped ORDER BY code, time;
```

### 边界处理

- 午休断裂：session 变化时分组键自然断裂，不跨午休聚合
- 不完整 bar（如收盘前最后一根不足5分钟）：按实际数据聚合，不补零
- 集合竞价数据（09:25-09:30）：归入早盘 session=0 开头

### 数据流

```
PG minute_bars → 批量拉取(单次SQL) → DuckDB 内存表 → SQL聚合 → DataFrame
```

### 拉取策略

单股票：`WHERE code = %s ORDER BY time DESC LIMIT %s`
多股票批量：`WHERE code = ANY(%s) AND time >= %s ORDER BY code, time`

### DuckDB 连接管理

模块级惰性单例 `_get_duckdb_conn()`，每次查询 `conn.register('minute_bars_1m', df_1m)` 注册临时视图，无持久化。

### 接口

新模块 `src/quant_etf/minute_resampler.py`：

```python
def resample_bars(code: str, interval: BarInterval, count: int = 200) -> pd.DataFrame
def resample_bars_batch(codes: list[str], interval: BarInterval, start_time: datetime | None = None) -> dict[str, pd.DataFrame]
```

### 替换策略

- `data_source._load_minute_data_resampled` 改为调用 `resample_bars`
- `minute_data_manager.get_minute_bars_for_interval` 标记 deprecated，内部转发到新模块
- `minute_data_manager.resample_to_interval` 标记 deprecated
- 保留旧函数签名，不破坏现有调用链
- 已有 `minute_bars_15m` 表不删除，不再主动写入

### 性能预期

100 只 × 5000 分钟 ≈ 50 万行，DuckDB 列式聚合 <100ms。PG 批量拉取 + 类型转换约 200-500ms。端到端单次批量查询 <1s。

### 使用场景

策略回测/信号生成，批量查询 20-100 只股票，几十天到几个月时间范围。
