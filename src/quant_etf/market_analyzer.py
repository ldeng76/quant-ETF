"""
市场状态分析模块

基于沪深300指数和ETF池整体表现判断市场状态（牛市/熊市/震荡市）
"""

import pandas as pd
import numpy as np
from loguru import logger
from datetime import datetime, timedelta
from typing import Literal, Tuple
from dataclasses import dataclass
from enum import Enum

import duckdb

from quant_etf.minute_collector import get_db_connection
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


class MarketAnalyzer:
    """市场状态分析器"""

    def __init__(self, index_code: str = "000300"):
        """
        初始化市场分析器
        :param index_code: 指数代码，默认沪深300
        """
        self.index_code = index_code
        self.conn = get_db_connection()

    def get_index_1min_bars(self, days: int = 5) -> pd.DataFrame:
        """
        获取指数1分钟K线数据
        :param days: 获取最近几天
        :return: DataFrame
        """
        start_time = datetime.now() - timedelta(days=days)
        query = f"""
            SELECT time, close
            FROM minute_bars
            WHERE code = '{self.index_code}'
              AND time >= '{start_time.strftime("%Y-%m-%d")}'
            ORDER BY time
        """
        return self.conn.execute(query).df()

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
        获取ETF池整体表现
        :param codes: ETF代码列表
        :param days: 获取最近几天
        :return: (整体收益率, 波动率, 短期均线, 长期均线)
        """
        start_time = datetime.now() - timedelta(days=days)

        avg_returns = []
        avg_closes = []

        for code in codes:
            query = f"""
                SELECT time, close
                FROM minute_bars
                WHERE code = '{code}'
                  AND time >= '{start_time.strftime("%Y-%m-%d")}'
                ORDER BY time
            """
            df = self.conn.execute(query).df()

            if not df.empty and len(df) >= 60:
                ret = self.calculate_returns(df, 60)
                avg_returns.append(ret)
                avg_closes.append(df["close"].tolist())

        if not avg_returns:
            return 0.0, 0.0, 0.0, 0.0

        avg_return = float(np.mean(avg_returns))

        all_closes = np.concatenate(avg_closes)
        df_pool = pd.DataFrame({"close": all_closes[-min(len(all_closes), 240) :]})
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
    获取当前市场状态（便捷函数）
    :param codes: ETF代码列表，如果为None则使用默认ETF_POOL
    :return: MarketState
    """
    if codes is None:
        codes = ETF_POOL

    analyzer = MarketAnalyzer()
    return analyzer.analyze_market(codes)
