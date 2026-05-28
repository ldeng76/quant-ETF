"""
15分钟K线数据管理模块

从1分钟K线数据计算生成15分钟K线，支持查询和更新
使用 PostgreSQL 数据库存储。
"""
import pandas as pd
from pathlib import Path
from loguru import logger
from datetime import datetime, timedelta
from typing import Optional, List

from quant_etf.bar_interval import BarInterval, get_interval
from quant_etf.conf import DATA_DIR


def _get_pg_conn():
    """获取 PG 同步连接（单例）"""
    import psycopg2
    from quant_etf.dashboard.config import (
        POSTGRES_HOST, POSTGRES_PORT,
        POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB,
    )
    return psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        database=POSTGRES_DB,
    )


def init_15min_db():
    """初始化15分钟数据数据库"""
    conn = _get_pg_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS minute_bars_15m (
            code        VARCHAR(20) NOT NULL,
            time        TIMESTAMP NOT NULL,
            open        NUMERIC(18, 4),
            high        NUMERIC(18, 4),
            low         NUMERIC(18, 4),
            close       NUMERIC(18, 4),
            volume      BIGINT,
            amount      NUMERIC(18, 2),
            year        INTEGER,
            month       INTEGER,
            day         INTEGER,
            hour        INTEGER,
            minute      INTEGER,
            PRIMARY KEY (code, time)
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_minute_15m_code ON minute_bars_15m(code)
    """)
    conn.commit()
    logger.info("Ensured minute_bars_15m table exists")
    return conn


def resample_to_interval(df_1m: pd.DataFrame, interval: BarInterval) -> pd.DataFrame:
    """
    将1分钟K线重采样为指定周期K线
    :param df_1m: 1分钟K线数据 DataFrame (必须有 time, open, high, low, close, volume, amount 列)
    :param interval: BarInterval 周期配置
    :return: 重采样后的 DataFrame
    """
    if df_1m.empty:
        return pd.DataFrame()

    df = df_1m.copy()
    df = df.set_index("time")

    resampled = (
        df.resample(interval.pandas_freq, label="right", closed="right")
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


def resample_to_15min(df_1m: pd.DataFrame) -> pd.DataFrame:
    """向后兼容：将1分钟K线重采样为15分钟K线"""
    return resample_to_interval(df_1m, get_interval("15m"))


def generate_15min_for_code(code: str, start_date: Optional[datetime] = None) -> int:
    """
    为单个代码生成15分钟K线数据
    :param code: ETF代码
    :param start_date: 开始日期，如果为None则生成全部
    :return: 生成的记录数
    """
    from quant_etf.minute_collector import query_minute_data

    if start_date:
        df_1m = query_minute_data(code, start=start_date)
    else:
        df_1m = query_minute_data(code)

    if df_1m.empty:
        logger.warning(f"No 1min data found for {code}")
        return 0

    df_15m = resample_to_15min(df_1m)

    if df_15m.empty:
        return 0

    conn = _get_pg_conn()
    cur = conn.cursor()

    data = []
    for _, row in df_15m.iterrows():
        data.append((
            code,
            row["time"],
            row["open"],
            row["high"],
            row["low"],
            row["close"],
            int(row["volume"]) if row["volume"] else 0,
            float(row["amount"]) if row["amount"] else 0.0,
            int(row["year"]) if row["year"] else None,
            int(row["month"]) if row["month"] else None,
            int(row["day"]) if row["day"] else None,
            int(row["hour"]) if row["hour"] else None,
            int(row["minute"]) if row["minute"] else None,
        ))

    cur.executemany("""
        INSERT INTO minute_bars_15m (code, time, open, high, low, close, volume, amount, year, month, day, hour, minute)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (code, time) DO UPDATE SET
            open = EXCLUDED.open,
            high = EXCLUDED.high,
            low = EXCLUDED.low,
            close = EXCLUDED.close,
            volume = EXCLUDED.volume,
            amount = EXCLUDED.amount
    """, data)
    conn.commit()

    logger.debug(f"Generated {len(data)} 15min bars for {code}")
    return len(data)


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
    :param query: SQL查询语句（未使用，保留兼容）
    :return: DataFrame
    """
    conn = _get_pg_conn()
    cur = conn.cursor()
    # 直接执行查询（保留兼容性）
    cur.execute(query)
    rows = cur.fetchall()
    if not rows:
        return pd.DataFrame()
    columns = [desc[0] for desc in cur.description]
    return pd.DataFrame(rows, columns=columns)


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
    conn = _get_pg_conn()
    cur = conn.cursor()

    if end_time:
        cur.execute("""
            SELECT * FROM minute_bars_15m
            WHERE code = %s AND time <= %s
            ORDER BY time DESC
            LIMIT %s
        """, [code, end_time, count])
    else:
        cur.execute("""
            SELECT * FROM minute_bars_15m
            WHERE code = %s
            ORDER BY time DESC
            LIMIT %s
        """, [code, count])

    rows = cur.fetchall()
    if not rows:
        return pd.DataFrame()

    columns = [desc[0] for desc in cur.description]
    df = pd.DataFrame(rows, columns=columns)
    df = df.sort_values("time").reset_index(drop=True)
    return df


def update_15min_data(code: str) -> int:
    """
    更新单个代码的15分钟数据（从1分钟重新计算）
    :param code: ETF代码
    :return: 更新数量
    """
    from quant_etf.minute_collector import get_latest_minute_time

    last_1m = get_latest_minute_time(code)
    if not last_1m:
        return generate_15min_for_code(code)

    conn = _get_pg_conn()
    cur = conn.cursor()
    cur.execute("SELECT MAX(time) FROM minute_bars_15m WHERE code = %s", [code])
    result = cur.fetchone()
    last_15m = result[0] if result and result[0] else None

    if not last_15m:
        return generate_15min_for_code(code)

    start_date = last_15m - timedelta(days=1)
    return generate_15min_for_code(code, start_date)


def get_latest_15min_time(code: str) -> Optional[datetime]:
    """
    获取指定代码最新的15分钟K线时间
    :param code: ETF代码
    :return: 最新时间
    """
    conn = _get_pg_conn()
    cur = conn.cursor()
    cur.execute("SELECT MAX(time) FROM minute_bars_15m WHERE code = %s", [code])
    result = cur.fetchone()
    return result[0] if result and result[0] else None


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


def get_minute_bars_for_interval(
    code: str, interval: BarInterval, count: int = 200
) -> pd.DataFrame:
    """
    [DEPRECATED] 请使用 quant_etf.minute_resampler.resample_bars
    """
    from quant_etf.minute_resampler import resample_bars
    return resample_bars(code, interval, count)


# ============================================================
# 兼容层：保留 DuckDB 风格的函数
# ============================================================

def get_15min_db_path() -> Path:
    """兼容函数"""
    return Path("postgresql:minute_bars_15m")


def get_15min_db_connection():
    """兼容函数"""
    return _get_pg_conn()