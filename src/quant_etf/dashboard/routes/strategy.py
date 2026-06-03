"""
策略执行API（多租户版本）
策略执行需要 admin 权限
"""
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse

from ..template_setup import templates
from ..models import StrategyRunRequest
from ..db import query, query_one
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

@router.get("/result-timestamps", response_class=JSONResponse)
async def get_result_timestamps(
    bar_interval: str,
    strategies: str = "",
    user: dict = Depends(get_current_user),
):
    """查询各策略最近一次执行的时间戳（轻量接口，用于轮询检测更新）"""
    if strategies:
        strategy_list = [s.strip() for s in strategies.split(",") if s.strip()]
    else:
        strategy_list = [s["name"] for s in list_available_strategies()]

    if not strategy_list:
        return {}

    runs = query("""
        SELECT DISTINCT ON (strategy) strategy, finished_at, run_id, status
        FROM strategy_runs
        WHERE bar_interval = %s
          AND status IN ('complete', 'error')
          AND strategy = ANY(%s)
        ORDER BY strategy, finished_at DESC
    """, [bar_interval, strategy_list])

    result = {}
    for run in runs:
        fa = run.get("finished_at")
        result[run["strategy"]] = {
            "finished_at": fa.isoformat() if fa else None,
            "run_id": run["run_id"],
            "status": run["status"],
        }
    return result


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


@router.get("/last-result", response_class=JSONResponse)
async def get_last_result(
    bar_interval: str,
    strategies: str = "",
    user: dict = Depends(get_current_user),
):
    """获取指定K线周期下各策略的最后一次执行结果"""
    # 1. 查询最新数据采集时间
    row = query_one("SELECT MAX(time) AS t FROM minute_bars")
    latest_collect_time = row["t"].isoformat() if row and row["t"] else None

    # 2. 确定要查询的策略列表
    if strategies:
        strategy_list = [s.strip() for s in strategies.split(",") if s.strip()]
    else:
        strategy_list = [s["name"] for s in list_available_strategies()]

    if not strategy_list:
        return {"latest_collect_time": latest_collect_time, "results": []}

    # 3. 查每个策略最近一次成功执行的 run
    runs = query("""
        SELECT DISTINCT ON (strategy) *
        FROM strategy_runs
        WHERE bar_interval = %s
          AND status = 'complete'
          AND strategy = ANY(%s)
        ORDER BY strategy, finished_at DESC
    """, [bar_interval, strategy_list])

    # 策略名 → title 映射
    title_map = {s["name"]: s.get("title", s["name"]) for s in list_available_strategies()}

    results = []
    for run in runs:
        run_id = run["run_id"]
        # 4. 查结果明细
        items = query("""
            SELECT code, name, p60, p20, p10, p5, target_weight, interval_, date_
            FROM strategy_run_results
            WHERE run_id = %s
            ORDER BY p60 DESC
        """, [run_id])

        finished_at = run.get("finished_at")
        finished_at_str = finished_at.isoformat() if finished_at else None

        # 5. 判断时效
        is_stale = False
        if latest_collect_time and finished_at_str:
            is_stale = finished_at_str < latest_collect_time

        results.append({
            "strategy": run["strategy"],
            "title": title_map.get(run["strategy"], run["strategy"]),
            "run_id": run_id,
            "finished_at": finished_at_str,
            "is_stale": is_stale,
            "result_count": run.get("result_count", len(items)),
            "market_regime": run.get("market_regime"),
            "items": items,
        })

    return {"latest_collect_time": latest_collect_time, "results": results}


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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Drilldown error: {e}")
