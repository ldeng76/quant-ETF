import pandas as pd
import pytest
from datetime import datetime
from quant_etf.data_source import ETFDataSource


def test_load_data_from_cache(tmp_path):
    """
    测试从缓存加载数据
    """
    ds = ETFDataSource(data_dir=tmp_path)
    code = "test_code"

    etf_dir = tmp_path / "etf"
    etf_dir.mkdir(parents=True, exist_ok=True)
    cache_file = etf_dir / f"{code}.csv"
    today = datetime.now().strftime("%Y-%m-%d")
    mock_data = f"date,open,close,high,low,volume\n{today},1.0,1.1,1.2,0.9,1000"
    cache_file.write_text(mock_data)

    df = ds.load_data(code, force_update=False, check_freshness=False)
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
