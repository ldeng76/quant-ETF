"""
分钟级K线数据采集器 (已迁移至统一 CLI)

Usage (preferred):
    uv run quant-etf minute-collect

Legacy usage (still works):
    uv run run_minute_collector.py
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent / "src"))

from quant_etf.cli import main

if __name__ == "__main__":
    sys.argv = [sys.argv[0], "minute-collect", *sys.argv[1:]]
    main()
