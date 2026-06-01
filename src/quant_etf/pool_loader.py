"""
股票池动态加载器

职责：
- 解析通达信自定义板块文件（.blk）
- 按 pool_type 返回对应股票池代码列表

数据源：
- stock / mid_term：从 TDX_BLOCK_DIR/<block_name>.blk 读取
- etf：直接返回 conf.ETF_POOL（硬编码）
"""
from pathlib import Path
from loguru import logger

from quant_etf.conf import (
    ETF_POOL,
    TDX_BLOCK_DIR,
    TDX_STOCK_BLOCKS,
)


def parse_blk_file(blk_path: Path) -> list[str]:
    """
    解析 .blk 文件，返回股票代码列表。

    文件格式（GBK 纯文本，\r\n 分隔）：
    每行 7 位数字：第 1 位市场代码（0=SZ, 1=SH），后 6 位股票代码。
    返回的 code 仅包含 6 位代码（去掉市场码前缀）。
    """
    if not blk_path.exists():
        raise RuntimeError(f"TDX block file not found: {blk_path}")

    content = blk_path.read_text(encoding="gbk", errors="ignore")
    codes: list[str] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        if not line.isdigit() or len(line) != 7:
            logger.debug(f"[pool_loader] skip invalid line: {line!r} in {blk_path}")
            continue
        codes.append(line[1:])  # 去市场码
    return codes


def load_pool_from_tdx(block_name: str) -> list[str]:
    """读取 TDX_BLOCK_DIR/<block_name>.blk，返回代码列表。"""
    blk_path = TDX_BLOCK_DIR / f"{block_name}.blk"
    codes = parse_blk_file(blk_path)
    if not codes:
        raise RuntimeError(f"TDX block is empty: {blk_path}")
    logger.info(f"[pool_loader] Loaded {len(codes)} codes from TDX block '{block_name}'")
    return codes


def get_stock_pool(pool_type: str) -> list[str]:
    """
    按 pool_type 返回股票池代码列表（运行时主入口）。

    - "etf"      → conf.ETF_POOL（硬编码）
    - "stock"    → TDX_STOCK_BLOCKS["stock"] 板块
    - "mid_term" → TDX_STOCK_BLOCKS["mid_term"] 板块
    - 其他       → []
    """
    if pool_type == "etf":
        return list(ETF_POOL)

    block_name = TDX_STOCK_BLOCKS.get(pool_type)
    if not block_name:
        logger.warning(f"[pool_loader] Unknown pool_type: {pool_type}")
        return []

    return load_pool_from_tdx(block_name)
