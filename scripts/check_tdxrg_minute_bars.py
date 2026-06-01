#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
校验：通达信热股(TDXRG)板块中的每只股票是否在 minute_bars 表的最后一次收集中出现。
"""

import sys
from pathlib import Path

# 通达信板块文件路径
TDX_ROOT = Path(r"C:\new_hxzq_hc")
BLK_FILE = TDX_ROOT / "T0002" / "blocknew" / "TDXRG.blk"


def parse_blk_file(blk_path: Path) -> list[tuple[str, str, str]]:
    """
    解析 .blk 文件。
    返回 [(原始7位, 6位代码, 市场), ...]
    """
    stocks = []
    with open(blk_path, "r", encoding="gbk") as f:
        content = f.read()
    for line in content.strip().split("\n"):
        line = line.strip()
        if not line or not line.isdigit():
            continue
        if len(line) == 7:
            raw = line
            code6 = line[1:]  # 后6位
            market = "SZ" if line[0] == "0" else "SH"
            stocks.append((raw, code6, market))
    return stocks


def main():
    if not BLK_FILE.exists():
        print(f"[ERROR] blk file not found: {BLK_FILE}")
        sys.exit(1)

    # 1. 读取通达信热股板块
    stocks = parse_blk_file(BLK_FILE)
    print(f"TDXRG.blk: {len(stocks)} stocks\n")

    # 显示前5个解析结果
    print("Sample parsed codes:")
    for raw, code6, mkt in stocks[:5]:
        print(f"  raw={raw} -> code6={code6} market={mkt}")
    print()

    # 2. 连接 PG
    import psycopg2

    env_path = Path(__file__).resolve().parent.parent / ".env"
    env = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()

    conn = psycopg2.connect(
        host=env.get("POSTGRES_HOST", "localhost"),
        port=int(env.get("POSTGRES_PORT", "5432")),
        user=env.get("POSTGRES_USER", "postgres"),
        password=env.get("POSTGRES_PASSWORD", ""),
        database=env.get("POSTGRES_DB", "quant_etf"),
    )
    cur = conn.cursor()

    # 3. 查看 minute_bars 总况
    cur.execute("SELECT MAX(time) FROM minute_bars")
    last_time = cur.fetchone()[0]
    print(f"minute_bars max time: {last_time}")

    cur.execute("SELECT COUNT(*), COUNT(DISTINCT code) FROM minute_bars")
    total, distinct = cur.fetchone()
    print(f"minute_bars: {total} rows, {distinct} distinct codes\n")

    # 4. 查看 minute_bars 中 code 的实际格式 (sample)
    cur.execute("SELECT DISTINCT code FROM minute_bars ORDER BY code LIMIT 20")
    sample_codes = [row[0] for row in cur.fetchall()]
    print(f"Sample codes in minute_bars ({len(sample_codes)}):")
    for c in sample_codes:
        print(f"  '{c}' (len={len(c)})")
    print()

    # 5. 构建 minute_bars code -> last_time 映射
    cur.execute("SELECT code, MAX(time) AS last_time FROM minute_bars GROUP BY code")
    db_codes = {row[0]: row[1] for row in cur.fetchall()}
    conn.close()

    # 6. 匹配 - 尝试两种格式: code6 和 raw
    found = []
    missing = []

    for raw, code6, market in stocks:
        # 先用 code6 匹配，再用 raw 匹配
        if code6 in db_codes:
            found.append((code6, market, db_codes[code6]))
        elif raw in db_codes:
            found.append((raw, market, db_codes[raw]))
        else:
            missing.append((raw, code6, market))

    print("=" * 60)
    print(f"RESULT: total={len(stocks)}, found={len(found)}, missing={len(missing)}")
    print("=" * 60)

    if found:
        print(f"\n[OK] Found in minute_bars ({len(found)}):")
        for code, market, lt in sorted(found):
            print(f"  {code} ({market})  last={lt}")

    if missing:
        print(f"\n[MISS] Not in minute_bars ({len(missing)}):")
        for raw, code6, market in sorted(missing):
            print(f"  raw={raw} code6={code6} market={market}")

    # 7. 额外: minute_bars 中存在但不在热股板块的
    blk_codes = set()
    for raw, code6, _ in stocks:
        blk_codes.add(code6)
        blk_codes.add(raw)
    extra = {c: t for c, t in db_codes.items() if c not in blk_codes}
    if extra:
        print(f"\n[INFO] {len(extra)} codes in minute_bars but NOT in TDXRG (first 20):")
        for c, lt in sorted(extra.items())[:20]:
            print(f"  {c}  last={lt}")
        if len(extra) > 20:
            print(f"  ... and {len(extra) - 20} more")


if __name__ == "__main__":
    main()
