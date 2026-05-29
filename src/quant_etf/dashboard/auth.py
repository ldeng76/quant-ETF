"""
OAuth2 认证客户端 + JWT 会话管理
支持 GitHub、企业微信 OAuth、微信小程序

PostgreSQL 语法：%s, %s... 占位符
"""
import json
import secrets
import time
from typing import Optional
from dataclasses import dataclass

import httpx
import jwt
from loguru import logger

from .config import (
    JWT_SECRET_KEY, JWT_ALGORITHM, JWT_EXPIRE_DAYS,
    GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, GITHUB_REDIRECT_URI,
    WECOM_CORPID, WECOM_AGENT_ID, WECOM_SECRET,
    ADMIN_OAUTH_ID,
)
from .db import execute, query_one


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
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def decode_jwt_unsafe(token: str) -> Optional[dict]:
    """不解签名，仅解析 payload（用于日志/调试）"""
    try:
        parts = token.split(".")
        if len(parts) == 3:
            import base64
            import json as _json
            payload = parts[1] + "=="
            return _json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        pass
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
    return state


def verify_oauth_state(state: str) -> Optional[str]:
    """验证 state，返回 provider 或 None（已过期则删除）"""
    if state not in _state_store:
        return None
    provider, created = _state_store.pop(state)
    # 10分钟过期
    if time.time() - created > 600:
        return None
    return provider


# ============================================================
# OAuth URL 生成
# ============================================================

def create_github_oauth_url() -> str:
    """生成 GitHub OAuth 授权 URL"""
    state = create_oauth_state("github")
    params = {
        "client_id": GITHUB_CLIENT_ID,
        "redirect_uri": GITHUB_REDIRECT_URI,
        "scope": "read:user user:email",
        "state": state,
    }
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"https://github.com/login/oauth/authorize?{qs}"


def create_wecom_oauth_url(redirect_uri: str) -> str:
    """生成企业微信 OAuth 授权 URL"""
    state = create_oauth_state("wecom")
    params = {
        "appid": WECOM_CORPID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "snsapi_base",
        "state": state,
    }
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"https://open.work.weixin.qq.com/wwopen/sso/qrConnect?{qs}"


# ============================================================
# OAuth 回调处理
# ============================================================

async def handle_github_callback(code: str, state: str) -> Optional[OAuthUserInfo]:
    """处理 GitHub OAuth 回调，返回用户信息"""
    provider = verify_oauth_state(state)
    if provider != "github":
        logger.warning(f"GitHub callback: invalid state")
        return None

    # 1. 交换 access_token
    async with httpx.AsyncClient() as client:
        # 获取 access_token
        token_resp = await client.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": GITHUB_REDIRECT_URI,
            },
            headers={"Accept": "application/json"},
            timeout=10,
        )
        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            logger.warning(f"GitHub token exchange failed: {token_data}")
            return None

        # 2. 获取用户信息
        user_resp = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github.v3+json",
            },
            timeout=10,
        )
        if user_resp.status_code != 200:
            logger.warning(f"GitHub user info failed: {user_resp.status_code}")
            return None
        user_data = user_resp.json()

        # 3. 获取邮箱（如果未公开）
        emails_resp = await client.get(
            "https://api.github.com/user/emails",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        email = None
        if emails_resp.status_code == 200:
            emails = emails_resp.json()
            email = next((e["email"] for e in emails if e.get("primary")), None)

        return OAuthUserInfo(
            provider="github",
            oauth_id=str(user_data["id"]),
            username=user_data["login"],
            display_name=user_data.get("name") or user_data["login"],
            email=email,
            avatar_url=user_data.get("avatar_url"),
        )


async def handle_wecom_callback(code: str, state: str) -> Optional[OAuthUserInfo]:
    """处理企业微信 OAuth 回调，返回用户信息"""
    provider = verify_oauth_state(state)
    if provider != "wecom":
        logger.warning(f"WeCom callback: invalid state")
        return None

    async with httpx.AsyncClient() as client:
        # 1. 获取 access_token
        token_resp = await client.get(
            "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
            params={"corpid": WECOM_CORPID, "corpsecret": WECOM_SECRET},
            timeout=10,
        )
        token_data = token_resp.json()
        if token_data.get("errcode", 0) != 0:
            logger.warning(f"WeCom token failed: {token_data}")
            return None
        access_token = token_data["access_token"]

        # 2. 通过 code 获取用户身份
        user_resp = await client.get(
            "https://qyapi.weixin.qq.com/cgi-bin/auth/getuserinfo",
            params={"access_token": access_token, "code": code},
            timeout=10,
        )
        user_data = user_resp.json()
        if user_data.get("errcode", 0) != 0:
            logger.warning(f"WeCom userinfo failed: {user_data}")
            return None

        userid = user_data["UserId"]

        # 3. 获取用户详情（姓名、头像）
        detail_resp = await client.get(
            "https://qyapi.weixin.qq.com/cgi-bin/user/get",
            params={"access_token": access_token, "userid": userid},
            timeout=10,
        )
        detail_data = detail_resp.json()
        name = detail_data.get("name", userid)

        return OAuthUserInfo(
            provider="wecom",
            oauth_id=userid,
            username=userid,
            display_name=name,
            email=detail_data.get("email"),
            avatar_url=None,  # 企业微信不支持获取头像URL
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