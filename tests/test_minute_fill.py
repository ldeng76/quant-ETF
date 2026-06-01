"""
minute_fill 模块单元测试
验证 _calc_bars_to_fetch、_get_pool_codes、fill_minute_gaps、_detect_missing_dates
"""
import sys
import math
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

# minute_fill uses lazy imports (from quant_etf.minute_collector import ...).
# Inject a mock module into sys.modules so that:
#   1) @patch decorators can resolve the target at class-definition time
#   2) the lazy `from ... import ...` inside functions returns our mocks
_original_mc = sys.modules.get("quant_etf.minute_collector")
sys.modules["quant_etf.minute_collector"] = MagicMock()


@pytest.fixture(autouse=True)
def _reset_mock_module():
    """Reset the mock minute_collector module between tests."""
    sys.modules["quant_etf.minute_collector"].reset_mock()
    yield


# ---------------------------------------------------------------------------
# TestCalcBarsToFetch
# ---------------------------------------------------------------------------
class TestCalcBarsToFetch:
    """测试 _calc_bars_to_fetch(latest_time, now, max_days)"""

    def _call(self, latest_time, now, max_days=60):
        from quant_etf.minute_fill import _calc_bars_to_fetch
        return _calc_bars_to_fetch(latest_time, now, max_days)

    def test_no_existing_data_uses_max_days(self):
        """latest_time=None -> 使用 max_days 作为 days_gap"""
        now = datetime(2026, 5, 28, 15, 0)
        result = self._call(None, now, max_days=60)
        # days_gap=60 -> bars = ceil(60*250/800)*800 = ceil(18.75)*800 = 19*800 = 15200
        expected = math.ceil(60 * 250 / 800) * 800
        assert result == expected

    def test_recent_data_small_gap(self):
        """间隔 1 天 -> 800 根 (ceil(1*250/800)=1, 1*800=800)"""
        now = datetime(2026, 5, 28, 15, 0)
        latest_time = datetime(2026, 5, 27, 15, 0)
        result = self._call(latest_time, now, max_days=60)
        assert result == 800

    def test_gap_capped_by_max_days(self):
        """超大间隔但受 max_days 封顶"""
        now = datetime(2026, 5, 28, 15, 0)
        latest_time = datetime(2025, 1, 1, 0, 0)  # ~513 days ago
        result = self._call(latest_time, now, max_days=30)
        # days_gap clamped to 30 -> ceil(30*250/800)*800 = ceil(9.375)*800 = 10*800 = 8000
        expected = math.ceil(30 * 250 / 800) * 800
        assert result == expected

    def test_zero_gap_returns_zero(self):
        """latest_time 在未来 -> days_gap < 0 -> 返回 0"""
        now = datetime(2026, 5, 28, 15, 0)
        latest_time = datetime(2026, 5, 30, 15, 0)  # 2 days in future
        result = self._call(latest_time, now, max_days=60)
        # (now - latest_time).days = -2, +1 = -1, min(-1, 60) = -1 < 0 -> 0
        assert result == 0


# ---------------------------------------------------------------------------
# TestGetPoolCodes
# ---------------------------------------------------------------------------
class TestGetPoolCodes:
    """测试 _get_pool_codes(pool_name)"""

    def test_etf_pool(self, monkeypatch):
        from quant_etf.minute_fill import _get_pool_codes
        from quant_etf.conf import ETF_POOL
        monkeypatch.setattr("quant_etf.pool_loader.get_stock_pool", lambda t: {
            "etf": list(ETF_POOL),
            "stock": ["000001", "600000"],
            "mid_term": ["000002"],
        }.get(t, []))
        result = _get_pool_codes("etf")
        assert result == list(ETF_POOL)

    def test_stock_pool(self, monkeypatch):
        from quant_etf.minute_fill import _get_pool_codes
        from quant_etf.conf import STOCK_POOL
        monkeypatch.setattr("quant_etf.pool_loader.get_stock_pool", lambda t: {
            "etf": ["510050"],
            "stock": list(STOCK_POOL),
            "mid_term": ["000002"],
        }.get(t, []))
        result = _get_pool_codes("stock")
        assert result == list(STOCK_POOL)

    def test_all_pool(self, monkeypatch):
        from quant_etf.minute_fill import _get_pool_codes
        from quant_etf.conf import ETF_POOL, STOCK_POOL, MID_TERM_STOCK_POOL
        monkeypatch.setattr("quant_etf.pool_loader.get_stock_pool", lambda t: {
            "etf": list(ETF_POOL),
            "stock": list(STOCK_POOL),
            "mid_term": list(MID_TERM_STOCK_POOL),
        }.get(t, []))
        result = _get_pool_codes("all")
        assert result == list(ETF_POOL) + list(STOCK_POOL) + list(MID_TERM_STOCK_POOL)

    def test_unknown_defaults_to_etf(self, monkeypatch):
        from quant_etf.minute_fill import _get_pool_codes
        from quant_etf.conf import ETF_POOL
        monkeypatch.setattr("quant_etf.pool_loader.get_stock_pool", lambda t: {
            "etf": list(ETF_POOL),
            "stock": ["000001"],
            "mid_term": ["000002"],
        }.get(t, []))
        result = _get_pool_codes("nonexistent")
        assert result == list(ETF_POOL)


# ---------------------------------------------------------------------------
# TestFillMinuteGaps
# ---------------------------------------------------------------------------
class TestFillMinuteGaps:
    """测试 fill_minute_gaps(codes, max_days)"""

    @patch("quant_etf.minute_collector.save_minute_data_from_dicts")
    @patch("quant_etf.minute_collector.get_minute_bars")
    @patch("quant_etf.minute_collector.get_latest_minute_time")
    def test_skip_when_up_to_date(
        self, mock_latest, mock_bars, mock_save,
    ):
        """bars_to_fetch=0 -> 跳过, 不调用 pytdx"""
        from quant_etf.minute_fill import fill_minute_gaps

        now = datetime.now()
        # latest_time 在未来 -> bars_to_fetch=0
        mock_latest.return_value = now + timedelta(days=2)

        stats = fill_minute_gaps(["510050"])

        assert stats["skipped"] == 1
        assert stats["success"] == 0
        mock_bars.assert_not_called()
        mock_save.assert_not_called()

    @patch("quant_etf.minute_collector.save_minute_data_from_dicts")
    @patch("quant_etf.minute_collector.get_minute_bars")
    @patch("quant_etf.minute_collector.get_latest_minute_time")
    def test_fill_gap_success(
        self, mock_latest, mock_bars, mock_save,
    ):
        """有缺口, pytdx 返回数据, save 成功"""
        from quant_etf.minute_fill import fill_minute_gaps

        now = datetime.now()
        latest_time = now - timedelta(days=2)
        mock_latest.return_value = latest_time

        bar_time = latest_time + timedelta(minutes=5)
        mock_bars.return_value = [
            {"time": bar_time, "open": 1.0, "close": 1.0},
        ]
        mock_save.return_value = True

        stats = fill_minute_gaps(["510050"])

        assert stats["success"] == 1
        assert stats["total_bars"] == 1
        mock_save.assert_called_once()

    @patch("quant_etf.minute_collector.save_minute_data_from_dicts")
    @patch("quant_etf.minute_collector.get_minute_bars")
    @patch("quant_etf.minute_collector.get_latest_minute_time")
    def test_fill_gap_no_data_from_tdx(
        self, mock_latest, mock_bars, mock_save,
    ):
        """pytdx 返回空 -> failed"""
        from quant_etf.minute_fill import fill_minute_gaps

        now = datetime.now()
        mock_latest.return_value = now - timedelta(days=2)
        mock_bars.return_value = []

        stats = fill_minute_gaps(["510050"])

        assert stats["failed"] == 1
        assert stats["failures"] == [("510050", "no data from pytdx")]
        mock_save.assert_not_called()

    @patch("quant_etf.minute_collector.save_minute_data_from_dicts")
    @patch("quant_etf.minute_collector.get_minute_bars")
    @patch("quant_etf.minute_collector.get_latest_minute_time")
    def test_fill_first_time_no_history(
        self, mock_latest, mock_bars, mock_save,
    ):
        """首次填充: latest_time=None"""
        from quant_etf.minute_fill import fill_minute_gaps

        mock_latest.return_value = None  # no existing data

        t1 = datetime(2026, 5, 28, 9, 31)
        t2 = datetime(2026, 5, 28, 9, 32)
        mock_bars.return_value = [
            {"time": t1, "open": 1.0, "close": 1.0},
            {"time": t2, "open": 1.1, "close": 1.1},
        ]
        mock_save.return_value = True

        stats = fill_minute_gaps(["159811"])

        assert stats["success"] == 1
        assert stats["total_bars"] == 2


# ---------------------------------------------------------------------------
# TestDetectMissingDates
# ---------------------------------------------------------------------------
class TestDetectMissingDates:
    """测试 _detect_missing_dates(code, trading_dates)"""

    @patch("quant_etf.minute_collector._get_pg_conn")
    def test_all_present(self, mock_get_conn):
        """所有交易日都有 >=100 根K线 -> 无缺失"""
        from quant_etf.minute_fill import _detect_missing_dates

        dates = [
            datetime(2026, 5, 26),
            datetime(2026, 5, 27),
            datetime(2026, 5, 28),
        ]

        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            (datetime(2026, 5, 26).date(), 240),
            (datetime(2026, 5, 27).date(), 240),
            (datetime(2026, 5, 28).date(), 240),
        ]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_get_conn.return_value = mock_conn

        result = _detect_missing_dates("510050", dates)
        assert result == []

    @patch("quant_etf.minute_collector._get_pg_conn")
    def test_one_missing(self, mock_get_conn):
        """一个交易日完全缺失"""
        from quant_etf.minute_fill import _detect_missing_dates

        dates = [
            datetime(2026, 5, 26),
            datetime(2026, 5, 27),
            datetime(2026, 5, 28),
        ]

        mock_cur = MagicMock()
        # 5/27 完全没有记录
        mock_cur.fetchall.return_value = [
            (datetime(2026, 5, 26).date(), 240),
            (datetime(2026, 5, 28).date(), 240),
        ]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_get_conn.return_value = mock_conn

        result = _detect_missing_dates("510050", dates)
        assert len(result) == 1
        assert result[0] == datetime(2026, 5, 27)

    @patch("quant_etf.minute_collector._get_pg_conn")
    def test_partial_missing_counts_as_missing(self, mock_get_conn):
        """有数据但 <100 根 -> 视为缺失"""
        from quant_etf.minute_fill import _detect_missing_dates

        dates = [
            datetime(2026, 5, 26),
            datetime(2026, 5, 27),
        ]

        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            (datetime(2026, 5, 26).date(), 240),  # OK
            (datetime(2026, 5, 27).date(), 50),    # < 100 -> missing
        ]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_get_conn.return_value = mock_conn

        result = _detect_missing_dates("510050", dates)
        assert len(result) == 1
        assert result[0] == datetime(2026, 5, 27)

    def test_empty_trading_dates(self):
        """空交易日列表 -> 直接返回空, 不调用 DB"""
        from quant_etf.minute_fill import _detect_missing_dates

        result = _detect_missing_dates("510050", [])
        assert result == []
