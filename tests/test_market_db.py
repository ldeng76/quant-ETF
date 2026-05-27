"""
market_db 模块单元测试
验证 DuckDB 行情数据的读写、upsert、日期格式等
"""
import pandas as pd
import pytest
from datetime import datetime
from quant_etf.market_db import (
    get_market_db_path,
    load_daily_from_db,
    save_daily_to_db,
    has_data_for_code,
    close_all_market_db_connections,
)


@pytest.fixture(autouse=True)
def cleanup():
    """每个测试后清理所有连接"""
    yield
    close_all_market_db_connections()


def _make_etf_df(rows: int = 5, start_date: str = "2024-01-02") -> pd.DataFrame:
    """构造 ETF 风格的日线数据（日期无时间部分）"""
    dates = pd.bdate_range(start=start_date, periods=rows, freq="B")
    return pd.DataFrame(
        {
            "open": [1.0 + i * 0.1 for i in range(rows)],
            "high": [1.1 + i * 0.1 for i in range(rows)],
            "low": [0.9 + i * 0.1 for i in range(rows)],
            "close": [1.0 + i * 0.1 for i in range(rows)],
            "amount": [1e6] * rows,
            "volume": [1e5] * rows,
            "pct_chg": [0.5] * rows,
        },
        index=dates,
    )


def _make_stock_df(rows: int = 5, start_date: str = "2024-01-02") -> pd.DataFrame:
    """构造 Stock 风格的日线数据（日期含 15:00:00）"""
    dates = pd.DatetimeIndex(
        [pd.Timestamp(f"{d.strftime('%Y-%m-%d')} 15:00:00") for d in pd.bdate_range(start=start_date, periods=rows, freq="B")]
    )
    return pd.DataFrame(
        {
            "open": [10.0 + i for i in range(rows)],
            "high": [10.5 + i for i in range(rows)],
            "low": [9.5 + i for i in range(rows)],
            "close": [10.0 + i for i in range(rows)],
            "amount": [5e6] * rows,
            "volume": [5e5] * rows,
            "pct_chg": [1.0] * rows,
        },
        index=dates,
    )


class TestDatabaseInit:
    def test_creates_db_file(self, tmp_path):
        db_path = get_market_db_path(tmp_path)
        save_daily_to_db("etf_daily", "000001", _make_etf_df(1), data_dir=tmp_path)
        assert db_path.exists()

    def test_creates_tables(self, tmp_path):
        from quant_etf.market_db import get_market_db_connection
        conn = get_market_db_connection(tmp_path)
        tables = [row[0] for row in conn.execute("SHOW TABLES").fetchall()]
        assert "etf_daily" in tables
        assert "stock_daily" in tables


class TestSaveAndLoad:
    def test_etf_round_trip(self, tmp_path):
        code = "510050"
        original = _make_etf_df(10)
        save_daily_to_db("etf_daily", code, original, data_dir=tmp_path)

        loaded = load_daily_from_db("etf_daily", code, data_dir=tmp_path)
        assert not loaded.empty
        assert len(loaded) == 10
        assert loaded.index.name == "date"
        for col in ["open", "high", "low", "close", "amount", "volume", "pct_chg"]:
            assert col in loaded.columns

    def test_stock_round_trip(self, tmp_path):
        code = "600783"
        original = _make_stock_df(10)
        save_daily_to_db("stock_daily", code, original, data_dir=tmp_path)

        loaded = load_daily_from_db("stock_daily", code, data_dir=tmp_path)
        assert not loaded.empty
        assert len(loaded) == 10
        # 验证时间部分保留
        first_time = loaded.index[0]
        assert first_time.hour == 15
        assert first_time.minute == 0

    def test_etf_date_no_time_component(self, tmp_path):
        """ETF 日期应为纯日期（午夜 00:00:00）"""
        code = "510050"
        original = _make_etf_df(3)
        save_daily_to_db("etf_daily", code, original, data_dir=tmp_path)

        loaded = load_daily_from_db("etf_daily", code, data_dir=tmp_path)
        first_ts = loaded.index[0]
        assert first_ts.hour == 0
        assert first_ts.minute == 0

    def test_load_empty_returns_empty_df(self, tmp_path):
        loaded = load_daily_from_db("etf_daily", "999999", data_dir=tmp_path)
        assert loaded.empty
        assert isinstance(loaded, pd.DataFrame)

    def test_save_empty_df_is_noop(self, tmp_path):
        """保存空 DataFrame 不应报错"""
        save_daily_to_db("etf_daily", "510050", pd.DataFrame(), data_dir=tmp_path)
        loaded = load_daily_from_db("etf_daily", "510050", data_dir=tmp_path)
        assert loaded.empty


class TestUpsert:
    def test_overwrite_on_second_save(self, tmp_path):
        code = "510050"
        df1 = _make_etf_df(5, "2024-01-02")
        save_daily_to_db("etf_daily", code, df1, data_dir=tmp_path)

        loaded1 = load_daily_from_db("etf_daily", code, data_dir=tmp_path)
        assert len(loaded1) == 5

        # 用包含部分重叠日期的新数据覆盖
        df2 = _make_etf_df(8, "2024-01-05")
        save_daily_to_db("etf_daily", code, df2, data_dir=tmp_path)

        loaded2 = load_daily_from_db("etf_daily", code, data_dir=tmp_path)
        # 应该只有 df2 的数据（先删后插）
        assert len(loaded2) == 8

    def test_different_codes_independent(self, tmp_path):
        df1 = _make_etf_df(3)
        df2 = _make_etf_df(5)
        save_daily_to_db("etf_daily", "510050", df1, data_dir=tmp_path)
        save_daily_to_db("etf_daily", "159949", df2, data_dir=tmp_path)

        loaded1 = load_daily_from_db("etf_daily", "510050", data_dir=tmp_path)
        loaded2 = load_daily_from_db("etf_daily", "159949", data_dir=tmp_path)
        assert len(loaded1) == 3
        assert len(loaded2) == 5


class TestHasDataForCode:
    def test_returns_true_when_data_exists(self, tmp_path):
        save_daily_to_db("etf_daily", "510050", _make_etf_df(1), data_dir=tmp_path)
        assert has_data_for_code("etf_daily", "510050", data_dir=tmp_path) is True

    def test_returns_false_when_no_data(self, tmp_path):
        assert has_data_for_code("etf_daily", "999999", data_dir=tmp_path) is False


class TestDataIsolation:
    def test_different_data_dirs_independent(self, tmp_path):
        dir1 = tmp_path / "data1"
        dir2 = tmp_path / "data2"

        save_daily_to_db("etf_daily", "510050", _make_etf_df(3), data_dir=dir1)
        save_daily_to_db("etf_daily", "510050", _make_etf_df(7), data_dir=dir2)

        loaded1 = load_daily_from_db("etf_daily", "510050", data_dir=dir1)
        loaded2 = load_daily_from_db("etf_daily", "510050", data_dir=dir2)
        assert len(loaded1) == 3
        assert len(loaded2) == 7
