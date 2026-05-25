"""
分钟级K线数据采集器模块

提供获取、存储和管理分钟级K线数据的功能。
使用 DuckDB 数据库存储。
"""
import pandas as pd
from pathlib import Path
from loguru import logger
from datetime import datetime, time, timedelta
from typing import Optional

import duckdb

from pytdx.hq import TdxHq_API
from pytdx.params import TDXParams
from pytdx.config import hosts

from quant_etf.conf import DATA_DIR, ALL_POOL
from quant_etf.tdx import CUSTOM_HQ_HOSTS
import time as time_module
import psutil
import subprocess as _subprocess

_server_failures: dict[str, float] = {}
SERVER_COOLDOWN = 120


def get_local_tdx_server() -> tuple[str, int] | None:
    """
    通过本地运行的通达信进程自动发现行情服务器地址
    :return: (ip, port) 元组，如果未找到则返回 None
    """
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
                parts = line.split()
                if len(parts) >= 3 and parts[3] == "ESTABLISHED":
                    remote = parts[2]
                    ip, port = remote.rsplit(":", 1)
                    logger.info(f"Discovered TDX server from local process: {ip}:{port}")
                    return ip, int(port)
    except Exception as e:
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


def get_minute_bars(
    code: str,
    count: int = 500,
    server: Optional[str] = None,
    port: int = 7709,
    max_servers: int = 5,
) -> list[dict]:
    """
    获取证券的分钟级K线数据
    :param code: 证券代码 (e.g. "510050", "000001")
    :param count: 获取数量（每次最多约 500 条）
    :param server: 行情服务器 IP（如果为 None，则自动尝试多个服务器）
    :param port: 行情服务器端口
    :param max_servers: 最多尝试的服务器数量
    :return: list of dicts 包含分钟级K线数据
    """
    market = code_to_market(code)

    if server is not None:
        return _get_minute_bars_single_server(code, market, count, server, port)

    # Priority 1: Try server discovered from local TDX process
    local_server = get_local_tdx_server()
    if local_server:
        ls_ip, ls_port = local_server
        server_key = f"{ls_ip}:{ls_port}"
        current_time = time_module.time()
        # Only skip if recently failed
        if server_key not in _server_failures or current_time - _server_failures[server_key] >= SERVER_COOLDOWN:
            result = _get_minute_bars_single_server(code, market, count, ls_ip, ls_port)
            if result:
                if server_key in _server_failures:
                    del _server_failures[server_key]
                return result
            else:
                _server_failures[server_key] = current_time
                logger.warning(f"Local TDX server {server_key} failed, falling back to configured list")

    current_time = time_module.time()

    hq_hosts = CUSTOM_HQ_HOSTS + list(hosts.hq_hosts)[:max_servers]
    available_servers = []

    for host_info in hq_hosts:
        if isinstance(host_info, (tuple, list)) and len(host_info) >= 3:
            try_server = str(host_info[1])
            try_port = int(host_info[2])
        elif isinstance(host_info, dict):
            try_server = host_info["ip"]
            try_port = int(host_info["port"])
        else:
            continue

        server_key = f"{try_server}:{try_port}"
        if server_key in _server_failures:
            fail_time = _server_failures[server_key]
            if current_time - fail_time < SERVER_COOLDOWN:
                remaining = int(SERVER_COOLDOWN - (current_time - fail_time))
                logger.debug(f"Server {server_key} is cooling down, {remaining}s remaining")
                continue
            else:
                del _server_failures[server_key]

        available_servers.append((try_server, try_port))

    for try_server, try_port in available_servers:
        server_key = f"{try_server}:{try_port}"
        result = _get_minute_bars_single_server(code, market, count, try_server, try_port)
        if result:
            if server_key in _server_failures:
                del _server_failures[server_key]
                logger.info(f"Server {server_key} recovered, removed from failure list")
            return result
        else:
            _server_failures[server_key] = current_time
            logger.warning(f"Server {server_key} failed, pausing for {SERVER_COOLDOWN}s")

    logger.warning(f"Failed to fetch minute bars for {code} from all servers")
    return []


def _get_minute_bars_single_server(
    code: str,
    market: int,
    count: int,
    server: str,
    port: int,
) -> list[dict]:
    """
    从单个服务器获取分钟级K线数据（分批获取）
    :return: list of dicts 包含分钟级K线数据
    """
    try:
        api = TdxHq_API(auto_retry=True, heartbeat=False)
        if not api.connect(server, port):
            logger.debug(f"Failed to connect to TDX HQ server {server}:{port}")
            return []

        try:
            # pytdx 单次最多返回约 800 条，分批获取
            batch_size = 500
            all_bars = []
            for start in range(0, count, batch_size):
                n = min(batch_size, count - start)
                bars = api.get_security_bars(8, market, code, start, n)
                if not bars:
                    break
                all_bars.extend(bars)

            if not all_bars:
                logger.debug(f"No minute bars returned for {code} from {server}:{port}")
                return []

            data = []
            for bar in all_bars:
                dt = bar.get("datetime", "")
                if dt:
                    try:
                        dt = pd.to_datetime(dt)
                    except:
                        pass
                    data.append({
                        "time": dt,
                        "open": float(bar.get("open", 0)),
                        "high": float(bar.get("high", 0)),
                        "low": float(bar.get("low", 0)),
                        "close": float(bar.get("close", 0)),
                        "volume": int(bar.get("vol", 0)) if bar.get("vol") else 0,
                        "amount": float(bar.get("amount", 0)) if bar.get("amount") else 0.0,
                    })

            data.sort(key=lambda x: x["time"] if x.get("time") else "")
            logger.info(f"Successfully fetched {len(data)} minute bars for {code} from {server}:{port}")
            return data

        finally:
            api.disconnect()

    except Exception as e:
        logger.debug(f"Failed to get minute bars from {server}:{port}: {e}")
        return []


def is_trading_time() -> bool:
    """
    判断当前是否为A股交易时间段
    :return: True 如果当前在交易时间段内，否则 False
    """
    now = datetime.now()

    weekday = now.weekday()
    if weekday >= 5:
        return False

    current_time = now.time()

    morning_start = time(9, 30)
    morning_end = time(11, 30)
    afternoon_start = time(13, 0)
    afternoon_end = time(15, 0)

    if morning_start <= current_time <= morning_end:
        return True
    if afternoon_start <= current_time <= afternoon_end:
        return True

    return False


def get_next_trading_time() -> datetime:
    """
    获取下一个交易时间段的开始时间
    :return: 下一个交易时段的开始时间
    """
    now = datetime.now()
    current_time = now.time()
    weekday = now.weekday()

    if weekday < 5:
        morning_start = time(9, 30)
        afternoon_start = time(13, 0)

        if current_time < morning_start:
            return now.replace(hour=9, minute=30, second=0, microsecond=0)
        elif current_time < afternoon_start:
            return now.replace(hour=13, minute=0, second=0, microsecond=0)
        elif current_time < time(15, 0):
            return now.replace(hour=13, minute=0, second=0, microsecond=0)

    next_day = now + timedelta(days=1)
    while next_day.weekday() >= 5:
        next_day += timedelta(days=1)

    return next_day.replace(hour=9, minute=30, second=0, microsecond=0)


def wait_until_trading_start(check_interval: int = 60, should_stop=None) -> bool:
    """
    等待直到交易时间开始
    :param check_interval: 检查间隔（秒）
    :param should_stop: 可选的回调函数，返回 True 时中断等待
    :return: True 如果进入交易时间，False 如果被中断
    """
    import time as time_module

    logger.info("Waiting for trading session to start...")

    while not is_trading_time():
        if should_stop and should_stop():
            logger.info("Shutdown requested, stopping wait...")
            return False

        next_time = get_next_trading_time()
        wait_seconds = (next_time - datetime.now()).total_seconds()

        if wait_seconds > 0:
            logger.info(f"Next trading session starts at {next_time}, waiting {wait_seconds:.0f} seconds...")
            sleep_duration = min(wait_seconds, check_interval)
            # 分段睡眠，方便响应中断
            for _ in range(int(sleep_duration)):
                if should_stop and should_stop():
                    logger.info("Shutdown requested, stopping wait...")
                    return False
                time_module.sleep(1)
        else:
            time_module.sleep(check_interval)

    logger.info("Trading session started!")
    return True


def get_minute_db_path() -> Path:
    """
    获取分钟数据数据库文件路径
    :return: 数据库文件路径
    """
    minute_dir = DATA_DIR / "minute"
    minute_dir.mkdir(parents=True, exist_ok=True)
    return minute_dir / "minute_data.duckdb"


def init_minute_db() -> duckdb.DuckDBPyConnection:
    """
    初始化分钟数据数据库，创建表结构
    :return: 数据库连接
    """
    db_path = get_minute_db_path()
    conn = duckdb.connect(str(db_path))

    conn.execute("""
        CREATE TABLE IF NOT EXISTS minute_bars (
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
        CREATE INDEX IF NOT EXISTS idx_code_time ON minute_bars(code, time)
    """)

    logger.info(f"Initialized minute data database: {db_path}")
    return conn


def get_db_connection() -> duckdb.DuckDBPyConnection:
    """
    获取数据库连接（单例模式）
    :return: 数据库连接
    """
    if not hasattr(get_db_connection, "_conn"):
        get_db_connection._conn = init_minute_db()
    return get_db_connection._conn


def close_db_connection():
    """
    关闭数据库连接
    """
    if hasattr(get_db_connection, "_conn"):
        get_db_connection._conn.close()
        delattr(get_db_connection, "_conn")
        logger.info("Closed minute data database connection")


def save_minute_data(code: str, df: pd.DataFrame) -> bool:
    """
    保存分钟数据到 DuckDB 数据库
    :param code: 证券代码
    :param df: 包含分钟数据的 DataFrame
    :return: 是否保存成功
    """
    if df.empty:
        return False

    try:
        conn = get_db_connection()

        df = df.reset_index()
        if "time" not in df.columns:
            return False

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

        conn.executemany("""
            INSERT OR REPLACE INTO minute_bars
            (code, time, open, high, low, close, volume, amount, year, month, day, hour, minute)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, data)

        logger.debug(f"Saved {len(data)} minute bars for {code} to DuckDB")
        return True

    except Exception as e:
        logger.error(f"Failed to save minute data for {code}: {e}")
        return False


def save_minute_data_from_dicts(code: str, data: list[dict]) -> bool:
    """
    保存分钟数据到 DuckDB 数据库 (使用 DuckDB from_df)
    :param code: 证券代码
    :param data: list of dicts 包含分钟数据
    :return: 是否保存成功
    """
    if not data:
        return False

    try:
        conn = get_db_connection()

        df = pd.DataFrame(data)
        if df.empty:
            return False

        df["time"] = pd.to_datetime(df["time"])
        df["code"] = code
        df["year"] = df["time"].dt.year
        df["month"] = df["time"].dt.month
        df["day"] = df["time"].dt.day
        df["hour"] = df["time"].dt.hour
        df["minute"] = df["time"].dt.minute

        columns = ["code", "time", "open", "high", "low", "close", "volume", "amount", "year", "month", "day", "hour", "minute"]
        df = df[columns]

        conn.execute("""
            INSERT OR REPLACE INTO minute_bars
            (code, time, open, high, low, close, volume, amount, year, month, day, hour, minute)
            SELECT code, time, open, high, low, close, volume, amount, year, month, day, hour, minute
            FROM df
        """)

        logger.debug(f"Saved {len(df)} minute bars for {code} to DuckDB")
        return True

    except Exception as e:
        logger.error(f"Failed to save minute data for {code}: {e}")
        return False


def load_minute_data(
    code: Optional[str] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: int = 1000,
) -> pd.DataFrame:
    """
    从 DuckDB 数据库加载分钟数据
    :param code: 证券代码（可选，None表示所有证券）
    :param start_time: 开始时间（可选）
    :param end_time: 结束时间（可选）
    :param limit: 返回最大行数
    :return: 包含分钟数据的 DataFrame
    """
    try:
        conn = get_db_connection()

        query = "SELECT * FROM minute_bars WHERE 1=1"
        params = []

        if code:
            query += " AND code = ?"
            params.append(code)

        if start_time:
            query += " AND time >= ?"
            params.append(start_time)

        if end_time:
            query += " AND time <= ?"
            params.append(end_time)

        query += " ORDER BY time DESC LIMIT ?"
        params.append(limit)

        df = conn.execute(query, params).df()
        df.set_index("time", inplace=True)
        df.sort_index(inplace=True)

        return df

    except Exception as e:
        logger.error(f"Failed to load minute data: {e}")
        return pd.DataFrame()


def query_minute_data(sql: str) -> pd.DataFrame:
    """
    直接执行 SQL 查询分钟数据
    :param sql: SQL 查询语句
    :return: 查询结果 DataFrame
    """
    try:
        conn = get_db_connection()
        return conn.execute(sql).df()
    except Exception as e:
        logger.error(f"Failed to execute query: {e}")
        return pd.DataFrame()


def collect_minute_data_for_all(
    codes: list[str],
    count: int = 500,
) -> dict[str, int]:
    """
    采集所有指定证券的分钟数据
    :param codes: 证券代码列表
    :param count: 获取的分钟数量
    :return: 采集结果统计 {"success": int, "failed": int}
    """
    success_count = 0
    failed_count = 0

    for code in codes:
        try:
            data = get_minute_bars(code, count)
            if data:
                if save_minute_data_from_dicts(code, data):
                    success_count += 1
                    logger.info(f"Collected {len(data)} minute bars for {code}")
                else:
                    failed_count += 1
            else:
                failed_count += 1
        except Exception as e:
            logger.error(f"Error collecting minute data for {code}: {e}")
            failed_count += 1

    result = {"success": success_count, "failed": failed_count}
    logger.info(f"Minute data collection completed: {result}")
    return result


if __name__ == "__main__":
    logger.info("Testing minute data collector with DuckDB...")

    test_code = "510050"
    df = get_minute_bars(test_code)
    logger.info(f"Got {len(df)} minute bars for {test_code}")

    if not df.empty:
        save_minute_data(test_code, df)
        logger.info("Data saved to DuckDB")

        loaded = load_minute_data(code=test_code, limit=10)
        logger.info(f"Loaded {len(loaded)} rows from DuckDB")
        logger.info(loaded)
