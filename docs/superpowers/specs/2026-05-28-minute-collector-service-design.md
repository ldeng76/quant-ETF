# 分钟K线交易时段自动采集服务设计

## 背景

现有 `ensure_minute_data_ready()` 在 Dashboard 启动时执行**一次性补全**，基于 PG 最新时间戳拉取缺失数据。但交易时段内会产生新的分钟K线数据，需要**持续采集**以保持数据新鲜度。

用户需求：在交易时段及其前后 5 分钟窗口内，每分钟自动采集 ETF_POOL 的分钟K线数据。

## 目标

1. Dashboard 启动时，若在触发窗口内则立即开始采集循环
2. 若不在触发窗口，启动定时器等待下一窗口
3. 采集循环每 60 秒执行一次，采集 50 条最新 K 线
4. 遵循 `IS_PRIMARY` 多节点机制，避免重复采集
5. 与现有 `startup_preload.py` 架构无缝集成

## 模块结构

新增 `src/quant_etf/dashboard/services/minute_collector_service.py`：

```
minute_collector_service.py
├── TradingWindow          # 交易时段窗口定义与判断
├── MinuteCollectorService # 采集服务主类
│   ├── start()            # 启动服务（检查 IS_PRIMARY）
│   ├── _schedule_next()   # 计算 Timer 等待时间
│   ├── _collect_loop()    # 采集循环（每 60 秒）
│   └── stop()             # 停止服务
└── start_minute_collector_service() # 对外入口函数
```

## 交易时段窗口定义

A股交易时段：
- 上午：09:30 - 11:30
- 下午：13:00 - 15:00

触发窗口（前后 5 分钟缓冲）：
- 上午窗口：09:25 - 11:35
- 下午窗口：12:55 - 15:05

```python
TRADING_WINDOWS = [
    (time(9, 25), time(11, 35)),   # 上午窗口
    (time(12, 55), time(15, 5)),   # 下午窗口
]
```

## 采集逻辑

每次采集：
1. 遍历 ETF_POOL 中的每个代码
2. 调用 `get_minute_bars(code, count=50)` 获取最新 50 条 K 线
3. 过滤掉已存在于 PG 的记录（time > latest_time）
4. 使用 `save_minute_data_from_dicts()` 写入 PG

与 `fill_minute_gaps()` 的区别：
- 固定采集 50 条（而非基于 latest_time 计算）
- 轻量化，适合高频循环
- 不生成详细报告，仅记录日志

## 启动流程

```
dashboard 启动
    ↓
startup_preload.py::start_background_preload()
    ↓
preload_market_state()
ensure_minute_data_ready()     # 一次性补全（现有）
start_minute_collector_service()  # 新增：启动采集服务
    ↓
检查 IS_PRIMARY → False → 跳过，日志记录
    ↓ True
检查当前时间是否在触发窗口内
    ↓ 在窗口内 → 立即开始 _collect_loop()
    ↓ 不在窗口 → 计算 Timer 等待时间，启动 Timer
```

## 定时等待机制

当不在触发窗口时，计算到下一窗口开始的等待时间：

```python
def _calc_wait_seconds() -> int:
    now = datetime.now()
    current_time = now.time()

    # 检查是否在窗口内
    for start, end in TRADING_WINDOWS:
        if start <= current_time <= end:
            return 0  # 在窗口内，立即开始

    # 计算到下一窗口的等待时间
    # 上午窗口：09:25
    # 下午窗口：12:55
    # 若已过 15:05，等待次日 09:25

    if current_time < time(9, 25):
        return seconds_until(time(9, 25))
    elif current_time < time(12, 55):
        return seconds_until(time(12, 55))
    else:
        # 已过下午窗口，等待次日
        return seconds_until_next_day(time(9, 25))
```

## 采集循环

```python
def _collect_loop(self):
    while self._running and self._is_in_window():
        # 采集 ETF_POOL
        for code in ETF_POOL:
            try:
                data = get_minute_bars(code, count=50)
                if data:
                    # 过滤已存在的记录
                    latest = get_latest_minute_time(code)
                    filtered = [b for b in data if b["time"] > latest] if latest else data
                    if filtered:
                        save_minute_data_from_dicts(code, filtered)
            except Exception as e:
                logger.warning(f"collect {code} failed: {e}")

        # 等待 60 秒
        if self._running:
            threading.Timer(60, self._collect_loop).start()
            break  # 退出当前循环，由 Timer 触发下一次

    # 窗口结束，调度下一次等待
    if self._running:
        self._schedule_next_window()
```

## 停止机制

服务为 daemon 线程，Dashboard 退出时自动终止。但提供显式 `stop()` 方法用于测试：

```python
def stop(self):
    self._running = False
    if self._timer:
        self._timer.cancel()
```

## 错误处理

- 单个代码采集失败 → 跳过，记录 warning，继续其他代码
- 整体循环异常 → 记录 error，等待下一窗口重试
- PG 连接失败 → 记录 error，等待下一窗口重试

不因单次失败中断整个服务。

## 日志输出

```
[INFO] minute_collector_service: starting (IS_PRIMARY=True)
[INFO] minute_collector_service: in window, starting collect loop
[DEBUG] minute_collector_service: collected 510050 - 3 new bars
[DEBUG] minute_collector_service: collected 510310 - 2 new bars
...
[INFO] minute_collector_service: window ended, scheduling next (wait 12345s)
[INFO] minute_collector_service: waiting for next window (09:25)
```

## 与现有模块的关系

| 模块 | 功能 | 关系 |
|------|------|------|
| `minute_fill.py::ensure_minute_data_ready()` | 启动时一次性补全缺失数据 | 先执行，补全历史缺口 |
| `minute_collector_service.py` | 交易时段持续采集新数据 | 后执行，保持数据新鲜 |
| `minute_collector.py` | 底层采集与存储 API | 被两者调用 |

执行顺序：
1. `ensure_minute_data_ready()` 补全历史缺口（最多 60 天）
2. `start_minute_collector_service()` 启动持续采集服务

## 配置参数

通过 `conf.py` 或环境变量可配置：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `MINUTE_COLLECT_POOL` | ETF_POOL | 采集池 |
| `MINUTE_COLLECT_COUNT` | 50 | 每次采集条数 |
| `MINUTE_COLLECT_INTERVAL` | 60 | 采集间隔秒数 |
| `MINUTE_COLLECT_BUFFER` | 5 | 窗口前后缓冲分钟数 |

当前阶段使用硬编码默认值，后续可扩展为可配置。

## 测试要点

1. **窗口判断**：验证各时间点是否正确判断在窗口内
2. **等待计算**：验证等待时间计算正确（跨日场景）
3. **采集循环**：验证每 60 秒触发一次
4. **IS_PRIMARY**：验证非 PRIMARY 节点跳过
5. **错误恢复**：验证单次失败不影响后续采集

## 文件清单

| 文件 | 变更 |
|------|------|
| `src/quant_etf/dashboard/services/minute_collector_service.py` | 新增 |
| `src/quant_etf/dashboard/services/startup_preload.py` | 添加 `start_minute_collector_service()` 调用 |

## 不在范围内

- 可配置证券池（当前硬编码 ETF_POOL）
- 多节点协调采集（仅 IS_PRIMARY 判断）
- 采集状态 Dashboard 展示（仅后台服务）