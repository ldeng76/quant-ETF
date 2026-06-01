# tests/test_pool_loader.py
"""pool_loader 单元测试：使用临时目录模拟通达信 blocknew"""
from pathlib import Path
import pytest
from quant_etf import pool_loader


@pytest.fixture
def fake_block_dir(tmp_path, monkeypatch):
    """创建临时 blocknew 目录，并让 pool_loader.TDX_BLOCK_DIR 指向它"""
    blk_dir = tmp_path / "blocknew"
    blk_dir.mkdir()
    monkeypatch.setattr(pool_loader, "TDX_BLOCK_DIR", blk_dir)
    return blk_dir


def _write_blk(directory: Path, name: str, lines: list[str]) -> Path:
    p = directory / f"{name}.blk"
    # GBK + \r\n 与通达信一致
    p.write_bytes("\r\n".join(lines).encode("gbk"))
    return p


def test_parse_blk_file_returns_codes_only(fake_block_dir):
    _write_blk(fake_block_dir, "TDXRG", ["0000063", "1600030", "0300750"])
    codes = pool_loader.load_pool_from_tdx("TDXRG")
    assert codes == ["000063", "600030", "300750"]


def test_parse_blk_file_skips_blank_and_invalid_lines(fake_block_dir):
    _write_blk(fake_block_dir, "TDXRG", ["0000063", "", "abc", "1600030"])
    codes = pool_loader.load_pool_from_tdx("TDXRG")
    assert codes == ["000063", "600030"]


def test_load_pool_from_tdx_missing_file_raises(fake_block_dir):
    with pytest.raises(RuntimeError, match="TDX block file not found"):
        pool_loader.load_pool_from_tdx("NOT_EXIST")


def test_load_pool_from_tdx_empty_block_raises(fake_block_dir):
    _write_blk(fake_block_dir, "EMPTY", [])
    with pytest.raises(RuntimeError, match="TDX block is empty"):
        pool_loader.load_pool_from_tdx("EMPTY")


def test_get_stock_pool_stock_uses_tdx(fake_block_dir):
    _write_blk(fake_block_dir, "TDXRG", ["0000063"])
    # conf.TDX_STOCK_BLOCKS["stock"] 默认为 "TDXRG"
    codes = pool_loader.get_stock_pool("stock")
    assert codes == ["000063"]


def test_get_stock_pool_etf_returns_hardcoded(monkeypatch):
    """etf 必须走硬编码，不能读板块"""
    from quant_etf.conf import ETF_POOL
    monkeypatch.delenv("TDX_DATA_PATH", raising=False)
    codes = pool_loader.get_stock_pool("etf")
    assert codes == list(ETF_POOL)
    assert len(codes) > 10


def test_get_stock_pool_unknown_type_returns_empty():
    assert pool_loader.get_stock_pool("whatever") == []
