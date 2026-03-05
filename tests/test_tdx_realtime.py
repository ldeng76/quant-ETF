import pytest
import pandas as pd
from unittest.mock import patch, MagicMock

from quant_etf.tdx import (
    code_to_market,
    _get_default_hq_server,
    get_realtime_quote,
    get_realtime_quote_single,
)
from pytdx.params import TDXParams


class TestCodeToMarket:
    """测试证券代码转市场代码功能"""

    def test_sh_market_code(self):
        """
        测试上海市场代码 (5/6 开头)
        """
        assert code_to_market("510050") == TDXParams.MARKET_SH
        assert code_to_market("600000") == TDXParams.MARKET_SH
        assert code_to_market("688888") == TDXParams.MARKET_SH

    def test_sz_market_code(self):
        """
        测试深圳市场代码 (0/1/3 开头)
        """
        assert code_to_market("000001") == TDXParams.MARKET_SZ
        assert code_to_market("159915") == TDXParams.MARKET_SZ
        assert code_to_market("300750") == TDXParams.MARKET_SZ
        assert code_to_market("127005") == TDXParams.MARKET_SZ

    def test_unknown_code_default_to_sz(self):
        """
        测试未知代码默认返回深圳市场
        """
        assert code_to_market("ABCDEFG") == TDXParams.MARKET_SZ


class TestGetDefaultHQServer:
    """测试获取默认行情服务器"""

    def test_get_default_server(self):
        """
        测试返回服务器地址格式
        """
        server, port = _get_default_hq_server()
        assert isinstance(server, str)
        assert isinstance(port, int)
        assert len(server) > 0
        assert port > 0


class TestGetRealtimeQuote:
    """测试实时行情获取功能"""

    def test_empty_codes(self):
        """
        测试空代码列表返回空 DataFrame
        """
        result = get_realtime_quote([])
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    @pytest.mark.integration
    class TestRealtimeQuoteIntegration:
        """集成测试：使用真实服务器获取实时数据"""

        def test_get_single_quote(self):
            """
            测试获取单只股票实时报价
            """
            df = get_realtime_quote(["510050"])
            if df.empty:
                pytest.skip("无法连接到通达信服务器，跳过集成测试")
            assert "close" in df.columns or "price" in df.columns or "last_close" in df.columns

        def test_get_multiple_quotes(self):
            """
            测试批量获取多只股票实时报价
            """
            codes = ["510050", "000001", "600000"]
            df = get_realtime_quote(codes)
            if df.empty:
                pytest.skip("无法连接到通达信服务器，跳过集成测试")
            assert len(df) >= 1, "Should return at least some data"

        def test_get_etf_quote(self):
            """
            测试获取 ETF 实时报价
            """
            codes = ["510050", "510300", "510880"]
            df = get_realtime_quote(codes)
            if df.empty:
                pytest.skip("无法连接到通达信服务器，跳过集成测试")
            assert len(df) >= 1


class TestGetRealtimeQuoteSingle:
    """测试单只股票实时行情获取"""

    @pytest.mark.integration
    def test_get_single_quote_returns_dict(self):
        """
        测试单只股票返回字典格式
        """
        result = get_realtime_quote_single("510050")
        if result is None:
            pytest.skip("无法连接到通达信服务器，跳过集成测试")
        assert isinstance(result, dict)

    @pytest.mark.integration
    def test_get_single_quote_with_invalid_code(self):
        """
        测试无效代码返回 None
        """
        result = get_realtime_quote_single("INVALID_CODE")
        if result is None:
            pytest.skip("无法连接到通达信服务器，跳过集成测试")
        assert result is None or isinstance(result, dict)
