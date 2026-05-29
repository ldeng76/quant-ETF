from pathlib import Path
import os
from quant_etf.conf import DATA_DIR, PROJECT_ROOT

# ============================================================
# PostgreSQL 数据库配置 (替代 SQLite)
# ============================================================
POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
POSTGRES_USER = os.environ.get("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "")
POSTGRES_DB = os.environ.get("POSTGRES_DB", "quant_etf")

def _get_postgres_dsn() -> str:
    """构建 PostgreSQL DSN 连接字符串"""
    return f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

POSTGRES_DSN = _get_postgres_dsn()
POSTGRES_POOL_SIZE = int(os.environ.get("POSTGRES_POOL_SIZE", "10"))
POSTGRES_MAX_OVERFLOW = int(os.environ.get("POSTGRES_MAX_OVERFLOW", "20"))

# PostgreSQL 数据表（统一存储）
# minute_bars, minute_bars_15m, market_daily, monitor_alerts

# 元数据
STOCK_CODE_NAME_PATH = DATA_DIR / "meta" / "stock_code_name.json"

# 看板服务配置
DASHBOARD_HOST = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "8522"))

# SSE配置
SSE_HEARTBEAT_INTERVAL = 30  # 秒

# 告警阈值
ALERT_MOMENTUM_SHOCK_THRESHOLD = 0.15  # 动量突变阈值 15%

# ============================================================
# 分布式多租户配置
# ============================================================

# 节点角色：primary | secondary，默认 primary（单机兼容）
NODE_ROLE = os.environ.get("NODE_ROLE", "primary")
IS_PRIMARY = NODE_ROLE == "primary"

# JWT配置
# 未设 JWT_SECRET_KEY 时禁用认证（开发模式兼容）
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 7  # 有效期7天

# GitHub OAuth配置
GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")
GITHUB_REDIRECT_URI = os.environ.get(
    "GITHUB_REDIRECT_URI",
    f"http://127.0.0.1:{DASHBOARD_PORT}/auth/github/callback"
)

# 企业微信OAuth配置
WECOM_CORPID = os.environ.get("WECOM_CORPID", "")
WECOM_AGENT_ID = os.environ.get("WECOM_AGENT_ID", "")
WECOM_SECRET = os.environ.get("WECOM_SECRET", "")

# ============================================================
# 微信小程序客户端配置
# ============================================================
WECHAT_MINI_APPID = os.environ.get("WECHAT_MINI_APPID", "")
WECHAT_MINI_SECRET = os.environ.get("WECHAT_MINI_SECRET", "")
# 微信小程序登录凭证校验地址
WECHAT_CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"

# 管理员OAuth ID（首个匹配此ID的登录用户成为admin）
ADMIN_OAUTH_ID = os.environ.get("ADMIN_OAUTH_ID", "")
