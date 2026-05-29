"""
分钟级K线数据采集器模块

提供获取、存储和管理分钟级K线数据的功能。
使用 PostgreSQL 数据库存储。
"""
import pandas as pd
from pathlib import Path
from loguru import logger
from datetime import datetime, time, timedelta
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

from pytdx.hq import TdxHq_API
from pytdx.params import TDXParams
from pytdx.config.hosts import hq_hosts

from quant_etf.tdx import CUSTOM_HQ_HOSTS, _set_cached_server, _get_cached_server
from quant_etf.conf import DATA_DIR, ALL_POOL
import time as time_module
import psutil
import subprocess as _subprocess

_server_failures: dict[str, float] = {}
SERVER_COOLDOWN = 120

# PostgreSQL 同步连接（用于数据存储）
_pg_conn = None


def _get_pg_conn():
    """获取 PG 同步连接（单例）"""
    global _pg_conn
    if _pg_conn is None:
        import psycopg2
        from quant_etf.dashboard.config import (
            POSTGRES_HOST, POSTGRES_PORT,
            POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB,
        )
        _pg_conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            database=POSTGRES_DB,
        )
        logger.info("PostgreSQL connection created for minute collector")
    return _pg_conn


def close_pg_conn():
    """关闭 PG 连接"""
    global _pg_conn
    if _pg_conn:
        _pg_conn.close()
        _pg_conn = None
        logger.info("PostgreSQL connection closed")


def get_local_tdx_server() -> tuple[str, int] | None:
    """
    通过本地运行的通达信进程自动发现行情服务器地址
    :return: (ip, port) 元组，如果未找到则返回 None
    """
    # 先查共享缓存
    cached = _get_cached_server()
    if cached:
        return cached

    # 查找通达信主进程 PID
    tdx_pid = None
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if proc.info["name"] and "tdxw.exe" == proc.info["name"].lower():
                tdx_pid = proc.pid
                break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if not tdx_pid:
        logger.debug("TdxW.exe process not found")
        return None

    # 通过 netstat 查找连接到 7709 端口的连接
    try:
        result = _subprocess.run(
            f'netstat -ano | findstr "{tdx_pid}" | findstr "7709"',
            shell=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.stdout.strip():
            for line in result.stdout.strip().split("\n"):
            if len(parts) >= 3 and parts[3] == "ESTABLISHED":
                remote = parts[2]
                ip, port_str = remote.rsplit(":", 1)
                port = int(port_str)
                _set_cached_server(ip, port)
                return ip, port
        logger.debug(f"Failed to discover TDX server from local process: {e}")

    return None


def code_to_market(code: str) -> int:
    """
    根据证券代码判断市场代码
    :param code: 证券代码 (e.g. "510050", "000001")
    :return: 市场代码 0:深圳，1:上海
    """
    if code.startswith(("5", "6")):
        return TDXParams.MARKET_SH
    elif code.startswith(("0", "1", "3")):
        return TDXParams.MARKET_SZ
    else:
        return TDXParams.MARKET_SZ


def _normalize_bar_data(raw_data: list[dict]) -> list[dict]:
    """为 pytdx 返回的数据添加 time 字段（datetime 对象），供 cli.py 过滤使用"""
    if not raw_data:
        return []
    result = []
    for b in raw_data:
        if isinstance(b, dict) and "year" in b:
            b["time"] = datetime(
                year=b["year"], month=b["month"], day=b["day"],
                hour=b.get("hour", 0), minute=b.get("minute", 0)
            )
        result.append(b)
    return result


_MAX_BARS_PER_CALL = 800


def _fetch_bars_paginated(api, market: int, code: str, count: int) -> list[dict]:
    """通过分页从已连接的 api 获取分钟 K 线，单次最多 800 条"""
    all_bars: list[dict] = []
    fetched = 0
    while fetched < count:
        batch_size = min(_MAX_BARS_PER_CALL, count - fetched)
        data = api.get_security_bars(
            category=8, market=market, code=code, start=fetched, count=batch_size
        )
        if not data:
            break
        all_bars.extend(data)
        fetched += len(data)
        if len(data) < batch_size:
            break
    return _normalize_bar_data(all_bars)


def get_minute_bars(
    code: str,
    count: int = 500,
    server: Optional[str] = None,
    port: int = 7709,
    max_servers: int = 5,
) -> list[dict]:
    """
    获取证券的分钟级K线数据（支持分页，count 可超过 800）
    :param code: 证券代码 (e.g. "510050", "000001")
    :param count: 获取数量
    :param server: 行情服务器 IP（如果为 None，则自动尝试多个服务器）
    :param port: 行情服务器端口
    :param max_servers: 最多尝试的服务器数量
    :return: list of dicts 包含分钟级K线数据
    """
    api = TdxHq_API()
    market = code_to_market(code)

    # 如果未指定服务器，使用自动发现
    if server is None:
        discovered = get_local_tdx_server()
        if discovered:
            server, port = discovered
            try:
                if api.connect(server, port):
                    time_module.sleep(0.5)
                    data = _fetch_bars_paginated(api, market, code, count)
                    api.disconnect()
                    return data
            except Exception as e:
                logger.warning(f"Local TDX server failed: {e!r}")

        # 使用配置的服务器列表
        for host_info in hq_hosts[:max_servers]:
            try:
                host_ip = host_info[1]
                host_port = host_info[2]
                if api.connect(host_ip, host_port):
                    time_module.sleep(0.5)
                    data = _fetch_bars_paginated(api, market, code, count)
                    api.disconnect()
                    return data
            except Exception as e:
                logger.debug(f"Trying {host_info[1]}:{host_info[2]} failed: {e}")
                continue

        return []

    # 使用指定的服务器
    try:
        if api.connect(server, port):
            time_module.sleep(0.5)
            data = _fetch_bars_paginated(api, market, code, count)
            api.disconnect()
            return data
    except Exception as e:
        logger.error(f"Failed to get minute bars for {code}: {e}")

    return []



def collect_for_pool(
    codes: list[str],
    count: int = 500,
    server: Optional[str] = None,
    port: int = 7709,
    max_servers: int = 5,
) -> dict[str, list[dict]]:
    """
    批量获取多个证券的分钟K线数据
    :param codes: 证券代码列表
    :param count: 每个证券获取数量
    :param server: 服务器 IP
    :param port: 服务器端口
    :param max_servers: 最大服务器尝试数
    :return: {code: data_list} 字典
    """
    result = {}

    # 使用线程池并发获取
    def fetch_one(code: str) -> tuple[str, list[dict]]:
        data = get_minute_bars(code, count, server, port, max_servers)
        return code, data

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_one, code) for code in codes]
        for future in futures:
            code, data = future.result()
            if data:
                result[code] = data

    return result


def get_db_connection():
    """兼容函数，返回 PG 连接"""
    return _get_pg_conn()


def close_db_connection():
    """关闭数据库连接"""
    close_pg_conn()


def init_minute_db():
    """初始化分钟数据数据库（PG 不需要初始化，确保表存在即可）"""
    conn = _get_pg_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS minute_bars (
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
        CREATE INDEX IF NOT EXISTS idx_minute_bars_code ON minute_bars(code)
    """)
    conn.commit()
    logger.info("Ensured minute_bars table exists")
    return conn


def save_minute_data(code: str, df: pd.DataFrame) -> bool:
    """
    保存分钟数据到 PostgreSQL 数据库
    :param code: 证券代码
    :param df: 包含分钟数据的 DataFrame
    :return: 是否保存成功
    """
    if df.empty:
        return False

    try:
        conn = _get_pg_conn()

        df = df.reset_index()
        if "time" not in df.columns:
            return False

        # pytdx 返回的列名是 vol，统一为 volume
        if "vol" in df.columns and "volume" not in df.columns:
            df.rename(columns={"vol": "volume"}, inplace=True)

        data = []
        for row in df.itertuples(index=False):
            time_val = row.time
            if isinstance(time_val, str):
                time_val = pd.to_datetime(time_val)
            data.append((
                code,
                time_val,
                row.open,
                row.high,
                row.low,
                row.close,
                int(row.volume) if row.volume else 0,
                float(row.amount) if row.amount else 0.0,
                time_val.year,
                time_val.month,
                time_val.day,
                time_val.hour,
                time_val.minute,
            ))

        if not data:
            return False

        cur = conn.cursor()
        cur.executemany("""
            INSERT INTO minute_bars (code, time, open, high, low, close, volume, amount, year, month, day, hour, minute)
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

        logger.debug(f"Saved {len(data)} minute bars for {code}")
        return True

    except Exception as e:
        logger.error(f"Failed to save minute data for {code}: {e}")
        return False


def save_minute_data_from_dicts(code: str, data: list[dict]) -> bool:
    """
    保存分钟数据到 PostgreSQL 数据库（使用 dict list）
    :param code: 证券代码
    :param data: 包含分钟数据的 dict list
    :return: 是否保存成功
    """
    if not data:
        return False

    try:
        df = pd.DataFrame(data)
        if "time" in df.columns and "datetime" in str(df["time"].dtype):
            df["time"] = pd.to_datetime(df["time"])
        return save_minute_data(code, df)
    except Exception as e:
        logger.error(f"Failed to save minute data from dicts for {code}: {e}")
        return False


def query_minute_data(
    code: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    limit: int = 5000,
) -> pd.DataFrame:
    """
    从 PostgreSQL 数据库加载分钟数据
    :param code: 证券代码
    :param start: 开始时间
    :param end: 结束时间
    :param limit: 限制数量
    :return: DataFrame
    """
    conn = _get_pg_conn()

    conditions = ["code = %s"]
    params = [code]

    if start:
        conditions.append("time >= %s")
        params.append(start)
    if end:
        conditions.append("time <= %s")
        params.append(end)

    query = f"""
        SELECT time, open, high, low, close, volume, amount
        FROM minute_bars
        WHERE {' AND '.join(conditions)}
        ORDER BY time
        LIMIT %s
    """
    params.append(limit)

    cur = conn.cursor()
    cur.execute(query, params)
    rows = cur.fetchall()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume", "amount"])
    df["time"] = pd.to_datetime(df["time"])
    df.set_index("time", inplace=True)

    return df


def load_minute_data(
    code: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: int = 5000,
) -> pd.DataFrame:
    """兼容层：调用 query_minute_data 并返回 DataFrame（供 cli.py minute-backfill 使用）"""
    return query_minute_data(code, start=start_time, end=end_time, limit=limit)


def get_latest_minute_time(code: str) -> Optional[datetime]:
    """
    获取某证券最新一条分钟数据的时间
    :param code: 证券代码
    :return: 最新时间
    """
    conn = _get_pg_conn()
    cur = conn.cursor()
    cur.execute("SELECT MAX(time) FROM minute_bars WHERE code = %s", [code])
    result = cur.fetchone()
    return result[0] if result and result[0] else None


def get_codes_in_db() -> list[str]:
    """
    获取数据库中已有数据的证券代码列表
    :return: 代码列表
    """
    conn = _get_pg_conn()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT code FROM minute_bars ORDER BY code")
    return [row[0] for row in cur.fetchall()]


def delete_minute_data(code: str, before: Optional[datetime] = None) -> int:
    """
    删除指定证券的分钟数据
    :param code: 证券代码
    :param before: 删除此时间之前的数据（不指定则删除全部）
    :return: 删除的行数
    """
    conn = _get_pg_conn()
    cur = conn.cursor()

    if before:
        cur.execute("DELETE FROM minute_bars WHERE code = %s AND time < %s", [code, before])
    else:
        cur.execute("DELETE FROM minute_bars WHERE code = %s", [code])

    conn.commit()
    deleted = cur.rowcount
    logger.info(f"Deleted {deleted} rows for {code}")
    return deleted


def clean_expired_minute_data(retain_months: int = 6, dry_run: bool = False) -> dict:
    """
    清理 minute_bars 表中的过期数据

    :param retain_months: 保留最近几个月的数据，默认6个月
    :param dry_run: True 时仅统计不删除
    :return: 统计信息 dict {total_before, total_after, deleted, codes_affected}
    """
    conn = _get_pg_conn()
    cur = conn.cursor()

    cutoff_time = datetime.now() - timedelta(days=retain_months * 30)

    cur.execute("SELECT COUNT(*) FROM minute_bars")
    total_before = cur.fetchone()[0]

    cur.execute("SELECT COUNT(DISTINCT code) FROM minute_bars")
    codes_total = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM minute_bars WHERE time < %s", (cutoff_time,))
    expired_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(DISTINCT code) FROM minute_bars WHERE time < %s", (cutoff_time,))
    codes_affected = cur.fetchone()[0]

    logger.info(f"Minute data cleanup summary:")
    logger.info(f"  Cutoff time: {cutoff_time}")
    logger.info(f"  Total rows before: {total_before}")
    logger.info(f"  Expired rows: {expired_count}")
    logger.info(f"  Codes affected: {codes_affected}/{codes_total}")

    if dry_run:
        logger.info("  [DRY RUN] No data deleted")
        return {
            "total_before": total_before,
            "total_after": total_before,
            "deleted": 0,
            "codes_affected": codes_affected,
            "cutoff_time": cutoff_time,
        }

    if expired_count > 0:
        cur.execute("DELETE FROM minute_bars WHERE time < %s", (cutoff_time,))
        conn.commit()
        deleted = cur.rowcount
        logger.info(f"  Deleted {deleted} rows")
    else:
        deleted = 0
        logger.info("  No expired data to delete")

    cur.execute("SELECT COUNT(*) FROM minute_bars")
    total_after = cur.fetchone()[0]

    return {
        "total_before": total_before,
        "total_after": total_after,
        "deleted": deleted,
        "codes_affected": codes_affected,
        "cutoff_time": cutoff_time,
    }


def test_minute_data_collection():
    """测试分钟数据采集"""
    from quant_etf.conf import ALL_POOL

    logger.info("Testing minute data collector with PostgreSQL...")

    # 初始化表
    init_minute_db()

    # 测试获取数据
    test_codes = ALL_POOL[:3]
    result = collect_for_pool(test_codes, count=500)

    for code, data in result.items():
        if data:
            df = pd.DataFrame(data)
            df["time"] = pd.to_datetime(df["time"])
            df.set_index("time", inplace=True)
            save_minute_data(code, df)
            logger.info(f"Saved {len(df)} bars for {code}")


if __name__ == "__main__":
    test_minute_data_collection()