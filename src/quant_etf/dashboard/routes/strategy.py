"""
策略执行API
"""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from ..template_setup import templates
from ..models import StrategyRunRequest
from ..services.strategy_runner import (
    run_strategy,
    get_task_status,
    list_available_strategies,
    get_history_summary,
    get_sell_signals,
)

router = APIRouter(tags=["strategy"])


@router.get("/strategies", response_class=JSONResponse)
async def list_strategies():
    """列出可用策略"""
    return list_available_strategies()


@router.post("/run")
async def start_strategy(data: StrategyRunRequest):
    """执行选定的策略"""
    run_ids = []
    for strategy_name in data.strategies:
        run_id = await run_strategy(strategy_name)
        run_ids.append(run_id)
    return {"run_ids": run_ids, "message": f"Started {len(run_ids)} strategy run(s)"}


@router.get("/status/{run_id}", response_class=JSONResponse)
async def check_status(run_id: str):
    """查询执行进度"""
    status = get_task_status(run_id)
    if not status:
        raise HTTPException(404, f"Unknown run_id: {run_id}")
    return status


@router.get("/results/{run_id}", response_class=HTMLResponse)
async def get_results(request: Request, run_id: str):
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
):
    """渲染卖出信号区块"""
    signals = get_sell_signals(strategy_name=strategy)
    return templates.TemplateResponse(
        request, "strategy/_sell_signals.html",
        {"signals": signals}
    )


@router.get("/history-summary", response_class=HTMLResponse)
async def get_history_summary_endpoint(
    request: Request,
    strategy: str = "etf",
    days: int = 30,
    backfill: bool = True,
):
    """渲染历史标的汇总表格"""
    summary = get_history_summary(strategy_name=strategy, days=days, auto_backfill=backfill)
    return templates.TemplateResponse(
        request, "strategy/_history_summary.html",
        {"summary": summary, "days": days}
    )
