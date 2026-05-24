"""
启动量化ETF看板

Usage:
    uv run python run_dashboard.py
"""
import sys
from pathlib import Path

# Add src to sys.path to ensure modules can be imported
sys.path.append(str(Path(__file__).parent / "src"))

from quant_etf.dashboard.app import main

if __name__ == "__main__":
    main()
