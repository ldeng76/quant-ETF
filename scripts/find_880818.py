"""查找 880818 板块指数成份股 - 通过 pytdx 服务器获取"""
from pytdx.hq import TdxHq_API
from pytdx.reader.block_reader import BlockReader
from quant_etf.tdx import CUSTOM_HQ_HOSTS


def try_server():
    """尝试从 TDX 服务器下载板块数据并查找 880818"""
    api = TdxHq_API()
    for host_info in CUSTOM_HQ_HOSTS[1:10]:
        ip, port = host_info[1], int(host_info[2])
        try:
            if api.connect(ip, port):
                print(f"Connected to {ip}:{port}")
                for bf in ['block.dat', 'block_zs.dat', 'block_fg.dat', 'block_gn.dat']:
                    try:
                        data = api.get_and_parse_block_info(bf)
                        if data:
                            names = sorted(set(d.get('blockname', '') for d in data))
                            print(f"\n{bf}: {len(names)} unique blocks")
                            print(f"  All names: {names[:50]}")
                        else:
                            print(f"{bf}: no data")
                    except Exception as e:
                        print(f"{bf}: error - {e}")
                api.disconnect()
                return
            else:
                print(f"{ip}: connect failed")
        except Exception as e:
            print(f"{ip}: {e}")


if __name__ == '__main__':
    try_server()
