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
