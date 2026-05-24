"""
Legacy CLI entry point (已迁移至统一 CLI)

Usage (preferred):
    uv run quant-etf run etf
    uv run quant-etf list-tasks

Legacy usage (still works):
    uv run python src/main.py etf
"""
import sys
from quant_etf.cli import main

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--list" in args or "-l" in args:
        sys.argv = [sys.argv[0], "list-tasks"]
    elif "--backfill-stock-code-name" in args or "-b" in args:
        sys.argv = [sys.argv[0], "backfill-stock-names"]
    else:
        sys.argv = [sys.argv[0], "run"] + [a for a in args if a not in ("--update", "-u")]
    main()
