"""
市场状态分析模块

基于沪深300指数和ETF池整体表现判断市场状态（牛市/熊市/震荡市）

缓存优化:
- MarketStateCache 类提供 TTL 缓存，避免频繁数据库查询
- 使用 get_market_state_cached() 获取带缓存的市场状态
"""

import time
import pandas as pd
import numpy as np
from loguru import logger
from datetime import datetime, timedelta
from typing import Literal, Tuple, Optional
from dataclasses import dataclass
from enum import Enum
from sqlalchemy import text
from quant_etf.dashboard.db import get_pg_conn, get_pg_engine, query_one
from quant_etf.conf import ETF_POOL

class MarketType(Enum):
    """市场类型枚举"""

    BULL = "牛市"
    BEAR = "熊市"
    RANGE = "震荡市"
    UNKNOWN = "未知"


@dataclass
class MarketState:
    """市场状态数据类"""

    time: datetime
    market_type: MarketType
    index_return: float
    etf_pool_return: float
    volatility: float
    trend_strength: float
    index_ma_short: float
    index_ma_long: float
    etf_pool_ma_short: float
    etf_pool_ma_long: float


class MarketStateCache:
    """市场状态内存缓存（TTL 60秒）"""

    def __init__(self, ttl: int = 60):
        self._cache: Optional[MarketState] = None
        self._fetched_at: float = 0
        self._ttl = ttl

    def get(self) -> Optional[MarketState]:
        """获取缓存的市场状态，如果过期返回 None"""
        if self._cache and (time.time() - self._fetched_at) < self._ttl:
            logger.debug("Using cached market state")
            return self._cache
        return None

    def set(self, state: MarketState) -> None:
        """设置缓存"""
        self._cache = state
        self._fetched_at = time.time()
        logger.debug(f"Market state cached (TTL: {self._ttl}s)")

    def invalidate(self) -> None:
        """清除缓存"""
        self._cache = None
        self._fetched_at = 0
        logger.debug("Market state cache invalidated")


# 全局缓存实例（TTL 4分钟）
_market_state_cache = MarketStateCache(ttl=240)


class MarketAnalyzer:
    """市场状态分析器"""

    def __init__(self, index_code: str = "000300"):
        """
        初始化市场分析器
        :param index_code: 指数代码，默认沪深300
        """
        self.index_code = index_code

    def get_index_1min_bars(self, days: int = 5) -> pd.DataFrame:
        """
        获取指数5分钟K线数据
        :param days: 获取最近几天
        :return: DataFrame
        """
        start_time = datetime.now() - timedelta(days=days)
        with get_pg_engine().connect() as conn:
            df = pd.read_sql(
                text("""
                SELECT time, close
                FROM minute_bars
                WHERE code = :code AND time >= :start_time
                ORDER BY time
                """),
                conn,
                params={"code": self.index_code, "start_time": start_time.strftime("%Y-%m-%d")},
            )
            return df

    def calculate_returns(self, df: pd.DataFrame, period: int = 60) -> float:
        """
        计算收益率
        :param df: 包含close的DataFrame
        :param period: 周期（分钟数）
        :return: 收益率
        """
        if df.empty or len(df) < period:
            return 0.0

        current = df.iloc[-1]["close"]
        past = df.iloc[-period]["close"]
        return (current - past) / past

    def calculate_volatility(self, df: pd.DataFrame, period: int = 240) -> float:
        """
        计算波动率（标准差）
        :param df: 包含close的DataFrame
        :param period: 周期（分钟数）
        :return: 波动率
        """
        if df.empty or len(df) < period:
            return 0.0

        recent = df.tail(period)
        returns = recent["close"].pct_change().dropna()
        return float(returns.std() * np.sqrt(240))

    def calculate_moving_averages(
        self, df: pd.DataFrame, short: int = 60, long: int = 240
    ) -> Tuple[float, float]:
        """
        计算移动平均线
        :param df: 包含close的DataFrame
        :param short: 短期均线周期
        :param long: 长期均线周期
        :return: (短期均线, 长期均线)
        """
        if df.empty:
            return 0.0, 0.0

        ma_short = df["close"].tail(short).mean()
        ma_long = df["close"].tail(long).mean()
        return float(ma_short), float(ma_long)

    def get_etf_pool_performance(
        self, codes: list[str], days: int = 3
    ) -> Tuple[float, float, float, float]:
        """
        获取ETF池整体表现（批量查询优化）

        使用单个 SQL 查询替代循环查询，大幅减少数据库往返次数。

        :param codes: ETF代码列表
        :param days: 获取最近几天
        :return: (整体收益率, 波动率, 短期均线, 长期均线)
        """
        if not codes:
            return 0.0, 0.0, 0.0, 0.0

        start_time = datetime.now() - timedelta(days=days)

        # 批量查询：一次查询所有 ETF 的数据
        with get_pg_conn() as conn:
            # 使用单次查询获取所有 ETF 的最新 240 条数据
            placeholders = ",".join(["%s"] * len(codes))
            df = pd.read_sql(
                f"""
                WITH ranked_bars AS (
                    SELECT
                        code, time, close,
                        ROW_NUMBER() OVER (
                            PARTITION BY code
                            ORDER BY time DESC
                        ) as rn
                    FROM minute_bars
                    WHERE code IN ({placeholders}) AND time >= %s
                )
                SELECT code, time, close
                FROM ranked_bars
                WHERE rn <= 240
                ORDER BY code, time DESC
                """,
                conn,
                params=list(codes) + [start_time.strftime("%Y-%m-%d")],
            )

        if df.empty:
            return 0.0, 0.0, 0.0, 0.0

        # 计算每个 ETF 的收益率
        returns = []
        for code in codes:
            code_df = df[df["code"] == code].sort_values("time")
            if len(code_df) >= 60:
                ret = self.calculate_returns(code_df, 60)
                returns.append(ret)

        if not returns:
            return 0.0, 0.0, 0.0, 0.0

        avg_return = float(np.mean(returns))

        # 计算池整体波动率和均线（使用所有 ETF 的最新数据）
        latest_closes = []
        for code in codes:
            code_df = df[df["code"] == code].sort_values("time").head(240)
            if not code_df.empty:
                latest_closes.extend(code_df["close"].tolist())

        if not latest_closes:
            return avg_return, 0.0, 0.0, 0.0

        df_pool = pd.DataFrame({"close": latest_closes[-min(len(latest_closes), 240) :]})
        volatility = self.calculate_volatility(df_pool, min(len(df_pool), 240))
        ma_short, ma_long = self.calculate_moving_averages(df_pool, 60, 240)

        return avg_return, volatility, ma_short, ma_long

    def analyze_market(self, codes: list[str]) -> MarketState:
        """
        分析市场状态
        :param codes: ETF代码列表
        :return: MarketState
        """
        df_index = self.get_index_1min_bars(days=5)

        if df_index.empty:
            logger.warning("No index data available")
            return MarketState(
                time=datetime.now(),
                market_type=MarketType.UNKNOWN,
                index_return=0.0,
                etf_pool_return=0.0,
                volatility=0.0,
                trend_strength=0.0,
                index_ma_short=0.0,
                index_ma_long=0.0,
                etf_pool_ma_short=0.0,
                etf_pool_ma_long=0.0,
            )

        index_return = self.calculate_returns(df_index, 60)
        index_volatility = self.calculate_volatility(df_index, 240)
        index_ma_short, index_ma_long = self.calculate_moving_averages(
            df_index, 60, 240
        )

        etf_pool_return, etf_pool_volatility, etf_pool_ma_short, etf_pool_ma_long = (
            self.get_etf_pool_performance(codes)
        )

        volatility = (index_volatility + etf_pool_volatility) / 2
        trend_strength = (index_return + etf_pool_return) / 2

        ma_bullish_index = index_ma_short > index_ma_long
        ma_bullish_pool = etf_pool_ma_short > etf_pool_ma_long

        market_type = self._determine_market_type(
            index_return, etf_pool_return, volatility, ma_bullish_index, ma_bullish_pool
        )

        return MarketState(
            time=datetime.now(),
            market_type=market_type,
            index_return=index_return,
            etf_pool_return=etf_pool_return,
            volatility=volatility,
            trend_strength=trend_strength,
            index_ma_short=index_ma_short,
            index_ma_long=index_ma_long,
            etf_pool_ma_short=etf_pool_ma_short,
            etf_pool_ma_long=etf_pool_ma_long,
        )

    def _determine_market_type(
        self,
        index_return: float,
        etf_pool_return: float,
        volatility: float,
        ma_bullish_index: bool,
        ma_bullish_pool: bool,
    ) -> MarketType:
        """
        根据各项指标判断市场类型
        """
        avg_return = (index_return + etf_pool_return) / 2

        if volatility > 0.03:
            return MarketType.BEAR

        if avg_return > 0.02 and ma_bullish_index and ma_bullish_pool:
            return MarketType.BULL

        if avg_return < -0.02:
            return MarketType.BEAR

        if abs(avg_return) <= 0.02:
            return MarketType.RANGE

        if avg_return > 0:
            if ma_bullish_index or ma_bullish_pool:
                return MarketType.BULL
            else:
                return MarketType.RANGE
        else:
            if ma_bullish_index or ma_bullish_pool:
                return MarketType.RANGE
            else:
                return MarketType.BEAR


def get_market_state(codes: list[str] = None) -> MarketState:
    """
    获取当前市场状态（便捷函数，无缓存）
    :param codes: ETF代码列表，如果为None则使用默认ETF_POOL
    :return: MarketState
    """
    if codes is None:
        codes = ETF_POOL

    analyzer = MarketAnalyzer()
    return analyzer.analyze_market(codes)


def get_market_state_cached(codes: list[str] = None, cache_ttl: int = 240) -> MarketState:
    """
    获取当前市场状态（带多层缓存，TTL 4分钟）

    缓存层级（按优先级）：
    1. 内存缓存（TTL 内） - 最快
    2. 数据库快照（5分钟内） - 快速，无需重新计算
    3. 重新计算 - 最慢，但获取最新数据

    :param codes: ETF代码列表，如果为None则使用默认ETF_POOL
    :param cache_ttl: 内存缓存过期时间（秒），默认60秒
    :return: MarketState
    """
    if codes is None:
        codes = ETF_POOL

    # 1. 检查内存缓存
    cached = _market_state_cache.get()
    if cached is not None:
        return cached

    # 2. 尝试从数据库快照加载（5分钟内的预计算结果）
    snapshot = get_latest_snapshot(max_age_seconds=300)
    if snapshot is not None:
        logger.info("Loaded market state from database snapshot")
        _market_state_cache.set(snapshot)
        return snapshot

    # 3. 缓存未命中，执行完整分析
    logger.info("Market state cache miss, analyzing market...")
    analyzer = MarketAnalyzer()
    state = analyzer.analyze_market(codes)

    # 更新内存缓存
    _market_state_cache.set(state)

    # 保存数据库快照供后续使用
    try:
        save_market_snapshot(state)
    except Exception as e:
        logger.warning(f"Failed to save market snapshot: {e}")

    return state


def invalidate_market_state_cache() -> None:
    """清除市场状态缓存（供外部调用）"""
    _market_state_cache.invalidate()


def save_market_snapshot(state: MarketState) -> None:
    """保存市场状态快照到数据库"""
    from quant_etf.dashboard.db import execute

    execute(
        """
        INSERT INTO market_snapshot (
            snapshot_time, market_type, index_return,
            etf_pool_return, volatility, trend_strength,
            index_ma_short, index_ma_long,
            etf_pool_ma_short, etf_pool_ma_long
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        [
            state.time,
            state.market_type.value,
            float(state.index_return),
            float(state.etf_pool_return),
            float(state.volatility),
            float(state.trend_strength),
            float(state.index_ma_short),
            float(state.index_ma_long),
            float(state.etf_pool_ma_short),
            float(state.etf_pool_ma_long),
        ],
    )
    logger.debug("Market snapshot saved")


def get_latest_snapshot(max_age_seconds: int = 300) -> Optional[MarketState]:
    """
    获取最近的市场状态快照（最多5分钟内的）

    用于加速启动：当存在有效快照时直接使用，避免重新计算。

    :param max_age_seconds: 最大允许的快照年龄（秒）
    :return: MarketState 或 None（如果快照不存在或已过期）
    """
    row = query_one(
        f"""
        SELECT snapshot_time, market_type, index_return,
               etf_pool_return, volatility, trend_strength,
               index_ma_short, index_ma_long,
               etf_pool_ma_short, etf_pool_ma_long
        FROM market_snapshot
        WHERE snapshot_time >= NOW() - INTERVAL '{max_age_seconds} seconds'
        ORDER BY snapshot_time DESC
        LIMIT 1
        """
    )

    if not row:
        return None

    return MarketState(
        time=row["snapshot_time"],
        market_type=MarketType(row["market_type"]),
        index_return=float(row["index_return"]),
        etf_pool_return=float(row["etf_pool_return"]),
        volatility=float(row["volatility"]),
        trend_strength=float(row["trend_strength"]),
        index_ma_short=float(row["index_ma_short"]),
        index_ma_long=float(row["index_ma_long"]),
        etf_pool_ma_short=float(row["etf_pool_ma_short"]),
        etf_pool_ma_long=float(row["etf_pool_ma_long"]),
    )
