"""Test whether pytdx can fetch minute bars for stock codes vs ETF codes"""
from pytdx.hq import TdxHq_API
from pytdx.params import TDXParams
from pytdx.config.hosts import hq_hosts
import time

# Test codes
STOCK_CODES = ["688981", "000063", "000547", "002050", "300058", "600519"]
ETF_CODES = ["510050", "159919", "512480"]

def code_to_market(code: str) -> int:
    if code.startswith(("5", "6")):
        return TDXParams.MARKET_SH  # 1
    elif code.startswith(("0", "1", "3")):
        return TDXParams.MARKET_SZ  # 0
    else:
        return TDXParams.MARKET_SZ

api = TdxHq_API()

# Try connecting to first available server
connected = False
for host_info in hq_hosts[:5]:
    host_ip = host_info[1]
    host_port = host_info[2]
    print(f"Trying {host_ip}:{host_port}...")
    try:
        if api.connect(host_ip, host_port):
            print(f"  Connected to {host_ip}:{host_port}")
            connected = True
            break
    except Exception as e:
        print(f"  Failed: {e}")

if not connected:
    print("ERROR: Could not connect to any TDX server")
    exit(1)

print("\n" + "="*60)
print("Testing STOCK codes:")
print("="*60)
for code in STOCK_CODES:
    market = code_to_market(code)
    market_name = "SH" if market == 1 else "SZ"
    time.sleep(0.3)
    data = api.get_security_bars(category=8, market=market, code=code, start=0, count=5)
    print(f"  {code} (market={market_name}/{market}): got {len(data) if data else 0} bars")
    if data:
        print(f"    First bar: {data[0]}")

print("\n" + "="*60)
print("Testing ETF codes:")
print("="*60)
for code in ETF_CODES:
    market = code_to_market(code)
    market_name = "SH" if market == 1 else "SZ"
    time.sleep(0.3)
    data = api.get_security_bars(category=8, market=market, code=code, start=0, count=5)
    print(f"  {code} (market={market_name}/{market}): got {len(data) if data else 0} bars")
    if data:
        print(f"    First bar: {data[0]}")

api.disconnect()
