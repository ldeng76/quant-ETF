"""
E2E tests for CLI entry point and risk management.
"""
import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime

import pytest
import pandas as pd

from quant_etf.risk import RiskManager, RiskLevel, RiskStatus


class TestRiskManagerE2E:
    """E2E tests for risk management with realistic data profiles."""

    def test_normal_etf_passes(self):
        """ETF in normal state should pass risk check."""
        rm = RiskManager()
        df = _create_normal_etf_data()
        result = rm.check_risk(df)
        assert result.level == RiskLevel.NORMAL
        assert result.suggested_action == "KEEP"

    def test_overbought_etf_warns(self):
        """Overbought ETF (high RSI) should trigger WARNING."""
        rm = RiskManager()
        df = _create_overbought_etf_data()
        result = rm.check_risk(df)
        # Should be WARNING or CRITICAL (depending on MA20)
        assert result.level in (RiskLevel.WARNING, RiskLevel.CRITICAL)

    def test_high_and_below_ma20_critical(self):
        """ETF at high position + below MA20 should be CRITICAL."""
        rm = RiskManager()
        df = _create_high_then_drop_data()
        result = rm.check_risk(df)
        assert result.level == RiskLevel.CRITICAL
        assert result.suggested_action == "CLEAR"

    def test_insufficient_data_normal(self):
        """ETF with < 60 days should return NORMAL."""
        rm = RiskManager()
        df = pd.DataFrame({"close": [1.0, 1.1, 1.2]}, index=pd.date_range("2026-01-01", periods=3))
        df.index.name = "date"
        result = rm.check_risk(df)
        assert result.level == RiskLevel.NORMAL

    def test_rsi_calculation(self):
        """RSI calculation should produce valid values."""
        rm = RiskManager()
        df = _create_normal_etf_data()
        rsi = rm.calculate_rsi(df["close"])
        assert 0 <= rsi <= 100


class TestCLIParsingE2E:
    """E2E tests for CLI argument parsing and dispatch."""

    def test_main_etf_task(self):
        """CLI should dispatch 'etf' task."""
        import main

        with patch.object(sys, "argv", ["main.py", "etf"]):
            args = main.parse_args()
            assert args.task == "etf"

    def test_main_short_task(self):
        """CLI should dispatch 'short' task."""
        import main

        with patch.object(sys, "argv", ["main.py", "short"]):
            args = main.parse_args()
            assert args.task == "short"

    def test_main_mid_task(self):
        """CLI should dispatch 'mid' task."""
        import main

        with patch.object(sys, "argv", ["main.py", "mid"]):
            args = main.parse_args()
            assert args.task == "mid"

    def test_main_default_is_etf(self):
        """Default task should be 'etf'."""
        import main

        with patch.object(sys, "argv", ["main.py"]):
            args = main.parse_args()
            assert args.task == "etf"

    def test_main_list_flag(self):
        """--list flag should be parsed."""
        import main

        with patch.object(sys, "argv", ["main.py", "--list"]):
            args = main.parse_args()
            assert args.list is True

    def test_main_unknown_task(self):
        """Unknown task should cause exit with error."""
        import io
        from contextlib import redirect_stderr

        with patch.object(sys, "argv", ["main.py", "unknown"]):
            # Should sys.exit(1)
            with pytest.raises(SystemExit):
                import main
                with redirect_stderr(io.StringIO()):
                    main.main()


class TestDailyRunScriptE2E:
    """E2E tests for run_daily.py script."""

    def test_date_validation(self):
        """Date validation should accept YYYY-MM-DD format."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        import run_daily

        assert run_daily.validate_date("2026-01-15") is True
        assert run_daily.validate_date("not-a-date") is False
        sys.path.pop(0)

    def test_run_task_delegates(self):
        """run_task should call task.run()."""
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        import run_daily

        mock_task = MagicMock()
        with patch("run_daily.TaskRegistry.get_task", return_value=mock_task):
            run_daily.run_task("etf", target_date="2026-01-15")
            mock_task.run.assert_called_once()
        sys.path.pop(0)

    def test_run_task_handles_error(self):
        """run_task should log errors, not crash."""
        import io
        from contextlib import redirect_stderr
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        import run_daily

        with patch("run_daily.TaskRegistry.get_task", return_value=None):
            with redirect_stderr(io.StringIO()):
                run_daily.run_task("nonexistent")
                # Should not raise
        sys.path.pop(0)


# ---------------------------------------------------------------------------
# Helper: create realistic ETF data for risk testing
# ---------------------------------------------------------------------------

def _create_normal_etf_data() -> pd.DataFrame:
    """Create 250 days of normal ETF price data (no extremes)."""
    from .conftest import generate_price_series
    return generate_price_series(
        start_price=3.0, days=250, trend=0.001, volatility=0.01, seed=200
    )


def _create_overbought_etf_data() -> pd.DataFrame:
    """Create ETF data with strong uptrend (likely overbought)."""
    from .conftest import generate_price_series
    return generate_price_series(
        start_price=3.0, days=250, trend=0.005, volatility=0.005, seed=201
    )


def _create_high_then_drop_data() -> pd.DataFrame:
    """
    Create ETF data where price is at high historical percentile AND below MA20.
    Design:
    - 213 days at low price (~2.0) -> 85.2% of data
    - 1 day jump to ~7.5
    - 1 day peak at 8.0
    - 35 days declining from 8.0 to 7.0 -> current price ~7.0
    Result: current ~7.0 is above 213/250 = 85.2% of history (high percentile)
            but MA20 includes the 8.0 peak, so MA20 > 7.0 (below MA20)
    """
    rng = pd.date_range(end=datetime.now().date(), periods=250, freq="B")

    prices = []
    # Days 1-213: low baseline
    prices.extend([2.0] * 213)
    # Day 214: jump to 7.5
    prices.append(7.5)
    # Day 215: peak
    prices.append(8.0)
    # Days 216-250: decline from ~7.9 to 7.0
    for i in range(35):
        prices.append(8.0 - (8.0 - 7.0) * (i + 1) / 35)

    close = pd.Series(prices)
    n = len(close)
    assert n == 250, f"Expected 250 rows, got {n}"

    open_ = close.values * 0.998
    high = close.values * 1.01
    low = close.values * 0.99
    volume = [5e6] * n
    amount = close.values * volume

    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close.values, "volume": volume, "amount": amount},
        index=rng,
    )
    df.index.name = "date"
    return df
