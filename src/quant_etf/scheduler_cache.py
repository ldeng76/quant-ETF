"""
共享数据缓存层

进程内字典，按 (code, interval) 缓存 K 线 DataFrame。
TTL = 300s，超时重新拉取。
"""
import time
import threading
from typing import Dict, Optional, Set

import pandas as pd
from loguru import logger


# 全局 TTL（秒）
CACHE_TTL = 300


class SharedDataCache:
    """
    线程安全的进程内缓存。

    结构：_cache[(code, interval)] = (df, timestamp)
    """

    def __init__(self, ttl: int = CACHE_TTL):
        self._cache: Dict[tuple[str, str], tuple[pd.DataFrame, float]] = {}
        self._ttl = ttl
        self._lock = threading.RLock()

    def get(self, code: str, interval: str) -> Optional[pd.DataFrame]:
        """
        获取缓存的 K 线 DataFrame。
        过期返回 None（需调用方重新 prefetch）。
        """
        key = (code, interval)
        with self._lock:
            if key not in self._cache:
                return None
            df, ts = self._cache[key]
            if time.time() - ts > self._ttl:
                del self._cache[key]
                return None
            return df.copy()

    def set(self, code: str, interval: str, df: pd.DataFrame) -> None:
        """写入缓存。DataFrame 被复制一份存入。"""
        if df is None or df.empty:
            return
        key = (code, interval)
        with self._lock:
            self._cache[key] = (df.copy(), time.time())

    def prefetch(self, codes: Set[str], interval: str, bar_count: int = 500) -> int:
        """
        批量预热缓存：从数据源加载所有证券的 K 线数据。

        策略：
        - '1d' → load_daily_from_db（本地 DuckDB/PostgreSQL 日线）
        - '60m'/'30m'/'15m' → get_security_bars（通达信/网络）

        返回成功加载的证券数量。单个证券失败不中断，记录 warning 后继续。
        """
        from quant_etf.bar_interval import get_interval

        loaded = 0
        interval_obj = get_interval(interval)
        if interval_obj is None:
            logger.error(f"[Cache] Unknown interval: {interval}")
            return 0

        # 如果是日线，从本地 DB 批量加载
        if interval == "1d":
            return self._prefetch_daily(codes, bar_count)

        # 分钟线，逐个证券拉取
        for code in codes:
            if (code, interval) in self._cache and time.time() - self._cache[(code, interval)][1] <= self._ttl:
                loaded += 1
                continue
            try:
                df = self._fetch_bars(code, interval_obj, bar_count)
                if df is not None and not df.empty:
                    self.set(code, interval, df)
                    loaded += 1
                else:
                    logger.warning(f"[Cache] No data for {code} @ {interval}")
            except Exception as e:
                logger.warning(f"[Cache] Failed to fetch {code} @ {interval}: {e}")

        return loaded

    def _prefetch_daily(self, codes: Set[str], bar_count: int) -> int:
        """批量加载日线数据（单证券查询）。"""
        from quant_etf.market_db import load_daily_from_db

        loaded = 0
        for code in codes:
            if (code, "1d") in self._cache and time.time() - self._cache[(code, "1d")][1] <= self._ttl:
                loaded += 1
                continue
            try:
                df = load_daily_from_db(code, n=bar_count)
                if df is not None and not df.empty:
                    self.set(code, "1d", df)
                    loaded += 1
                else:
                    logger.warning(f"[Cache] No daily data for {code}")
            except Exception as e:
                logger.warning(f"[Cache] Failed to load daily for {code}: {e}")
        return loaded

    def _fetch_bars(self, code: str, interval_obj, bar_count: int) -> Optional[pd.DataFrame]:
        """从数据源获取 K 线并复权。"""
        from quant_etf.tdx import get_security_bars, adjust_price_qfq

        df = get_security_bars(code, interval_obj, n=bar_count)
        if df is None or df.empty:
            return None
        df = adjust_price_qfq(df, code)
        return df

    def clear(self) -> None:
        """清空缓存（测试用）。"""
        with self._lock:
            self._cache.clear()

    @property
    def size(self) -> int:
        """当前缓存条目数。"""
        with self._lock:
            return len(self._cache)

    def stats(self) -> Dict[str, int]:
        """按 interval 统计缓存命中数。"""
        result: Dict[str, int] = {}
        with self._lock:
            for (code, interval) in self._cache:
                result[interval] = result.get(interval, 0) + 1
        return result


# 全局单例（进程内共享）
_global_cache: Optional[SharedDataCache] = None


def get_cache() -> SharedDataCache:
    """获取全局缓存实例。"""
    global _global_cache
    if _global_cache is None:
        _global_cache = SharedDataCache()
    return _global_cache