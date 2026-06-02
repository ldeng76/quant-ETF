# ETF 数据粒度调整：从 1 分钟迁移到 5 分钟

## 背景

当前系统通过 `minute_collector.py` 采集 1 分钟级 K 线（pytdx `category=8`），存储于 PostgreSQL `minute_bars` 表，供策略计算使用。

## 目标

将数据粒度从 1 分钟调整为 5 分钟，实现：
- **减容**：5 分钟数据量约为 1 分钟的 1/5
- **降频**：数据更新频率降低，存储压力减小
- **增效**：数据量减少后回测和计算更快

## 约束

- 纯 5 分钟方案：彻底放弃 1 分钟数据，不保留双轨
- 一步到位：不分步验证，一次性切换

## 改动范围

### 模块一：数据采集层

**文件：** `src/quant_etf/minute_collector.py`

**改动：** `_fetch_bars_paginated` 中 pytdx `get_security_bars` 的 `category` 参数从 `8`（1分钟，通达信扩展）改为 `0`（5分钟）。

> pytdx category 映射（非直观编号，不可按数字类推）：
> - `category=8` → 1 分钟线（通达信扩展接口）
> - `category=0` → 5 分钟线
> - `category=1` → 15 分钟线
> - `category=5` → **周线**（非5分钟！）

```python
# 原来（_fetch_bars_paginated 函数，约第 146 行）
data = api.get_security_bars(
    category=8, market=market, code=code, start=fetched, count=batch_size
)

# 改为
data = api.get_security_bars(
    category=0, market=market, code=code, start=fetched, count=batch_size
)
```

影响函数：
- `_fetch_bars_paginated()` — 直接改动点
- `get_minute_bars()` — 调用 `_fetch_bars_paginated`，无需改动
- `collect_for_pool()` — 调用 `get_minute_bars`，无需改动

其他采集逻辑不变。

### 模块二：重采样层（关键！）

**文件：** `src/quant_etf/minute_resampler.py`

**问题：** 整个 resampler 假设 `minute_bars` 表存的是 **1 分钟** 数据。切到 5 分钟后，聚合逻辑全部错误。

**数据流：** 策略引擎在非日线周期（60m/30m/15m/5m）下，通过 `data_source._load_minute_data_resampled_batch` → `minute_resampler.resample_bars_batch` → 读 `minute_bars` 表 → DuckDB 聚合。这是 `minute_bars` 表的实际消费方。

**改动一：** 聚合分组公式

当前 `_AGG_SQL` 中 `bar_seq // {interval_minutes}` 假设 `bar_seq` 每递增 1 = 1 分钟。改为 5 分钟基数据后：

| 目标周期 | 当前分组 (1m基) | 正确分组 (5m基) |
|----------|-----------------|-----------------|
| 5m | `// 5` (每5根) | `// 1` (直接透传) |
| 15m | `// 15` (每15根) | `// 3` (每3根) |
| 30m | `// 30` (每30根) | `// 6` (每6根) |
| 60m | `// 60` (每60根) | `// 12` (每12根) |

公式变化：`interval_minutes` → `interval_minutes // 5`（即 `bars_to_group = minutes_per_bar // 5`）

```python
# 原来（约第 184 行）
minutes_per_bar = 240 // interval.bars_per_day

# 改为
BASE_BAR_MINUTES = 5  # 基数据粒度
minutes_per_bar = 240 // interval.bars_per_day
bars_to_group = minutes_per_bar // BASE_BAR_MINUTES  # 每组多少根5分钟K线
```

聚合 SQL 中 `bar_seq // {interval_minutes}` 改为 `bar_seq // {bars_to_group}`。

**改动二：** fetch 缓冲量

`_fetch_1m_single` 中 `fetch_count = count * minutes_per_bar + 240`，240 是 1 分钟数据 1 天的量。改为 5 分钟后，1 天 = 48 根：

```python
# 原来
fetch_count = count * minutes_per_bar + 240

# 改为
BARS_PER_DAY = 48  # 5分钟基数据每天48根
fetch_count = count * bars_to_group + BARS_PER_DAY
```

**改动三：** 函数/变量重命名

- `_fetch_1m_single` → `_fetch_5m_single`（语义对齐）
- `_fetch_1m_batch` → `_fetch_5m_batch`
- `_AGG_SQL` 中 `minute_bars_1m` → `minute_bars_5m`
- 模块文档字符串更新

**改动四：** `5m` 周期特殊处理

当 `bars_to_group == 1`（即目标周期 = 基数据粒度 5m）时，无需聚合，直接从 PG 读取返回，跳过 DuckDB 聚合步骤。

### 模块三：存储层

**文件：** `src/quant_etf/minute_collector.py`（`init_minute_db` 函数，约第 264 行）

**改动：** `minute_bars` 表 schema 不变，继续复用。

- 表内存储的数据从 1 分钟变为 5 分钟
- 可在建表 SQL 中添加 `COMMENT` 说明存的是 5 分钟数据
- **历史 1 分钟数据需清空，采集时重新拉取 5 分钟数据**

### 模块四：策略计算层

**文件：** `src/quant_etf/strategy.py`

**核心问题：** `calculate_returns` 方法中 `b60, b20, b10, b5 = 60, 20, 10, 5` 表示**回看的 K 线根数**，不是分钟数。当前系统以日线为主（`DEFAULT_INTERVAL = "1d"`），所以 b60 = 回看 60 个交易日 ≈ 3 个月。

切换到 5 分钟数据后，每天 48 根 K 线（见 `bar_interval.py` 中 `5m` 的 `bars_per_day=48`），回看 60 天需要 `60 × 48 = 2880` 根 K 线。

**改动：** 将 `calculate_returns` 改为使用 `bars_for_days()` 函数（与 `calculate_short_term_stock_score` 和 `calculate_rebound_stock_score` 保持一致），而非硬编码数值。

```python
# 原来（calculate_returns 方法，约第 73-79 行）
min_bars = 61
...
b60, b20, b10, b5 = 60, 20, 10, 5

# 改为：引入 bar_interval 参数，使用 bars_for_days 计算
min_bars = bars_for_days(60, self._bar_interval) + 1
...
b60 = bars_for_days(60, self._bar_interval)
b20 = bars_for_days(20, self._bar_interval)
b10 = bars_for_days(10, self._bar_interval)
b5  = bars_for_days(5, self._bar_interval)
```

> 注意：`calculate_returns` 被 `rank_etfs`、`calculate_short_term_stock_score`、
> `calculate_rebound_stock_score` 三个方法调用，改动影响面广，需确保所有调用方
> 传入正确的 `bar_interval`。

**注释更新：**
- `b60` 代表 **回看 60 个交易日**（5 分钟数据下 = 2880 根 K 线）
- `b5` 代表 **回看 5 个交易日**（5 分钟数据下 = 240 根 K 线）

### 模块五：历史数据清理

**方式：** 脚本一次性清理

1. 清空 `minute_bars` 表的旧数据（`TRUNCATE TABLE minute_bars`）
2. 重新运行 `minute_fill` 命令（`src/quant_etf/minute_fill.py` 中的 `fill_minute_gaps`）采集 5 分钟数据

## 风险

- 清空历史数据后，策略需重新验证
- 改动后需跑一个完整交易日确认数据采集正常
- `calculate_returns` 被多处复用，需确保所有策略类型（ETF 排名、短线股票、中期反弹）在新粒度下逻辑正确
- `minute_resampler` 聚合逻辑变更影响所有非日线周期策略（60m/30m/15m/5m），需回归测试

## 改动文件清单

- [x] `src/quant_etf/minute_collector.py` — `_fetch_bars_paginated` 中 category 从 8 改为 0；更新注释
- [x] `src/quant_etf/minute_resampler.py` — 聚合公式、缓冲量、变量重命名、5m直通优化
- [x] `src/quant_etf/strategy.py` — `calculate_returns` 改用 `bars_for_days()` 替代硬编码
- [x] `src/quant_etf/bar_interval.py` — 5m 周期 tdx_category 从 8 改为 0
- [x] `src/quant_etf/data_source.py` — 注释更新
- [x] `src/quant_etf/minute_data_manager.py` — 注释更新
- [x] `src/quant_etf/market_analyzer.py` — 注释更新
- [x] `src/quant_etf/monitor.py` — 注释更新
- [ ] `scripts/` — 清理旧数据脚本（`TRUNCATE TABLE minute_bars`）