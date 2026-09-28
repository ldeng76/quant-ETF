"""TDX 行情服务器选择逻辑的单元测试。

覆盖的真实教训（2026-09-28 实测）：
- TDX 节点"协议握手成功"不等于"供数"：多数节点 get_security_count 有返回，
  但所有行情/历史接口返回空。因此探活必须实际拉一根 K 线。
- 本机通达信当前连接的节点（112.45.28.4）恰恰是坏节点，不能盲信。
- connect.cfg 里的官方清单比 pytdx 内置 hq_hosts 更贴近真实可用节点。

全部用例都不打真实网络，用 monkeypatch 隔离。
"""
import pytest

from quant_etf import tdx


# ---------------------------------------------------------------- connect.cfg


def _write_connect_cfg(tmp_path, body: str):
    cfg = tmp_path / "connect.cfg"
    cfg.write_bytes(body.encode("gbk", errors="ignore"))
    return cfg


def test_parse_connect_cfg_extracts_ip_port_pairs(tmp_path, monkeypatch):
    cfg = _write_connect_cfg(
        tmp_path,
        "[HQHOST]\n"
        "HostNum=3\n"
        "IPAddress01=218.6.198.164\n"
        "Port01=7709\n"
        "IPAddress02=117.139.166.52\n"
        "Port02=7709\n"
        "IPAddress03=119.147.212.81\n"
        "Port03=7709\n",
    )
    monkeypatch.setattr(tdx, "_find_connect_cfg", lambda: cfg)

    servers = tdx.discover_hosts_from_connect_cfg()

    assert servers == [
        ("218.6.198.164", 7709),
        ("117.139.166.52", 7709),
        ("119.147.212.81", 7709),
    ]


def test_parse_connect_cfg_dedupes_and_skips_ipv6_and_lan(tmp_path, monkeypatch):
    cfg = _write_connect_cfg(
        tmp_path,
        "[HQHOST]\n"
        "IPAddress01=1.2.3.4\nPort01=7709\n"
        "IPAddress02=1.2.3.4\nPort02=7709\n"      # 重复
        "IPAddress03=192.168.0.188\nPort03=7709\n"  # 内网
        "IPAddress04=240e:d9:a003:1300::56:11\nPort04=7709\n"  # IPv6
        "IPAddress05=5.6.7.8\nPort05=7727\n",
    )
    monkeypatch.setattr(tdx, "_find_connect_cfg", lambda: cfg)

    servers = tdx.discover_hosts_from_connect_cfg()

    assert servers == [("1.2.3.4", 7709), ("5.6.7.8", 7727)]


def test_parse_connect_cfg_missing_file_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(
        tdx, "_find_connect_cfg", lambda: tmp_path / "does-not-exist.cfg"
    )
    assert tdx.discover_hosts_from_connect_cfg() == []


# ------------------------------------------------------------------ 候选构建


def test_candidates_put_verified_hosts_first(monkeypatch):
    monkeypatch.setattr(tdx, "discover_hosts_from_connect_cfg", lambda: [])
    monkeypatch.setattr(tdx, "get_local_tdx_server", lambda: None)

    candidates = tdx.get_hq_server_candidates(5)

    # 头两个必须是实测供数的节点，且 CUSTOM_HQ_HOSTS 已把可用节点排到最前
    assert candidates[0] == ("218.6.198.164", 7709)
    assert candidates[1] == ("117.139.166.52", 7709)


def test_candidates_merge_connect_cfg_and_local_process(monkeypatch):
    """connect.cfg 官方节点与本机通达信节点都要进候选，且不重复。"""
    monkeypatch.setattr(
        tdx, "discover_hosts_from_connect_cfg", lambda: [("9.9.9.9", 7709)]
    )
    # 本机通达信当前连的节点（实测是坏节点）应被收录但排在实测可用节点之后
    monkeypatch.setattr(tdx, "get_local_tdx_server", lambda: ("112.45.28.4", 7709))

    candidates = tdx.get_hq_server_candidates(20)

    assert ("9.9.9.9", 7709) in candidates
    assert ("112.45.28.4", 7709) in candidates
    assert candidates.index(("218.6.198.164", 7709)) < candidates.index(("112.45.28.4", 7709))
    assert len(candidates) == len(set(candidates)), "候选列表不应有重复"


def test_candidates_respect_max_servers(monkeypatch):
    monkeypatch.setattr(tdx, "discover_hosts_from_connect_cfg", lambda: [])
    monkeypatch.setattr(tdx, "get_local_tdx_server", lambda: None)
    assert len(tdx.get_hq_server_candidates(3)) == 3


# -------------------------------------------------------------------- 探活


class _FakeApi:
    """模拟 TdxHq_API：可控制 connect 是否成功、bars 是否有数据。"""

    def __init__(self, connect_ok, bars, raises=False):
        self._connect_ok = connect_ok
        self._bars = bars
        self._raises = raises
        self.connected_to = None
        self.disconnected = False

    def connect(self, server, port, **kwargs):
        self.connected_to = (server, port)
        if self._raises:
            raise ConnectionRefusedError("boom")
        return self._connect_ok

    def get_security_bars(self, *args, **kwargs):
        return self._bars

    def disconnect(self):
        self.disconnected = True


def test_probe_rejects_handshake_ok_but_empty(monkeypatch):
    """核心回归：握手成功但没有数据 => 探活必须判 False。"""
    monkeypatch.setattr(tdx, "TdxHq_API", lambda **kw: _FakeApi(True, None))
    assert tdx.probe_server_has_data("1.2.3.4", 7709) is False


def test_probe_rejects_empty_list_bars(monkeypatch):
    monkeypatch.setattr(tdx, "TdxHq_API", lambda **kw: _FakeApi(True, []))
    assert tdx.probe_server_has_data("1.2.3.4", 7709) is False


def test_probe_accepts_server_with_bars(monkeypatch):
    bars = [{"datetime": "2026-09-28 15:00", "close": 2.918}]
    monkeypatch.setattr(tdx, "TdxHq_API", lambda **kw: _FakeApi(True, bars))
    assert tdx.probe_server_has_data("1.2.3.4", 7709) is True


def test_probe_rejects_connect_failure(monkeypatch):
    monkeypatch.setattr(tdx, "TdxHq_API", lambda **kw: _FakeApi(False, None))
    assert tdx.probe_server_has_data("1.2.3.4", 7709) is False


def test_probe_swallows_exceptions(monkeypatch):
    monkeypatch.setattr(tdx, "TdxHq_API", lambda **kw: _FakeApi(True, None, raises=True))
    assert tdx.probe_server_has_data("1.2.3.4", 7709) is False


def test_probe_always_disconnects(monkeypatch):
    created = []

    def factory(**kw):
        api = _FakeApi(True, None)
        created.append(api)
        return api

    monkeypatch.setattr(tdx, "TdxHq_API", factory)
    tdx.probe_server_has_data("1.2.3.4", 7709)
    assert created[0].disconnected is True


# ------------------------------------------------------------ 择优与缓存


def test_select_working_server_skips_empty_and_caches_winner(monkeypatch):
    """前两个候选供数为空，第三个有数据 => 应选中第三个并写入缓存。"""
    monkeypatch.setattr(
        tdx,
        "get_hq_server_candidates",
        lambda n=8: [("bad1", 7709), ("bad2", 7709), ("good", 7709)],
    )
    responses = {"bad1": None, "bad2": [], "good": [{"close": 1.0}]}
    monkeypatch.setattr(
        tdx,
        "probe_server_has_data",
        lambda s, p, *a, **k: bool(responses.get(s)),
    )
    monkeypatch.setattr(tdx, "_set_cached_server", lambda s, p: None)
    monkeypatch.setattr(tdx, "_cached_server", None)

    assert tdx.select_working_server() == ("good", 7709)


def test_select_working_server_returns_none_when_all_empty(monkeypatch):
    monkeypatch.setattr(
        tdx, "get_hq_server_candidates", lambda n=8: [("bad1", 7709), ("bad2", 7709)]
    )
    monkeypatch.setattr(tdx, "probe_server_has_data", lambda *a, **k: False)

    assert tdx.select_working_server() is None


def test_select_working_server_caches_the_verified_server(monkeypatch):
    monkeypatch.setattr(
        tdx, "get_hq_server_candidates", lambda n=8: [("good", 7709)]
    )
    monkeypatch.setattr(tdx, "probe_server_has_data", lambda *a, **k: True)
    monkeypatch.setattr(tdx, "_cached_server", None)

    tdx.select_working_server()

    assert tdx._get_cached_server() == ("good", 7709)


# ------------------------------------------------------ 默认服务器选择优先级


def test_default_server_prefers_cached(monkeypatch):
    monkeypatch.setattr(tdx, "_cached_server", ("cached-host", 7709))
    monkeypatch.setattr(tdx, "get_hq_server_candidates", lambda n=8: [("x", 7709)])

    assert tdx._get_default_hq_server() == ("cached-host", 7709)


def test_default_server_does_not_blindly_trust_local_tdx_process(monkeypatch):
    """
    回归：通达信当前连接的节点曾经是"第一优先级"，而它恰恰对 pytdx 返回 0 数据，
    会让整条采集链路静默失效。现在它只能作为候选之一。
    """
    monkeypatch.setattr(tdx, "_cached_server", None)
    monkeypatch.setattr(tdx, "get_hq_server_candidates", lambda n=8: [("218.6.198.164", 7709)])
    # 本机通达信进程正在连一个坏节点
    monkeypatch.setattr(tdx, "get_local_tdx_server", lambda: ("112.45.28.4", 7709))

    server, port = tdx._get_default_hq_server()

    assert (server, port) == ("218.6.198.164", 7709)
    assert (server, port) != ("112.45.28.4", 7709)


def test_default_server_falls_back_to_hardcoded_tail(monkeypatch):
    monkeypatch.setattr(tdx, "_cached_server", None)
    monkeypatch.setattr(tdx, "get_hq_server_candidates", lambda n=8: [])

    server, port = tdx._get_default_hq_server()

    assert isinstance(server, str) and isinstance(port, int)
