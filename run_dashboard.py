"""
启动量化ETF看板 (已迁移至统一 CLI)

Usage (preferred):
    uv run quant-etf dashboard [--port PORT] [--host HOST]

Legacy usage (still works):
    uv run python run_dashboard.py
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent / "src"))

from quant_etf.cli import main

if __name__ == "__main__":
    sys.argv = [sys.argv[0], "dashboard", *sys.argv[1:]]
    main()
