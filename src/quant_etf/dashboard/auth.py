"""
OAuth2 认证客户端 + JWT 会话管理
支持 GitHub、企业微信 OAuth、微信小程序、本地密码登录

PostgreSQL 语法：%s, %s... 占位符
"""
import secrets
import time
import urllib.parse
from dataclasses import dataclass
from typing import Optional

import httpx
import jwt
from argon2 import PasswordHasher
from loguru import logger

from .config import (
    JWT_SECRET_KEY, JWT_ALGORITHM, JWT_EXPIRE_DAYS,
    GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, GITHUB_REDIRECT_URI,
    WECOM_CORPID, WECOM_AGENT_ID, WECOM_SECRET,
    ADMIN_OAUTH_ID,
)
from .db import execute, query_one

_ph = PasswordHasher()


# ============================================================
# 暴力破解防护（进程内内存计数）
# ============================================================
_login_lockout: dict[str, tuple] = {}  # username -> (failure_count, locked_until_ts)


def _is_locked(username: str) -> bool:
    if username not in _login_lockout:
        return False
    count, locked_until = _login_lockout[username]
    if count >= 5 and time.time() < locked_until:
        return True
    return False


def _record_failure(username: str):
    now = time.time()
    if username not in _login_lockout:
        _login_lockout[username] = (1, now + 900)
    else:
        count, locked_until = _login_lockout[username]
        if time.time() >= locked_until:
            _login_lockout[username] = (1, now + 900)
        else:
            _login_lockout[username] = (count + 1, now + 900)


def _clear_failures(username: str):
    _login_lockout.pop(username, None)

# ============================================================
# 本地密码登录
# ============================================================

def hash_password(password: str) -> str:
    """Argon2id 哈希密码"""
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """验证密码，返回 bool"""
    try:
        _ph.verify(password_hash, password)
        return True
    except Exception:
        return False


def is_local_login_locked(username: str) -> bool:
    """检查账号是否被锁定"""
    return _is_locked(username)


def record_local_failure(username: str):
    """记录一次失败登录"""
    _record_failure(username)


def clear_local_failures(username: str):
    """清除失败计数（登录成功后）"""
    _clear_failures(username)


def get_local_user_by_username(username: str) -> Optional[dict]:
    """根据用户名查找本地用户（仅返回激活账号）"""
    return query_one(
        "SELECT * FROM local_users WHERE username = %s AND is_active = true",
        [username],
    )


def create_local_user(username: str, password: str, role: str = "user", display_name: str = "") -> int:
    """创建本地用户，返回 user_id"""
    password_hash = hash_password(password)
    return execute(
        """INSERT INTO local_users (username, password_hash, display_name, role)
           VALUES (%s, %s, %s, %s)""",
        [username, password_hash, display_name or username, role],
    )


def init_local_users_table():
    """创建 local_users 表（如不存在）"""
    execute("""
        CREATE TABLE IF NOT EXISTS local_users (
            id              SERIAL PRIMARY KEY,
            username        VARCHAR(50) UNIQUE NOT NULL,
            password_hash   VARCHAR(255) NOT NULL,
            display_name    VARCHAR(100),
            role            VARCHAR(20) DEFAULT 'user',
            is_active       BOOLEAN DEFAULT true,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


@dataclass
class OAuthUserInfo:
    """OAuth获取到的用户信息"""
    provider: str          # 'github' | 'wecom'
    oauth_id: str          # provider侧的用户ID
    username: str          # 用户名（login名称）
    display_name: str      # 显示名称
    email: Optional[str]
    avatar_url: Optional[str]


# ============================================================
# JWT 工具
# ============================================================

def is_auth_enabled() -> bool:
    """是否启用了认证（JWT_SECRET_KEY已配置）"""
    return bool(JWT_SECRET_KEY)


def create_jwt(user_id: int, role: str = "user", provider: str = "oauth") -> str:
    """签发 JWT"""
    payload = {
        "sub": str(user_id),
        "role": role,
        "iat": int(time.time()),
        "exp": int(time.time()) + JWT_EXPIRE_DAYS * 86400,
        "provider": provider,
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def verify_jwt(token: str) -> Optional[dict]:
    """验证 JWT，返回 payload 或 None"""
    if not JWT_SECRET_KEY:
        return None
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


def decode_jwt_unsafe(token: str) -> Optional[dict]:
    """不解签名，仅解析 payload（用于日志/调试）"""
    try:
        return jwt.decode(token, options={"verify_signature": False})
    except Exception:
        return None


# ============================================================
# CSRF State 管理
# ============================================================

# 简单的内存缓存（进程内，多实例不共享，仅防CSRF）
_state_store: dict[str, tuple] = {}  # state -> (provider, created_at)


def create_oauth_state(provider: str) -> str:
    """生成安全的随机 state"""
    state = secrets.token_urlsafe(32)
    _state_store[state] = (provider, time.time())
    # 清理 10 分钟前的 state
    now = time.time()
    for k, (_, ts) in list(_state_store.items()):
        if now - ts > 600:
            del _state_store[k]
    return state


def verify_oauth_state(state: str) -> Optional[str]:
    """验证 state，返回 provider 或 None（已过期则删除）"""
    if state not in _state_store:
        return None
    provider, created_at = _state_store.pop(state)
    if time.time() - created_at > 600:
        return None
    return provider


# ============================================================
# OAuth URL 生成
# ============================================================

def create_github_oauth_url() -> str:
    """生成 GitHub OAuth 授权 URL"""
    qs = urllib.parse.urlencode({
        "client_id": GITHUB_CLIENT_ID,
        "redirect_uri": GITHUB_REDIRECT_URI,
        "scope": "user:email",
        "state": create_oauth_state("github"),
    })
    return f"https://github.com/login/oauth/authorize?{qs}"


def create_wecom_oauth_url(redirect_uri: str) -> str:
    """生成企业微信 OAuth 授权 URL"""
    qs = urllib.parse.urlencode({
        "appid": WECOM_CORPID,
        "agentid": WECOM_AGENT_ID,
        "redirect_uri": redirect_uri,
        "state": create_oauth_state("wecom"),
    })
    return f"https://open.work.weixin.qq.com/wwopen/sso/qrConnect?{qs}"


# ============================================================
# OAuth 回调处理
# ============================================================

async def handle_github_callback(code: str, state: str) -> Optional[OAuthUserInfo]:
    """处理 GitHub OAuth 回调，返回用户信息"""
    provider = verify_oauth_state(state)
    if provider != "github":
        return None

    # 1. 用 code 换 access_token
    token_url = "https://github.com/login/oauth/access_token"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            token_url,
            data={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": GITHUB_REDIRECT_URI,
            },
            headers={"Accept": "application/json"},
        )
        token_data = resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            return None

        # 2. 用 access_token 获取用户信息
        resp2 = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp2.status_code != 200:
            return None
        gh_user = resp2.json()

        # 3. 获取 email
        emails_resp = await client.get(
            "https://api.github.com/user/emails",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        email = None
        if emails_resp.status_code == 200:
            emails = emails_resp.json()
            for e in emails:
                if e.get("primary") and e.get("verified"):
                    email = e.get("email")
                    break

        return OAuthUserInfo(
            provider="github",
            oauth_id=str(gh_user.get("id", "")),
            username=gh_user.get("login", ""),
            display_name=gh_user.get("name") or gh_user.get("login", ""),
            email=email,
            avatar_url=gh_user.get("avatar_url"),
        )


async def handle_wecom_callback(code: str, state: str) -> Optional[OAuthUserInfo]:
    """处理企业微信 OAuth 回调，返回用户信息"""
    provider = verify_oauth_state(state)
    if provider != "wecom":
        return None

    # 1. 用 code 换 access_token
    token_url = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
    params = {"corpid": WECOM_CORPID, "corpsecret": WECOM_SECRET}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(token_url, params=params)
        if resp.status_code != 200:
            return None
        token_data = resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            return None

        # 2. 用 access_token + code 换 userid
        user_url = "https://qyapi.weixin.qq.com/cgi-bin/user/getuserinfo"
        user_resp = await client.get(
            user_url,
            params={"access_token": access_token, "code": code},
        )
        if user_resp.status_code != 200:
            return None
        user_data = user_resp.json()
        userid = user_data.get("UserId")
        if not userid:
            return None

        # 3. 获取用户详情
        detail_url = "https://qyapi.weixin.qq.com/cgi-bin/user/get"
        detail_resp = await client.get(
            detail_url,
            params={"access_token": access_token, "userid": userid},
        )
        if detail_resp.status_code != 200:
            return None
        detail = detail_resp.json()

        return OAuthUserInfo(
            provider="wecom",
            oauth_id=userid,
            username=userid,
            display_name=detail.get("name", userid),
            email=detail.get("email"),
            avatar_url=None,
        )


# ============================================================
# 用户查找/创建
# ============================================================

def find_or_create_user(info: OAuthUserInfo) -> dict:
    """查找或创建用户，返回用户记录（PostgreSQL 语法）"""
    existing = query_one(
        "SELECT * FROM users WHERE oauth_provider = %s AND oauth_id = %s",
        [info.provider, info.oauth_id],
    )

    if existing:
        # 更新最后访问信息
        execute(
            "UPDATE users SET username = %s, display_name = %s, email = %s, "
            "avatar_url = %s, updated_at = CURRENT_TIMESTAMP, last_login_at = CURRENT_TIMESTAMP "
            "WHERE id = %s",
            [info.username, info.display_name, info.email, info.avatar_url, existing["id"]],
        )
        return {**existing, "username": info.username, "display_name": info.display_name}

    # 新用户：检查是否是第一个用户（作为admin）
    row = query_one("SELECT COUNT(*) as cnt FROM users")
    is_first_user = row and row["cnt"] == 0

    # 检查是否是配置的管理员 OAuth ID
    is_admin = is_first_user or (
        ADMIN_OAUTH_ID and
        f"{info.provider}:{info.oauth_id}" == ADMIN_OAUTH_ID
    )

    user_id = execute(
        """INSERT INTO users
           (oauth_provider, oauth_id, username, display_name, email, avatar_url, role)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        [
            info.provider,
            info.oauth_id,
            info.username,
            info.display_name,
            info.email,
            info.avatar_url,
            "admin" if is_admin else "user",
        ],
    )

    logger.info(f"Created new user: {info.username} (role={'admin' if is_admin else 'user'})")
    return {
        "id": user_id,
        "oauth_provider": info.provider,
        "oauth_id": info.oauth_id,
        "username": info.username,
        "display_name": info.display_name,
        "email": info.email,
        "avatar_url": info.avatar_url,
        "role": "admin" if is_admin else "user",
    }


def get_user_by_id(user_id: int) -> Optional[dict]:
    """根据ID获取用户"""
    return query_one("SELECT * FROM users WHERE id = %s", [user_id])


def get_all_users() -> list[dict]:
    """获取所有用户列表"""
    from .db import query
    return query("SELECT * FROM users ORDER BY created_at")