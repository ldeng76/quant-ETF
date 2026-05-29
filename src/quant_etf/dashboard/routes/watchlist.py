"""
自选关注列表 API
"""
from fastapi import APIRouter, HTTPException, Depends

from ..db import query, query_one, execute
from ..models import WatchlistAdd
from ..services.strategy_runner import get_today_results, get_history_summary
from ..deps import get_current_user

router = APIRouter(tags=["watchlist"])


@router.get("/")
async def list_watchlist(
    strategy: str = "etf",
    user: dict = Depends(get_current_user),
):
    """自选列表 + 与最新策略结果交叉对比"""
    items = query(
        "SELECT * FROM watchlist WHERE user_id = %s ORDER BY created_at DESC",
        [user["id"]]
    )

    # 获取当日策略结果，构建 code 集合
    today_data = get_today_results(strategy)
    today_codes = {r["code"] for r in today_data.get("records", [])}

    # 获取历史汇总
    history = get_history_summary(strategy_name=strategy, days=30, auto_backfill=False)
    history_map = {h["code"]: h for h in history}

    for item in items:
        code = item["code"]
        if code in today_codes:
            item["status"] = "in"
            hist = history_map.get(code, {})
            item["last_on_date"] = hist.get("last_on_date")
            item["on_days"] = hist.get("on_days", 0)
        elif code in history_map:
            item["status"] = "out"
            item["last_on_date"] = history_map[code].get("last_on_date")
            item["on_days"] = history_map[code].get("on_days", 0)
        else:
            item["status"] = "never"
            item["last_on_date"] = None
            item["on_days"] = 0

    return {"strategy": strategy, "count": len(items), "items": items}


@router.post("/")
async def add_watchlist(
    data: WatchlistAdd,
    user: dict = Depends(get_current_user),
):
    """添加到自选列表"""
    try:
        row = query_one(
            "INSERT INTO watchlist (user_id, code, name, notes) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (user_id, code) DO NOTHING RETURNING id",
            [user["id"], data.code, data.name, data.notes]
        )
        if row:
            return {"id": row["id"], "message": "Added"}
        return {"id": None, "message": "Already in watchlist"}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.delete("/{item_id}")
async def delete_watchlist(
    item_id: int,
    user: dict = Depends(get_current_user),
):
    """从自选列表移除"""
    existing = query_one(
        "SELECT id FROM watchlist WHERE id = %s AND user_id = %s",
        [item_id, user["id"]]
    )
    if not existing:
        raise HTTPException(404, "Watchlist item not found")

    execute("DELETE FROM watchlist WHERE id = %s", [item_id])
    return {"message": "Deleted"}
