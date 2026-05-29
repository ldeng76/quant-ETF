"""
页面渲染路由（多租户版本）
- 直接浏览器访问: 返回完整 base.html 页面
- HTMX 请求 (HX-Request 头): 返回纯内容片段
所有页面路由需要认证
"""
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse
from datetime import datetime

from ..template_setup import templates
from ..db import query
from ..deps import get_current_user

router = APIRouter(tags=["pages"])


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request", "").lower() == "true"


def _overview_stats(user_id: int) -> dict:
    """获取总览页统计数据"""
    accounts = query("SELECT COUNT(*) as cnt FROM accounts WHERE user_id = %s", [user_id])
    alerts_today = query(
        "SELECT COUNT(*) as cnt FROM alerts_dashboard "
        "WHERE (user_id = %s OR user_id IS NULL) AND date(created_at) = CURRENT_DATE",
        [user_id]
    )
    schedules = query("SELECT COUNT(*) as cnt FROM schedules WHERE enabled = TRUE")
    return {
        "account_count": accounts[0]["cnt"] if accounts else 0,
        "alert_count": alerts_today[0]["cnt"] if alerts_today else 0,
        "schedule_count": schedules[0]["cnt"] if schedules else 0,
    }


@router.get("/pages/overview", response_class=HTMLResponse)
async def overview_page(request: Request, user: dict = Depends(get_current_user)):
    ctx = {"now": _now(), **_overview_stats(user["id"])}
    tpl = "index.html" if not _is_htmx(request) else "overview/_content.html"
    return templates.TemplateResponse(request, tpl, ctx)


@router.get("/pages/portfolio", response_class=HTMLResponse)
async def portfolio_page(request: Request, user: dict = Depends(get_current_user)):
    tpl = "portfolio/index.html" if not _is_htmx(request) else "portfolio/_content.html"
    return templates.TemplateResponse(request, tpl, {"now": _now()})


@router.get("/pages/strategy", response_class=HTMLResponse)
async def strategy_page(request: Request, user: dict = Depends(get_current_user)):
    tpl = "strategy/index.html" if not _is_htmx(request) else "strategy/_content.html"
    return templates.TemplateResponse(request, tpl, {"now": _now()})


@router.get("/pages/monitor", response_class=HTMLResponse)
async def monitor_page(request: Request, user: dict = Depends(get_current_user)):
    tpl = "monitor/index.html" if not _is_htmx(request) else "monitor/_content.html"
    return templates.TemplateResponse(request, tpl, {"now": _now()})


@router.get("/pages/alerts", response_class=HTMLResponse)
async def alerts_page(request: Request, user: dict = Depends(get_current_user)):
    tpl = "alerts/index.html" if not _is_htmx(request) else "alerts/_content.html"
    return templates.TemplateResponse(request, tpl, {"now": _now()})


@router.get("/pages/settings", response_class=HTMLResponse)
async def settings_page(request: Request, user: dict = Depends(get_current_user)):
    tpl = "settings/index.html" if not _is_htmx(request) else "settings/_content.html"
    return templates.TemplateResponse(request, tpl, {"now": _now()})