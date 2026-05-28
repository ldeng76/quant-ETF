# 分钟K线数据智能补全与审计设计

## 背景

分钟级K线数据存储在 PostgreSQL `minute_bars` 表中，由 `minute-collect` 实时采集器和 `minute-backfill` 手动回填命令维护。但存在以下痛点：

1. **不知道从哪开始补**：人工无法判断数据库中每个代码的最新时间戳，难以构造正确的 `minute-backfill --start/--end` 参数
2. **无法检测中间空洞**：现有 `minute-backfill` 只做范围批量拉取 + upsert，无法发现某天中间的缺失
3. **Dashboard 启动无自愈能力**：采集器中断后，重启 dashboard 不会自动补全缺失数据

## 设计目标

- **CLI 命令 `minute-fill`**：基于最新时间戳的智能增量补全，零参数即可补到最新
- **CLI 命令 `minute-audit`**：基于交易日历的缺失检测审计，可选自动修复
- **Dashboard 启动集成**：启动时自动触发增量补全

## 方案选择

最终选择**混合方案**（方案 C）：

| 方案 | 思路 | 优点 | 缺点 |
|------|------|------|------|
| A: 尾部增量 | 基于最新时间戳向后拉取 | 简单可靠 | 无法检测中间空洞 |
| B: 逐分钟检测 | 交易日历逐根K线对比 | 精确 | pytdx API 不友好，计算量大 |
| **C: 混合（选定）** | A 做日常 + B 做审计 | 兼顾效率和完整性 | 两个命令需维护 |

## 模块结构

```
src/quant_etf/
├── minute_fill.py              # 新增：智能补全 + 审计
├── minute_collector.py         # 不改动：pytdx 获取 + PG 存储
├── cli.py                      # 修改：注册 minute-fill / minute-audit
└── dashboard/
    └── services/
        └── startup_preload.py  # 修改：启动时调用 ensure_minute_data_ready()
```

## 详细设计

### 1. `minute-fill` 命令

**用法**：`uv run quant-etf minute-fill [--pool etf|stock|all] [--days 60] [--codes 510050,159949]`

**流程**：

```
对每个 code:
  1. 查询 PG: SELECT MAX(time) FROM minute_bars WHERE code = ?
  2. 如果无数据 → 设为 (today - days*2) 作为起点
  3. 计算 bars_to_fetch = ceil(days_gap + 1) * 250，向上取整到 800 的倍数
  4. 调用 get_minute_bars(code, count=bars_to_fetch)
  5. 过滤: 保留 time > latest_time 的记录
  6. save_minute_data_from_dicts(code, filtered_bars)
  7. 跳过失败代码，记录失败原因
```

**估算公式说明**：
- A 股每天约 240 根 1 分钟 K 线（09:30-11:30 + 13:00-15:00）
- 用 250 根/天作为安全余量
- 向上取整到 800 的倍数是因为 pytdx 每次最多返回 800 根，这样分页对齐

**`--pool` 参数映射**：
- `etf` → `ETF_POOL`（默认）
- `stock` → `STOCK_POOL`
- `all` → `ALL_POOL`

**报告输出**：
```
补全完成: 60/62 成功, 2 失败
  补入数据: 15,230 条
  失败代码: 159352 (无数据), 518880 (连接失败)
```

### 2. `minute-audit` 命令

**用法**：`uv run quant-etf minute-audit [--pool etf|stock|all] [--days 60] [--codes ...] [--fix]`

**流程**：

```
1. 获取最近 --days 个交易日列表 (via trading_day 模块)
2. 对每个 code:
   a. 查询 PG: SELECT date(time), COUNT(*) FROM minute_bars
      WHERE code = ? AND time >= start_date GROUP BY date(time)
   b. 缺失判断：交易日在已有日期中不存在 → 缺失；已有但 count < 100 → 部分缺失
   c. 如果 --fix 且有缺失:
      - 计算缺失日期范围 [min_missing, max_missing]
      - bars = ceil((today - min_missing).days / 7 * 5 + 1) * 250，向上取整到 800 的倍数
      - 调用 get_minute_bars(code, count=bars)
      - 过滤只保留缺失日期范围内的记录
      - save_minute_data_from_dicts(code, filtered) 执行 upsert
3. 输出缺失报告
```

**审计粒度**：日期级别（不做分钟级别）。
- 原因：信息量适中，且 pytdx 的分页拉取 API 不适合精确到分钟的段拉取
- "缺失"定义：某交易日中该代码无数据 → 缺失；有数据但 < 100 根 → 部分缺失（也算缺失）

**报告输出**：
```
审计范围: 2025-03-28 ~ 2025-05-28 (42 个交易日)

代码     缺失天数  状态
510050   0        完整
159352   3        2025-04-15, 2025-04-16, 2025-04-17
518880   42       全部缺失

汇总: 2/62 代码有缺失, 共 45 个代码天
```

### 3. Dashboard 启动集成

在 `startup_preload.py` 中添加后台线程调用 `ensure_minute_data_ready()`：

- 仅在 `IS_PRIMARY` 时执行
- 仅补全 ETF_POOL（dashboard 只用 ETF 数据）
- 失败不阻塞 dashboard 启动，只记录日志
- 复用 `minute_fill.py` 中的 `fill_minute_gaps()` 函数

### 4. `minute_fill.py` 核心函数

| 函数 | 职责 |
|------|------|
| `fill_minute_gaps(codes, max_days=60)` | 增量补全主入口，返回统计结果 |
| `audit_minute_gaps(codes, max_days=60, fix=False)` | 审计 + 可选修复 |
| `ensure_minute_data_ready()` | Dashboard 启动入口，调用 fill |
| `_calc_bars_to_fetch(latest_time, now, max_days)` | 估算需要拉取的K线数量 |
| `_detect_missing_dates(code, trading_dates)` | 对比交易日历返回缺失日期 |
| `_get_pool_codes(pool_name)` | 根据 pool 名获取对应代码列表 |

### 5. 与现有命令的关系

| 现有命令 | 关系 |
|---------|------|
| `minute-backfill` | 保留不删除（向后兼容），`minute-fill` 是其智能版替代 |
| `minute-collect` | 不受影响，实时采集器继续运行 |
| `clean-minute-data` | 不受影响 |

### 6. 容错策略

- 单个代码拉取失败不影响其他代码
- pytdx 连接失败：复用现有的服务器发现和冷却机制（`_server_failures` + `SERVER_COOLDOWN`）
- 无数据代码：记录为失败，报告汇总
- Dashboard 启动补全失败：仅日志警告，不抛异常

### 7. 交易日历

复用项目已有的 `trading_day` 模块：
- `get_trading_dates_between(start, end)` 获取交易日列表
- 节假日自动排除（该模块已实现）

## 不做的事

- 不做分钟级精确空洞检测（信息过载，pytdx API 不适合）
- 不修改现有 `minute-backfill` 命令（保持向后兼容）
- 不在 dashboard 中添加专门的 UI 页面（仅后台自动补全）
- 不引入新的外部依赖
