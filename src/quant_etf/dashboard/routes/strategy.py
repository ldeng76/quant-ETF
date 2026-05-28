"""
策略执行API（多租户版本）
策略执行需要 admin 权限
"""
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse

from ..template_setup import templates
from ..models import StrategyRunRequest
from ..services.strategy_runner import (
    run_strategy,
    get_task_status,
    list_available_strategies,
    get_history_summary,
    get_sell_signals,
    get_today_results,
    get_drilldown_data,
)
from ..deps import get_current_user, require_admin

router = APIRouter(tags=["strategy"])


@router.get("/strategies", response_class=JSONResponse)
async def list_strategies(user: dict = Depends(get_current_user)):
    """列出可用策略"""
    return list_available_strategies()


@router.post("/run")
async def start_strategy(data: StrategyRunRequest, user: dict = Depends(require_admin)):
    """执行选定的策略（仅管理员）"""
    run_ids = []
    for strategy_name in data.strategies:
        run_id = await run_strategy(strategy_name, bar_interval=data.bar_interval)
        run_ids.append(run_id)
    return {"run_ids": run_ids, "message": f"Started {len(run_ids)} strategy run(s)"}


@router.get("/status/{run_id}", response_class=JSONResponse)
async def check_status(run_id: str, user: dict = Depends(get_current_user)):
    """查询执行进度"""
    status = get_task_status(run_id)
    if not status:
        raise HTTPException(404, f"Unknown run_id: {run_id}")
    return status


@router.get("/results/{run_id}", response_class=HTMLResponse)
async def get_results(request: Request, run_id: str, user: dict = Depends(get_current_user)):
    """渲染结果表格+图表"""
    status = get_task_status(run_id)
    if not status:
        raise HTTPException(404, f"Unknown run_id: {run_id}")
    return templates.TemplateResponse(
        request, "strategy/_results.html",
        {"status": status, "run_id": run_id}
    )


@router.get("/sell-signals", response_class=HTMLResponse)
async def get_sell_signals_fragment(
    request: Request,
    strategy: str = "etf",
    bar_interval: str = "1d",
    user: dict = Depends(get_current_user),
):
    """渲染卖出信号区块"""
    signals = get_sell_signals(strategy_name=strategy, bar_interval=bar_interval)
    return templates.TemplateResponse(
        request, "strategy/_sell_signals.html",
        {"signals": signals}
    )


@router.get("/history-summary", response_class=HTMLResponse)
async def get_history_summary_endpoint(
    request: Request,
    strategy: str = "etf",
    days: int = 30,
    bar_interval: str = "1d",
    backfill: bool = True,
    user: dict = Depends(get_current_user),
):
    """渲染历史标的汇总表格"""
    summary = get_history_summary(strategy_name=strategy, days=days, auto_backfill=backfill, bar_interval=bar_interval)
    return templates.TemplateResponse(
        request, "strategy/_history_summary.html",
        {"summary": summary, "days": days}
    )


# ============================================================
# 小程序用 JSON 接口
# ============================================================

@router.get("/list", response_class=JSONResponse)
async def list_strategies_json(user: dict = Depends(get_current_user)):
    """列出可用策略（JSON）"""
    return list_available_strategies()


@router.get("/today/{name}", response_class=JSONResponse)
async def today_results(name: str, bar_interval: str = "1d", user: dict = Depends(get_current_user)):
    """当日策略结果 JSON"""
    return get_today_results(name, bar_interval=bar_interval)


@router.get("/history-summary-data", response_class=JSONResponse)
async def history_summary_data(
    strategy: str = "etf",
    days: int = 30,
    bar_interval: str = "1d",
    user: dict = Depends(get_current_user),
):
    """历史汇总 JSON"""
    summary = get_history_summary(strategy_name=strategy, days=days, auto_backfill=True, bar_interval=bar_interval)
    return {"strategy": strategy, "days": days, "count": len(summary), "items": summary}


@router.get("/sell-signals-data", response_class=JSONResponse)
async def sell_signals_data(
    strategy: str = "etf",
    bar_interval: str = "1d",
    user: dict = Depends(get_current_user),
):
    """卖出信号 JSON"""
    signals = get_sell_signals(strategy_name=strategy, bar_interval=bar_interval)
    return {"strategy": strategy, "count": len(signals), "signals": signals}

@router.get("/drilldown/{run_id}", response_class=JSONResponse)
async def drilldown_data(
    run_id: str,
    code: str,
    field: str,
    user: dict = Depends(get_current_user),
):
    """获取某标的某周期字段的累计涨幅序列"""
    try:
        data = get_drilldown_data(run_id, code, field)
        return data
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
