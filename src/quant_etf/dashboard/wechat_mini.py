"""
微信小程序客户端支持
通过 wx.login() 获取的 code 换取 openid/session_key
用户无微信开放平台账号，无需绑定 OAuth，直接用 code 登录

开发模式：设置环境变量 MINI_DEV=1 或使用非生产 AppID 时自动启用 Mock
"""
import httpx
import jwt
import time
import secrets
import os
from typing import Optional
from dataclasses import dataclass
from loguru import logger

from .config import (
    WECHAT_MINI_APPID, WECHAT_MINI_SECRET, WECHAT_CODE2SESSION_URL,
    JWT_SECRET_KEY, JWT_ALGORITHM, JWT_EXPIRE_DAYS,
    ADMIN_OAUTH_ID,
)
from .db import execute, query_one


def is_dev_mode() -> bool:
    """检查是否处于开发模式"""
    return os.environ.get("MINI_DEV", "").lower() in ("1", "true", "yes")


@dataclass
class WechatUserInfo:
    """微信小程序用户信息（匿名用户，无手机号）"""
    openid: str           # 微信用户唯一标识
    session_key: str      # 会话密钥（用于解密数据）
    unionid: Optional[str]  # 同一用户在多个应用下的唯一标识（需绑定开放平台）


# ============================================================
# code 换 session
# ============================================================

async def code_to_session(code: str) -> Optional[WechatUserInfo]:
    """通过 wx.login() 的 code 换取 session

    开发模式（MINI_DEV=1 或未配置 WECHAT_MINI_APPID）：直接返回模拟 openid
    """
    # 开发模式：直接返回模拟数据
    if is_dev_mode() or not WECHAT_MINI_APPID or not WECHAT_MINI_SECRET:
        logger.info(f"Dev mode: code2session mock for code={code}")
        return WechatUserInfo(
            openid=f"dev_openid_{code}",
            session_key="dev_session_key",
            unionid=None,
        )

    # 生产模式：真实调用微信 API
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                WECHAT_CODE2SESSION_URL,
                params={
                    "appid": WECHAT_MINI_APPID,
                    "secret": WECHAT_MINI_SECRET,
                    "js_code": code,
                    "grant_type": "authorization_code",
                },
                timeout=10,
            )
            data = resp.json()
            errcode = data.get("errcode", 0)
            if errcode != 0:
                logger.warning(f"WeChat code2session failed: {data}")
                return None

            return WechatUserInfo(
                openid=data["openid"],
                session_key=data.get("session_key", ""),
                unionid=data.get("unionid"),
            )
        except Exception as e:
            logger.error(f"WeChat code2session exception: {e}")
            return None


# ============================================================
# 用户查找/创建（微信小程序用户）
# ============================================================

def find_or_create_wechat_user(openid: str, display_name: str = "", unionid: str = "") -> dict:
    """
    微信小程序用户：查找或创建
    provider='wechat_mini', oauth_id=openid
    """
    existing = query_one(
        "SELECT * FROM users WHERE oauth_provider = 'wechat_mini' AND oauth_id = %s",
        [openid],
    )

    if existing:
        # 更新访问信息
        execute(
            "UPDATE users SET display_name = %s, updated_at = CURRENT_TIMESTAMP, last_login_at = CURRENT_TIMESTAMP WHERE id = %s",
            [display_name, existing["id"]],
        )
        return {**existing, "display_name": display_name}

    # 检查是否是第一个用户（作为 admin）
    row = query_one("SELECT COUNT(*) as cnt FROM users")
    is_first_user = row and row["cnt"] == 0

    # 检查管理员配置
    is_admin = is_first_user or (
        ADMIN_OAUTH_ID and f"wechat_mini:{openid}" == ADMIN_OAUTH_ID
    )

    user_id = execute(
        """INSERT INTO users
           (oauth_provider, oauth_id, username, display_name, email, avatar_url, role)
           VALUES ('wechat_mini', %s, %s, %s, NULL, NULL, %s)""",
        [openid, openid[:16], display_name or f"用户{openid[:6]}", "admin" if is_admin else "user"],
    )

    logger.info(f"WeChat user created: {openid[:16]}... (role={'admin' if is_admin else 'user'})")
    return {
        "id": user_id,
        "oauth_provider": "wechat_mini",
        "oauth_id": openid,
        "username": openid[:16],
        "display_name": display_name or f"用户{openid[:6]}",
        "email": None,
        "avatar_url": None,
        "role": "admin" if is_admin else "user",
    }


# ============================================================
# JWT（复用 auth.py 的逻辑）
# ============================================================

def create_wechat_jwt(user_id: int, role: str = "user") -> str:
    """签发 JWT（微信小程序用户）"""
    if not JWT_SECRET_KEY:
        # 开发模式：返回伪token
        return secrets.token_urlsafe(32)

    payload = {
        "sub": str(user_id),
        "role": role,
        "iat": int(time.time()),
        "exp": int(time.time()) + JWT_EXPIRE_DAYS * 86400,
        "provider": "wechat_mini",
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def verify_wechat_jwt(token: str) -> Optional[dict]:
    """验证 JWT"""
    if not JWT_SECRET_KEY:
        return None
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


def get_user_by_id(user_id: int) -> Optional[dict]:
    """根据ID获取用户"""
    return query_one("SELECT * FROM users WHERE id = %s", [user_id])