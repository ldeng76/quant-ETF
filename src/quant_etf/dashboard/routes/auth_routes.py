"""
OAuth 认证路由 + 微信小程序登录 + 本地密码登录
- /auth/login         - 登录页面
- /auth/github        - GitHub OAuth
- /auth/github/callback - GitHub 回调
- /auth/wecom         - 企业微信 OAuth
- /auth/wecom/callback  - 企业微信回调
- /auth/wechat/login  - 微信小程序 login (POST code)
- /auth/logout        - 登出
- /auth/me            - 当前用户信息
- /auth/status        - 认证状态
- POST /auth/login    - 本地密码登录
"""
from fastapi import APIRouter, Request, HTTPException, Depends, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from loguru import logger

from ..template_setup import templates
from ..deps import get_current_user, require_admin
from ..auth import (
    is_auth_enabled,
    create_jwt,
    create_github_oauth_url,
    create_wecom_oauth_url,
    handle_github_callback,
    handle_wecom_callback,
    find_or_create_user,
    get_user_by_id,
    verify_password,
    is_local_login_locked,
    record_local_failure,
    clear_local_failures,
    get_local_user_by_username,
    init_local_users_table,
    create_local_user,
)
from ..config import (
    GITHUB_CLIENT_ID, WECOM_CORPID, DASHBOARD_PORT,
    INIT_ADMIN_USER, INIT_ADMIN_PASS,
)
from ..wechat_mini import (
    code_to_session,
    find_or_create_wechat_user,
    create_wechat_jwt,
)
router = APIRouter(prefix="/auth", tags=["auth"])


# ============================================================
# 模块加载时初始化
# ============================================================

def _init_local_auth():
    """初始化本地用户表和管理员账号"""
    init_local_users_table()
    if INIT_ADMIN_USER and INIT_ADMIN_PASS:
        existing = get_local_user_by_username(INIT_ADMIN_USER)
        if not existing:
            create_local_user(INIT_ADMIN_USER, INIT_ADMIN_PASS, role="admin", display_name="管理员")
            logger.info(f"Created initial admin user: {INIT_ADMIN_USER}")


_init_local_auth()


# ============================================================
# 微信小程序登录模型
# ============================================================

class WechatLoginRequest(BaseModel):
    code: str  # wx.login() 获取的 code


# ============================================================
# 登录页面
# ============================================================

@router.get("/login")
async def login_page(request: Request):
    """登录页面"""
    if not is_auth_enabled():
        return RedirectResponse(url="/pages/overview")

    return templates.TemplateResponse(
        request, "auth/login.html",
        {
            "github_enabled": bool(GITHUB_CLIENT_ID),
            "wecom_enabled": bool(WECOM_CORPID),
            "local_login_enabled": bool(INIT_ADMIN_USER),
        }
    )


# ============================================================
# 本地密码登录（表单）
# ============================================================

@router.post("/login")
async def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    """用户名密码表单登录"""
    if not is_auth_enabled():
        raise HTTPException(403, "Authentication is disabled")

    # 1. 检查是否锁定
    if is_local_login_locked(username):
        return RedirectResponse(url="/auth/login?error=locked", status_code=302)

    # 2. 查找用户并验证密码
    user = get_local_user_by_username(username)
    if not user or not verify_password(password, user["password_hash"]):
        record_local_failure(username)
        return RedirectResponse(url="/auth/login?error=invalid", status_code=302)

    # 3. 签发 JWT
    clear_local_failures(username)
    jwt_token = create_jwt(user["id"], user["role"], provider="local")

    # 4. 写 Cookie + 重定向
    logger.info(f"User {username} logged in via local password")
    response = RedirectResponse(url="/pages/overview", status_code=302)
    response.set_cookie(
        key="session",
        value=jwt_token,
        httponly=True,
        samesite="strict",
        secure=False,  # 开发环境，生产环境设为 True
        max_age=7 * 86400,
    )
    return response


# ============================================================
# GitHub OAuth
# ============================================================

@router.get("/github")
async def github_oauth(request: Request):
    """重定向到 GitHub OAuth"""
    if not is_auth_enabled():
        raise HTTPException(403, "Authentication is disabled")
    if not GITHUB_CLIENT_ID:
        raise HTTPException(500, "GitHub OAuth not configured")

    url = create_github_oauth_url()
    return RedirectResponse(url=url)


@router.get("/github/callback")
async def github_callback(request: Request, code: str, state: str):
    """GitHub OAuth 回调"""
    info = await handle_github_callback(code, state)
    if not info:
        raise HTTPException(400, "OAuth callback failed")

    user = find_or_create_user(info)
    jwt_token = create_jwt(user["id"], user["role"], provider="github")

    response = RedirectResponse(url="/pages/overview", status_code=302)
    response.set_cookie(
        key="session",
        value=jwt_token,
        httponly=True,
        samesite="lax",
        secure=False,  # 开发环境，生产环境应设为 True
        max_age=7 * 86400,
    )
    logger.info(f"User {user['username']} logged in via GitHub")
    return response


# ============================================================
# 企业微信 OAuth
# ============================================================

@router.get("/wecom")
async def wecom_oauth(request: Request):
    """重定向到企业微信 OAuth"""
    if not is_auth_enabled():
        raise HTTPException(403, "Authentication is disabled")
    if not WECOM_CORPID:
        raise HTTPException(500, "WeCom OAuth not configured")

    redirect_uri = f"http://{request.client.host if request.client else 'localhost'}:{DASHBOARD_PORT}/auth/wecom/callback"
    url = create_wecom_oauth_url(redirect_uri)
    return RedirectResponse(url=url)


@router.get("/wecom/callback")
async def wecom_callback(request: Request, code: str, state: str):
    """企业微信 OAuth 回调"""
    info = await handle_wecom_callback(code, state)
    if not info:
        raise HTTPException(400, "OAuth callback failed")

    user = find_or_create_user(info)
    jwt_token = create_jwt(user["id"], user["role"], provider="wecom")

    response = RedirectResponse(url="/pages/overview", status_code=302)
    response.set_cookie(
        key="session",
        value=jwt_token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=7 * 86400,
    )
    logger.info(f"User {user['username']} logged in via WeCom")
    return response


# ============================================================
# 登出
# ============================================================

@router.post("/logout")
async def logout(request: Request):
    """登出"""
    response = RedirectResponse(url="/auth/login", status_code=302)
    response.delete_cookie(key="session")
    return response


# ============================================================
# 开发模式自动登录（仅开发环境使用）
# ============================================================

@router.get("/dev-login")
async def dev_login(request: Request):
    """
    开发模式自动登录（无需 OAuth）
    仅当 JWT_SECRET_KEY 已配置时生效，创建/获取 dev_user 并设 cookie
    生产环境应删除此端点
    """
    if not is_auth_enabled():
        return RedirectResponse(url="/pages/overview")

    from ..wechat_mini import find_or_create_wechat_user, create_wechat_jwt
    from ..db import query_one, execute

    # 创建或获取开发用户
    user = find_or_create_wechat_user(
        openid="dev_web_user",
        display_name="开发用户",
    )

    # 强制设为管理员（开发环境专用）
    if user.get("role") != "admin":
        execute(
            "UPDATE users SET role = 'admin' WHERE oauth_provider = 'wechat_mini' AND oauth_id = 'dev_web_user'",
            [],
        )
        user["role"] = "admin"

    # 签发 JWT
    jwt_token = create_wechat_jwt(user["id"], user["role"])

    # 设 cookie 并跳转
    response = RedirectResponse(url="/pages/overview", status_code=302)
    response.set_cookie(
        key="session",
        value=jwt_token,
        httponly=True,
        max_age=7 * 24 * 3600,  # 7 天
    )
    return response


# ============================================================
# 用户信息
# ============================================================

@router.get("/me")
async def get_me(request: Request, user: dict = Depends(get_current_user)):
    """获取当前用户信息（JSON）"""
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user.get("display_name", user["username"]),
        "email": user.get("email"),
        "avatar_url": user.get("avatar_url"),
        "role": user["role"],
    }


@router.get("/status")
async def auth_status(request: Request):
    """认证状态检查"""
    if not is_auth_enabled():
        return {"enabled": False, "logged_in": False}

    token = request.cookies.get("session")
    if not token:
        return {"enabled": True, "logged_in": False}

    payload = verify_wechat_jwt(token)  # 通用 JWT 验证
    if not payload:
        return {"enabled": True, "logged_in": False}

    user = wc_get_user_by_id(int(payload["sub"]))
    if not user:
        return {"enabled": True, "logged_in": False}

    return {
        "enabled": True,
        "logged_in": True,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "display_name": user.get("display_name", user["username"]),
            "avatar_url": user.get("avatar_url"),
            "role": user["role"],
        }
    }


# ============================================================
# 微信小程序 REST API（/api/ 开头，Bearer Token 验证）
# ============================================================

api_router = APIRouter(prefix="/api", tags=["api"])


# ============================================================
# 微信小程序登录
# ============================================================

@api_router.post("/wechat/login")
async def wechat_login(request: Request, body: WechatLoginRequest):
    """
    微信小程序登录接口
    微信小程序端调用 wx.login() 获取 code，
    传入此接口换取 JWT token。
    """
    if not is_auth_enabled():
        raise HTTPException(403, "Authentication is disabled")

    # 1. code 换 session（openid）
    session_info = await code_to_session(body.code)
    if not session_info:
        raise HTTPException(400, "Invalid WeChat code")

    # 2. 查找或创建用户
    user = find_or_create_wechat_user(
        openid=session_info.openid,
        display_name=f"用户{session_info.openid[:6]}",
        unionid=session_info.unionid or "",
    )

    # 3. 签发 JWT
    jwt_token = create_wechat_jwt(user["id"], user["role"])

    logger.info(f"WeChat user {user['username']} logged in")

    return JSONResponse({
        "token": jwt_token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "role": user["role"],
        }
    })


@api_router.get("/me")
async def api_me(user: dict = Depends(get_current_user)):
    """
    微信小程序调用此接口，header 携带 Authorization: Bearer <token>
    """
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user.get("display_name", user["username"]),
        "role": user["role"],
    }


@api_router.get("/portfolio/accounts")
async def api_list_accounts(user: dict = Depends(get_current_user)):
    """微信小程序：账户列表"""
    from ..db import query
    accounts = query(
        "SELECT * FROM accounts WHERE user_id = %s ORDER BY name",
        [user["id"]]
    )
    return {"accounts": accounts}


@api_router.get("/portfolio/accounts/{account_id}/holdings")
async def api_list_holdings(request: Request, account_id: int, user: dict = Depends(get_current_user)):
    """微信小程序：持仓列表"""
    from ..db import query_one, query

    # 验证账户属于当前用户
    account = query_one(
        "SELECT * FROM accounts WHERE id = %s AND user_id = %s",
        [account_id, user["id"]]
    )
    if not account:
        raise HTTPException(404, "Account not found")

    holdings = query(
        "SELECT * FROM holdings WHERE account_id = %s ORDER BY code",
        [account_id]
    )
    return {"account": account, "holdings": holdings}


@api_router.get("/alerts/rules")
async def api_list_rules(user: dict = Depends(get_current_user)):
    """微信小程序：告警规则列表"""
    from ..db import query
    rules = query(
        "SELECT * FROM alert_rules WHERE user_id = %s ORDER BY name",
        [user["id"]]
    )
    return {"rules": rules}


@api_router.get("/alerts/dashboard")
async def api_list_alerts(user: dict = Depends(get_current_user), limit: int = 50):
    """微信小程序：告警列表"""
    from ..db import query
    alerts = query(
        """SELECT * FROM alerts_dashboard
           WHERE user_id = %s
           ORDER BY
               CASE status WHEN 'active' THEN 0 WHEN 'acknowledged' THEN 1 ELSE 2 END,
               created_at DESC
           LIMIT %s""",
        [user["id"], limit]
    )
    return {"alerts": alerts}


@api_router.get("/market/status")
async def api_market_status(user: dict = Depends(get_current_user)):
    """微信小程序：市场状态"""
    from quant_etf.market_analyzer import get_market_state
    state = get_market_state()
    return {
        "market_type": state.market_type.value,
        "time": state.time.isoformat(),
        "index_return": round(state.index_return * 100, 3),
        "etf_pool_return": round(state.etf_pool_return * 100, 3),
        "volatility": round(state.volatility * 100, 3),
        "trend_strength": round(state.trend_strength * 100, 3),
    }


# ============================================================
# 用户管理 API
# ============================================================

class _UserProfileUpdate(BaseModel):
    display_name: str | None = None


class _UserExtendRequest(BaseModel):
    days: int


@api_router.get("/user/profile")
async def api_user_profile(user: dict = Depends(get_current_user)):
    """获取当前用户详情（含有效期）"""
    from ..db import query_one
    from datetime import datetime
    row = query_one(
        "SELECT * FROM users WHERE id = %s",
        [user["id"]]
    )
    if not row:
        raise HTTPException(404, "User not found")

    expires_at = row.get("expires_at")
    days_remaining = None
    if expires_at is not None:
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)
        days_remaining = max(0, (expires_at - datetime.now()).days)

    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row.get("display_name", ""),
        "email": row.get("email"),
        "avatar_url": row.get("avatar_url"),
        "role": row["role"],
        "oauth_provider": row["oauth_provider"],
        "expires_at": expires_at.isoformat() if expires_at else None,
        "days_remaining": days_remaining,
        "last_login_at": row.get("last_login_at").isoformat() if row.get("last_login_at") else None,
    }


@api_router.put("/user/profile")
async def api_update_profile(data: _UserProfileUpdate, user: dict = Depends(get_current_user)):
    """更新当前用户资料"""
    from ..db import execute
    if data.display_name is not None:
        execute(
            "UPDATE users SET display_name = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            [data.display_name, user["id"]]
        )
    return {"message": "Profile updated"}


@api_router.get("/admin/users")
async def api_list_users(user: dict = Depends(require_admin)):
    """管理员：获取所有用户列表"""
    from ..db import query
    from ..auth import get_all_users
    from datetime import datetime
    users = get_all_users()
    result = []
    for u in users:
        expires_at = u.get("expires_at")
        days_remaining = None
        if expires_at is not None:
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(expires_at)
            days_remaining = max(0, (expires_at - datetime.now()).days)
        result.append({
            **u,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "days_remaining": days_remaining,
        })
    return {"users": result}


@api_router.put("/admin/users/{user_id}/extend")
async def api_extend_user(user_id: int, data: _UserExtendRequest, user: dict = Depends(require_admin)):
    """管理员：延长用户有效期"""
    from ..db import execute, query_one
    execute(
        """UPDATE users SET expires_at = COALESCE(
               CASE WHEN expires_at IS NULL THEN CURRENT_TIMESTAMP ELSE expires_at END,
               CURRENT_TIMESTAMP
           ) + INTERVAL '%s days'
           WHERE id = %s""",
        [data.days, user_id]
    )
    updated = query_one("SELECT * FROM users WHERE id = %s", [user_id])
    return {
        "id": updated["id"],
        "expires_at": updated.get("expires_at").isoformat() if updated.get("expires_at") else None,
        "message": f"Extended by {data.days} days",
    }


# 引入 wechat_mini 中的验证函数（auth_status 使用）
from ..wechat_mini import verify_wechat_jwt, get_user_by_id as wc_get_user_by_id