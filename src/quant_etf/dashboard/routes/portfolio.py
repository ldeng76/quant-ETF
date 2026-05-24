"""
持仓管理API
"""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from typing import Optional

from ..template_setup import templates
from ..db import query, query_one, execute
from ..models import AccountCreate, AccountUpdate, HoldingCreate, HoldingUpdate
from ..config import STOCK_CODE_NAME_PATH
from ..services.portfolio_sync import sync_prices_async
import json

router = APIRouter(tags=["portfolio"])


def _load_etf_name_map() -> dict:
    """加载ETF名称映射"""
    try:
        if STOCK_CODE_NAME_PATH.exists():
            with open(STOCK_CODE_NAME_PATH, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


# ========== 账户 ==========

@router.get("/accounts", response_class=HTMLResponse)
async def list_accounts(request: Request):
    """账户列表（侧边栏片段）"""
    accounts = query("SELECT * FROM accounts ORDER BY name")
    return templates.TemplateResponse(
        request, "portfolio/_account_list.html",
        {"accounts": accounts}
    )


@router.post("/accounts", response_class=HTMLResponse)
async def create_account(request: Request, data: AccountCreate):
    execute(
        "INSERT INTO accounts (name, broker, cash) VALUES (?, ?, ?)",
        [data.name, data.broker, data.cash]
    )
    accounts = query("SELECT * FROM accounts ORDER BY name")
    return templates.TemplateResponse(
        request, "portfolio/_account_list.html",
        {"accounts": accounts}
    )


@router.put("/accounts/{account_id}", response_class=HTMLResponse)
async def update_account(request: Request, account_id: int, data: AccountUpdate):
    fields = []
    params = []
    if data.name is not None:
        fields.append("name = ?")
        params.append(data.name)
    if data.broker is not None:
        fields.append("broker = ?")
        params.append(data.broker)
    if data.cash is not None:
        fields.append("cash = ?")
        params.append(data.cash)
    if not fields:
        raise HTTPException(400, "No fields to update")
    fields.append("updated_at = CURRENT_TIMESTAMP")
    params.append(account_id)
    execute(f"UPDATE accounts SET {', '.join(fields)} WHERE id = ?", params)
    accounts = query("SELECT * FROM accounts ORDER BY name")
    return templates.TemplateResponse(
        request, "portfolio/_account_list.html",
        {"accounts": accounts}
    )


@router.delete("/accounts/{account_id}", response_class=HTMLResponse)
async def delete_account(request: Request, account_id: int):
    execute("DELETE FROM holdings WHERE account_id = ?", [account_id])
    execute("DELETE FROM accounts WHERE id = ?", [account_id])
    # 返回空列表片段
    accounts = query("SELECT * FROM accounts ORDER BY name")
    return templates.TemplateResponse(
        request, "portfolio/_account_list.html",
        {"accounts": accounts}
    )


# ========== 持仓 ==========

@router.get("/accounts/{account_id}/holdings", response_class=HTMLResponse)
async def list_holdings(request: Request, account_id: int):
    """账户持仓表格"""
    account = query_one("SELECT * FROM accounts WHERE id = ?", [account_id])
    if not account:
        raise HTTPException(404, "Account not found")
    holdings = query("SELECT * FROM holdings WHERE account_id = ? ORDER BY code", [account_id])
    names = _load_etf_name_map()
    return templates.TemplateResponse(
        request, "portfolio/_holdings_table.html",
        {
            "account": account,
            "holdings": holdings,
            "names": names,
        }
    )


@router.post("/holdings", response_class=HTMLResponse)
async def create_holding(request: Request, data: HoldingCreate):
    account = query_one("SELECT id FROM accounts WHERE id = ?", [data.account_id])
    if not account:
        raise HTTPException(404, "Account not found")
    execute(
        "INSERT INTO holdings (account_id, code, name, quantity, cost_price, strategy, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [data.account_id, data.code, data.name, data.quantity, data.cost_price, data.strategy, data.notes]
    )
    holdings = query("SELECT * FROM holdings WHERE account_id = ? ORDER BY code", [data.account_id])
    names = _load_etf_name_map()
    return templates.TemplateResponse(
        request, "portfolio/_holdings_table.html",
        {"account": account, "holdings": holdings, "names": names}
    )


@router.put("/holdings/{holding_id}", response_class=HTMLResponse)
async def update_holding(request: Request, holding_id: int, data: HoldingUpdate):
    existing = query_one("SELECT * FROM holdings WHERE id = ?", [holding_id])
    if not existing:
        raise HTTPException(404, "Holding not found")
    fields = []
    params = []
    for field in ["code", "name", "quantity", "cost_price", "strategy", "notes"]:
        val = getattr(data, field, None)
        if val is not None:
            fields.append(f"{field} = ?")
            params.append(val)
    if not fields:
        raise HTTPException(400, "No fields to update")
    fields.append("updated_at = CURRENT_TIMESTAMP")
    params.append(holding_id)
    execute(f"UPDATE holdings SET {', '.join(fields)} WHERE id = ?", params)
    holdings = query("SELECT * FROM holdings WHERE account_id = ? ORDER BY code", [existing["account_id"]])
    names = _load_etf_name_map()
    account = query_one("SELECT * FROM accounts WHERE id = ?", [existing["account_id"]])
    return templates.TemplateResponse(
        request, "portfolio/_holdings_table.html",
        {"account": account, "holdings": holdings, "names": names}
    )


@router.delete("/holdings/{holding_id}", response_class=HTMLResponse)
async def delete_holding(request: Request, holding_id: int):
    existing = query_one("SELECT * FROM holdings WHERE id = ?", [holding_id])
    if not existing:
        raise HTTPException(404, "Holding not found")
    execute("DELETE FROM holdings WHERE id = ?", [holding_id])
    holdings = query("SELECT * FROM holdings WHERE account_id = ? ORDER BY code", [existing["account_id"]])
    names = _load_etf_name_map()
    account = query_one("SELECT * FROM accounts WHERE id = ?", [existing["account_id"]])
    return templates.TemplateResponse(
        request, "portfolio/_holdings_table.html",
        {"account": account, "holdings": holdings, "names": names}
    )


@router.post("/sync-prices")
async def sync_holding_prices():
    """手动触发持仓价格同步"""
    stats = await sync_prices_async()
    return stats
