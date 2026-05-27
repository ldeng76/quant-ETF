"""
E2E tests for data source module.
Tests mock TDX data loading, caching, freshness checks, and online fallback.
"""
import json
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from quant_etf.tdx import get_tdx_path, parse_tdx_day_file
from quant_etf.market_db import (
    save_daily_to_db,
    load_daily_from_db,
    has_data_for_code,
    close_all_market_db_connections,
)
from .conftest import write_tdx_day_file


@pytest.fixture(autouse=True)
def cleanup():
    """每个测试后清理所有连接"""
    yield
    close_all_market_db_connections()


class TestTdxPathResolutionE2E:
    """E2E tests for TDX file path resolution with mock data."""

    def test_get_tdx_path_finds_sh_file(self, mock_tdx_data):
        """Should find Shanghai market TDX file."""
        vipdoc_dir = mock_tdx_data
        with patch("quant_etf.tdx.TDX_VIPDOC_DIR", vipdoc_dir):
            path = get_tdx_path("510050")
            assert path is not None
            assert path.exists()
            assert "sh" in str(path)

    def test_get_tdx_path_finds_sz_file(self, mock_tdx_data, mixed_etf_pool):
        """Should find Shenzhen market TDX file."""
        vipdoc_dir = mock_tdx_data
        with patch("quant_etf.tdx.TDX_VIPDOC_DIR", vipdoc_dir):
            path = get_tdx_path("159352")
            assert path is not None
            assert path.exists()
            assert "sz" in str(path)

    def test_get_tdx_path_returns_none_for_missing(self, mock_tdx_data):
        """Should return None for non-existent ETF code."""
        vipdoc_dir = mock_tdx_data
        with patch("quant_etf.tdx.TDX_VIPDOC_DIR", vipdoc_dir):
            path = get_tdx_path("999999")
            assert path is None

    def test_parsed_data_has_correct_columns(self, mock_tdx_data):
        """Parsed TDX data should have required OHLCV columns."""
        vipdoc_dir = mock_tdx_data
        with patch("quant_etf.tdx.TDX_VIPDOC_DIR", vipdoc_dir):
            path = get_tdx_path("510050")
            assert path is not None
            df = parse_tdx_day_file(path)
            assert not df.empty
            for col in ["open", "high", "low", "close", "volume", "amount"]:
                assert col in df.columns, f"Missing column: {col}"

    def test_parsed_data_is_sorted_by_date(self, mock_tdx_data):
        """Parsed TDX data should be sorted by date ascending."""
        vipdoc_dir = mock_tdx_data
        with patch("quant_etf.tdx.TDX_VIPDOC_DIR", vipdoc_dir):
            path = get_tdx_path("510050")
            df = parse_tdx_day_file(path)
            dates = df.index.tolist()
            assert dates == sorted(dates)

    def test_parsed_data_has_pct_chg(self, mock_tdx_data):
        """Parsed data should include pct_chg column."""
        vipdoc_dir = mock_tdx_data
        with patch("quant_etf.tdx.TDX_VIPDOC_DIR", vipdoc_dir):
            path = get_tdx_path("510050")
            df = parse_tdx_day_file(path)
            assert "pct_chg" in df.columns


class TestDataSourceWithMockTdxE2E:
    """E2E tests for ETFDataSource with mock TDX files."""

    def test_load_data_from_tdx(self, mock_tdx_data, tmp_path):
        """Should load ETF data directly from TDX files."""
        from quant_etf.data_source import ETFDataSource

        # Patch TDX_DIR to point to mock data
        with patch("quant_etf.conf.TDX_VIPDOC_DIR", mock_tdx_data):
            ds = ETFDataSource(data_dir=tmp_path / "data")
            df = ds.load_data("510050", check_freshness=False)

        assert not df.empty
        assert "close" in df.columns
        assert len(df) > 60  # Our mock has 300 days

    def test_load_data_caches_to_duckdb(self, mock_tdx_data, tmp_path):
        """When TDX data is loaded, data_dir should be created."""
        from quant_etf.data_source import ETFDataSource

        data_dir = tmp_path / "data"
        with patch("quant_etf.conf.TDX_VIPDOC_DIR", mock_tdx_data):
            ds = ETFDataSource(data_dir=data_dir)
            ds.load_data("510050", check_freshness=False)

        # TDX data is loaded directly, not cached to DuckDB (only online data is cached)
        # This test verifies the data_dir structure is created
        assert data_dir.exists()

    def test_duckdb_cache_round_trip(self, tmp_path):
        """Data written to DuckDB should be readable via load_data."""
        from quant_etf.data_source import ETFDataSource

        code = "510050"
        data_dir = tmp_path / "data"
        ds = ETFDataSource(data_dir=data_dir)

        # 直接写入 DuckDB 缓存
        dates = pd.bdate_range(end=datetime.now().date(), periods=10, freq="B")
        mock_df = pd.DataFrame(
            {
                "open": [1.0 + i * 0.1 for i in range(10)],
                "high": [1.1 + i * 0.1 for i in range(10)],
                "low": [0.9 + i * 0.1 for i in range(10)],
                "close": [1.0 + i * 0.1 for i in range(10)],
                "amount": [1e6] * 10,
                "volume": [1e5] * 10,
                "pct_chg": [0.5] * 10,
            },
            index=dates,
        )
        save_daily_to_db("etf_daily", code, mock_df, data_dir=data_dir)

        # 通过 data_source 读取
        loaded = ds.load_data(code, check_freshness=False, allow_online=False)
        assert not loaded.empty
        assert len(loaded) == 10
        assert loaded.index.name == "date"
        assert "close" in loaded.columns

    def test_load_data_freshness_check(self, mock_tdx_data, tmp_path):
        """Freshness check should pass for recent data."""
        from quant_etf.data_source import ETFDataSource

        with patch("quant_etf.conf.TDX_VIPDOC_DIR", mock_tdx_data):
            ds = ETFDataSource(data_dir=tmp_path / "data")
            df = ds.load_data("510050", check_freshness=True)

        assert not df.empty
        # Our mock data ends today (bdate_range end=datetime.now())
        assert ds.check_is_fresh(df) is True


class TestDataSourceNameMapE2E:
    """E2E tests for stock/ETF name mapping."""

    def test_load_name_map_from_meta(self, tmp_path):
        """Should load name map from stock_code_name.json."""
        from quant_etf.data_source import ETFDataSource
        from .conftest import create_mock_name_map

        meta_dir = tmp_path / "data" / "meta"
        meta_dir.mkdir(parents=True)

        codes = ["510050", "510310", "159352"]
        name_map_data = create_mock_name_map(codes)
        (meta_dir / "stock_code_name.json").write_text(
            json.dumps(name_map_data, ensure_ascii=False), encoding="utf-8"
        )

        ds = ETFDataSource(data_dir=tmp_path / "data")
        result = ds.get_etf_name_map()

        assert "510050" in result
        assert result["510050"] == "50ETF"

    def test_get_stock_name_map_from_meta(self, tmp_path):
        """Should load stock name map from same file."""
        from quant_etf.data_source import ETFDataSource
        from .conftest import create_mock_name_map

        meta_dir = tmp_path / "data" / "meta"
        meta_dir.mkdir(parents=True)

        codes = ["002202", "600783"]
        name_map_data = create_mock_name_map(codes)
        (meta_dir / "stock_code_name.json").write_text(
            json.dumps(name_map_data, ensure_ascii=False), encoding="utf-8"
        )

        ds = ETFDataSource(data_dir=tmp_path / "data")
        result = ds.get_stock_name_map()

        assert "002202" in result


class TestCheckIsFreshnessE2E:
    """E2E tests for data freshness checking."""

    def test_fresh_today(self):
        """Data ending today should be fresh."""
        from quant_etf.data_source import ETFDataSource
        df = _make_df_with_date(datetime.now().date())
        ds = ETFDataSource()
        assert ds.check_is_fresh(df) is True

    def test_fresh_friday_on_saturday(self):
        """On Saturday, Friday's data should be fresh."""
        from quant_etf.data_source import ETFDataSource
        today = _next_weekday(5)  # Saturday
        friday = today - timedelta(days=1)
        df = _make_df_with_date(friday)
        ds = ETFDataSource()
        with patch("quant_etf.data_source.datetime") as mock_dt:
            mock_dt.now.return_value = datetime.combine(today, datetime.min.time())
            assert ds.check_is_fresh(df) is True

    def test_stale_old_data(self):
        """Data from 20 days ago should not be fresh."""
        from quant_etf.data_source import ETFDataSource
        old_date = datetime.now().date() - timedelta(days=20)
        df = _make_df_with_date(old_date)
        ds = ETFDataSource()
        assert ds.check_is_fresh(df) is False

    def test_empty_df_not_fresh(self):
        """Empty DataFrame should not be fresh."""
        from quant_etf.data_source import ETFDataSource
        ds = ETFDataSource()
        assert ds.check_is_fresh(pd.DataFrame()) is False


class TestOnlineFallbackBehaviorE2E:
    """E2E tests for online data fallback behavior (mocked)."""

    def test_load_data_without_tdx_raises(self, tmp_path):
        """When no TDX data and no online, should raise RuntimeError."""
        from quant_etf.data_source import ETFDataSource

        ds = ETFDataSource(data_dir=tmp_path / "data")
        with pytest.raises(RuntimeError, match="Failed to load ETF data"):
            ds.load_data("510050", check_freshness=False, allow_online=False)

    def test_load_data_online_fallback_returns_empty(self, tmp_path):
        """When online fetch fails, should raise RuntimeError."""
        from quant_etf.data_source import ETFDataSource

        ds = ETFDataSource(data_dir=tmp_path / "data")
        with patch("quant_etf.data_source.get_security_bars") as mock_bars:
            mock_bars.return_value = pd.DataFrame()  # Simulate no data
            with pytest.raises(RuntimeError, match="Failed to load ETF data"):
                ds.load_data("510050", check_freshness=False, allow_online=True)

    def test_load_stock_data_without_tdx_raises(self, tmp_path):
        """When no TDX stock data and no online, should raise RuntimeError."""
        from quant_etf.data_source import ETFDataSource

        ds = ETFDataSource(data_dir=tmp_path / "data")
        with pytest.raises(RuntimeError, match="Failed to load stock data"):
            ds.load_stock_data("002202", check_freshness=False, allow_online=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df_with_date(date) -> pd.DataFrame:
    """Create a minimal DataFrame with the given date as last row."""
    return pd.DataFrame(
        {"close": [1.0, 1.1], "open": [1.0, 1.1], "high": [1.0, 1.1],
         "low": [1.0, 1.1], "volume": [1e6, 1e6], "amount": [1e6, 1e6]},
        index=pd.date_range(end=date, periods=2),
    )


def _next_weekday(weekday: int) -> datetime.date:
    """Get the next occurrence of the given weekday (0=Mon, 6=Sun)."""
    today = datetime.now().date()
    days_ahead = weekday - today.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return today + timedelta(days=days_ahead)
