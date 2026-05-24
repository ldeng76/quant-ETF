"""
批量补跑历史日期任务 (已迁移至统一 CLI)

Usage (preferred):
    uv run quant-etf backfill 2026-03-02 2026-03-05

Legacy usage (still works):
    uv run python backfill_daily.py
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent / "src"))

from quant_etf.cli import main

if __name__ == "__main__":
    sys.argv = [sys.argv[0], "backfill", *sys.argv[1:]]
    main()
