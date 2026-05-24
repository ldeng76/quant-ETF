"""
Dashboard 健康检查 (已迁移至统一 CLI)

Usage (preferred):
    uv run quant-etf check [--port PORT]

Legacy usage (still works):
    uv run python _check.py
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent / "src"))

from quant_etf.cli import main

if __name__ == "__main__":
    sys.argv = [sys.argv[0], "check", *sys.argv[1:]]
    main()
