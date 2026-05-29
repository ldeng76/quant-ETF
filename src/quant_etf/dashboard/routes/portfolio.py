"""
持仓管理API（多租户版本）
所有查询通过 user_id 隔离
"""
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse
from typing import Optional

from ..template_setup import templates
from ..db import query, query_one, execute
from ..models import AccountCreate, AccountUpdate, HoldingCreate, HoldingUpdate
from ..config import STOCK_CODE_NAME_PATH
from ..services.portfolio_sync import sync_prices_async
from ..deps import get_current_user
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
async def list_accounts(request: Request, user: dict = Depends(get_current_user)):
    """账户列表（侧边栏片段）"""
    accounts = query(
        "SELECT * FROM accounts WHERE user_id = %s ORDER BY name",
        [user["id"]]
    )
    return templates.TemplateResponse(
        request, "portfolio/_account_list.html",
        {"accounts": accounts}
    )


@router.post("/accounts", response_class=HTMLResponse)
async def create_account(request: Request, data: AccountCreate, user: dict = Depends(get_current_user)):
    execute(
        "INSERT INTO accounts (user_id, name, broker, cash) VALUES (%s, %s, %s, %s)",
        [user["id"], data.name, data.broker, data.cash]
    )
    accounts = query(
        "SELECT * FROM accounts WHERE user_id = %s ORDER BY name",
        [user["id"]]
    )
    return templates.TemplateResponse(
        request, "portfolio/_account_list.html",
        {"accounts": accounts}
    )


@router.put("/accounts/{account_id}", response_class=HTMLResponse)
async def update_account(request: Request, account_id: int, data: AccountUpdate, user: dict = Depends(get_current_user)):
    fields = []
    params = []
    if data.name is not None:
        fields.append("name = %s")
        params.append(data.name)
        offset = 2
    else:
        offset = 1
    if data.broker is not None:
        fields.append(f"broker = ${offset}")
        params.append(data.broker)
        offset += 1
    if data.cash is not None:
        fields.append(f"cash = ${offset}")
        params.append(data.cash)
        offset += 1
    if not fields:
        raise HTTPException(400, "No fields to update")
    fields.append("updated_at = CURRENT_TIMESTAMP")
    params.append(account_id)
    params.append(user["id"])
    execute(
        f"UPDATE accounts SET {', '.join(fields)} WHERE id = ${offset} AND user_id = ${offset+1}",
        params
    )
    accounts = query(
        "SELECT * FROM accounts WHERE user_id = %s ORDER BY name",
        [user["id"]]
    )
    return templates.TemplateResponse(
        request, "portfolio/_account_list.html",
        {"accounts": accounts}
    )


@router.delete("/accounts/{account_id}", response_class=HTMLResponse)
async def delete_account(request: Request, account_id: int, user: dict = Depends(get_current_user)):
    # 先验证账户属于当前用户
    account = query_one(
        "SELECT id FROM accounts WHERE id = %s AND user_id = %s",
        [account_id, user["id"]]
    )
    if not account:
        raise HTTPException(404, "Account not found")
    execute("DELETE FROM holdings WHERE account_id = %s", [account_id])
    execute("DELETE FROM accounts WHERE id = %s AND user_id = %s", [account_id, user["id"]])
    accounts = query(
        "SELECT * FROM accounts WHERE user_id = %s ORDER BY name",
        [user["id"]]
    )
    return templates.TemplateResponse(
        request, "portfolio/_account_list.html",
        {"accounts": accounts}
    )


# ========== 持仓 ==========

@router.get("/accounts/{account_id}/holdings", response_class=HTMLResponse)
async def list_holdings(request: Request, account_id: int, user: dict = Depends(get_current_user)):
    """账户持仓表格"""
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
async def create_holding(request: Request, data: HoldingCreate, user: dict = Depends(get_current_user)):
    account = query_one(
        "SELECT id FROM accounts WHERE id = %s AND user_id = %s",
        [data.account_id, user["id"]]
    )
    if not account:
        raise HTTPException(404, "Account not found")
    execute(
        "INSERT INTO holdings (account_id, code, name, quantity, cost_price, strategy, notes) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        [data.account_id, data.code, data.name, data.quantity, data.cost_price, data.strategy, data.notes]
    )
    holdings = query("SELECT * FROM holdings WHERE account_id = %s ORDER BY code", [data.account_id])
    names = _load_etf_name_map()
    account = query_one("SELECT * FROM accounts WHERE id = %s", [data.account_id])
    return templates.TemplateResponse(
        request, "portfolio/_holdings_table.html",
        {"account": account, "holdings": holdings, "names": names}
    )


@router.put("/holdings/{holding_id}", response_class=HTMLResponse)
async def update_holding(request: Request, holding_id: int, data: HoldingUpdate, user: dict = Depends(get_current_user)):
    # 验证持仓所属账户属于当前用户
    existing = query_one(
        """SELECT h.* FROM holdings h
           JOIN accounts a ON h.account_id = a.id
           WHERE h.id = %s AND a.user_id = %s""",
        [holding_id, user["id"]]
    )
    if not existing:
        raise HTTPException(404, "Holding not found")

    fields = []
    params = []
    for field in ["code", "name", "quantity", "cost_price", "strategy", "notes"]:
        val = getattr(data, field, None)
        if val is not None:
            fields.append(f"{field} = ${len(params)+1}")
            params.append(val)
    if not fields:
        raise HTTPException(400, "No fields to update")
    fields.append(f"updated_at = CURRENT_TIMESTAMP")
    params.append(holding_id)
    execute(f"UPDATE holdings SET {', '.join(fields)} WHERE id = ${len(params)}", params)

    holdings = query("SELECT * FROM holdings WHERE account_id = %s ORDER BY code", [existing["account_id"]])
    names = _load_etf_name_map()
    account = query_one("SELECT * FROM accounts WHERE id = %s", [existing["account_id"]])
    return templates.TemplateResponse(
        request, "portfolio/_holdings_table.html",
        {"account": account, "holdings": holdings, "names": names}
    )


@router.delete("/holdings/{holding_id}", response_class=HTMLResponse)
async def delete_holding(request: Request, holding_id: int, user: dict = Depends(get_current_user)):
    existing = query_one(
        """SELECT h.* FROM holdings h
           JOIN accounts a ON h.account_id = a.id
           WHERE h.id = %s AND a.user_id = %s""",
        [holding_id, user["id"]]
    )
    if not existing:
        raise HTTPException(404, "Holding not found")
    execute("DELETE FROM holdings WHERE id = %s", [holding_id])
    holdings = query("SELECT * FROM holdings WHERE account_id = %s ORDER BY code", [existing["account_id"]])
    names = _load_etf_name_map()
    account = query_one("SELECT * FROM accounts WHERE id = %s", [existing["account_id"]])
    return templates.TemplateResponse(
        request, "portfolio/_holdings_table.html",
        {"account": account, "holdings": holdings, "names": names}
    )


@router.post("/sync-prices")
async def sync_holding_prices(user: dict = Depends(get_current_user)):
    """手动触发持仓价格同步"""
    stats = await sync_prices_async()
    return stats