# 60分钟K线数据持久化设计文档

**日期**: 2026-05-28
**状态**: 待审核
**作者**: Qoder

---

## 1. 背景与问题

### 1.1 当前架构

项目分钟级数据存储策略：

| 周期 | 存储方式 | 表名 | 查询延迟 (66 codes) |
|------|---------|------|---------------------|
| 1m | 原始采集，持久化 | `minute_bars` | - |
| 15m | 预计算持久化 | `minute_bars_15m` | ~0.4s |
| 5m | 运行时 resample | 无 | ~6.4s |
| 30m | 运行时 resample | 无 | ~6.6s |
| 60m | 运行时 resample | 无 | ~6.7s |
| 1d | 日线数据 | `market_daily` | - |

### 1.2 问题陈述

60分钟周期是 Dashboard 策略执行的核心周期（默认选项），但当前采用实时 resample 方式：
- 每次查询需从 `minute_bars` 读取 ~12,000 行 1m 数据
- pandas resample 计算后返回 ~200 行 60m 数据
- 66 个代码总耗时约 **6.7 秒**
- 多周期同时查询时累计延迟明显

### 1.3 性能基准

| 测试项 | 结果 |
|--------|------|
| 60m resample (66 codes) | 6.73s |
| 15m resample (66 codes) | 6.67s |
| 15m 直接查表 (5 codes) | 0.42s (84ms/code) |
| resample vs 查表 速度比 | 查表快 1.5x |
| 瓶颈分布 | ~70% PG 查询, ~30% pandas resample |

---

## 2. 设计目标

1. **核心目标**: 将 60m 策略查询延迟从 ~7s 降至 ~0.4s
2. **存储约束**: 新增数据量不超过现有 `minute_bars` 的 15%
3. **开发成本**: 复用现有 15m 模式，代码量控制在 200 行内
4. **数据一致性**: upsert 幂等写入，无破坏性变更
5. **向后兼容**: 不改变现有 API 接口

---

## 3. 方案设计

### 3.1 架构概览

```
┌─────────────────────────────────────────────────┐
│                  数据采集层                       │
│              TDX → minute_bars (1m)              │
└──────────────────┬──────────────────────────────┘
                   │ 触发 resample
        ┌──────────┼──────────┐
        ▼          ▼          ▼
   minute_bars_15m  │   minute_bars_60m (新增)
   (已有)           │   (新增)
                    │
        5m/30m: 运行时从 minute_bars resample
```

### 3.2 存储策略变更

| 周期 | 变更前 | 变更后 |
|------|--------|--------|
| 1m | 物理表 `minute_bars` | 不变 |
| 15m | 物理表 `minute_bars_15m` | 不变 |
| **60m** | **实时 resample** | **物理表 `minute_bars_60m`** |
| 5m | 实时 resample | 不变 |
| 30m | 实时 resample | 不变 |
| 1d | 物理表 `market_daily` | 不变 |

### 3.3 数据库 Schema 变更

#### 3.3.1 新增表 `minute_bars_60m`

```sql
CREATE TABLE IF NOT EXISTS minute_bars_60m (
    code        VARCHAR(20) NOT NULL,
    time        TIMESTAMP NOT NULL,
    open        NUMERIC(18, 4),
    high        NUMERIC(18, 4),
    low         NUMERIC(18, 4),
    close       NUMERIC(18, 4),
    volume      BIGINT,
    amount      NUMERIC(18, 2),
    year        INTEGER,
    month       INTEGER,
    day         INTEGER,
    hour        INTEGER,
    minute      INTEGER,
    PRIMARY KEY (code, time)
);
CREATE INDEX IF NOT EXISTS idx_minute_60m_code ON minute_bars_60m(code);
CREATE INDEX IF NOT EXISTS idx_minute_60m_time ON minute_bars_60m(time DESC);
```

**设计说明**:
- 字段与 `minute_bars_15m` 完全相同
- 主键 `(code, time)` 保证唯一性，支持 upsert
- 索引 `idx_minute_60m_code` 优化按代码查询
- 索引 `idx_minute_60m_time` 优化按时间排序查询

#### 3.3.2 Schema 文件位置

- `src/quant_etf/dashboard/db.py` 的 `_SCHEMA_SQL` 常量中添加
- `src/quant_etf/minute_data_manager.py` 的 `init_15min_db()` 模式新增 `init_60min_db()`

### 3.4 代码变更

#### 3.4.1 新增文件：`init_60min_data.py`

**位置**: `src/quant_etf/init_60min_data.py`

**功能**: 初始化回填 60m 历史数据

```python
#!/usr/bin/env python
"""初始化 60 分钟 K 线数据（从 1 分钟数据 resample）"""

from quant_etf.minute_data_manager import (
    init_60min_db,
    generate_60min_for_pool,
)
from quant_etf.conf import ALL_POOL

def main():
    conn = init_60min_db()
    total = generate_60min_for_pool(ALL_POOL)
    print(f"Generated {total} 60min bars for {len(ALL_POOL)} codes")
    conn.close()

if __name__ == "__main__":
    main()
```

#### 3.4.2 修改文件：`minute_data_manager.py`

**变更 1**: 新增 `init_60min_db()` 函数

```python
def init_60min_db():
    """初始化60分钟数据数据库"""
    conn = _get_pg_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS minute_bars_60m (
            code        VARCHAR(20) NOT NULL,
            time        TIMESTAMP NOT NULL,
            open        NUMERIC(18, 4),
            high        NUMERIC(18, 4),
            low         NUMERIC(18, 4),
            close       NUMERIC(18, 4),
            volume      BIGINT,
            amount      NUMERIC(18, 2),
            year        INTEGER,
            month       INTEGER,
            day         INTEGER,
            hour        INTEGER,
            minute      INTEGER,
            PRIMARY KEY (code, time)
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_minute_60m_code ON minute_bars_60m(code)
    """)
    conn.commit()
    logger.info("Ensured minute_bars_60m table exists")
    return conn
```

**变更 2**: 新增 `generate_60min_for_code()` 函数

```python
def generate_60min_for_code(code: str, start_date: Optional[datetime] = None) -> int:
    """
    为单个代码生成60分钟K线数据
    :param code: ETF代码
    :param start_date: 开始日期，如果为None则生成全部
    :return: 生成的记录数
    """
    from quant_etf.minute_collector import query_minute_data

    if start_date:
        df_1m = query_minute_data(code, start=start_date)
    else:
        df_1m = query_minute_data(code)

    if df_1m.empty:
        logger.warning(f"No 1min data found for {code}")
        return 0

    df_60m = resample_to_interval(df_1m, get_interval("60m"))

    if df_60m.empty:
        return 0

    conn = _get_pg_conn()
    cur = conn.cursor()

    data = []
    for _, row in df_60m.iterrows():
        data.append((
            code,
            row["time"],
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            int(row["volume"]) if row["volume"] else 0,
            float(row["amount"]) if row["amount"] else 0.0,
            int(row["year"]) if row["year"] else None,
            int(row["month"]) if row["month"] else None,
            int(row["day"]) if row["day"] else None,
            int(row["hour"]) if row["hour"] else None,
            int(row["minute"]) if row["minute"] else None,
        ))

    cur.executemany("""
        INSERT INTO minute_bars_60m (code, time, open, high, low, close, volume, amount, year, month, day, hour, minute)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (code, time) DO UPDATE SET
            open = EXCLUDED.open,
            high = EXCLUDED.high,
            low = EXCLUDED.low,
            close = EXCLUDED.close,
            volume = EXCLUDED.volume,
            amount = EXCLUDED.amount
    """, data)
    conn.commit()

    logger.debug(f"Generated {len(data)} 60min bars for {code}")
    return len(data)
```

**变更 3**: 新增 `generate_60min_for_pool()` 函数

```python
def generate_60min_for_pool(
    codes: List[str], start_date: Optional[datetime] = None
) -> int:
    """
    为ETF池生成60分钟K线数据
    :param codes: ETF代码列表
    :param start_date: 开始日期
    :return: 总记录数
    """
    total = 0
    for code in codes:
        try:
            count = generate_60min_for_code(code, start_date)
            total += count
        except Exception as e:
            logger.error(f"Failed to generate 60min data for {code}: {e}")
    return total
```

**变更 4**: 新增 `get_60min_bars()` 函数

```python
def get_60min_bars(
    code: str, count: int = 200, end_time: Optional[datetime] = None
) -> pd.DataFrame:
    """
    获取单个代码的60分钟K线数据
    :param code: ETF代码
    :param count: 获取数量
    :param end_time: 结束时间，默认为最新
    :return: DataFrame
    """
    conn = _get_pg_conn()
    cur = conn.cursor()

    if end_time:
        cur.execute("""
            SELECT * FROM minute_bars_60m
            WHERE code = %s AND time <= %s
            ORDER BY time DESC
            LIMIT %s
        """, [code, end_time, count])
    else:
        cur.execute("""
            SELECT * FROM minute_bars_60m
            WHERE code = %s
            ORDER BY time DESC
            LIMIT %s
        """, [code, count])

    rows = cur.fetchall()
    if not rows:
        return pd.DataFrame()

    columns = [desc[0] for desc in cur.description]
    df = pd.DataFrame(rows, columns=columns)
    # PostgreSQL NUMERIC → float
    for c in ("open", "high", "low", "close", "amount"):
        if c in df.columns:
            df[c] = df[c].astype(float)
    if "volume" in df.columns:
        df["volume"] = df["volume"].astype(int)
    df = df.sort_values("time").reset_index(drop=True)
    return df
```

**变更 5**: 新增 `update_60min_data()` 函数

```python
def update_60min_data(code: str) -> int:
    """
    更新单个代码的60分钟数据（从1分钟重新计算）
    :param code: ETF代码
    :return: 更新数量
    """
    from quant_etf.minute_collector import get_latest_minute_time

    last_1m = get_latest_minute_time(code)
    if not last_1m:
        return generate_60min_for_code(code)

    conn = _get_pg_conn()
    cur = conn.cursor()
    cur.execute("SELECT MAX(time) FROM minute_bars_60m WHERE code = %s", [code])
    result = cur.fetchone()
    last_60m = result[0] if result and result[0] else None

    if not last_60m:
        return generate_60min_for_code(code)

    start_date = last_60m - timedelta(days=1)
    return generate_60min_for_code(code, start_date)
```

**变更 6**: 新增 `get_latest_60min_time()` 函数

```python
def get_latest_60min_time(code: str) -> Optional[datetime]:
    """
    获取指定代码最新的60分钟K线时间
    :param code: ETF代码
    :return: 最新时间
    """
    conn = _get_pg_conn()
    cur = conn.cursor()
    cur.execute("SELECT MAX(time) FROM minute_bars_60m WHERE code = %s", [code])
    result = cur.fetchone()
    return result[0] if result and result[0] else None
```

**变更 7**: 修改 `get_minute_bars_for_interval()` 函数

```python
def get_minute_bars_for_interval(
    code: str, interval: BarInterval, count: int = 200
) -> pd.DataFrame:
    """
    获取指定代码的任意周期K线数据
    - 60m/15m: 从物理表直接读取
    - 其它周期: 从1分钟数据实时重采样
    """
    if interval.is_daily:
        raise ValueError("get_minute_bars_for_interval does not support daily interval")

    # 60m/15m 有物理表，直接查询
    if interval.name == "60m":
        return get_60min_bars(code, count)
    elif interval.name == "15m":
        return get_15min_bars(code, count)

    # 其它周期从 1m resample
    conn = _get_pg_conn()
    cur = conn.cursor()

    minutes_per_bar = 240 // interval.bars_per_day
    fetch_count = count * minutes_per_bar + 240

    cur.execute("""
        SELECT time, open, high, low, close, volume, amount
        FROM minute_bars
        WHERE code = %s
        ORDER BY time DESC
        LIMIT %s
    """, [code, fetch_count])

    rows = cur.fetchall()
    if not rows:
        return pd.DataFrame()

    columns = [desc[0] for desc in cur.description]
    df_1m = pd.DataFrame(rows, columns=columns)
    for c in ("open", "high", "low", "close", "amount"):
        if c in df_1m.columns:
            df_1m[c] = df_1m[c].astype(float)
    if "volume" in df_1m.columns:
        df_1m["volume"] = df_1m["volume"].astype(int)
    df_1m = df_1m.sort_values("time").reset_index(drop=True)

    df = resample_to_interval(df_1m, interval)

    if df.empty:
        return pd.DataFrame()

    return df
```

#### 3.4.3 修改文件：`dashboard/db.py`

在 `_SCHEMA_SQL` 常量中添加 `minute_bars_60m` 表定义：

```sql
-- 60分钟K线
CREATE TABLE IF NOT EXISTS minute_bars_60m (
    code        VARCHAR(20) NOT NULL,
    time        TIMESTAMP NOT NULL,
    open        NUMERIC(18, 4),
    high        NUMERIC(18, 4),
    low         NUMERIC(18, 4),
    close       NUMERIC(18, 4),
    volume      BIGINT,
    amount      NUMERIC(18, 2),
    year        INTEGER,
    month       INTEGER,
    day         INTEGER,
    hour        INTEGER,
    minute      INTEGER,
    PRIMARY KEY (code, time)
);
CREATE INDEX IF NOT EXISTS idx_minute_60m_code ON minute_bars_60m(code);
CREATE INDEX IF NOT EXISTS idx_minute_60m_time ON minute_bars_60m(time DESC);
```

#### 3.4.4 修改文件：`minute_collector.py`

在 `save_minute_data()` 完成后，触发 60m 增量更新（复用现有 15m 模式）：

```python
# 现有 15m 更新逻辑（保留）
from quant_etf.minute_data_manager import update_15min_data
for code in codes:
    update_15min_data(code)

# 新增 60m 更新逻辑
from quant_etf.minute_data_manager import update_60min_data
for code in codes:
    update_60min_data(code)
```

### 3.5 数据流

```
[数据采集] TDX → minute_bars (1m)
                │
                ├──→ 触发 update_15min_data() → minute_bars_15m
                │
                └──→ 触发 update_60min_data() → minute_bars_60m

[数据查询]
  - 60m 策略 → get_minute_bars_for_interval(code, 60m)
              → get_60min_bars() → SELECT FROM minute_bars_60m
  - 15m 策略 → get_minute_bars_for_interval(code, 15m)
              → get_15min_bars() → SELECT FROM minute_bars_15m
  - 5m/30m   → get_minute_bars_for_interval(code, 5m/30m)
              → SELECT FROM minute_bars → resample_to_interval()
```

---

## 4. 存储估算

### 4.1 数据量估算

假设每个 code 有 ~23,000 行 1m 数据（约 96 个交易日 × 240 分钟/日）：

| 表 | 行数/code | 66 codes 总行数 | 占比 |
|----|-----------|----------------|------|
| `minute_bars` (1m) | ~23,000 | ~1,518,000 | 78.5% |
| `minute_bars_15m` (15m) | ~4,000 | ~264,000 | 13.6% |
| `minute_bars_60m` (60m) | ~2,300 | ~151,800 | 7.9% |
| **总计** | | **~1,933,800** | **100%** |

**新增存储**: 仅 ~151,800 行，占现有 `minute_bars` 的 **10%**

### 4.2 磁盘空间

假设每行平均 100 字节（含索引开销）：
- `minute_bars_60m`: ~15 MB
- 总存储: ~193 MB

---

## 5. 写入策略

### 5.1 初始回填

```bash
uv run python -m quant_etf.init_60min_data
```

- 遍历 ALL_POOL 中 66 个代码
- 从 `minute_bars` 读取全部 1m 数据
- resample 为 60m 后 upsert 到 `minute_bars_60m`
- 预计耗时: ~10-15 秒

### 5.2 增量更新

**触发时机**:
- `minute_collector.save_minute_data()` 完成后自动触发
- 手动调用 `update_60min_data(code)`

**更新策略**:
1. 获取 `minute_bars` 最新时间 `last_1m`
2. 获取 `minute_bars_60m` 最新时间 `last_60m`
3. 如果 `last_60m` 为空，全量生成
4. 否则从 `last_60m - 1天` 开始重新计算（覆盖最后一天处理不完整 bar）
5. upsert 写入，幂等安全

**更新范围计算**:
```python
start_date = last_60m - timedelta(days=1)
df_1m = query_minute_data(code, start=start_date)
df_60m = resample_to_interval(df_1m, get_interval("60m"))
# upsert to minute_bars_60m
```

---

## 6. 性能预期

### 6.1 查询性能

| 场景 | 当前 | 方案 C | 提升 |
|------|------|--------|------|
| 60m 策略加载 (66 codes) | ~6.7s | ~0.4s | **17x** |
| 15m 策略加载 | ~0.4s | ~0.4s | 不变 |
| 5m 查询 | ~6.4s | ~6.4s | 不变 |
| 30m 查询 | ~6.6s | ~6.6s | 不变 |

### 6.2 写入性能

| 操作 | 耗时 | 说明 |
|------|------|------|
| 1m 数据采集 (66 codes) | ~5s | 现有 |
| + 15m 增量更新 | ~2s | 现有 |
| + 60m 增量更新 | ~2s | 新增 |
| **总写入延迟** | **~9s** | 增加 ~2s |

---

## 7. 测试计划

### 7.1 单元测试

1. `test_generate_60min_for_code()` — 验证 60m 数据生成正确性
2. `test_get_60min_bars()` — 验证 60m 数据查询正确性
3. `test_update_60min_data()` — 验证 60m 增量更新正确性

### 7.2 集成测试

1. **数据一致性测试**: 对比 resample 结果与物理表数据是否一致
2. **性能测试**: 66 codes 60m 查询延迟 < 1s
3. **幂等测试**: 重复调用 `generate_60min_for_code()` 不产生重复数据

### 7.3 验收标准

- [ ] `minute_bars_60m` 表创建成功
- [ ] 66 个代码 60m 数据回填完成（~151,800 行）
- [ ] `get_minute_bars_for_interval(code, 60m)` 返回正确数据
- [ ] 60m 策略执行延迟 < 1s
- [ ] 增量更新正常工作（覆盖最后一天）
- [ ] 无 FutureWarning 或 TypeError

---

## 8. 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 60m 数据与 1m 不一致 | 低 | 中 | upsert 幂等，resample 从 1m 实时计算 |
| 写入延迟增加 | 低 | 低 | 增量更新仅覆盖最后 1 天 |
| 存储空间不足 | 极低 | 低 | 新增仅 ~15MB |
| API 兼容性破坏 | 无 | - | 不改接口，仅改内部实现 |

---

## 9. 扩展性

未来可按需增加 5m/30m 物理表，升级为方案 B（全量持久化）：
- 复制 `generate_60min_for_code()` 模式创建 `generate_5min_for_code()` / `generate_30min_for_code()`
- 在 `get_minute_bars_for_interval()` 中增加路由判断

---

## 10. 变更文件清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `src/quant_etf/dashboard/db.py` | 修改 | `_SCHEMA_SQL` 添加 `minute_bars_60m` |
| `src/quant_etf/minute_data_manager.py` | 修改 | 新增 7 个函数，修改 1 个函数 |
| `src/quant_etf/minute_collector.py` | 修改 | 添加 60m 增量更新触发 |
| `src/quant_etf/init_60min_data.py` | **新增** | 60m 初始化回填脚本 |

---

## 11. 附录

### 11.1 性能基准测试命令

```bash
# 测试 60m resample 性能（当前）
uv run python -c "
from quant_etf.minute_data_manager import get_minute_bars_for_interval
from quant_etf.bar_interval import get_interval
import time

codes = ['510050', '510300', ...]  # 66 codes
interval = get_interval('60m')
start = time.time()
for code in codes:
    get_minute_bars_for_interval(code, interval, 200)
print(f'60m query time: {time.time()-start:.2f}s')
"

# 测试 60m 物理表性能（实施后）
# 相同代码，预期 ~0.4s
```

### 11.2 相关文档

- `docs/superpowers/specs/2026-05-27-dashboard-multi-period-design.md` — Dashboard 多周期支持改造
- `src/quant_etf/minute_data_manager.py` — 现有 15m 实现参考
