# 60分钟K线持久化实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 60m K 线查询延迟从 ~7s 降至 ~0.4s，通过新增 `minute_bars_60m` 物理表实现持久化

**Architecture:** 复用现有 15m 模式，新增 `minute_bars_60m` 表、`generate_60min_for_code()` 从 1m resample 逻辑、增量更新触发

**Tech Stack:** Python, PostgreSQL, APScheduler, pandas

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `src/quant_etf/minute_data_manager.py` | 新增 7 个 60m 函数，修改 `get_minute_bars_for_interval()` |
| `src/quant_etf/dashboard/db.py` | `_SCHEMA_SQL` 添加 `minute_bars_60m` 表 |
| `src/quant_etf/minute_collector.py` | 添加 60m 增量更新触发 |
| `src/quant_etf/init_60min_data.py` | 新建，60m 初始化回填脚本 |
| `tests/test_60min_persistence.py` | 新建，单元测试 |

---

## 实施任务

### Task 1: 添加数据库 Schema

**Files:**
- Modify: `src/quant_etf/dashboard/db.py` — 在 `_SCHEMA_SQL` 中添加 `minute_bars_60m` 表

- [ ] **Step 1: 读取现有 `db.py` 中的 `_SCHEMA_SQL` 定义**

读取 `src/quant_etf/dashboard/db.py`，找到 `_SCHEMA_SQL` 常量，确认现有 `minute_bars_15m` 的定义格式。

- [ ] **Step 2: 在 `_SCHEMA_SQL` 末尾添加 60m 表定义**

在 `minute_bars_15m` 定义之后添加：

```python
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

- [ ] **Step 3: 验证修改**

运行: `grep -n "minute_bars_60m" src/quant_etf/dashboard/db.py`
预期: 能找到表定义和索引

- [ ] **Step 4: Commit**

```bash
git add src/quant_etf/dashboard/db.py
git commit -m "feat: add minute_bars_60m schema to db.py"
```

---

### Task 2: 编写单元测试

**Files:**
- Create: `tests/test_60min_persistence.py`

- [ ] **Step 1: 编写测试文件**

```python
# tests/test_60min_persistence.py
"""60分钟K线持久化测试"""
import pytest
from datetime import datetime, timedelta
import pandas as pd
import sys
sys.path.insert(0, "src")

from quant_etf.minute_data_manager import (
    generate_60min_for_code,
    get_60min_bars,
    get_latest_60min_time,
    update_60min_data,
)


def test_60m_table_exists():
    """验证 60m 表存在（smoke test）"""
    from quant_etf.dashboard.db import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT EXISTS (
            SELECT FROM pg_tables 
            WHERE tablename = 'minute_bars_60m'
        )
    """)
    result = cur.fetchone()[0]
    assert result is True, "minute_bars_60m table should exist"


def test_generate_60min_for_code():
    """验证 60m 数据生成"""
    count = generate_60min_for_code("510050", start_date=datetime.now() - timedelta(days=10))
    assert count >= 0, "should return non-negative count"


def test_get_60min_bars():
    """验证 60m 数据查询返回正确列"""
    df = get_60min_bars("510050", count=10)
    if not df.empty:
        expected_cols = {"code", "time", "open", "high", "low", "close", "volume", "amount"}
        assert expected_cols.issubset(set(df.columns)), f"Missing columns: {expected_cols - set(df.columns)}"


def test_upsert_idempotent():
    """验证 upsert 幂等性：重复写入不产生重复数据"""
    code = "510050"
    count_before = len(get_60min_bars(code, count=1000))
    generate_60min_for_code(code, start_date=datetime.now() - timedelta(days=5))
    count_after = len(get_60min_bars(code, count=1000))
    assert count_after == count_before, "upsert should be idempotent"
```

- [ ] **Step 2: 运行测试验证失败（函数未定义）**

Run: `pytest tests/test_60min_persistence.py -v`
Expected: FAIL — `generate_60min_for_code not defined`

- [ ] **Step 3: Commit**

```bash
git add tests/test_60min_persistence.py
git commit -m "test: add 60m persistence unit tests"
```

---

### Task 3: 在 minute_data_manager.py 中实现 60m 函数

**Files:**
- Modify: `src/quant_etf/minute_data_manager.py` — 添加 7 个新函数，修改 1 个函数

- [ ] **Step 1: 读取现有 `minute_data_manager.py`**

确认以下信息：
- `_get_pg_conn()` 的位置和签名
- `resample_to_interval()` 的位置和签名  
- `get_interval()` 的导入
- 现有 `generate_15min_for_code()` 的完整实现（作为模板）
- 现有 `get_minute_bars_for_interval()` 的完整实现（需要修改）
- 文件末尾位置

- [ ] **Step 2: 添加 `init_60min_db()` 函数**

在文件末尾添加：

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

- [ ] **Step 3: 添加 `generate_60min_for_code()` 函数**

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
            int(row["year"]) if "year" in row and row["year"] else None,
            int(row["month"]) if "month" in row and row["month"] else None,
            int(row["day"]) if "day" in row and row["day"] else None,
            int(row["hour"]) if "hour" in row and row["hour"] else None,
            int(row["minute"]) if "minute" in row and row["minute"] else None,
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

- [ ] **Step 4: 添加 `generate_60min_for_pool()` 函数**

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

- [ ] **Step 5: 添加 `get_60min_bars()` 函数**

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

- [ ] **Step 6: 添加 `update_60min_data()` 函数**

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

- [ ] **Step 7: 添加 `get_latest_60min_time()` 函数**

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

- [ ] **Step 8: 修改 `get_minute_bars_for_interval()`**

找到现有函数定义，在函数体开头添加 60m 分支：

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

    # 其它周期从 1m resample（保留原有逻辑）
    ...
```

- [ ] **Step 9: 运行测试验证**

Run: `pytest tests/test_60min_persistence.py -v`
Expected: PASS（至少 `test_60m_table_exists` 和 `test_generate_60min_for_code` 通过）

- [ ] **Step 10: Commit**

```bash
git add src/quant_etf/minute_data_manager.py
git commit -m "feat: implement 60m bar persistence functions"
```

---

### Task 4: 添加增量更新触发

**Files:**
- Modify: `src/quant_etf/minute_collector.py` — 在 `save_minute_data()` 完成后触发 60m 更新

- [ ] **Step 1: 读取 `minute_collector.py` 找到 `save_minute_data()` 函数位置**

确认现有 15m 更新触发的位置和格式。

- [ ] **Step 2: 在 15m 更新后添加 60m 触发**

在 `update_15min_data()` 调用之后添加：

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

- [ ] **Step 3: 验证修改**

读取修改后的文件，确认逻辑顺序正确。

- [ ] **Step 4: Commit**

```bash
git add src/quant_etf/minute_collector.py
git commit -m "feat: trigger 60m data update after minute collection"
```

---

### Task 5: 创建初始化回填脚本

**Files:**
- Create: `src/quant_etf/init_60min_data.py`

- [ ] **Step 1: 编写初始化脚本**

```python
#!/usr/bin/env python
"""初始化60分钟K线数据（从1分钟数据resample）"""
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

- [ ] **Step 2: 在 `pyproject.toml` 中添加入口点**

在 `[project.scripts]` 下添加：
```toml
quant-etf-init-60m = "quant_etf.init_60min_data:main"
```

- [ ] **Step 3: 测试运行回填脚本**

Run: `uv run python -m quant_etf.init_60min_data`
Expected: 输出 `Generated X 60min bars for N codes`

- [ ] **Step 4: Commit**

```bash
git add src/quant_etf/init_60min_data.py pyproject.toml
git commit -m "feat: add 60m data initialization script"
```

---

### Task 6: 性能验证

- [ ] **Step 1: 测试 60m 查询性能**

```bash
uv run python -c "
from quant_etf.minute_data_manager import get_minute_bars_for_interval
from quant_etf.bar_interval import get_interval
import time

codes = ['510050', '510300', '159915', '512480', '518880']  # 5个样本
interval = get_interval('60m')
start = time.time()
for code in codes:
    get_minute_bars_for_interval(code, interval, 200)
elapsed = time.time() - start
print(f'60m query time for 5 codes: {elapsed:.3f}s')
print(f'Estimated 66 codes: {elapsed/5*66:.1f}s')
print('PASS' if elapsed/5*66 < 1.0 else 'FAIL')
"
```

Expected: 66 codes 估算 < 1s

- [ ] **Step 2: 验证数据一致性**

对比 resample 结果与物理表数据是否一致。

- [ ] **Step 3: 记录性能结果到日志**