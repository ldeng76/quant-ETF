# Design: Dashboard 启动时自动开启 Scheduler

## 背景

`minute_collector_service` 在 Dashboard 启动时已实现自动启动（通过 `start_background_preload()`），但 `scheduler.start_all()` 从未被调用，导致通过 Dashboard UI 创建的 schedule 需要手动触发。

## 改动方案

在 `src/quant_etf/dashboard/services/startup_preload.py` 的 `start_background_preload()` 中，添加一行调用：

```python
if IS_PRIMARY:
    await scheduler.start_all()
```

## 行为

- **主节点（IS_PRIMARY=True）**：从数据库读取所有 `enabled=TRUE` 的 schedule，为每个创建 asyncio 任务，进入各自的 `start_loop`
- **非主节点（IS_PRIMARY=False）**：scheduler 不自动启动，与 `minute_collector_service` 行为一致
- **Dashboard 关闭时**：已有的 `scheduler.stop_all()` 在 shutdown 事件中清理所有任务

## 验证

- 启动 dashboard，主节点日志出现 `Scheduler started: <strategy_name>`
- 非主节点启动，日志出现 `scheduler: skipped (not primary)`
- Dashboard 关闭，日志出现 `Scheduler stopped: id=<schedule_id>`
