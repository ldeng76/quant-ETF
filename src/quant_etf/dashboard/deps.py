"""
FastAPI 认证依赖注入
支持 Cookie 和 Bearer Token 两种认证方式
"""
from datetime import datetime
from fastapi import Request, HTTPException, Depends
from loguru import logger

from .auth import is_auth_enabled, verify_jwt, get_user_by_id


# 缓存用户信息，避免每个请求多次查询DB
_async_user_cache: dict[str, dict] = {}


async def get_current_user(request: Request) -> dict:
    """
    从 Bearer Token 或 cookie 解析 JWT，返回用户信息。
    - API 请求（小程序）：优先读取 Authorization: Bearer <token>
    - 页面请求（HTMX）：回退到 cookie "session"
    - 未认证则 401 JSON 或 302 重定向到登录页
    """
    if not is_auth_enabled():
        # 认证禁用时，返回一个模拟的 admin 用户（兼容开发模式）
        return {
            "id": 1,
            "username": "dev",
            "display_name": "开发者",
            "role": "admin",
        }

    # 1. 优先尝试 Bearer Token
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        # 2. 回退到 Cookie
        token = request.cookies.get("session")

    if not token:
        raise _unauthenticated(request, "no auth token")

    payload = verify_jwt(token)
    if not payload:
        raise _unauthenticated(request, "invalid/expired token")

    user_id = int(payload["sub"])

    # 缓存：请求级别
    cache_key = f"{id(request)}:{user_id}"
    if cache_key in _async_user_cache:
        return _async_user_cache[cache_key]

    user = get_user_by_id(user_id)
    if not user:
        raise _unauthenticated(request, "user not found")

    # 检查账户有效期
    expires_at = user.get("expires_at")
    if expires_at is not None:
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)
        if expires_at < datetime.now():
            raise HTTPException(
                status_code=403,
                detail="Account expired. Please contact admin to renew."
            )

    _async_user_cache[cache_key] = user
    return user


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """检查用户是否是 admin"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


async def require_admin_async(request: Request) -> dict:
    """异步版本的 admin 检查"""
    user = await get_current_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def _unauthenticated(request: Request, reason: str):
    """
    未认证响应：认证禁用时返回 mock 用户，否则重定向到登录页
    """
    if not is_auth_enabled():
        # 认证禁用时，返回一个模拟的 admin 用户（兼容开发模式）
        return {
            "id": 1,
            "username": "admin",
            "role": "admin",
            "provider": "mock",
            "display_name": "Admin (dev mode)",
        }
    if request.headers.get("HX-Request"):
        raise HTTPException(status_code=401, detail="Unauthorized")
    # 使用绝对路径，避免在子路径下变成 //auth/login
    raise HTTPException(status_code=302, detail="/auth/login")


def clear_user_cache(request: Request) -> None:
    """清除请求级别的用户缓存"""
    global _async_user_cache
    pass