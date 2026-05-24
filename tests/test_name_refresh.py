"""
单元测试：ETFDataSource.refresh_stock_names

验证：
1. 已存在的错误条目会被在线 API 返回值覆盖（与 backfill 跳过差集形成对比）。
2. market 字段统一由 _market_for_code 判定（5/6 -> sh，0/1/3 -> sz）。
3. 在线查询失败的代码：旧条目保留，且出现在 failed 列表。
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from quant_etf.data_source import ETFDataSource


@pytest.fixture
def patched_pools(monkeypatch):
    """
    将 ETF_POOL/STOCK_POOL/MID_TERM_STOCK_POOL 统一打桩，避免依赖真实配置。
    """
    fake_etf_pool = ["159516", "510050"]
    fake_stock_pool = ["000001"]
    fake_mid_pool = ["600036"]
    import quant_etf.conf as conf
    monkeypatch.setattr(conf, "ETF_POOL", fake_etf_pool, raising=False)
    monkeypatch.setattr(conf, "STOCK_POOL", fake_stock_pool, raising=False)
    monkeypatch.setattr(conf, "MID_TERM_STOCK_POOL", fake_mid_pool, raising=False)
    return {
        "etf": fake_etf_pool,
        "stock": fake_stock_pool,
        "mid": fake_mid_pool,
        "all": sorted(set(fake_etf_pool + fake_stock_pool + fake_mid_pool)),
    }


def _write_existing_json(target_file: Path, items: list[dict]) -> None:
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def test_refresh_overwrites_wrong_existing_name(tmp_path, patched_pools):
    """
    场景：JSON 中 159516 名字错误（"易方达中证1000ETF"），refresh 应覆盖为新值。
    并且 5xxxxx 的 market 应被统一修正为 sh。
    """
    target = tmp_path / "stock_code_name.json"
    _write_existing_json(target, [
        {"code": "159516", "name": "易方达中证1000ETF", "market": "sz"},
        {"code": "510050", "name": "华夏上证50ETF", "market": "sz"},  # market 错
        {"code": "000001", "name": "平安银行", "market": "sz"},
        {"code": "600036", "name": "招商银行", "market": "sh"},
    ])

    fake_api_results = [
        {"code": "159516", "name": "机器人ETF", "market": "sz", "source": "sina"},
        {"code": "510050", "name": "华夏上证50ETF", "market": "sh", "source": "sina"},
        {"code": "000001", "name": "平安银行", "market": "sz", "source": "sina"},
        {"code": "600036", "name": "招商银行", "market": "sh", "source": "sina"},
    ]

    with patch("simple_stock_api.SimpleStockAPI.batch_query", return_value=fake_api_results):
        ds = ETFDataSource(data_dir=tmp_path)
        report = ds.refresh_stock_names(target_file=target)

    # 校准 159516 的 name 应被覆盖
    updated_codes = {u["code"] for u in report["updated"]}
    assert "159516" in updated_codes
    assert "510050" in updated_codes  # market 也算 updated

    # 真实写入的内容
    written = json.loads(target.read_text(encoding="utf-8"))
    by_code = {it["code"]: it for it in written}
    assert by_code["159516"]["name"] == "机器人ETF"
    # 5xxxxx 必须修正为 sh
    assert by_code["510050"]["market"] == "sh"
    assert by_code["159516"]["market"] == "sz"
    # 未变更的不应出现在 updated
    assert "000001" not in updated_codes
    assert "600036" not in updated_codes
    assert report["failed"] == []


def test_market_inferred_locally_ignores_api_market(tmp_path, patched_pools):
    """
    market 必须由 _market_for_code 判定，即便 API 返回错误的 market 也要纠正。
    """
    target = tmp_path / "stock_code_name.json"
    _write_existing_json(target, [])

    fake_api_results = [
        # API 返回错误 market，应被忽略并按代码本地推断
        {"code": "159516", "name": "机器人ETF", "market": "sh", "source": "x"},
        {"code": "510050", "name": "华夏上证50ETF", "market": "sz", "source": "x"},
        {"code": "000001", "name": "平安银行", "market": "sh", "source": "x"},
        {"code": "600036", "name": "招商银行", "market": "sz", "source": "x"},
    ]

    with patch("simple_stock_api.SimpleStockAPI.batch_query", return_value=fake_api_results):
        ds = ETFDataSource(data_dir=tmp_path)
        ds.refresh_stock_names(target_file=target)

    written = json.loads(target.read_text(encoding="utf-8"))
    by_code = {it["code"]: it for it in written}
    assert by_code["159516"]["market"] == "sz"
    assert by_code["510050"]["market"] == "sh"
    assert by_code["000001"]["market"] == "sz"
    assert by_code["600036"]["market"] == "sh"


def test_refresh_keeps_old_entry_when_query_fails(tmp_path, patched_pools):
    """
    在线查询失败（name 为空）时：保留旧条目，并加入 failed 列表。
    """
    target = tmp_path / "stock_code_name.json"
    _write_existing_json(target, [
        {"code": "159516", "name": "旧错误名", "market": "sz"},
        {"code": "510050", "name": "华夏上证50ETF", "market": "sh"},
        {"code": "000001", "name": "平安银行", "market": "sz"},
        {"code": "600036", "name": "招商银行", "market": "sh"},
    ])

    fake_api_results = [
        # 159516 查询失败
        {"code": "159516", "name": None, "market": "sz", "source": None, "error": "fail"},
        {"code": "510050", "name": "华夏上证50ETF", "market": "sh", "source": "sina"},
        {"code": "000001", "name": "平安银行", "market": "sz", "source": "sina"},
        {"code": "600036", "name": "招商银行", "market": "sh", "source": "sina"},
    ]

    with patch("simple_stock_api.SimpleStockAPI.batch_query", return_value=fake_api_results):
        ds = ETFDataSource(data_dir=tmp_path)
        report = ds.refresh_stock_names(target_file=target)

    assert "159516" in report["failed"]

    written = json.loads(target.read_text(encoding="utf-8"))
    by_code = {it["code"]: it for it in written}
    # 旧条目原样保留
    assert by_code["159516"]["name"] == "旧错误名"


def test_market_for_code_helper():
    """
    _market_for_code 的边界判定。
    """
    fn = ETFDataSource._market_for_code
    assert fn("510050") == "sh"
    assert fn("600036") == "sh"
    assert fn("159516") == "sz"
    assert fn("000001") == "sz"
    assert fn("300750") == "sz"
    # 6 位补齐
    assert fn("1") == "sz"


def test_dry_run_does_not_write(tmp_path, patched_pools):
    """
    dry_run=True 时不应写入文件。
    """
    target = tmp_path / "stock_code_name.json"
    original = [{"code": "159516", "name": "旧错误名", "market": "sz"}]
    _write_existing_json(target, original)

    fake_api_results = [
        {"code": "159516", "name": "机器人ETF", "market": "sz", "source": "sina"},
        {"code": "510050", "name": "华夏上证50ETF", "market": "sh", "source": "sina"},
        {"code": "000001", "name": "平安银行", "market": "sz", "source": "sina"},
        {"code": "600036", "name": "招商银行", "market": "sh", "source": "sina"},
    ]

    with patch("simple_stock_api.SimpleStockAPI.batch_query", return_value=fake_api_results):
        ds = ETFDataSource(data_dir=tmp_path)
        report = ds.refresh_stock_names(target_file=target, dry_run=True)

    # 报告显示有差异
    assert any(u["code"] == "159516" for u in report["updated"])
    # 但磁盘内容未变
    assert json.loads(target.read_text(encoding="utf-8")) == original
