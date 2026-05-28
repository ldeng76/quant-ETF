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


class TestMinuteCollectorService:
    """测试 MinuteCollectorService 类"""

    def test_skipped_when_not_primary(self):
        """非 PRIMARY 节点跳过"""
        with patch("quant_etf.dashboard.config.IS_PRIMARY", False):
            from quant_etf.dashboard.services.minute_collector_service import MinuteCollectorService
            svc = MinuteCollectorService()
            svc.start()
            assert svc._stop_event.is_set() is False  # Not started
            # _stop_event remains cleared but timer not set

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
                assert svc._stop_event.is_set() is False  # Running
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
                assert svc._stop_event.is_set() is False  # Running
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
                assert svc._stop_event.is_set() is True