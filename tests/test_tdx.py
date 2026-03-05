import pytest
import pandas as pd
from pathlib import Path
from unittest.mock import patch, MagicMock

from quant_etf.tdx import get_tdx_path, parse_tdx_day_file
from quant_etf.conf import TDX_VIPDOC_DIR


class TestGetTdxPath:
    """测试通达信文件路径获取功能"""

    def test_get_tdx_path_sh_market(self):
        """
        测试上海市场股票代码路径生成正确
        """
        code = "510050"
        result = get_tdx_path(code)
        if result:
            assert "sh" in str(result)
            assert "510050" in str(result)

    def test_get_tdx_path_sz_market(self):
        """
        测试深圳市场股票代码路径生成正确
        """
        code = "000001"
        result = get_tdx_path(code)
        if result:
            assert "sz" in str(result)
            assert "000001" in str(result)

    def test_get_tdx_path_etf_code(self):
        """
        测试 ETF 代码路径生成
        """
        code = "510300"
        result = get_tdx_path(code)
        if result:
            assert "sh" in str(result)

    def test_get_tdx_path_not_found(self):
        """
        测试无法识别市场的股票代码返回 None
        """
        code = "ABCDEFG"
        result = get_tdx_path(code)
        assert result is None


class TestParseTdxDayFile:
    """测试通达信 .day 文件解析功能"""

    def test_parse_tdx_day_file_not_exists(self):
        """
        测试解析不存在的文件返回空 DataFrame
        """
        result = parse_tdx_day_file("not_exists.day")
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_parse_tdx_day_file_structure(self, tmp_path):
        """
        测试解析文件后返回的数据结构正确
        """
        test_file = tmp_path / "sh000001.day"
        test_file.write_bytes(b"")

        result = parse_tdx_day_file(test_file)
        assert isinstance(result, pd.DataFrame)


@pytest.mark.integration
class TestTdxRealData:
    """集成测试：使用本地真实通达信数据"""

    def test_parse_real_tdx_data_exists(self):
        """
        测试通达信数据目录是否存在
        """
        assert TDX_VIPDOC_DIR.exists(), f"通达信数据目录不存在: {TDX_VIPDOC_DIR}"

    def test_parse_real_etf_data(self):
        """
        测试解析真实的 ETF 数据文件
        """
        if not TDX_VIPDOC_DIR.exists():
            pytest.skip("通达信数据目录不存在，跳过集成测试")

        code = "510050"
        file_path = get_tdx_path(code)

        if file_path is None:
            pytest.skip(f"未找到 {code} 的通达信数据文件")

        df = parse_tdx_day_file(file_path)
        print(df.tail(5))

        assert not df.empty, "数据文件解析结果不应为空"
        assert len(df) > 0, "应包含至少一条数据记录"

        required_columns = ["open", "high", "low", "close", "volume", "amount"]
        for col in required_columns:
            assert col in df.columns, f"缺少必要列: {col}"

        assert df.index.name == "date", "索引应为 date"

        last_row = df.iloc[-1]
        assert last_row["close"] > 0, "收盘价应大于 0"
        assert last_row["close"] < 100, "ETF 价格应在合理范围内"

    def test_parse_real_stock_data(self):
        """
        测试解析真实的股票数据文件
        """
        if not TDX_VIPDOC_DIR.exists():
            pytest.skip("通达信数据目录不存在，跳过集成测试")

        code = "000001"
        file_path = get_tdx_path(code)

        if file_path is None:
            pytest.skip(f"未找到 {code} 的通达信数据文件")

        df = parse_tdx_day_file(file_path)

        assert not df.empty, "数据文件解析结果不应为空"

        required_columns = ["open", "high", "low", "close", "volume", "amount"]
        for col in required_columns:
            assert col in df.columns, f"缺少必要列: {col}"

    def test_data_is_sorted_by_date(self):
        """
        测试数据按日期升序排列
        """
        if not TDX_VIPDOC_DIR.exists():
            pytest.skip("通达信数据目录不存在，跳过集成测试")

        code = "510050"
        file_path = get_tdx_path(code)

        if file_path is None:
            pytest.skip(f"未找到 {code} 的通达信数据文件")

        df = parse_tdx_day_file(file_path)

        dates = df.index.tolist()
        assert dates == sorted(dates), "数据应按日期升序排列"

    def test_multiple_etf_codes(self):
        """
        测试读取多个不同的 ETF 数据
        """
        if not TDX_VIPDOC_DIR.exists():
            pytest.skip("通达信数据目录不存在，跳过集成测试")

        codes = ["510050", "510300", "510880"]

        for code in codes:
            file_path = get_tdx_path(code)
            if file_path is None:
                pytest.skip(f"未找到 {code} 的通达信数据文件")

            df = parse_tdx_day_file(file_path)
            assert not df.empty, f"{code} 数据不应为空"
            assert df.iloc[-1]["close"] > 0, f"{code} 收盘价应大于 0"
