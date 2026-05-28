# 分钟K线交易时段自动采集服务实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Dashboard 启动时启动后台服务，交易时段内每分钟自动采集 ETF_POOL 的分钟K线数据。

**Architecture:** 使用 threading.Timer 实现轻量级定时循环，遵循 IS_PRIMARY 多节点机制，与现有 startup_preload.py 无缝集成。

**Tech Stack:** Python threading, datetime, loguru, pytdx, PostgreSQL

---

## 文件结构

| 文件 | 变更类型 | 负责内容 |
|------|----------|----------|
| `src/quant_etf/dashboard/services/minute_collector_service.py` | 新增 | 采集服务主模块 |
| `tests/test_minute_collector_service.py` | 新增 | 单元测试 |
| `src/quant_etf/dashboard/services/startup_preload.py` | 修改 | 添加服务启动调用 |

---

### Task 1: 创建 TradingWindow 辅助函数

**Files:**
- Create: `src/quant_etf/dashboard/services/minute_collector_service.py`

- [ ] **Step 1: 创建模块文件并定义交易窗口常量**

```python
"""
分钟K线交易时段自动采集服务

在 Dashboard 启动时检查是否在交易时段窗口内，启动后台采集循环。
遵循 IS_PRIMARY 多节点机制。
"""
import threading
from datetime import datetime, time, timedelta
from loguru import logger


# 交易时段窗口（前后 5 分钟缓冲）
TRADING_WINDOWS = [
    (time(9, 25), time(11, 35)),   # 上午窗口：09:25-11:35
    (time(12, 55), time(15, 5)),   # 下午窗口：12:55-15:05
]

# 采集参数（硬编码，后续可扩展为可配置）
COLLECT_COUNT = 50       # 每次采集 K 线条数
COLLECT_INTERVAL = 60    # 采集间隔秒数


def is_in_trading_window(now: datetime | None = None) -> bool:
    """
    判断当前时间是否在交易时段窗口内。

    :param now: 当前时间，None 则使用 datetime.now()
    :return: 是否在窗口内
    """
    if now is None:
        now = datetime.now()
    current = now.time()

    for start, end in TRADING_WINDOWS:
        if start <= current <= end:
            return True
    return False


def calc_wait_seconds(now: datetime | None = None) -> int:
    """
    计算到下一交易窗口开始的等待秒数。

    :param now: 当前时间，None 则使用 datetime.now()
    :return: 等待秒数，0 表示已在窗口内
    """
    if now is None:
        now = datetime.now()
    current = now.time()

    # 已在窗口内
    if is_in_trading_window(now):
        return 0

    # 计算到下一窗口的等待时间
    if current < time(9, 25):
        # 等待上午窗口
        target = datetime.combine(now.date(), time(9, 25))
        return int((target - now).total_seconds())

    elif current < time(12, 55):
        # 等待下午窗口
        target = datetime.combine(now.date(), time(12, 55))
        return int((target - now).total_seconds())

    else:
        # 已过下午窗口，等待次日上午窗口
        next_day = now.date() + timedelta(days=1)
        target = datetime.combine(next_day, time(9, 25))
        return int((target - now).total_seconds())


def get_next_window_name(now: datetime | None = None) -> str:
    """
    获取下一窗口名称（用于日志）。

    :param now: 当前时间
    :return: 窗口名称 "上午" / "下午"
    """
    if now is None:
        now = datetime.now()
    current = now.time()

    if is_in_trading_window(now):
        return "当前窗口"
    elif current < time(9, 25):
        return "上午"
    elif current < time(12, 55):
        return "下午"
    else:
        return "次日上午"
```

- [ ] **Step 2: 验证语法**

Run: `uv run python -c "from quant_etf.dashboard.services.minute_collector_service import is_in_trading_window, calc_wait_seconds; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/quant_etf/dashboard/services/minute_collector_service.py
git commit -m "feat: add TradingWindow helper functions for minute collector service"
```

---

### Task 2: 实现 MinuteCollectorService 类

**Files:**
- Modify: `src/quant_etf/dashboard/services/minute_collector_service.py` (追加)

- [ ] **Step 1: 添加 MinuteCollectorService 类**

在文件末尾追加：

```python
class MinuteCollectorService:
    """
    分钟K线交易时段采集服务。

    在交易时段窗口内每分钟采集 ETF_POOL 的分钟K线数据。
    """

    def __init__(self):
        self._running = False
        self._timer: threading.Timer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """
        启动采集服务。

        检查 IS_PRIMARY，若非主节点则跳过。
        若在窗口内立即开始采集，否则等待下一窗口。
        """
        from quant_etf.dashboard.config import IS_PRIMARY

        if not IS_PRIMARY:
            logger.debug("minute_collector_service: skipped (not primary)")
            return

        self._running = True
        logger.info("minute_collector_service: starting (IS_PRIMARY=True)")

        wait_seconds = calc_wait_seconds()

        if wait_seconds == 0:
            # 已在窗口内，立即开始
            logger.info("minute_collector_service: in window, starting collect loop")
            self._start_collect_thread()
        else:
            # 等待下一窗口
            next_window = get_next_window_name()
            logger.info(
                f"minute_collector_service: waiting for next window ({next_window}), "
                f"wait {wait_seconds}s"
            )
            self._timer = threading.Timer(wait_seconds, self._on_window_start)
            self._timer.daemon = True
            self._timer.start()

    def _on_window_start(self) -> None:
        """Timer 触发：窗口开始时的回调"""
        if not self._running:
            return
        logger.info("minute_collector_service: window started, beginning collect loop")
        self._start_collect_thread()

    def _start_collect_thread(self) -> None:
        """启动采集线程"""
        self._thread = threading.Thread(
            target=self._collect_loop,
            daemon=True,
            name="minute-collector"
        )
        self._thread.start()

    def _collect_loop(self) -> None:
        """采集循环：在窗口内每 60 秒采集一次"""
        from quant_etf.conf import ETF_POOL
        from quant_etf.minute_collector import (
            get_minute_bars,
            get_latest_minute_time,
            save_minute_data_from_dicts,
        )

        while self._running and is_in_trading_window():
            collected_count = 0
            new_bars_count = 0

            for code in ETF_POOL:
                try:
                    data = get_minute_bars(code, count=COLLECT_COUNT)
                    if not data:
                        continue

                    # 过滤已存在的记录
                    latest = get_latest_minute_time(code)
                    if latest:
                        filtered = [b for b in data if b.get("time") and b["time"] > latest]
                    else:
                        filtered = [b for b in data if b.get("time")]

                    if filtered:
                        saved = save_minute_data_from_dicts(code, filtered)
                        if saved:
                            collected_count += 1
                            new_bars_count += len(filtered)
                            logger.debug(
                                f"minute_collector_service: collected {code} - "
                                f"{len(filtered)} new bars"
                            )
                except Exception as e:
                    logger.warning(f"minute_collector_service: collect {code} failed: {e}")

            if collected_count > 0:
                logger.info(
                    f"minute_collector_service: round complete - "
                    f"{collected_count}/{len(ETF_POOL)} codes, {new_bars_count} new bars"
                )

            # 等待下一轮
            if self._running and is_in_trading_window():
                self._timer = threading.Timer(COLLECT_INTERVAL, self._collect_loop)
                self._timer.daemon = True
                self._timer.start()
                break  # 退出当前循环，由 Timer 触发下一轮

        # 窗口结束，调度下一窗口
        if self._running:
            self._schedule_next_window()

    def _schedule_next_window(self) -> None:
        """窗口结束后调度下一窗口"""
        wait_seconds = calc_wait_seconds()
        next_window = get_next_window_name()

        logger.info(
            f"minute_collector_service: window ended, "
            f"scheduling next ({next_window}), wait {wait_seconds}s"
        )

        self._timer = threading.Timer(wait_seconds, self._on_window_start)
        self._timer.daemon = True
        self._timer.start()

    def stop(self) -> None:
        """停止服务（用于测试）"""
        self._running = False
        if self._timer:
            self._timer.cancel()
            self._timer = None
        logger.info("minute_collector_service: stopped")


# 单例实例
_service: MinuteCollectorService | None = None


def start_minute_collector_service() -> None:
    """
    启动分钟采集服务的对外入口。

    在 Dashboard 启动时调用。
    """
    global _service
    if _service is None:
        _service = MinuteCollectorService()
    _service.start()


def stop_minute_collector_service() -> None:
    """停止服务（用于测试）"""
    global _service
    if _service:
        _service.stop()
```

- [ ] **Step 2: 验证语法**

Run: `uv run python -c "from quant_etf.dashboard.services.minute_collector_service import MinuteCollectorService, start_minute_collector_service; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/quant_etf/dashboard/services/minute_collector_service.py
git commit -m "feat: add MinuteCollectorService class with collect loop"
```

---

### Task 3: 添加单元测试

**Files:**
- Create: `tests/test_minute_collector_service.py`

- [ ] **Step 1: 创建测试文件 - TradingWindow 测试**

```python
"""
minute_collector_service 模块单元测试
验证 is_in_trading_window、calc_wait_seconds、MinuteCollectorService
"""
import sys
from datetime import datetime, time, timedelta
from unittest.mock import patch, MagicMock

import pytest

# Mock minute_collector module for lazy imports
sys.modules["quant_etf.minute_collector"] = MagicMock()


class TestIsInTradingWindow:
    """测试 is_in_trading_window()"""

    def _call(self, now: datetime) -> bool:
        from quant_etf.dashboard.services.minute_collector_service import is_in_trading_window
        return is_in_trading_window(now)

    def test_in_morning_window(self):
        """上午窗口内（09:30）"""
        now = datetime(2026, 5, 28, 9, 30)
        assert self._call(now) is True

    def test_in_morning_buffer_before(self):
        """上午窗口缓冲区前（09:25）"""
        now = datetime(2026, 5, 28, 9, 25)
        assert self._call(now) is True

    def test_in_morning_buffer_after(self):
        """上午窗口缓冲区后（11:35）"""
        now = datetime(2026, 5, 28, 11, 35)
        assert self._call(now) is True

    def test_before_morning_window(self):
        """上午窗口前（09:20）"""
        now = datetime(2026, 5, 28, 9, 20)
        assert self._call(now) is False

    def test_after_morning_window(self):
        """上午窗口后（11:40）"""
        now = datetime(2026, 5, 28, 11, 40)
        assert self._call(now) is False

    def test_in_afternoon_window(self):
        """下午窗口内（14:00）"""
        now = datetime(2026, 5, 28, 14, 0)
        assert self._call(now) is True

    def test_in_afternoon_buffer_before(self):
        """下午窗口缓冲区前（12:55）"""
        now = datetime(2026, 5, 28, 12, 55)
        assert self._call(now) is True

    def test_in_afternoon_buffer_after(self):
        """下午窗口缓冲区后（15:05）"""
        now = datetime(2026, 5, 28, 15, 5)
        assert self._call(now) is True

    def test_after_afternoon_window(self):
        """下午窗口后（15:10）"""
        now = datetime(2026, 5, 28, 15, 10)
        assert self._call(now) is False

    def test_lunch_break(self):
        """午休时段（12:00）"""
        now = datetime(2026, 5, 28, 12, 0)
        assert self._call(now) is False

    def test_weekend(self):
        """周末（同一时间点）"""
        # 注意：当前实现不检查周末，仅检查时间窗口
        # 如需检查周末，需在 is_in_trading_window 中添加 weekday() 检查
        now = datetime(2026, 5, 31, 9, 30)  # Saturday
        # 当前实现会返回 True（时间在窗口内）
        assert self._call(now) is True


class TestCalcWaitSeconds:
    """测试 calc_wait_seconds()"""

    def _call(self, now: datetime) -> int:
        from quant_etf.dashboard.services.minute_collector_service import calc_wait_seconds
        return calc_wait_seconds(now)

    def test_in_window_returns_zero(self):
        """窗口内返回 0"""
        now = datetime(2026, 5, 28, 10, 0)
        assert self._call(now) == 0

    def test_before_morning_window(self):
        """上午窗口前（09:00）"""
        now = datetime(2026, 5, 28, 9, 0)
        wait = self._call(now)
        target = datetime(2026, 5, 28, 9, 25)
        expected = int((target - now).total_seconds())
        assert wait == expected

    def test_before_afternoon_window(self):
        """下午窗口前（12:00）"""
        now = datetime(2026, 5, 28, 12, 0)
        wait = self._call(now)
        target = datetime(2026, 5, 28, 12, 55)
        expected = int((target - now).total_seconds())
        assert wait == expected

    def test_after_afternoon_window(self):
        """下午窗口后（16:00）- 等待次日"""
        now = datetime(2026, 5, 28, 16, 0)
        wait = self._call(now)
        next_day = datetime(2026, 5, 29, 9, 25)
        expected = int((next_day - now).total_seconds())
        assert wait == expected

    def test_night_time(self):
        """夜间（02:00）- 等待当日上午"""
        now = datetime(2026, 5, 28, 2, 0)
        wait = self._call(now)
        target = datetime(2026, 5, 28, 9, 25)
        expected = int((target - now).total_seconds())
        assert wait == expected


class TestGetNextWindowName:
    """测试 get_next_window_name()"""

    def _call(self, now: datetime) -> str:
        from quant_etf.dashboard.services.minute_collector_service import get_next_window_name
        return get_next_window_name(now)

    def test_in_window(self):
        """窗口内"""
        now = datetime(2026, 5, 28, 10, 0)
        assert self._call(now) == "当前窗口"

    def test_before_morning(self):
        """上午窗口前"""
        now = datetime(2026, 5, 28, 8, 0)
        assert self._call(now) == "上午"

    def test_before_afternoon(self):
        """下午窗口前"""
        now = datetime(2026, 5, 28, 12, 0)
        assert self._call(now) == "下午"

    def test_after_afternoon(self):
        """下午窗口后"""
        now = datetime(2026, 5, 28, 16, 0)
        assert self._call(now) == "次日上午"
```

- [ ] **Step 2: 运行 TradingWindow 测试验证通过**

Run: `uv run python -m pytest tests/test_minute_collector_service.py -v -k "TestIsInTradingWindow or TestCalcWaitSeconds or TestGetNextWindowName"`
Expected: 所有测试 PASS

- [ ] **Step 3: 添加 MinuteCollectorService 测试**

在文件末尾追加：

```python


class TestMinuteCollectorService:
    """测试 MinuteCollectorService 类"""

    def test_skipped_when_not_primary(self):
        """非 PRIMARY 节点跳过"""
        with patch("quant_etf.dashboard.config.IS_PRIMARY", False):
            from quant_etf.dashboard.services.minute_collector_service import MinuteCollectorService
            svc = MinuteCollectorService()
            svc.start()
            assert svc._running is False

    def test_starts_when_primary_in_window(self):
        """PRIMARY 节点在窗口内启动"""
        with patch("quant_etf.dashboard.config.IS_PRIMARY", True):
            with patch(
                "quant_etf.dashboard.services.minute_collector_service.is_in_trading_window",
                return_value=True
            ):
                from quant_etf.dashboard.services.minute_collector_service import MinuteCollectorService
                svc = MinuteCollectorService()
                svc.start()
                assert svc._running is True
                svc.stop()

    def test_schedules_timer_when_not_in_window(self):
        """不在窗口内时启动 Timer 等待"""
        with patch("quant_etf.dashboard.config.IS_PRIMARY", True):
            with patch(
                "quant_etf.dashboard.services.minute_collector_service.calc_wait_seconds",
                return_value=3600  # 1 小时
            ):
                from quant_etf.dashboard.services.minute_collector_service import MinuteCollectorService
                svc = MinuteCollectorService()
                svc.start()
                assert svc._running is True
                assert svc._timer is not None
                svc.stop()

    def test_stop_cancels_timer(self):
        """stop() 取消 Timer"""
        with patch("quant_etf.dashboard.config.IS_PRIMARY", True):
            with patch(
                "quant_etf.dashboard.services.minute_collector_service.calc_wait_seconds",
                return_value=3600
            ):
                from quant_etf.dashboard.services.minute_collector_service import MinuteCollectorService
                svc = MinuteCollectorService()
                svc.start()
                assert svc._timer is not None
                svc.stop()
                assert svc._timer is None
                assert svc._running is False
```

- [ ] **Step 4: 运行全部测试验证通过**

Run: `uv run python -m pytest tests/test_minute_collector_service.py -v`
Expected: 所有测试 PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_minute_collector_service.py
git commit -m "test: add unit tests for minute_collector_service"
```

---

### Task 4: 集成到 startup_preload.py

**Files:**
- Modify: `src/quant_etf/dashboard/services/startup_preload.py`

- [ ] **Step 1: 在 preload 线程中添加服务启动调用**

修改 `_preload_in_thread` 函数，在 `ensure_minute_data_ready()` 后添加：

```python
def _preload_in_thread():
    global _preload_completed, _preload_error
    try:
        preload_market_state()
        from quant_etf.minute_fill import ensure_minute_data_ready
        ensure_minute_data_ready()
        from quant_etf.dashboard.services.minute_collector_service import start_minute_collector_service
        start_minute_collector_service()
        _preload_completed = True
    except Exception as e:
        _preload_error = str(e)
        logger.error(f"Background preload failed: {e}")
```

- [ ] **Step 2: 验证语法**

Run: `uv run python -c "from quant_etf.dashboard.services.startup_preload import start_background_preload; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/quant_etf/dashboard/services/startup_preload.py
git commit -m "feat: integrate minute collector service into dashboard startup"
```

---

### Task 5: E2E 验证

**Files:**
- 无新增文件

- [ ] **Step 1: 运行全量测试确保无回归**

Run: `uv run python -m pytest tests/ -v --tb=short`
Expected: 所有测试 PASS

- [ ] **Step 2: 模拟启动验证服务初始化**

Run: `uv run python -c "
from quant_etf.dashboard.services.startup_preload import start_background_preload
start_background_preload()
print('Background preload started')
"`

Expected: 输出包含 `Background preload started` 和相关日志

- [ ] **Step 3: 最终 Commit（如有遗漏文件）**

```bash
git status
# 若有未提交文件，添加并提交
```

---

## 自检清单

**1. Spec 覆盖检查：**

| Spec 要求 | 对应任务 |
|-----------|----------|
| 交易时段窗口定义（09:25-11:35, 12:55-15:05） | Task 1 Step 1 |
| 窗口判断函数 | Task 1 Step 1 `is_in_trading_window()` |
| 等待时间计算 | Task 1 Step 1 `calc_wait_seconds()` |
| IS_PRIMARY 检查 | Task 2 Step 1 `start()` |
| 采集循环（每 60 秒） | Task 2 Step 1 `_collect_loop()` |
| 过滤已存在记录 | Task 2 Step 1 `_collect_loop()` |
| 错误处理（单代码失败跳过） | Task 2 Step 1 `_collect_loop()` |
| 停止机制 | Task 2 Step 1 `stop()` |
| 单例入口函数 | Task 2 Step 1 `start_minute_collector_service()` |
| startup_preload 集成 | Task 4 Step 1 |
| 单元测试 | Task 3 |

**2. Placeholder 扫描：** 无 TBD/TODO

**3. 类型一致性：** 函数签名在各任务间一致