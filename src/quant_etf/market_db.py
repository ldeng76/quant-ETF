"""
行情数据 PostgreSQL 存储模块

管理 ETF 和股票日线数据的 PostgreSQL 存储，替代原有 DuckDB。
使用 dashboard/db.py 的连接池接口。
"""
import pandas as pd
from loguru import logger
from datetime import datetime
from typing import Optional

from quant_etf.dashboard import db as dashboard_db
from typing import Dict, List

_DAILY_COLUMNS = ["code", "date", "open", "high", "low", "close", "amount", "volume", "pct_chg"]


def load_daily_from_db(table: str, code: str, data_dir=None) -> pd.DataFrame:
    """
    从 PostgreSQL 加载单只证券的日线数据
    :param table: 表名 ("etf_daily" 或 "stock_daily") - 实际都存到 market_daily
    :param code: 证券代码
    :param data_dir: 兼容参数（未使用，PostgreSQL 无需 data_dir）
    :return: DataFrame，date 列为 DatetimeIndex；空结果返回空 DataFrame
    """
    rows = dashboard_db.query(
        """
        SELECT date, open, high, low, close, amount, volume, pct_chg
        FROM market_daily
        WHERE code = %s
        ORDER BY date
        """,
        [code],
    )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    df.index.name = "date"
    return df


def load_daily_batch_from_db(table: str, codes: List[str], data_dir=None) -> Dict[str, pd.DataFrame]:
    """
    从 PostgreSQL 批量加载多只证券的日线数据（一次查询）
    :param table: 表名（未使用，保留兼容性）
    :param codes: 证券代码列表
    :param data_dir: 兼容参数（未使用）
    :return: {code: DataFrame} 字典，仅包含有数据的 code
    """
    if not codes:
        return {}

    rows = dashboard_db.query(
        """
        SELECT code, date, open, high, low, close, amount, volume, pct_chg
        FROM market_daily
        WHERE code = ANY(%s)
        ORDER BY code, date
        """,
        [codes],
    )

    if not rows:
        return {}

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])

    # 按 code 拆分
    result: Dict[str, pd.DataFrame] = {}
    for code, group in df.groupby("code"):
        group = group.drop(columns="code")
        group.set_index("date", inplace=True)
        group.index.name = "date"
        result[code] = group

    return result


def save_daily_to_db(table: str, code: str, df: pd.DataFrame, data_dir=None) -> None:
    """
    保存日线数据到 PostgreSQL（upsert）
    :param table: 表名 ("etf_daily" 或 "stock_daily") - 实际都存到 market_daily
    :param code: 证券代码
    :param df: 日线数据 DataFrame（DatetimeIndex）
    :param data_dir: 兼容参数（未使用，PostgreSQL 无需 data_dir）
    """
    if df.empty:
        return

    df_copy = df.copy()
    if df_copy.index.name != "date":
        df_copy.index.name = "date"
    df_copy = df_copy.reset_index()
    df_copy["code"] = code

    df_copy["date"] = pd.to_datetime(df_copy["date"]).dt.strftime("%Y-%m-%d")

    available_cols = [c for c in _DAILY_COLUMNS if c in df_copy.columns]
    df_copy = df_copy[available_cols]

    # 去重：同一 code + date 只保留最后一行
    if df_copy.duplicated(subset=["code", "date"]).any():
        dup_count = df_copy.duplicated(subset=["code", "date"]).sum()
        df_copy = df_copy.drop_duplicates(subset=["code", "date"], keep="last")
        logger.debug(f"Deduplicated {dup_count} rows for {code}")

    # 批量 upsert（必须在同一个连接上执行 cursor + commit，否则 upsert 不会持久化）
    rows_to_insert = [
        (
            row["code"],
            row["date"],
            row.get("open"),
            row.get("high"),
            row.get("low"),
            row.get("close"),
            row.get("amount"),
            row.get("volume"),
            row.get("pct_chg"),
        )
        for _, row in df_copy.iterrows()
    ]

    conn = dashboard_db.get_pg_conn()
    cur = conn.cursor()
    try:
        cur.executemany("""
            INSERT INTO market_daily (code, date, open, high, low, close, amount, volume, pct_chg)
            VALUES (%s, %s::date, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (code, date) DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                amount = EXCLUDED.amount,
                volume = EXCLUDED.volume,
                pct_chg = EXCLUDED.pct_chg
        """, rows_to_insert)
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to save data for {code}: {e}")
        conn.rollback()
        raise

    logger.debug(f"Saved {len(df_copy)} rows for {code} to market_daily")


def has_data_for_code(table: str, code: str, data_dir=None) -> bool:
    """
    检查表中是否存在指定代码的数据
    :param table: 表名（未使用，保留兼容性）
    :param code: 证券代码
    :param data_dir: 兼容参数（未使用，PostgreSQL 无需 data_dir）
    :return: 是否存在数据
    """
    row = dashboard_db.query_one(
        "SELECT 1 FROM market_daily WHERE code = %s LIMIT 1",
        [code],
    )
    return row is not None


# ============================================================
# 兼容层：保留 DuckDB 风格的函数供旧代码调用
# ============================================================

def get_market_db_path(data_dir=None) -> str:
    """兼容函数，返回 'postgresql' 标记"""
    return "postgresql:market_daily"


def init_market_db(db_path=None):
    """兼容函数，PG 不需要初始化"""
    return None


_connections = {}


def get_market_db_connection(data_dir=None):
    """兼容函数，返回 None（使用 db.py 连接池）"""
    return None


def close_market_db_connection(data_dir=None) -> None:
    """兼容函数"""
    pass


def close_all_market_db_connections() -> None:
    """兼容函数"""
    pass