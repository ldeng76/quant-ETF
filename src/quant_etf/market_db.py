"""
行情数据 DuckDB 存储模块

管理 ETF 和股票日线数据的 DuckDB 存储，替代原有 CSV 缓存。
遵循 minute_collector.py 的 DuckDB 使用模式。
"""
import duckdb
import pandas as pd
from pathlib import Path
from loguru import logger

from quant_etf.conf import DATA_DIR

_TABLES_SQL = {
    "etf_daily": """
        CREATE TABLE IF NOT EXISTS etf_daily (
            code VARCHAR NOT NULL,
            date TIMESTAMP NOT NULL,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            amount DOUBLE,
            volume DOUBLE,
            pct_chg DOUBLE,
            PRIMARY KEY (code, date)
        )
    """,
    "stock_daily": """
        CREATE TABLE IF NOT EXISTS stock_daily (
            code VARCHAR NOT NULL,
            date TIMESTAMP NOT NULL,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            amount DOUBLE,
            volume DOUBLE,
            pct_chg DOUBLE,
            PRIMARY KEY (code, date)
        )
    """,
}

_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_etf_code_date ON etf_daily(code, date)",
    "CREATE INDEX IF NOT EXISTS idx_stock_code_date ON stock_daily(code, date)",
]

_DAILY_COLUMNS = ["code", "date", "open", "high", "low", "close", "amount", "volume", "pct_chg"]


def get_market_db_path(data_dir: Path = DATA_DIR) -> Path:
    """获取行情数据库路径"""
    return data_dir / "market.duckdb"


def init_market_db(db_path: Path) -> duckdb.DuckDBPyConnection:
    """
    初始化行情数据库，创建表结构和索引
    :param db_path: 数据库文件路径
    :return: 数据库连接
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(db_path))

    for table_sql in _TABLES_SQL.values():
        conn.execute(table_sql)

    for index_sql in _INDEXES_SQL:
        conn.execute(index_sql)

    logger.info(f"Initialized market database: {db_path}")
    return conn


# 单例连接缓存：{str(db_path): connection}
_connections: dict[str, duckdb.DuckDBPyConnection] = {}


def get_market_db_connection(data_dir: Path = DATA_DIR) -> duckdb.DuckDBPyConnection:
    """
    获取数据库连接（单例模式，按路径缓存）
    :param data_dir: 数据目录
    :return: 数据库连接
    """
    db_path = get_market_db_path(data_dir)
    key = str(db_path)
    if key not in _connections:
        _connections[key] = init_market_db(db_path)
    return _connections[key]


def close_market_db_connection(data_dir: Path = DATA_DIR) -> None:
    """关闭数据库连接"""
    db_path = get_market_db_path(data_dir)
    key = str(db_path)
    if key in _connections:
        _connections[key].close()
        del _connections[key]
        logger.info("Closed market database connection")


def close_all_market_db_connections() -> None:
    """关闭所有数据库连接（测试清理用）"""
    for key, conn in list(_connections.items()):
        conn.close()
    _connections.clear()


def load_daily_from_db(table: str, code: str, data_dir: Path = DATA_DIR) -> pd.DataFrame:
    """
    从 DuckDB 加载单只证券的日线数据
    :param table: 表名 ("etf_daily" 或 "stock_daily")
    :param code: 证券代码
    :param data_dir: 数据目录
    :return: DataFrame，date 列为 DatetimeIndex；空结果返回空 DataFrame
    """
    conn = get_market_db_connection(data_dir)
    result = conn.execute(
        f"SELECT date, open, high, low, close, amount, volume, pct_chg "
        f"FROM {table} WHERE code = ? ORDER BY date",
        [code],
    ).fetchdf()

    if result.empty:
        return pd.DataFrame()

    result["date"] = pd.to_datetime(result["date"])
    result.set_index("date", inplace=True)
    result.index.name = "date"
    return result


def save_daily_to_db(table: str, code: str, df: pd.DataFrame, data_dir: Path = DATA_DIR) -> None:
    """
    保存日线数据到 DuckDB（先删后插，确保 upsert 语义）
    :param table: 表名 ("etf_daily" 或 "stock_daily")
    :param code: 证券代码
    :param df: 日线数据 DataFrame（DatetimeIndex）
    :param data_dir: 数据目录
    """
    if df.empty:
        return

    conn = get_market_db_connection(data_dir)

    df_copy = df.copy()
    # 确保 index name 为 "date"，以便 reset_index 后列名正确
    if df_copy.index.name != "date":
        df_copy.index.name = "date"
    df_copy = df_copy.reset_index()
    df_copy["code"] = code

    # 确保 date 列为 datetime 类型
    df_copy["date"] = pd.to_datetime(df_copy["date"])

    # 只保留需要的列
    available_cols = [c for c in _DAILY_COLUMNS if c in df_copy.columns]
    df_copy = df_copy[available_cols]

    # 去重：同一 code + date 只保留最后一行
    if df_copy.duplicated(subset=["code", "date"]).any():
        dup_count = df_copy.duplicated(subset=["code", "date"]).sum()
        df_copy = df_copy.drop_duplicates(subset=["code", "date"], keep="last")
        logger.debug(f"Deduplicated {dup_count} rows for {code}")

    # 先删除该 code 的已有数据（简单 upsert 策略）
    conn.execute(f"DELETE FROM {table} WHERE code = ?", [code])

    # 批量插入
    conn.execute(
        f"INSERT INTO {table} ({', '.join(available_cols)}) "
        f"SELECT {', '.join(available_cols)} FROM df_copy"
    )

    logger.debug(f"Saved {len(df_copy)} rows for {code} to {table}")


def has_data_for_code(table: str, code: str, data_dir: Path = DATA_DIR) -> bool:
    """
    检查表中是否存在指定代码的数据
    :param table: 表名
    :param code: 证券代码
    :param data_dir: 数据目录
    :return: 是否存在数据
    """
    conn = get_market_db_connection(data_dir)
    result = conn.execute(
        f"SELECT 1 FROM {table} WHERE code = ? LIMIT 1",
        [code],
    ).fetchone()
    return result is not None
