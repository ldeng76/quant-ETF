"""
15分钟K线数据管理模块

从1分钟K线数据计算生成15分钟K线，支持查询和更新
"""

import pandas as pd
from pathlib import Path
from loguru import logger
from datetime import datetime, timedelta
from typing import Optional, List
from dataclasses import dataclass

import duckdb

from quant_etf.minute_collector import get_db_connection, query_minute_data
from quant_etf.conf import DATA_DIR


def get_15min_db_path() -> Path:
    """
    获取15分钟数据数据库文件路径
    """
    minute_dir = DATA_DIR / "minute"
    minute_dir.mkdir(parents=True, exist_ok=True)
    return minute_dir / "minute_data_15m.duckdb"


def init_15min_db() -> duckdb.DuckDBPyConnection:
    """
    初始化15分钟数据数据库
    """
    db_path = get_15min_db_path()
    conn = duckdb.connect(str(db_path))

    conn.execute("""
        CREATE TABLE IF NOT EXISTS minute_bars_15m (
            code VARCHAR,
            time TIMESTAMP,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume BIGINT,
            amount DOUBLE,
            year INTEGER,
            month INTEGER,
            day INTEGER,
            hour INTEGER,
            minute INTEGER,
            PRIMARY KEY (code, time)
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_code_time ON minute_bars_15m(code, time)
    """)

    logger.info(f"Initialized 15min data database: {db_path}")
    return conn


def get_15min_db_connection() -> duckdb.DuckDBPyConnection:
    """
    获取15分钟数据库连接（单例模式）
    """
    if not hasattr(get_15min_db_connection, "_conn"):
        get_15min_db_connection._conn = init_15min_db()
    return get_15min_db_connection._conn


def resample_to_15min(df_1m: pd.DataFrame) -> pd.DataFrame:
    """
    将1分钟K线重采样为15分钟K线
    :param df_1m: 1分钟K线数据 DataFrame
    :return: 15分钟K线数据 DataFrame
    """
    if df_1m.empty:
        return pd.DataFrame()

    df = df_1m.copy()
    df = df.set_index("time")

    resampled = (
        df.resample("15T", label="right", closed="right")
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
                "amount": "sum",
            }
        )
        .dropna()
    )

    resampled = resampled.reset_index()

    resampled["year"] = resampled["time"].dt.year
    resampled["month"] = resampled["time"].dt.month
    resampled["day"] = resampled["time"].dt.day
    resampled["hour"] = resampled["time"].dt.hour
    resampled["minute"] = resampled["time"].dt.minute

    return resampled


def generate_15min_for_code(code: str, start_date: Optional[datetime] = None) -> int:
    """
    为单个代码生成15分钟K线数据
    :param code: ETF代码
    :param start_date: 开始日期，如果为None则生成全部
    :return: 生成的记录数
    """
    conn_1m = get_db_connection()
    conn_15m = get_15min_db_connection()

    where_clause = f"code = '{code}'"
    if start_date:
        where_clause += f" AND time >= '{start_date.strftime('%Y-%m-%d')}'"

    query = f"""
        SELECT time, open, high, low, close, volume, amount
        FROM minute_bars
        WHERE {where_clause}
        ORDER BY time
    """

    df_1m = conn_1m.execute(query).df()

    if df_1m.empty:
        logger.warning(f"No 1min data found for {code}")
        return 0

    df_15m = resample_to_15min(df_1m)

    if df_15m.empty:
        return 0

    df_15m["code"] = code
    columns = [
        "code",
        "time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "year",
        "month",
        "day",
        "hour",
        "minute",
    ]
    df_15m = df_15m[columns]

    conn_15m.execute("""
        INSERT OR REPLACE INTO minute_bars_15m
        (code, time, open, high, low, close, volume, amount, year, month, day, hour, minute)
        SELECT code, time, open, high, low, close, volume, amount, year, month, day, hour, minute
        FROM df_15m
    """)

    logger.debug(f"Generated {len(df_15m)} 15min bars for {code}")
    return len(df_15m)


def generate_15min_for_pool(
    codes: List[str], start_date: Optional[datetime] = None
) -> int:
    """
    为ETF池生成15分钟K线数据
    :param codes: ETF代码列表
    :param start_date: 开始日期
    :return: 总记录数
    """
    total = 0
    for code in codes:
        try:
            count = generate_15min_for_code(code, start_date)
            total += count
        except Exception as e:
            logger.error(f"Failed to generate 15min data for {code}: {e}")

    return total


def query_15min_data(query: str) -> pd.DataFrame:
    """
    查询15分钟数据
    :param query: SQL查询语句
    :return: DataFrame
    """
    conn = get_15min_db_connection()
    return conn.execute(query).df()


def get_15min_bars(
    code: str, count: int = 200, end_time: Optional[datetime] = None
) -> pd.DataFrame:
    """
    获取单个代码的15分钟K线数据
    :param code: ETF代码
    :param count: 获取数量
    :param end_time: 结束时间，默认为最新
    :return: DataFrame
    """
    where_clause = f"code = '{code}'"
    if end_time:
        where_clause += f" AND time <= '{end_time.strftime('%Y-%m-%d %H:%M:%S')}'"

    query = f"""
        SELECT * FROM minute_bars_15m
        WHERE {where_clause}
        ORDER BY time DESC
        LIMIT {count}
    """

    df = query_15min_data(query)
    if not df.empty:
        df = df.sort_values("time").reset_index(drop=True)

    return df


def update_15min_data(code: str) -> int:
    """
    更新单个代码的15分钟数据（从1分钟重新计算）
    :param code: ETF代码
    :return: 更新数量
    """
    conn_1m = get_db_connection()

    last_15min = conn_1m.execute(f"""
        SELECT MAX(time) as last_time FROM minute_bars WHERE code = '{code}'
    """).fetchone()[0]

    if not last_15min:
        return generate_15min_for_code(code)

    conn_15m = get_15min_db_connection()
    last_15min_time = conn_15m.execute(f"""
        SELECT MAX(time) as last_time FROM minute_bars_15m WHERE code = '{code}'
    """).fetchone()[0]

    if not last_15min_time:
        return generate_15min_for_code(code)

    start_date = last_15min_time - timedelta(days=1)
    return generate_15min_for_code(code, start_date)


def get_latest_15min_time(code: str) -> Optional[datetime]:
    """
    获取指定代码最新的15分钟K线时间
    :param code: ETF代码
    :return: 最新时间
    """
    conn = get_15min_db_connection()
    result = conn.execute(f"""
        SELECT MAX(time) FROM minute_bars_15m WHERE code = '{code}'
    """).fetchone()

    return result[0] if result[0] else None


def get_pool_15min_bars(codes: List[str], count: int = 200) -> dict[str, pd.DataFrame]:
    """
    获取ETF池的15分钟K线数据
    :param codes: ETF代码列表
    :param count: 每个代码获取数量
    :return: 字典 {code: DataFrame}
    """
    result = {}
    for code in codes:
        df = get_15min_bars(code, count)
        if not df.empty:
            result[code] = df
    return result
