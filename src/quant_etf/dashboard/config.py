from pathlib import Path
import os
from quant_etf.conf import DATA_DIR, PROJECT_ROOT

# 数据路径
DASHBOARD_DB_PATH = DATA_DIR / "dashboard.db"

# 已有DuckDB数据路径
RESULTS_DUCKDB_PATH = DATA_DIR / "results" / "results.duckdb"
ALERTS_DUCKDB_PATH = DATA_DIR / "alerts" / "alerts.duckdb"
MINUTE_DUCKDB_PATH = DATA_DIR / "minute" / "minute_data.duckdb"
MARKET_DUCKDB_PATH = DATA_DIR / "market.duckdb"

# 元数据
STOCK_CODE_NAME_PATH = DATA_DIR / "meta" / "stock_code_name.json"

# 看板服务配置
DASHBOARD_HOST = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "8522"))

# SSE配置
SSE_HEARTBEAT_INTERVAL = 30  # 秒

# 告警阈值
ALERT_MOMENTUM_SHOCK_THRESHOLD = 0.15  # 动量突变阈值 15%
