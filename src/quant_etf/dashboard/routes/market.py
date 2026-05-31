"""
市场状态与概览 API（多租户版本）
schedules 表为全局配置，CRUD 需要 admin 权限
market/status / overview 为读操作，普通用户可访问
"""
import asyncio
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse

from ..template_setup import templates
from ..db import query, execute, query_one
from ..models import ScheduleCreate
from ..services.scheduler import scheduler
from ..services.strategy_runner import list_available_strategies
from ..deps import get_current_user, require_admin
from quant_etf.market_analyzer import get_market_state_cached, MarketType

_STRATEGY_TITLE_MAP = {
    "etf": "ETF 组合",
    "short": "短线股票",
    "mid": "中期反弹",
}


def _enrich_schedules_with_title(schedules):
    """为每个调度记录添加 strategy_title 字段"""
    for s in schedules:
        s["running"] = scheduler.is_running(s["id"])
        s["strategy_title"] = _STRATEGY_TITLE_MAP.get(s["strategy"], s["strategy"])
    return schedules


router = APIRouter(tags=["market"])


@router.get("/status", response_class=JSONResponse)
async def market_status(user: dict = Depends(get_current_user)):
    """市场环境判断（使用缓存，TTL 60秒）"""
    state = get_market_state_cached()
    return {
        "market_type": state.market_type.value,
        "time": state.time.isoformat(),
        "index_return": round(state.index_return * 100, 3),
        "etf_pool_return": round(state.etf_pool_return * 100, 3),
        "volatility": round(state.volatility * 100, 3),
        "trend_strength": round(state.trend_strength * 100, 3),
        "ma_short_vs_long": {
            "index": "bullish" if state.index_ma_short > state.index_ma_long else "bearish",
            "etf_pool": "bullish" if state.etf_pool_ma_short > state.etf_pool_ma_long else "bearish",
        },
    }


@router.get("/overview", response_class=HTMLResponse)
async def overview_data(request: Request, user: dict = Depends(get_current_user)):
    """总览概览数据卡片"""
    accounts = query("SELECT COUNT(*) as cnt FROM accounts WHERE user_id = %s", [user["id"]])
    alerts_today = query(
        "SELECT COUNT(*) as cnt FROM alerts_dashboard "
        "WHERE (user_id = %s OR user_id IS NULL) AND date(created_at) = CURRENT_DATE",
        [user["id"]]
    )
    schedules = query("SELECT COUNT(*) as cnt FROM schedules WHERE enabled = TRUE")
    return templates.TemplateResponse(
        request, "index.html",
        {
            "account_count": accounts[0]["cnt"] if accounts else 0,
            "alert_count": alerts_today[0]["cnt"] if alerts_today else 0,
            "schedule_count": schedules[0]["cnt"] if schedules else 0,
        }
    )


@router.get("/schedules", response_class=HTMLResponse)
async def list_schedules(request: Request, user: dict = Depends(get_current_user)):
    """列出调度配置（HTML 片段，admin 可管理）"""
    schedules = query("SELECT * FROM schedules ORDER BY strategy")
    _enrich_schedules_with_title(schedules)
    return templates.TemplateResponse(
        request, "monitor/_schedule_table.html",
        {"schedules": schedules}
    )


@router.post("/schedules")
async def create_schedule(request: Request, data: ScheduleCreate, user: dict = Depends(require_admin)):
    """创建调度（仅管理员）"""
    sid = execute(
        "INSERT INTO schedules (strategy, interval, bar_interval) VALUES (%s, %s, %s)",
        [data.strategy, data.interval, data.bar_interval]
    )
    schedules = query("SELECT * FROM schedules ORDER BY strategy")
    _enrich_schedules_with_title(schedules)
    return templates.TemplateResponse(
        request, "monitor/_schedule_table.html",
        {"schedules": schedules}
    )


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(request: Request, schedule_id: int, user: dict = Depends(require_admin)):
    """删除调度（仅管理员）"""
    await scheduler.stop(schedule_id)
    execute("DELETE FROM schedules WHERE id = %s", [schedule_id])
    schedules = query("SELECT * FROM schedules ORDER BY strategy")
    _enrich_schedules_with_title(schedules)
    return templates.TemplateResponse(
        request, "monitor/_schedule_table.html",
        {"schedules": schedules}
    )


@router.post("/schedules/{schedule_id}/toggle")
async def toggle_schedule(request: Request, schedule_id: int, user: dict = Depends(require_admin)):
    """启停调度（仅管理员）"""
    s = query_one("SELECT * FROM schedules WHERE id = %s", [schedule_id])
    if not s:
        raise HTTPException(404, "Schedule not found")
    if s["enabled"]:
        # 已启用 → 停止
        await scheduler.stop(schedule_id)
        execute("UPDATE schedules SET enabled = FALSE WHERE id = %s", [schedule_id])
    else:
        # 已停用 → 启动
        execute("UPDATE schedules SET enabled = TRUE WHERE id = %s", [schedule_id])
        bar_interval = s.get("bar_interval", "1d")
        asyncio.create_task(scheduler.start_loop(schedule_id, s["strategy"], s["interval"], bar_interval))
    schedules = query("SELECT * FROM schedules ORDER BY strategy")
    _enrich_schedules_with_title(schedules)
    return templates.TemplateResponse(
        request, "monitor/_schedule_table.html",
        {"schedules": schedules}
    )