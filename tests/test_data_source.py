import pandas as pd
import pytest
from datetime import datetime
from quant_etf.data_source import ETFDataSource
from quant_etf.market_db import save_daily_to_db, close_all_market_db_connections


@pytest.fixture(autouse=True)
def cleanup():
    """每个测试后清理所有连接"""
    yield
    close_all_market_db_connections()


def test_load_data_from_duckdb_cache(tmp_path):
    """
    测试从 DuckDB 缓存加载数据
    """
    code = "test_code"
    today = datetime.now().date()
    mock_df = pd.DataFrame(
        {
            "open": [1.0],
            "high": [1.2],
            "low": [0.9],
            "close": [1.1],
            "amount": [1000000.0],
            "volume": [1000.0],
            "pct_chg": [0.5],
        },
        index=pd.DatetimeIndex([today]),
    )
    save_daily_to_db("etf_daily", code, mock_df, data_dir=tmp_path)

    ds = ETFDataSource(data_dir=tmp_path)
    df = ds.load_data(code, check_freshness=False, allow_online=False)
    assert not df.empty
    assert len(df) == 1
    assert df.iloc[0]["close"] == 1.1


def test_load_data_raises_when_no_data(tmp_path):
    """
    测试当没有数据时抛出异常
    """
    ds = ETFDataSource(data_dir=tmp_path)

    with pytest.raises(RuntimeError) as exc_info:
        ds.load_data("nonexistent_code")

    assert "Failed to load ETF data" in str(exc_info.value)
    assert "No TDX data found" in str(exc_info.value)


def test_check_is_fresh_for_today():
    """
    测试数据为今天时被认为是新鲜的
    """
    ds = ETFDataSource()
    today = datetime.now().date()
    df = pd.DataFrame({"close": [1.0]}, index=pd.DatetimeIndex([today]))
    assert ds.check_is_fresh(df) is True


def test_check_is_fresh_for_old_data():
    """
    测试过期数据被认为不新鲜
    """
    ds = ETFDataSource()
    old_date = datetime.now().date() - pd.Timedelta(days=30)
    df = pd.DataFrame({"close": [1.0]}, index=pd.DatetimeIndex([old_date]))
    assert ds.check_is_fresh(df) is False


def test_check_is_fresh_for_empty():
    """
    测试空数据被认为不新鲜
    """
    ds = ETFDataSource()
    df = pd.DataFrame()
    assert ds.check_is_fresh(df) is False
