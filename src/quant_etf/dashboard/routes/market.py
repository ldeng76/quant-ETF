"""
市场状态与概览 API
"""
import asyncio
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from ..template_setup import templates
from ..db import query, execute, query_one
from ..models import ScheduleCreate
from ..services.scheduler import scheduler
from ..services.strategy_runner import list_available_strategies
from quant_etf.market_analyzer import get_market_state, MarketType

router = APIRouter(tags=["market"])


@router.get("/status", response_class=JSONResponse)
async def market_status():
    """市场环境判断 - 基于指数 + ETF 池数据分析"""
    state = get_market_state()
    return {
        "market_type": state.market_type.value,
        "time": state.time.isoformat(),
        "index_return": round(state.index_return * 100, 3),       # 转百分比
        "etf_pool_return": round(state.etf_pool_return * 100, 3),
        "volatility": round(state.volatility * 100, 3),
        "trend_strength": round(state.trend_strength * 100, 3),
        "ma_short_vs_long": {
            "index": "bullish" if state.index_ma_short > state.index_ma_long else "bearish",
            "etf_pool": "bullish" if state.etf_pool_ma_short > state.etf_pool_ma_long else "bearish",
        },
    }


@router.get("/overview", response_class=HTMLResponse)
async def overview_data(request: Request):
    """总览概览数据卡片"""
    accounts = query("SELECT COUNT(*) as cnt FROM accounts")
    alerts_today = query(
        "SELECT COUNT(*) as cnt FROM alerts_dashboard "
        "WHERE date(created_at) = date('now')"
    )
    schedules = query("SELECT COUNT(*) as cnt FROM schedules WHERE enabled = 1")
    return templates.TemplateResponse(
        request, "index.html",
        {
            "account_count": accounts[0]["cnt"] if accounts else 0,
            "alert_count": alerts_today[0]["cnt"] if alerts_today else 0,
            "schedule_count": schedules[0]["cnt"] if schedules else 0,
        }
    )


@router.get("/schedules", response_class=HTMLResponse)
async def list_schedules(request: Request):
    """列出调度配置（HTML 片段）"""
    schedules = query("SELECT * FROM schedules ORDER BY strategy")
    for s in schedules:
        s["running"] = scheduler.is_running(s["id"])
    return templates.TemplateResponse(
        request, "monitor/_schedule_table.html",
        {"schedules": schedules}
    )


@router.post("/schedules")
async def create_schedule(request: Request, data: ScheduleCreate):
    """创建调度"""
    sid = execute(
        "INSERT INTO schedules (strategy, interval) VALUES (?, ?)",
        [data.strategy, data.interval]
    )
    schedules = query("SELECT * FROM schedules ORDER BY strategy")
    for s in schedules:
        s["running"] = scheduler.is_running(s["id"])
    return templates.TemplateResponse(
        request, "monitor/_schedule_table.html",
        {"schedules": schedules}
    )


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(request: Request, schedule_id: int):
    """删除调度"""
    await scheduler.stop(schedule_id)
    execute("DELETE FROM schedules WHERE id = ?", [schedule_id])
    schedules = query("SELECT * FROM schedules ORDER BY strategy")
    for s in schedules:
        s["running"] = scheduler.is_running(s["id"])
    return templates.TemplateResponse(
        request, "monitor/_schedule_table.html",
        {"schedules": schedules}
    )


@router.post("/schedules/{schedule_id}/toggle")
async def toggle_schedule(request: Request, schedule_id: int):
    """启停调度"""
    s = query_one("SELECT * FROM schedules WHERE id = ?", [schedule_id])
    if not s:
        raise HTTPException(404, "Schedule not found")
    if scheduler.is_running(schedule_id):
        await scheduler.stop(schedule_id)
        execute("UPDATE schedules SET enabled = 0 WHERE id = ?", [schedule_id])
    else:
        execute("UPDATE schedules SET enabled = 1 WHERE id = ?", [schedule_id])
        asyncio.create_task(scheduler.start_loop(schedule_id, s["strategy"], s["interval"]))
    schedules = query("SELECT * FROM schedules ORDER BY strategy")
    for sc in schedules:
        sc["running"] = scheduler.is_running(sc["id"])
    return templates.TemplateResponse(
        request, "monitor/_schedule_table.html",
        {"schedules": schedules}
    )
