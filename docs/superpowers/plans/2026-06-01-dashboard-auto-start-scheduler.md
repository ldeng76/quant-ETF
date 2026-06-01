# Dashboard 自动启动 Scheduler 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Dashboard 启动时自动开启已启用的 scheduler，与 minute_collector_service 行为一致。

**Architecture:** 在 `start_background_preload()` 中，`start_minute_collector_service()` 调用之后，添加 `IS_PRIMARY` 检查和 `scheduler.start_all()` 调用。

**Tech Stack:** Python, asyncio, FastAPI

---

## Task 1: 添加 scheduler 自动启动

**Files:**
- Modify: `src/quant_etf/dashboard/services/startup_preload.py`

- [ ] **Step 1: 读取 startup_preload.py 确认 IS_PRIMARY 导入和调用位置**

查看文件确认：
1. `IS_PRIMARY` 已从 `..config` 导入
2. `scheduler` 已从 `..services.scheduler` 导入（或需要添加）
3. `start_minute_collector_service()` 调用的位置

- [ ] **Step 2: 确认 scheduler 导入**

如果 `scheduler` 尚未导入，添加导入：
```python
from .services.scheduler import scheduler
```

- [ ] **Step 3: 添加 scheduler 自动启动调用**

在 `start_minute_collector_service()` 调用之后，添加：
```python
if IS_PRIMARY:
    await scheduler.start_all()
```

- [ ] **Step 4: 验证**

启动 dashboard（日志中应出现 `Scheduler started:`）

- [ ] **Step 5: 提交**

```bash
git add src/quant_etf/dashboard/services/startup_preload.py
git commit -m "feat(dashboard): auto-start scheduler on startup"
```
