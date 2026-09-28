import socket
from contextlib import contextmanager

import pandas as pd
from pathlib import Path
from loguru import logger
from pytdx.reader import TdxDailyBarReader
from pytdx.hq import TdxHq_API
from pytdx.params import TDXParams
from pytdx.config import hosts

from quant_etf.conf import TDX_DIR, TDX_VIPDOC_DIR

import psutil
import subprocess as _subprocess

# pytdx socket 超时保护（秒）
TDX_SOCKET_TIMEOUT = 15

# 探活超时（秒）——探活必须快，否则 69 个候选逐个试要等很久
TDX_PROBE_TIMEOUT = 5.0


@contextmanager
def _tdx_timeout(timeout: float = TDX_SOCKET_TIMEOUT):
    """临时设置 socket 超时，防止 pytdx 网络操作无限阻塞"""
    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        yield
    finally:
        socket.setdefaulttimeout(old)


# 实测可用的行情服务器（2026-09-28 全量实测通达信官方 69 个节点的结果）
#
# 重要教训：TDX 服务器"协议握手成功"≠"有数据"。实测 69 个节点中：
#   - 3 个连得上且 get_security_count 有响应，但所有行情/历史接口返回空
#   - 仅 XCXX01 / XCXX02 真正供数（5m 可回溯 2 年，23832 根）
# 因此选服务器必须实际拉一根 K 线验证，不能只看 connect() 返回值。
# 复查工具：uv run python scripts/probe_data_sources.py
CUSTOM_HQ_HOSTS = [
    ("XCXX01(实测供数)", "218.6.198.164", 7709),
    ("XCXX02(实测供数)", "117.139.166.52", 7709),
    ("XCXX03(时通时断)", "119.4.167.131", 7709),
    # 以下为历史遗留节点，2026-09-28 实测均返回空数据，仅作最后兜底
    ("扩展行情(测试文件)", "112.74.214.43", 7727),
    ("上海电信主站Z1", "180.153.18.170", 7709),
    ("杭州电信主站J1", "60.191.117.167", 7709),
    ("上证云成都电信一", "218.6.170.47", 7709),
    ("上证云北京联通一", "123.125.108.14", 7709),
    ("广发", "119.29.19.242", 7709),
]

# 通达信行情服务器清单可能出现的路径（connect.cfg 记录了官方节点表）
CONNECT_CFG_CANDIDATES = [
    TDX_DIR / "connect.cfg",
    TDX_VIPDOC_DIR.parent / "connect.cfg",
]



def get_local_tdx_server() -> tuple[str, int] | None:
    """
    通过本地运行的通达信进程自动发现行情服务器地址

    注意：这里拿到的是**通达信客户端自己连的节点**，不是"对 pytdx 供数的节点"。
    两者可能不一致（实测 112.45.28.4 通达信在用，但 pytdx 从它拉不到任何数据），
    所以本函数的结果只应作为候选之一，必须经 probe_server_has_data 验证后才能用。
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
                    return ip, int(port)
    except Exception as e:
        logger.debug(f"Failed to discover TDX server from local process: {e}")

    return None


def _find_connect_cfg() -> Path | None:
    """定位通达信的 connect.cfg（记录官方行情服务器清单）"""
    for path in CONNECT_CFG_CANDIDATES:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def discover_hosts_from_connect_cfg() -> list[tuple[str, int]]:
    """
    解析本机通达信 connect.cfg，返回官方行情服务器列表 [(ip, port), ...]

    connect.cfg 是通达信客户端自己的服务器清单，比 pytdx 内置的 hq_hosts 更贴近
    当前真实可用的节点。文件是 GBK 编码，HostName 可能是问号占位，只需 IP+Port。
    :return: 解析到的服务器列表，文件不存在或损坏时返回空列表
    """
    import re

    cfg = _find_connect_cfg()
    if cfg is None:
        return []

    try:
        raw = cfg.read_bytes()
    except OSError as e:
        logger.warning(f"Failed to read {cfg}: {e}")
        return []

    text = raw.decode("gbk", errors="ignore")
    # 形如 IPAddress01=1.2.3.4 \r\n ... Port01=7709
    pairs = re.findall(
        r"IPAddress(\d+)=([0-9.]+)\s*[\r\n]+\w*\1=(\d{4,5})", text
    )
    if not pairs:
        # 退化：只要 IP 和端口都出现就算
        pairs = [
            (str(i), ip, port)
            for i, (ip, port) in enumerate(
                re.findall(r"([0-9]{1,3}(?:\.[0-9]{1,3}){3})[^\d\n]{0,20}?(\d{4,5})", text)
            )
        ]

    servers: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for _, ip, port in pairs:
        ip = ip.strip()
        if ip.startswith("192.168.") or ":" in ip:  # 跳过内网与 IPv6
            continue
        try:
            key = (ip, int(port))
        except ValueError:
            continue
        if key in seen:
            continue
        seen.add(key)
        servers.append(key)

    if servers:
        logger.info(f"Loaded {len(servers)} TDX hosts from {cfg}")
    return servers


def get_hq_server_candidates(max_servers: int = 8) -> list[tuple[str, int]]:
    """
    构造按可信度排序的候选行情服务器列表

    顺序：实测供数的硬编码节点 → connect.cfg 官方清单 → 本机通达信当前连接的节点
    → pytdx 内置 hq_hosts。去重后截断。

    只做"排序"不做"验证"；要确认某个候选真的供数，用 probe_server_has_data。
    :param max_servers: 最多返回的候选数量
    :return: [(ip, port), ...]
    """
    ordered: list[tuple[str, int]] = []

    def add(ip: str, port: int) -> None:
        key = (str(ip), int(port))
        if key not in ordered:
            ordered.append(key)

    # 1) 实测供数的硬编码节点（最高优先级）
    for host_info in CUSTOM_HQ_HOSTS:
        if isinstance(host_info, (tuple, list)) and len(host_info) >= 3:
            add(str(host_info[1]), int(host_info[2]))

    # 2) 通达信官方清单
    for ip, port in discover_hosts_from_connect_cfg():
        add(ip, port)

    # 3) 本机通达信当前连接的节点（可能是坏节点，放后面兜底）
    local = get_local_tdx_server()
    if local:
        add(local[0], local[1])

    # 4) pytdx 内置
    try:
        for host_info in list(hosts.hq_hosts)[:max_servers]:
            if isinstance(host_info, (tuple, list)) and len(host_info) >= 3:
                add(str(host_info[1]), int(host_info[2]))
            elif isinstance(host_info, dict):
                add(str(host_info["ip"]), int(host_info["port"]))
    except Exception as e:  # pragma: no cover - pytdx 内置数据异常
        logger.debug(f"Failed to read pytdx builtin hq_hosts: {e}")

    return ordered[:max_servers]


def probe_server_has_data(
    server: str,
    port: int,
    probe_code: str = "510050",
    timeout: float = TDX_PROBE_TIMEOUT,
) -> bool:
    """
    验证服务器是否真的供数：实际拉一根 K 线，而不只是握手成功

    TDX 协议有个坑：部分节点 TCP 与协议握手都正常、get_security_count 也有返回，
    但 get_security_bars / get_security_quotes 一律返回空。只看 connect() 会误判。
    :return: True 表示能拉到数据
    """
    market = code_to_market(probe_code)
    api = TdxHq_API(auto_retry=False, heartbeat=False)
    try:
        with _tdx_timeout(timeout):
            if not api.connect(server, port, time_out=timeout):
                return False
            bars = api.get_security_bars(9, market, probe_code, 0, 5)
            return bool(bars)
    except Exception as e:
        logger.debug(f"Probe failed for {server}:{port}: {type(e).__name__}: {e}")
        return False
    finally:
        try:
            api.disconnect()
        except Exception:
            pass


def select_working_server(
    probe_code: str = "510050",
    max_candidates: int = 8,
) -> tuple[str, int] | None:
    """
    逐个探活候选服务器，返回第一个真正供数的节点并写入缓存

    :param probe_code: 探活用的标的（默认 510050，流动性好、不易停牌）
    :param max_candidates: 最多探活多少个候选
    :return: 可用服务器；全部不可用时返回 None
    """
    for server, port in get_hq_server_candidates(max_candidates):
        if probe_server_has_data(server, port, probe_code):
            logger.info(f"TDX server verified: {server}:{port}")
            _set_cached_server(server, port)
            return server, port
        logger.debug(f"TDX server has no data, skipping: {server}:{port}")
    logger.warning("No TDX server returned data; pytdx upstream appears unavailable")
    return None


# 全局工作服务器缓存（只存经探活验证的服务器）
_cached_server: tuple[str, int] | None = None
_failed_servers: set[tuple[str, int]] = set()  # 记录失败的服务器
_xdxr_cache: dict[str, pd.DataFrame] = {}  # 缓存xdxr数据


def _get_cached_server() -> tuple[str, int] | None:
    """获取缓存的工作服务器"""
    return _cached_server


def _set_cached_server(server: str, port: int) -> None:
    """设置缓存的工作服务器"""
    global _cached_server
    _cached_server = (server, port)
    logger.info(f"TDX server cached: {server}:{port}")


def _try_connect_and_fetch(
    server: str,
    port: int,
    market: int,
    code: str,
    start: int,
    count: int,
    auto_retry: bool,
    heartbeat: bool,
) -> pd.DataFrame | None:
    """
    尝试连接服务器并获取数据
    :return: DataFrame 如果成功，None 如果失败
    """
    try:
        with _tdx_timeout():
            api = TdxHq_API(auto_retry=auto_retry, heartbeat=heartbeat)
            if not api.connect(server, port):
                logger.debug(f"Failed to connect to TDX server {server}:{port}")
                return None

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

                    logger.info(f"Successfully fetched data for {code} from {server}:{port}")
                    return df
                else:
                    logger.debug(f"No bars returned from server {server}:{port}")
                    return None

            finally:
                api.disconnect()

    except socket.timeout:
        logger.warning(f"Socket timeout connecting to {server}:{port} ({TDX_SOCKET_TIMEOUT}s)")
        return None
    except Exception as e:
        logger.debug(f"Failed to get data from {server}:{port}: {e}")
        return None


SH_INDICES = {"000001", "000016", "000300", "000905", "000985"}
SZ_INDICES = {"399001", "399006", "399005"}
ALL_INDICES = SH_INDICES | SZ_INDICES


def code_to_market(code: str) -> int:
    """
    根据证券代码判断市场代码
    :param code: 证券代码 (e.g. "510050", "000001", "000300", "399001")
    :return: 市场代码 0:深圳，1:上海
    
    主要指数代码：
    - 000001: 上证指数 (SH)
    - 000016: 上证50 (SH)
    - 000300: 沪深300 (SH)
    - 000905: 中证500 (SH)
    - 000985: 中证1000 (SH)
    - 399001: 深证成指 (SZ)
    - 399006: 创业板指 (SZ)
    """
    if code in SH_INDICES:
        return TDXParams.MARKET_SH
    if code in SZ_INDICES:
        return TDXParams.MARKET_SZ
    
    # 深证指数系列
    if code.startswith("399"):
        return TDXParams.MARKET_SZ
    
    # 股票/ETF代码
    if code.startswith(("5", "6")):
        return TDXParams.MARKET_SH
    elif code.startswith(("0", "1", "3")):
        return TDXParams.MARKET_SZ
    else:
        return TDXParams.MARKET_SZ


def _get_default_hq_server() -> tuple[str, int]:
    """
    获取默认行情服务器地址

    优先级: 已验证的缓存服务器 > 候选列表首个（实测供数硬编码 → connect.cfg 官方清单
    → 本机通达信当前节点 → pytdx 内置）

    注意：这里**不再**把"本机通达信进程当前连接的节点"放在最前。实测该节点
    （112.45.28.4）通达信自己在用，但 pytdx 从它拉不到任何数据，优先用它会让整个
    采集链路静默地一直取不到数。该节点只作为候选之一，由探活逻辑决定去留。
    :return: (ip, port) 元组
    """
    # Priority 1: 已验证的缓存服务器
    cached = _get_cached_server()
    if cached:
        logger.info(f"Using cached server as default: {cached[0]}:{cached[1]}")
        return cached

    # Priority 2: 候选列表首个（已按实测可信度排序）
    candidates = get_hq_server_candidates()
    if candidates:
        server, port = candidates[0]
        logger.info(f"Using TDX server candidate as default: {server}:{port}")
        return server, port

    # Priority 3: 兜底
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

    market_codes = [(code_to_market(code), code) for code in codes]

    # 如果未指定服务器，按可信度依次尝试候选节点
    if server is None:
        cached = _get_cached_server()
        candidates = get_hq_server_candidates()
        if cached:
            candidates = [cached] + [c for c in candidates if c != cached]

        for try_server, try_port in candidates:
            try:
                result = _try_realtime_quote(
                    market_codes, try_server, try_port, auto_retry, heartbeat
                )
                if not result.empty:
                    _set_cached_server(try_server, try_port)
                    return result
                logger.debug(
                    f"TDX server {try_server}:{try_port} returned empty quotes, trying next"
                )
            except Exception as e:
                logger.debug(
                    f"TDX server {try_server}:{try_port} failed: {e}, trying next"
                )

        logger.warning("No TDX server returned realtime quotes")
        return pd.DataFrame()

    try:
        result = _try_realtime_quote(market_codes, server, port, auto_retry, heartbeat)
        if not result.empty:
            _set_cached_server(server, port)
        return result
    except Exception as e:
        logger.error(f"Failed to get realtime quote: {e}")
        return pd.DataFrame()


def _try_realtime_quote(
    market_codes: list[tuple[int, str]],
    server: str,
    port: int,
    auto_retry: bool,
    heartbeat: bool,
) -> pd.DataFrame:
    """
    尝试连接指定服务器获取实时行情
    :return: DataFrame 包含实时行情数据
    """
    with _tdx_timeout():
        api = TdxHq_API(auto_retry=auto_retry, heartbeat=heartbeat)
        if not api.connect(server, port):
            logger.debug(f"Failed to connect to TDX HQ server {server}:{port}")
            return pd.DataFrame()

        try:
            quotes = api.get_security_quotes(market_codes)
            if not quotes:
                logger.debug(f"No quotes returned from server {server}:{port}")
                return pd.DataFrame()

            df = api.to_df(quotes)
            logger.info(f"Successfully fetched realtime quotes for {len(market_codes)} codes from {server}:{port}")
            return df

        finally:
            api.disconnect()


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

    # 如果没有指定服务器，优先尝试缓存的工作服务器
    if server is None:
        # 先尝试缓存的服务器
        cached = _get_cached_server()
        if cached:
            try_server, try_port = cached
            logger.debug(f"Trying cached TDX server: {try_server}:{try_port}")
            result = _try_connect_and_fetch(try_server, try_port, market, code, start, count, auto_retry, heartbeat)
            if result is not None:
                return result
            # 缓存服务器失败，清除缓存并继续尝试其他服务器
            logger.debug(f"Cached server failed, clearing cache")
            _cached_server = None

        # 尝试其他服务器（按可信度排序的候选清单）
        for try_server, try_port in get_hq_server_candidates(max_servers):
            # 跳过已缓存的服务器（已经尝试过了）
            if cached and try_server == cached[0] and try_port == cached[1]:
                continue

            result = _try_connect_and_fetch(try_server, try_port, market, code, start, count, auto_retry, heartbeat)
            if result is not None:
                # 缓存这个成功的服务器
                _set_cached_server(try_server, try_port)
                return result

        logger.warning(f"Failed to fetch data for {code} from all servers")
        return pd.DataFrame()

    # 如果指定了服务器，只尝试该服务器
    try:
        with _tdx_timeout():
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

    except socket.timeout:
        logger.warning(f"Socket timeout fetching bars for {code} from {server}:{port} ({TDX_SOCKET_TIMEOUT}s)")
        return pd.DataFrame()
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


def get_xdxr_info(code: str) -> pd.DataFrame:
    """
    获取股票的除权除息信息（带缓存和失败记录优化）
    :param code: 证券代码 (e.g. "510050", "000001")
    :return: DataFrame 包含除权除息信息
    """
    # 先检查缓存
    if code in _xdxr_cache:
        logger.debug(f"Using cached xdxr info for {code}")
        return _xdxr_cache[code]
    
    market = code_to_market(code)
    
    # 尝试使用缓存的服务器（如果之前失败过则跳过）
    cached = _get_cached_server()
    if cached and cached not in _failed_servers:
        server, port = cached
        try:
            with _tdx_timeout():
                api = TdxHq_API()
                if api.connect(server, port):
                    try:
                        xdxr_data = api.get_xdxr_info(market, code)
                        if xdxr_data:
                            df = api.to_df(xdxr_data)
                            logger.info(f"Successfully fetched xdxr info for {code} from cached server")
                            _xdxr_cache[code] = df
                            return df
                    finally:
                        api.disconnect()
        except socket.timeout:
            logger.debug(f"Socket timeout fetching xdxr for {code} from cached server ({TDX_SOCKET_TIMEOUT}s)")
            _failed_servers.add(cached)
        except Exception as e:
            logger.debug(f"Failed to fetch xdxr from cached server: {e}")
            _failed_servers.add(cached)
    
    # 尝试其他服务器（跳过已失败的）
    for server, port in get_hq_server_candidates(5):
        server_key = (server, port)
        
        # 跳过已失败的服务器
        if server_key in _failed_servers:
            logger.debug(f"Skipping failed server {server}:{port}")
            continue
        
        try:
            with _tdx_timeout():
                api = TdxHq_API()
                # 设置连接超时为2秒，避免长时间等待
                if api.connect(server, port, time_out=2.0):
                    try:
                        xdxr_data = api.get_xdxr_info(market, code)
                        if xdxr_data:
                            df = api.to_df(xdxr_data)
                            logger.info(f"Successfully fetched xdxr info for {code} from {server}:{port}")
                            _set_cached_server(server, port)
                            _xdxr_cache[code] = df
                            return df
                        else:
                            # 服务器连接成功但无数据
                            _xdxr_cache[code] = pd.DataFrame()
                            return pd.DataFrame()
                    finally:
                        api.disconnect()
                else:
                    # 连接失败，记录到失败集合
                    _failed_servers.add(server_key)
                    logger.debug(f"Failed to connect to {server}:{port}")
        except socket.timeout:
            logger.debug(f"Socket timeout fetching xdxr for {code} from {server}:{port} ({TDX_SOCKET_TIMEOUT}s)")
            _failed_servers.add(server_key)
        except Exception as e:
            logger.debug(f"Failed to fetch xdxr from {server}:{port}: {e}")
            _failed_servers.add(server_key)
    
    logger.warning(f"Failed to fetch xdxr info for {code} after trying all available servers")
    return pd.DataFrame()


def adjust_price_qfq(df: pd.DataFrame, xdxr_df: pd.DataFrame) -> pd.DataFrame:
    """
    对价格数据进行前复权处理
    :param df: 原始日线数据 DataFrame (必须有 close 列)
    :param xdxr_df: 除权除息信息 DataFrame
    :return: 前复权后的 DataFrame
    """
    if df.empty or xdxr_df.empty:
        logger.warning("No data to adjust, returning original data")
        return df
    
    # 确保日期索引（只保留日期，去掉时间）
    df = df.copy()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"]).normalize()
        df.set_index("date", inplace=True)
    else:
        # 如果索引是DatetimeIndex包含时间，normalize到日期
        if hasattr(df.index, 'normalize'):
            df.index = df.index.normalize()
    
    # 处理 xdxr_df 的日期
    xdxr_df = xdxr_df.copy()
    # pytdx 返回的 xdxr 数据有 year, month, day 列，需要构建 date
    if "year" in xdxr_df.columns and "month" in xdxr_df.columns and "day" in xdxr_df.columns:
        xdxr_df["date"] = pd.to_datetime(
            xdxr_df["year"].astype(str) + "-" + 
            xdxr_df["month"].astype(str).str.zfill(2) + "-" + 
            xdxr_df["day"].astype(str).str.zfill(2)
        )
        xdxr_df.set_index("date", inplace=True)
    elif "date" in xdxr_df.columns:
        xdxr_df["date"] = pd.to_datetime(xdxr_df["date"])
        xdxr_df.set_index("date", inplace=True)
    elif "datetime" in xdxr_df.columns:
        xdxr_df["date"] = pd.to_datetime(xdxr_df["datetime"])
        xdxr_df.set_index("date", inplace=True)
    
    # 筛选有效的除权数据（category=1, 11 等表示除权除息）
    if "category" in xdxr_df.columns:
        # category 1: 除权除息, 11: 可能是其他类型的除权事件
        xdxr_df = xdxr_df[xdxr_df["category"].isin([1, 11])]
    
    if xdxr_df.empty:
        logger.info("No xdxr events found, returning original data")
        return df
    
    # 初始化复权因子为 1.0
    df["adj_factor"] = 1.0
    
    # 按时间倒序处理除权事件（从最新到最旧）
    xdxr_sorted = xdxr_df.sort_index(ascending=False)
    
    for xdxr_date, xdxr_row in xdxr_sorted.iterrows():
        # 找到除权日在 df 中的位置
        if xdxr_date in df.index:
            # 获取除权日之前的收盘价（前一个交易日）
            mask_before = df.index < xdxr_date
            if mask_before.any():
                # 除权日前一天的收盘价
                last_close_before = df.loc[mask_before, "close"].iloc[-1]
                # 除权日的收盘价
                close_on_xdxr = df.loc[xdxr_date, "close"]
                
                # 计算前复权因子
                # 前复权：保持最新价格不变，调整历史价格
                # 历史价格 = 原始价格 × (除权日收盘价 / 除权日前收盘价)
                if last_close_before > 0:
                    factor = close_on_xdxr / last_close_before
                    
                    # 对除权日之前的所有数据应用这个因子
                    df.loc[mask_before, "adj_factor"] *= factor
    
    # 应用前复权因子到价格
    price_columns = ["open", "high", "low", "close"]
    for col in price_columns:
        if col in df.columns:
            df[col] = df[col] * df["adj_factor"]
    
    # 删除临时列
    if "adj_factor" in df.columns:
        df.drop(columns=["adj_factor"], inplace=True)
    
    # 重新计算涨跌幅（基于复权后的价格）
    df["pct_chg"] = df["close"].pct_change() * 100
    df["pct_chg"] = df["pct_chg"].fillna(0.0)
    
    logger.info(f"Applied forward adjustment (前复权) to {len(xdxr_sorted)} xdxr events")
    return df
