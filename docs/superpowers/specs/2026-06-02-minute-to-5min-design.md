# ETF 数据粒度调整：从 1 分钟迁移到 5 分钟

## 背景

当前系统采集 1 分钟级 K 线作为基础数据，存储于 PostgreSQL `minute_bars` 表，供策略计算使用。

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

**改动：** `get_minute_bars` 调用 `pytdx` 时将时间周期从 `1` 改为 `5`

```python
# 原来
data = api.get_security_bars(SecurityBars, 1, code, count)  # 1=1分钟

# 改为
data = api.get_security_bars(SecurityBars, 5, code, count)  # 5=5分钟
```

影响函数：
- `get_minute_bars()`
- `collect_for_pool()`

其他采集逻辑不变。

### 模块二：存储层

**文件：** `src/quant_etf/scheduler_db.py`

**改动：** `minute_bars` 表 schema 不变，继续复用。

- 表内存储的数据从 1 分钟变为 5 分钟
- 表注释需更新说明存的是 5 分钟数据
- **历史 1 分钟数据需清空，采集时重新拉取 5 分钟数据**

### 模块三：策略计算层

**文件：** `src/quant_etf/strategy.py`

**改动一：** `calculate_returns` 中 `min_bars = 61` → `13`

理由：5 分钟每小时 12 根 K 线，需要 12 根 + 1 根基准 = 13 根

```python
# 原来
min_bars = 61

# 改为
min_bars = 13
```

**改动二：** 注释更新

`b60, b20, b10, b5` 的值本身不变，但含义变了：
- `b60 = 60` 代表 **60 分钟 = 12 根 5 分钟 K 线**
- `b5 = 5` 代表 **5 分钟 = 1 根 5 分钟 K 线**

### 模块四：历史数据清理

**方式：** 脚本一次性清理

1. 清空 `minute_bars` 表的旧数据
2. 重新运行 `fill_minute_gaps` 采集 5 分钟数据

## 风险

- 清空历史数据后，策略需重新验证
- 改动后需跑一个完整交易日确认数据采集正常

## 改动文件清单

- [ ] `src/quant_etf/minute_collector.py` — 采集周期改为 5 分钟
- [ ] `src/quant_etf/scheduler_db.py` — 更新表注释
- [ ] `src/quant_etf/strategy.py` — min_bars 从 61 改为 13，注释更新
- [ ] `scripts/` — 清理旧数据脚本（SQL 或 Python）