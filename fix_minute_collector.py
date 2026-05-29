import re

with open('src/quant_etf/minute_collector.py', 'r', encoding='utf-8') as f:
    content = f.read()

func_def = '''def get_minute_bars(
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


'''

pattern = r'def get_minute_bars\(.*?(?=\ndef collect_for_pool)'
new_content = re.sub(pattern, func_def, content, flags=re.DOTALL)

with open('src/quant_etf/minute_collector.py', 'w', encoding='utf-8') as f:
    f.write(new_content)

print('Done')