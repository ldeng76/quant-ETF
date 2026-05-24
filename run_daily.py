"""
运行每日任务脚本 (已迁移至统一 CLI)

Usage (preferred):
    uv run quant-etf daily-run [--days N] [--date YYYY-MM-DD]

Legacy usage (still works):
    uv run run_daily.py --days 3
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent / "src"))

from quant_etf.cli import main

if __name__ == "__main__":
    sys.argv = [sys.argv[0], "daily-run", *sys.argv[1:]]
    main()
