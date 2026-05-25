"""
[已废弃] 获取ETF_POOL各票的最近10个交易日1分钟K线数据

请使用新的 CLI 子命令替代:
    uv run quant-etf minute-backfill --days 30          # 补采最近30个交易日
    uv run quant-etf minute-backfill --start 2026-04-01 --end 2026-05-25  # 指定范围
    uv run quant-etf minute-backfill --codes 510050,159991  # 指定标的
"""
import sys
from loguru import logger

logger.warning("此脚本已废弃，请使用: uv run quant-etf minute-backfill --help")
sys.exit(1)
