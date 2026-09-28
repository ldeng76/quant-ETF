"""数据源体检：检查 pytdx 行情服务器与备选源的可用性。

背景：TDX 公共行情服务器已停止向 pytdx 客户端返回行情数据（协议握手正常、
get_security_count 有响应，但 get_security_bars / get_security_quotes 全部返回空）。
本脚本用于定期复查，以及在切换数据源前做横向对比。

用法：
    uv run python scripts/probe_data_sources.py            # 全量体检
    uv run python scripts/probe_data_sources.py --sina     # 只测 Sina 分钟线
"""
import argparse
import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor

from pytdx.hq import TdxHq_API

# CUSTOM_HQ_HOSTS 之外补充的公共行情节点
TDX_HOSTS = [
    ("上海电信Z1", "180.153.18.170", 7709),
    ("深圳证通", "218.6.170.47", 7709),
    ("江苏双线", "58.63.254.191", 7709),
    ("上海双线", "114.80.63.12", 7709),
    ("华西证券", "119.147.212.81", 7709),
    ("扩展行情", "112.74.214.43", 7727),
]

SINA_KLINE = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
SINA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://finance.sina.com.cn",
}


def probe_tdx(entry):
    """返回 (name, host, 状态, 证券数, bar数, 行情)"""
    name, ip, port = entry[0], str(entry[1]), int(entry[2])
    api = TdxHq_API(heartbeat=False, auto_retry=False)
    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(6)
    try:
        if not api.connect(ip, port, time_out=6):
            return (name, f"{ip}:{port}", "CONNECT_FAIL", "-", "-", "-")
        time.sleep(0.4)
        cnt = api.get_security_count(1)
        try:
            bars = api.get_security_bars(9, 1, "600519", 0, 5)
            nbar = len(bars) if bars else 0
        except Exception as e:
            nbar = f"ERR({type(e).__name__})"
        try:
            q = api.get_security_quotes([(1, "600519")])
            qt = f"last={q[0].get('last')}" if q else "None"
        except Exception as e:
            qt = f"ERR({type(e).__name__})"
        return (name, f"{ip}:{port}", "CONNECTED", str(cnt), str(nbar), qt)
    except Exception as e:
        return (name, f"{ip}:{port}", f"ERR:{type(e).__name__}", "-", "-", "-")
    finally:
        socket.setdefaulttimeout(old)
        try:
            api.disconnect()
        except Exception:
            pass


def probe_sina(symbol="sh510050", scales=(5, 15, 60)):
    import requests

    print(f"\n=== Sina K线 {symbol} ===")
    for scale in scales:
        try:
            r = requests.get(
                SINA_KLINE,
                params={"symbol": symbol, "scale": scale, "ma": "no", "datalen": 1023},
                headers=SINA_HEADERS, timeout=15,
            )
            data = json.loads(r.text.strip()) if r.status_code == 200 else []
            if not data:
                print(f"  {scale:2d}min: EMPTY")
                continue
            print(f"  {scale:2d}min: {len(data):4d} 根  "
                  f"{data[0]['day'][:16]} → {data[-1]['day'][:16]}  "
                  f"close={data[-1]['close']}")
        except Exception as e:
            print(f"  {scale:2d}min: ERR {type(e).__name__}: {str(e)[:80]}")


def probe_sina_quote(symbol="sh510050"):
    import requests

    try:
        r = requests.get(f"https://hq.sinajs.cn/list={symbol}",
                         headers=SINA_HEADERS, timeout=10)
        print(f"\n=== Sina 实时快照 {symbol} ===\n  HTTP {r.status_code}: "
              f"{r.text.strip()[:160]}")
    except Exception as e:
        print(f"\n=== Sina 实时快照 ===\n  ERR {type(e).__name__}: {str(e)[:80]}")


def probe_eastmoney():
    import requests

    print("\n=== 东财（akshare 依赖的源）===")
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
           "?secid=1.510050&fields1=f1&fields2=f51,f53&klt=5&fqt=0&beg=20250101&end=20500101")
    try:
        r = requests.get(url, headers=SINA_HEADERS, timeout=12)
        print(f"  HTTP {r.status_code} len={len(r.text)} :: {r.text[:100]}")
    except Exception as e:
        print(f"  ERR {type(e).__name__}: {str(e)[:100]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sina", action="store_true", help="只测 Sina")
    ap.add_argument("--symbol", default="sh510050")
    args = ap.parse_args()

    if args.sina:
        probe_sina(args.symbol)
        probe_sina_quote(args.symbol)
        return 0

    print(f"probing {len(TDX_HOSTS)} TDX 行情节点 …")
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(probe_tdx, TDX_HOSTS))

    print(f"\n{'状态':<16}{'节点':<22}{'证券数':<9}{'日线根数':<9}行情")
    ok = 0
    for name, host, status, cnt, nbar, qt in results:
        print(f"{status:<16}{host:<22}{cnt:<9}{nbar:<9}{qt}   {name}")
        if nbar not in ("0", "-"):
            ok += 1
    print(f"\n=> pytdx 可用节点: {ok}/{len(results)}"
          f"{'  （全部为空 = 服务端已停止供数）' if ok == 0 else ''}")

    probe_eastmoney()
    probe_sina(args.symbol)
    probe_sina_quote(args.symbol)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
