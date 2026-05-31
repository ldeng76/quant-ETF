"""
单元测试：scheduler_cache.py
只测 SharedDataCache 的 TTL、get/set、stats、clear。
prefetch 逻辑依赖 market_db（asyncpg），跳过 prefetch 相关集成测试。
"""
import time
import threading
import pandas as pd

from quant_etf.scheduler_cache import SharedDataCache


def make_df(n: int = 10) -> pd.DataFrame:
    return pd.DataFrame(
        {"close": [100.0 + i for i in range(n)]},
        index=pd.date_range("20230101", periods=n),
    )


class TestSharedDataCacheBasic:
    """基本 get/set/clear/stats 测试（无外部依赖）。"""

    def test_get_returns_none_on_empty(self):
        cache = SharedDataCache(ttl=1)
        assert cache.get("000001", "1d") is None

    def test_set_and_get_returns_copy(self):
        cache = SharedDataCache(ttl=300)
        df = make_df()
        cache.set("000001", "1d", df)

        result = cache.get("000001", "1d")
        assert result is not None
        assert result["close"].iloc[0] == 100.0

        # 修改返回的 df 不影响缓存内部
        result.iloc[0, 0] = 999.0
        cached = cache.get("000001", "1d")
        assert cached["close"].iloc[0] == 100.0

    def test_get_returns_none_after_ttl(self):
        cache = SharedDataCache(ttl=1)
        df = make_df()
        cache.set("000001", "1d", df)

        # 立即读取，命中
        assert cache.get("000001", "1d") is not None

        # 等待 TTL 过期
        time.sleep(1.1)
        assert cache.get("000001", "1d") is None

    def test_set_with_none_is_noop(self):
        cache = SharedDataCache(ttl=300)
        cache.set("000001", "1d", None)
        assert cache.get("000001", "1d") is None

    def test_set_with_empty_df_is_noop(self):
        cache = SharedDataCache(ttl=300)
        cache.set("000001", "1d", pd.DataFrame())
        assert cache.get("000001", "1d") is None

    def test_clear(self):
        cache = SharedDataCache(ttl=300)
        cache.set("000001", "1d", make_df())
        cache.set("000002", "60m", make_df())
        assert cache.size == 2
        cache.clear()
        assert cache.size == 0

    def test_stats(self):
        cache = SharedDataCache(ttl=300)
        cache.set("000001", "1d", make_df())
        cache.set("000002", "1d", make_df())
        cache.set("000003", "60m", make_df())
        stats = cache.stats()
        assert stats["1d"] == 2
        assert stats["60m"] == 1

    def test_size(self):
        cache = SharedDataCache(ttl=300)
        for code in ["000001", "000002", "000003"]:
            cache.set(code, "1d", make_df())
        assert cache.size == 3

    def test_different_intervals_separate(self):
        cache = SharedDataCache(ttl=300)
        cache.set("000001", "1d", make_df(5))
        cache.set("000001", "60m", make_df(10))
        assert cache.size == 2
        assert cache.get("000001", "1d") is not None
        assert cache.get("000001", "60m") is not None

    def test_different_codes_separate(self):
        cache = SharedDataCache(ttl=300)
        cache.set("000001", "1d", make_df())
        cache.set("000002", "1d", make_df())
        assert cache.size == 2
        assert cache.stats()["1d"] == 2


class TestSharedDataCacheTTL:
    """TTL 边界情况。"""

    def test_custom_ttl(self):
        cache = SharedDataCache(ttl=60)
        df = make_df()
        cache.set("000001", "1d", df)
        time.sleep(1.1)
        # 1 秒远小于 60 秒 TTL，应该仍在
        assert cache.get("000001", "1d") is not None

    def test_ttl_is_per_entry(self):
        cache = SharedDataCache(ttl=2)
        cache.set("000001", "1d", make_df())
        time.sleep(1)
        assert cache.get("000001", "1d") is not None  # 未过期
        time.sleep(1.1)
        assert cache.get("000001", "1d") is None  # 已过期


class TestSharedDataCacheConcurrency:
    """并发安全。"""

    def test_concurrent_set_and_get(self):
        errors: list[Exception] = []

        def writer(code: str):
            try:
                for _ in range(100):
                    _cache.set(code, "1d", make_df())
            except Exception as e:
                errors.append(e)

        def reader(code: str):
            try:
                for _ in range(100):
                    _cache.get(code, "1d")
            except Exception as e:
                errors.append(e)

        _cache = SharedDataCache(ttl=300)
        threads = []
        for i in range(5):
            code = f"00000{i}"
            threads.append(threading.Thread(target=writer, args=(code,)))
            threads.append(threading.Thread(target=reader, args=(code,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Concurrency errors: {errors}"