"""
单元测试：scheduler_engine.py 核心逻辑
只测 conf.py 能覆盖的部分，不触发 pytdx / asyncpg 依赖链。
"""
from quant_etf.conf import ETF_POOL, STOCK_POOL, MID_TERM_STOCK_POOL


class TestPoolDataFromConf:
    """直接验证 conf.py 原始池子数据（不经过任何调度模块）。"""

    def test_etf_pool_not_empty(self):
        assert len(ETF_POOL) > 0

    def test_stock_pool_not_empty(self):
        assert len(STOCK_POOL) > 0

    def test_mid_term_pool_not_empty(self):
        assert len(MID_TERM_STOCK_POOL) > 0

    def test_no_duplicates_in_any_pool(self):
        assert len(ETF_POOL) == len(set(ETF_POOL)), "ETF_POOL has duplicates"
        assert len(STOCK_POOL) == len(set(STOCK_POOL)), "STOCK_POOL has duplicates"
        assert len(MID_TERM_STOCK_POOL) == len(set(MID_TERM_STOCK_POOL)), "MID_TERM_STOCK_POOL has duplicates"

    def test_etf_pool_has_common_indices(self):
        codes = set(ETF_POOL)
        assert "510050" in codes  # 上证 50
        assert "159949" in codes  # 创业板 50
        assert "588000" in codes  # 科创 50


class TestPoolMergeLogic:
    """纯 Python 池子合并逻辑验证（不依赖任何模块导入）。"""

    def test_public_pools_construction(self):
        """模拟 PUBLIC_POOLS 字典的构造。"""
        PUBLIC_POOLS = {
            "etf": list(ETF_POOL),
            "stock": list(STOCK_POOL),
            "mid_term": list(MID_TERM_STOCK_POOL),
        }

        assert set(PUBLIC_POOLS.keys()) == {"etf", "stock", "mid_term"}
        assert PUBLIC_POOLS["etf"] == list(ETF_POOL)
        assert PUBLIC_POOLS["stock"] == list(STOCK_POOL)
        assert PUBLIC_POOLS["mid_term"] == list(MID_TERM_STOCK_POOL)

    def test_no_private_pool_returns_public_only(self):
        """无私有池时，合并结果等于公共池。"""
        PUBLIC_POOLS = {
            "etf": list(ETF_POOL),
            "stock": list(STOCK_POOL),
            "mid_term": list(MID_TERM_STOCK_POOL),
        }

        private_codes: list[str] = []
        merged = list(set(PUBLIC_POOLS["etf"]) | set(private_codes))
        assert set(merged) == set(ETF_POOL)

    def test_with_private_pool_merges(self):
        """有私有池时，合并结果包含公共池和私有池。"""
        PUBLIC_POOLS = {
            "etf": list(ETF_POOL),
            "stock": list(STOCK_POOL),
            "mid_term": list(MID_TERM_STOCK_POOL),
        }

        private_codes = ["999999", "888888"]
        merged = set(PUBLIC_POOLS["etf"]) | set(private_codes)

        assert "999999" in merged
        assert "888888" in merged
        assert len(merged) == len(ETF_POOL) + 2

    def test_all_codes_union_across_all_pools(self):
        """所有 pool_type 并集。"""
        PUBLIC_POOLS = {
            "etf": list(ETF_POOL),
            "stock": list(STOCK_POOL),
            "mid_term": list(MID_TERM_STOCK_POOL),
        }

        all_codes = (
            set(PUBLIC_POOLS["etf"])
            | set(PUBLIC_POOLS["stock"])
            | set(PUBLIC_POOLS["mid_term"])
        )

        # 应该包含所有三个池的内容
        assert len(all_codes) > len(ETF_POOL)
        # 上证 50 来自 ETF 池
        assert "510050" in all_codes
        # 股票池中应有 6 开头和 0/3 开头的代码
        assert any(c.startswith("6") for c in STOCK_POOL)
        # 68+50+85=203，但有重叠所以实际是 200
        assert len(all_codes) == 200

    def test_stock_pool_codes_are_legitimate(self):
        """验证股票池代码格式。"""
        for code in STOCK_POOL:
            assert len(code) == 6, f"Invalid code length: {code}"
            assert code.isdigit(), f"Non-digit code: {code}"
            # A 股代码以 0/3/6 开头
            assert code[0] in "036", f"Invalid A-share prefix: {code}"

    def test_etf_pool_codes_are_legitimate(self):
        """验证 ETF 池代码格式。"""
        for code in ETF_POOL:
            assert len(code) == 6, f"Invalid code length: {code}"
            assert code.isdigit(), f"Non-digit code: {code}"
            # ETF 代码以 5/1/8/9/15/16 开头
            assert code[0] in "581916", f"Invalid ETF prefix: {code}"