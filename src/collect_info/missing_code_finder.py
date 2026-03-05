#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缺失代码查找工具
用于找出 ETF_POOL、STOCK_POOL、MID_TERM_STOCK_POOL 中缺失于 stock_code_name.json 的代码
"""

import sys
import json
from pathlib import Path
from typing import Dict, List, Set

# 添加项目根目录到 path，以便导入 quant_etf.conf
_project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_project_root / "src"))

from quant_etf.conf import ETF_POOL, STOCK_POOL, MID_TERM_STOCK_POOL


def normalize_code(code: str) -> str:
    """
    标准化代码为 6 位字符串
    :param code: 原始代码
    :return: 6 位字符串
    """
    code = str(code).strip()
    if len(code) > 6:
        code = code[-6:]
    return code.zfill(6)


def load_target_codes(target_file: str | Path) -> Set[str]:
    """
    解析目标 JSON，返回已有的 code 集合
    :param target_file: stock_code_name.json 文件路径
    :return: code 集合
    """
    target_path = Path(target_file)
    if not target_path.exists():
        return set()

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            items = json.load(f)

        if isinstance(items, list):
            return {normalize_code(item["code"]) for item in items if "code" in item}
        return set()
    except Exception:
        return set()


def find_missing_in_pool(pool: List[str], existing: Set[str]) -> List[str]:
    """
    比对单个池，返回缺失列表
    :param pool: 代码池列表
    :param existing: 已存在的代码集合
    :return: 缺失代码列表
    """
    normalized_pool = {normalize_code(c) for c in pool}
    missing = normalized_pool - existing
    return sorted(missing)


def find_missing_codes(target_file: str | Path) -> Dict[str, List[str]]:
    """
    读取目标 JSON，对比 ETF_POOL/STOCK_POOL/MID_TERM_STOCK_POOL，返回缺失代码
    :param target_file: stock_code_name.json 文件路径
    :return: {"etf": [...], "stock": [...], "mid_term_stock": [...]}
    """
    existing = load_target_codes(target_file)

    return {
        "etf": find_missing_in_pool(ETF_POOL, existing),
        "stock": find_missing_in_pool(STOCK_POOL, existing),
        "mid_term_stock": find_missing_in_pool(MID_TERM_STOCK_POOL, existing),
    }


def get_all_missing_codes(target_file: str | Path) -> List[str]:
    """
    获取所有缺失代码的合并列表（去重）
    :param target_file: stock_code_name.json 文件路径
    :return: 缺失代码列表（已去重排序）
    """
    missing = find_missing_codes(target_file)
    all_codes = set()
    for codes in missing.values():
        all_codes.update(codes)
    return sorted(all_codes)


def main():
    """命令行入口"""
    import argparse

    parser = argparse.ArgumentParser(description="查找缺失的股票代码")
    parser.add_argument(
        "--target",
        type=str,
        default="data/meta/stock_code_name.json",
        help="目标 JSON 文件路径 (默认: data/meta/stock_code_name.json)",
    )
    args = parser.parse_args()

    target_path = _project_root / args.target
    missing = find_missing_codes(target_path)

    print(f"目标文件: {target_path}")
    print(f"ETF 缺失: {len(missing['etf'])} 个 - {missing['etf']}")
    print(f"短线股票缺失: {len(missing['stock'])} 个 - {missing['stock']}")
    print(f"中线股票缺失: {len(missing['mid_term_stock'])} 个 - {missing['mid_term_stock']}")

    total = sum(len(v) for v in missing.values())
    print(f"\n总计缺失: {total} 个")

    all_missing = get_all_missing_codes(target_path)
    if all_missing:
        print(f"\n所有缺失代码 (去重): {all_missing}")


if __name__ == "__main__":
    main()
