"""
DuckDB 内存聚合：分钟K线动态重采样

从 PG minute_bars 拉取1分钟数据，用 DuckDB SQL 聚合为目标周期。
正确处理 A 股交易时段边界（午休 11:30-13:00 不跨时段聚合）。
"""
import duckdb
import pandas as pd
from datetime import datetime
from typing import Optional


from quant_etf.bar_interval import BarInterval

# 模块级惰性单例
_duck_conn: duckdb.DuckDBPyConnection | None = None


def _get_duck_conn() -> duckdb.DuckDBPyConnection:
    """获取 DuckDB 内存连接（惰性单例）"""
    global _duck_conn
    if _duck_conn is None:
        _duck_conn = duckdb.connect(database=":memory:")
    return _duck_conn


def _get_pg_conn():
    """获取 PG 同步连接"""
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


def _fetch_1m_single(code: str, count: int, minutes_per_bar: int) -> pd.DataFrame:
    """从 PG 拉取单只股票的1分钟数据"""
    conn = _get_pg_conn()
    try:
        fetch_count = count * minutes_per_bar + 240  # 多拉一天做 buffer
        cur = conn.cursor()
        cur.execute(
            """
            SELECT time, open, high, low, close, volume, amount
            FROM minute_bars
            WHERE code = %s
            ORDER BY time DESC
            LIMIT %s
            """,
            [code, fetch_count],
        )
        rows = cur.fetchall()
        if not rows:
            return pd.DataFrame()
        columns = [desc[0] for desc in cur.description]
        df = pd.DataFrame(rows, columns=columns)
    finally:
        conn.close()

    for c in ("open", "high", "low", "close", "amount"):
        if c in df.columns:
            df[c] = df[c].astype(float)
    if "volume" in df.columns:
        df["volume"] = df["volume"].astype(int)
    df = df.sort_values("time").reset_index(drop=True)
    return df


def _fetch_1m_batch(
    codes: list[str], start_time: Optional[datetime] = None
) -> pd.DataFrame:
    """从 PG 批量拉取多只股票的1分钟数据"""
    conn = _get_pg_conn()
    try:
        cur = conn.cursor()
        if start_time:
            cur.execute(
                """
                SELECT code, time, open, high, low, close, volume, amount
                FROM minute_bars
                WHERE code = ANY(%s) AND time >= %s
                ORDER BY code, time
                """,
                [codes, start_time],
            )
        else:
            cur.execute(
                """
                SELECT code, time, open, high, low, close, volume, amount
                FROM minute_bars
                WHERE code = ANY(%s)
                ORDER BY code, time
                """,
                [codes],
            )
        rows = cur.fetchall()
        if not rows:
            return pd.DataFrame()
        columns = [desc[0] for desc in cur.description]
        df = pd.DataFrame(rows, columns=columns)
    finally:
        conn.close()

    for c in ("open", "high", "low", "close", "amount"):
        if c in df.columns:
            df[c] = df[c].astype(float)
    if "volume" in df.columns:
        df["volume"] = df["volume"].astype(int)
    return df


_AGG_SQL = """
WITH ordered AS (
    SELECT
        code,
        time,
        open,
        high,
        low,
        close,
        volume,
        amount,
        CASE
            WHEN EXTRACT(HOUR FROM time) < 12 THEN 0
            ELSE 1
        END AS session,
        ROW_NUMBER() OVER (
            PARTITION BY code, DATE(time), session
            ORDER BY time
        ) - 1 AS bar_seq
    FROM minute_bars_1m
),
grouped AS (
    SELECT
        code,
        MAX(time) AS time,
        FIRST(open ORDER BY time) AS open,
        MAX(high) AS high,
        MIN(low) AS low,
        LAST(close ORDER BY time) AS close,
        SUM(volume) AS volume,
        SUM(amount) AS amount
    FROM ordered
    GROUP BY code, DATE(time), session, bar_seq // {interval_minutes}
)
SELECT code, time, open, high, low, close, volume, amount
FROM grouped
ORDER BY code, time
"""


def _do_aggregate(df_1m: pd.DataFrame, interval_minutes: int, has_code_col: bool) -> pd.DataFrame:
    """在 DuckDB 中执行聚合"""
    conn = _get_duck_conn()
    conn.register("minute_bars_1m", df_1m)
    result = conn.execute(_AGG_SQL.format(interval_minutes=interval_minutes)).df()
    return result


def resample_bars(
    code: str,
    interval: BarInterval,
    count: int = 200,
) -> pd.DataFrame:
    """
    从1分钟K线重采样为目标周期K线（DuckDB 引擎）
    正确处理 A 股交易时段边界

    :param code: 标的代码
    :param interval: BarInterval 周期配置（不能是日线）
    :param count: 需要的 bar 数量
    :return: DataFrame，列为 time/open/high/low/close/volume/amount
    """
    if interval.is_daily:
        raise ValueError("resample_bars does not support daily interval")

    minutes_per_bar = 240 // interval.bars_per_day

    df_1m = _fetch_1m_single(code, count, minutes_per_bar)
    if df_1m.empty:
        return pd.DataFrame()

    # 单股票没有 code 列，补上以匹配批量 SQL
    df_1m["code"] = code

    result = _do_aggregate(df_1m, minutes_per_bar, has_code_col=True)
    if result.empty:
        return pd.DataFrame()

    # 截取尾部 count 根
    result = result.drop(columns=["code"])
    result = result.tail(count).reset_index(drop=True)
    return result


def resample_bars_batch(
    codes: list[str],
    interval: BarInterval,
    start_time: Optional[datetime] = None,
) -> dict[str, pd.DataFrame]:
    """
    批量重采样（单次 PG 拉取 + DuckDB 聚合）

    :param codes: 标的代码列表
    :param interval: BarInterval 周期配置
    :param start_time: 起始时间（None 则拉全部）
    :return: {code: DataFrame}
    """
    if interval.is_daily:
        raise ValueError("resample_bars_batch does not support daily interval")

    minutes_per_bar = 240 // interval.bars_per_day

    df_1m = _fetch_1m_batch(codes, start_time)
    if df_1m.empty:
        return {c: pd.DataFrame() for c in codes}

    result = _do_aggregate(df_1m, minutes_per_bar, has_code_col=True)
    if result.empty:
        return {c: pd.DataFrame() for c in codes}

    out: dict[str, pd.DataFrame] = {}
    for code in codes:
        sub = result[result["code"] == code].drop(columns=["code"]).reset_index(drop=True)
        out[code] = sub
    return out
