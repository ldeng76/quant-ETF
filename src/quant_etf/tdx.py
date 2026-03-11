import pandas as pd
from pathlib import Path
from loguru import logger
from pytdx.reader import TdxDailyBarReader
from pytdx.hq import TdxHq_API
from pytdx.params import TDXParams
from pytdx.config import hosts

from quant_etf.conf import TDX_VIPDOC_DIR


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


def _get_default_hq_server() -> tuple[str, int]:
    """
    获取默认行情服务器地址
    :return: (ip, port) 元组
    """
    hq_hosts = hosts.hq_hosts
    if hq_hosts:
        first_host = hq_hosts[0]
        if isinstance(first_host, (tuple, list)):
            if len(first_host) >= 3:
                ip = str(first_host[1])
                port = int(first_host[2])
            else:
                ip = str(first_host[0])
                port = int(first_host[1])
            return ip, port
        elif isinstance(first_host, dict):
            return first_host["ip"], int(first_host["port"])
    return "119.147.212.81", 7709


def get_realtime_quote(
    codes: list[str],
    server: str | None = None,
    port: int | None = 7709,
    auto_retry: bool = True,
    heartbeat: bool = False,
) -> pd.DataFrame:
    """
    批量获取多只股票的实时行情
    :param codes: 证券代码列表 (e.g. ["510050", "000001"])
    :param server: 行情服务器 IP (默认使用第一个可用服务器)
    :param port: 行情服务器端口 (默认 7709)
    :param auto_retry: 是否启用自动重连
    :param heartbeat: 是否启用心跳保活
    :return: DataFrame 包含实时行情数据
    """
    if not codes:
        logger.warning("No codes provided for realtime quote")
        return pd.DataFrame()

    if server is None:
        server, port = _get_default_hq_server()

    market_codes = [(code_to_market(code), code) for code in codes]

    try:
        api = TdxHq_API(auto_retry=auto_retry, heartbeat=heartbeat)
        if not api.connect(server, port):
            logger.warning(f"Failed to connect to TDX HQ server {server}:{port}")
            return pd.DataFrame()

        try:
            quotes = api.get_security_quotes(market_codes)
            if not quotes:
                logger.warning("No quotes returned from server")
                return pd.DataFrame()

            df = api.to_df(quotes)
            return df

        finally:
            api.disconnect()

    except Exception as e:
        logger.error(f"Failed to get realtime quote: {e}")
        return pd.DataFrame()


def get_realtime_quote_single(
    code: str,
    server: str | None = None,
    port: int | None = 7709,
    auto_retry: bool = True,
    heartbeat: bool = False,
) -> dict | None:
    """
    获取单只股票的实时行情
    :param code: 证券代码 (e.g. "510050", "000001")
    :param server: 行情服务器 IP (默认使用第一个可用服务器)
    :param port: 行情服务器端口 (默认 7709)
    :param auto_retry: 是否启用自动重连
    :param heartbeat: 是否启用心跳保活
    :return: 包含实时行情的字典，失败返回 None
    """
    df = get_realtime_quote([code], server, port, auto_retry, heartbeat)
    if df.empty:
        return None
    return df.iloc[0].to_dict()


def get_tdx_path(code: str) -> Path | None:
    """
    根据代码获取通达信 .day 文件路径
    :param code: 证券代码 (e.g. "510050", "000001")
    :return: .day 文件的绝对路径，如果未找到则返回 None
    """
    if code.startswith(("5", "6")):
        market = "sh"
    elif code.startswith(("0", "1", "3")):
        market = "sz"
    else:
        market = None

    if market:
        file_path = TDX_VIPDOC_DIR / market / "lday" / f"{market}{code}.day"
        if file_path.exists():
            return file_path
    else:
        for m in ["sh", "sz"]:
            p = TDX_VIPDOC_DIR / m / "lday" / f"{m}{code}.day"
            if p.exists():
                return p

    return None


def get_security_bars(
    code: str,
    start: int = 0,
    count: int = 800,
    server: str | None = None,
    port: int | None = 7709,
    auto_retry: bool = True,
    heartbeat: bool = False,
    max_servers: int = 5,
) -> pd.DataFrame:
    """
    获取证券的历史日线数据（在线）
    :param code: 证券代码 (e.g. "510050", "000001")
    :param start: 起始位置（0 表示最新数据）
    :param count: 获取数量（每次最多约 800 条）
    :param server: 行情服务器 IP（如果为 None，则自动尝试多个服务器）
    :param port: 行情服务器端口
    :param auto_retry: 是否启用自动重连
    :param heartbeat: 是否启用心跳保活
    :param max_servers: 最多尝试的服务器数量
    :return: DataFrame 包含历史日线数据
    """
    market = code_to_market(code)

    # 如果没有指定服务器，尝试多个服务器
    if server is None:
        hq_hosts = hosts.hq_hosts[:max_servers]
        for host_info in hq_hosts:
            if isinstance(host_info, (tuple, list)) and len(host_info) >= 3:
                try_server = str(host_info[1])
                try_port = int(host_info[2])
            elif isinstance(host_info, dict):
                try_server = host_info["ip"]
                try_port = int(host_info["port"])
            else:
                continue

            try:
                api = TdxHq_API(auto_retry=auto_retry, heartbeat=heartbeat)
                if not api.connect(try_server, try_port):
                    logger.debug(f"Failed to connect to TDX server {try_server}:{try_port}")
                    continue

                try:
                    # 获取日线数据，category=9 表示日线
                    bars = api.get_security_bars(9, market, code, start, count)
                    if bars:
                        df = api.to_df(bars)

                        # 转换为与 parse_tdx_day_file 相同的格式
                        if "datetime" in df.columns:
                            df.rename(columns={"datetime": "date"}, inplace=True)
                        df["date"] = pd.to_datetime(df["date"])
                        df.set_index("date", inplace=True)
                        df.sort_index(inplace=True)

                        # 只保留需要的列，与 parse_tdx_day_file 保持一致
                        # API 返回的是 vol 而不是 volume
                        if "vol" in df.columns and "volume" not in df.columns:
                            df.rename(columns={"vol": "volume"}, inplace=True)
                        df = df[["open", "high", "low", "close", "amount", "volume"]]

                        # 计算涨跌幅
                        df["pct_chg"] = df["close"].pct_change() * 100
                        df["pct_chg"] = df["pct_chg"].fillna(0.0)

                        logger.info(f"Successfully fetched data for {code} from {try_server}:{try_port}")
                        return df
                    else:
                        logger.debug(f"No bars returned from server {try_server}:{try_port}")
                        continue

                finally:
                    api.disconnect()

            except Exception as e:
                logger.debug(f"Failed to get data from {try_server}:{try_port}: {e}")
                continue

        logger.warning(f"Failed to fetch data for {code} from all {len(hq_hosts)} servers")
        return pd.DataFrame()

    # 如果指定了服务器，只尝试该服务器
    try:
        api = TdxHq_API(auto_retry=auto_retry, heartbeat=heartbeat)
        if not api.connect(server, port):
            logger.warning(f"Failed to connect to TDX HQ server {server}:{port}")
            return pd.DataFrame()

        try:
            # 获取日线数据，category=9 表示日线
            bars = api.get_security_bars(9, market, code, start, count)
            if not bars:
                logger.warning(f"No bars returned from server for {code}")
                return pd.DataFrame()

            df = api.to_df(bars)

            # 转换为与 parse_tdx_day_file 相同的格式
            if "datetime" in df.columns:
                df.rename(columns={"datetime": "date"}, inplace=True)
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)
            df.sort_index(inplace=True)

            # 只保留需要的列，与 parse_tdx_day_file 保持一致
            # API 返回的是 vol 而不是 volume
            if "vol" in df.columns and "volume" not in df.columns:
                df.rename(columns={"vol": "volume"}, inplace=True)
            df = df[["open", "high", "low", "close", "amount", "volume"]]

            # 计算涨跌幅
            df["pct_chg"] = df["close"].pct_change() * 100
            df["pct_chg"] = df["pct_chg"].fillna(0.0)

            return df

        finally:
            api.disconnect()

    except Exception as e:
        logger.error(f"Failed to get security bars for {code}: {e}")
        return pd.DataFrame()


def parse_tdx_day_file(file_path: Path | str) -> pd.DataFrame:
    """
    使用 pytdx 解析通达信 .day 文件
    :param file_path: 文件路径
    :return: DataFrame (columns: date, open, high, low, close, amount, volume)
    """
    path = Path(file_path)

    if not path.exists():
        logger.warning(f"TDX file not found: {path}")
        return pd.DataFrame()

    try:
        reader = TdxDailyBarReader()
        df = reader.get_df(str(path))

        if df.empty:
            logger.warning(f"TDX file is empty: {path}")
            return df

        df = df.reset_index()
        df.rename(columns={"index": "date"}, inplace=True)
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        df.sort_index(inplace=True)

        df["pct_chg"] = df["close"].pct_change() * 100
        df["pct_chg"] = df["pct_chg"].fillna(0.0)

        return df

    except Exception as e:
        logger.error(f"Failed to parse TDX file {path}: {e}")
        return pd.DataFrame()
