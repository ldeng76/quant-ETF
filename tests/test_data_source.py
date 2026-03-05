import pandas as pd
import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch
from quant_etf.data_source import ETFDataSource

def test_fetch_from_akshare_success():
    """
    测试成功从 akshare 获取数据
    """
    # 模拟 ak.fund_etf_hist_em 返回的数据
    mock_df = pd.DataFrame({
        "日期": ["2023-01-01", "2023-01-02"],
        "开盘": [1.0, 1.1],
        "收盘": [1.1, 1.2],
        "最高": [1.2, 1.3],
        "最低": [0.9, 1.0],
        "成交量": [1000, 1100],
        "成交额": [10000, 11000],
        "涨跌幅": [1.0, 2.0]
    })
    
    with patch("akshare.fund_etf_hist_em", return_value=mock_df) as mock_ak:
        ds = ETFDataSource()
        df = ds.fetch_from_akshare("510050")
        
        # 验证是否调用了 akshare 接口
        mock_ak.assert_called_once()
        
        # 验证返回的数据格式是否正确
        assert not df.empty
        assert "open" in df.columns
        assert "close" in df.columns
        assert df.index.name == "date"
        assert len(df) == 2

def test_fetch_from_akshare_empty():
    """
    测试 akshare 返回空数据
    """
    with patch("akshare.fund_etf_hist_em", return_value=pd.DataFrame()) as mock_ak:
        ds = ETFDataSource()
        with pytest.raises(RuntimeError):
            ds.fetch_from_akshare("999999")


def test_fetch_from_akshare_retries_then_success():
    """
    测试 akshare 瞬时失败时会重试并最终成功
    """
    mock_df = pd.DataFrame({
        "日期": ["2023-01-01", "2023-01-02"],
        "开盘": [1.0, 1.1],
        "收盘": [1.1, 1.2],
        "最高": [1.2, 1.3],
        "最低": [0.9, 1.0],
        "成交量": [1000, 1100],
        "成交额": [10000, 11000],
        "涨跌幅": [1.0, 2.0]
    })

    calls = {"n": 0}

    def _side_effect(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("connection aborted")
        return mock_df

    with patch("akshare.fund_etf_hist_em", side_effect=_side_effect) as mock_ak, \
         patch("quant_etf.data_source.time_module.sleep", return_value=None):
        ds = ETFDataSource()
        df = ds.fetch_from_akshare("510050")
        assert not df.empty
        assert calls["n"] == 2
        assert mock_ak.call_count == 2

def test_load_data_cache(tmp_path):
    """
    测试缓存加载
    """
    # 创建一个临时的 DataSource，使用 tmp_path 作为数据目录
    ds = ETFDataSource(data_dir=tmp_path)
    code = "test_code"
    
    # 手动创建一个缓存文件
    etf_dir = tmp_path / "etf"
    etf_dir.mkdir(parents=True, exist_ok=True)
    cache_file = etf_dir / f"{code}.csv"
    today = datetime.now().strftime("%Y-%m-%d")
    mock_data = f"date,open,close,high,low,volume\n{today},1.0,1.1,1.2,0.9,1000"
    cache_file.write_text(mock_data)
    
    # 尝试加载数据 (应该直接读取缓存，不调用网络)
    with patch.object(ds, "update_data") as mock_update:
        df = ds.load_data(code)
        assert not df.empty
        assert len(df) == 1
        assert df.iloc[0]["close"] == 1.1
        # 验证没有调用 update_data
        mock_update.assert_not_called()


def test_fetch_stock_from_akshare_success():
    """
    测试成功从 akshare 获取股票数据
    """
    mock_df = pd.DataFrame(
        {
            "日期": ["2023-01-01", "2023-01-02"],
            "开盘": [10.0, 10.5],
            "收盘": [10.2, 10.8],
            "最高": [10.3, 11.0],
            "最低": [9.9, 10.4],
            "成交量": [10000, 12000],
            "成交额": [100000, 130000],
            "涨跌幅": [1.0, 2.0],
        }
    )

    with patch("akshare.stock_zh_a_hist", return_value=mock_df) as mock_ak:
        ds = ETFDataSource()
        df = ds.fetch_stock_from_akshare("000001")
        mock_ak.assert_called_once()
        assert not df.empty
        assert df.index.name == "date"
        assert "close" in df.columns
        assert len(df) == 2


def test_get_etf_name_map_raises_when_akshare_fails_even_with_cache(tmp_path):
    """
    测试 ETF 名称映射在第三方失败时直接抛错（Fail-Fast），不会降级读取本地缓存继续执行
    """
    ds = ETFDataSource(data_dir=tmp_path)
    meta_dir = tmp_path / "meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    cache_path = meta_dir / "etf_name_map.json"
    cache_path.write_text('{"updated_at":"2026-03-05T00:00:00Z","data":{"510050":"上证50ETF"}}', encoding="utf-8")

    with patch("akshare.fund_etf_spot_em", side_effect=ConnectionError("blocked")), \
         patch("quant_etf.data_source.time_module.sleep", return_value=None):
        with pytest.raises(Exception):
            ds.get_etf_name_map()


def test_throttle_enforces_min_interval_between_requests():
    """
    测试限频：连续两次请求会等待 >=3 秒的间隔
    """
    ds = ETFDataSource()
    ds._min_request_interval_s = 5.0

    def _dummy():
        return 1

    with patch("quant_etf.data_source.time_module.monotonic", side_effect=[0.0, 0.1, 3.1]), \
         patch("quant_etf.data_source.random.random", return_value=0.0), \
         patch("quant_etf.data_source.time_module.sleep", return_value=None) as mock_sleep:
        assert ds._call_with_retry(_dummy) == 1
        assert ds._call_with_retry(_dummy) == 1
        assert mock_sleep.call_count == 1
        assert mock_sleep.call_args[0][0] >= 2.9
