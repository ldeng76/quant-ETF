"""
告警管理API（多租户版本）
alert_rules / alerts_dashboard 通过 user_id 隔离
"""
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse

from ..template_setup import templates
from ..db import query, query_one, execute, get_pg_conn
from ..models import AlertRuleCreate, AlertUpdate
from ..deps import get_current_user
from loguru import logger

router = APIRouter(tags=["alerts"])


@router.get("/rules", response_class=JSONResponse)
async def list_rules(user: dict = Depends(get_current_user)):
    """列出告警规则（当前用户）"""
    return query(
        "SELECT * FROM alert_rules WHERE user_id = %s OR user_id IS NULL ORDER BY name",
        [user["id"]]
    )


@router.post("/rules")
async def create_rule(data: AlertRuleCreate, user: dict = Depends(get_current_user)):
    """创建告警规则"""
    rid = execute(
        "INSERT INTO alert_rules (user_id, name, rule_type, config) VALUES (%s, %s, %s, %s)",
        [user["id"], data.name, data.rule_type, data.config]
    )
    return {"id": rid}


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: int, user: dict = Depends(get_current_user)):
    """删除告警规则（仅自己创建的）"""
    rule = query_one(
        "SELECT id FROM alert_rules WHERE id = %s AND user_id = %s",
        [rule_id, user["id"]]
    )
    if not rule:
        raise HTTPException(404, "Rule not found")
    execute("DELETE FROM alert_rules WHERE id = %s", [rule_id])
    return {"message": "Deleted"}


@router.get("/dashboard", response_class=HTMLResponse)
async def list_dashboard_alerts(request: Request, user: dict = Depends(get_current_user)):
    """告警列表片段"""
    alerts = query(
        """SELECT * FROM alerts_dashboard
           WHERE user_id = %s OR user_id IS NULL
           ORDER BY
               CASE status WHEN 'active' THEN 0 WHEN 'acknowledged' THEN 1 ELSE 2 END,
               created_at DESC
           LIMIT 100""",
        [user["id"]]
    )
    return templates.TemplateResponse(
        request, "alerts/_alert_list.html",
        {"alerts": alerts}
    )


@router.put("/dashboard/{alert_id}/status")
async def update_alert_status(alert_id: int, data: AlertUpdate, user: dict = Depends(get_current_user)):
    """更新告警状态"""
    # 验证告警属于当前用户
    alert = query_one(
        "SELECT id FROM alerts_dashboard WHERE id = %s AND user_id = %s",
        [alert_id, user["id"]]
    )
    if not alert:
        raise HTTPException(404, "Alert not found")

    if data.status == "resolved":
        execute(
            "UPDATE alerts_dashboard SET status = %s, resolved_at = CURRENT_TIMESTAMP WHERE id = %s",
            [data.status, alert_id]
        )
    else:
        execute(
            "UPDATE alerts_dashboard SET status = %s WHERE id = %s",
            [data.status, alert_id]
        )
    return {"message": "Updated"}


@router.get("/dashboard/stats", response_class=JSONResponse)
async def alert_stats(user: dict = Depends(get_current_user)):
    """告警统计数据"""
    total = query_one(
        "SELECT COUNT(*) as cnt FROM alerts_dashboard WHERE user_id = %s OR user_id IS NULL",
        [user["id"]]
    )["cnt"]
    active = query_one(
        "SELECT COUNT(*) as cnt FROM alerts_dashboard WHERE user_id = %s AND status = 'active'",
        [user["id"]]
    )["cnt"]
    return {"total": total, "active": active}


@router.get("/monitor-signals", response_class=HTMLResponse)
async def monitor_signals(request: Request, limit: int = 50, user: dict = Depends(get_current_user)):
    """从 PostgreSQL 读取 ETFMonitor 产生的监控信号"""
    signals = []
    try:
        conn = get_pg_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, time, code, strategy_name, signal_type, direction,
                   score, entry_price, reason, market_state
            FROM monitor_alerts
            ORDER BY time DESC
            LIMIT %s
        """, [limit])
        rows = cur.fetchall()
        cols = [desc[0] for desc in cur.description]
        signals = [dict(zip(cols, row)) for row in rows]
    except Exception as e:
        logger.warning(f"Failed to read monitor signals from PostgreSQL: {e}")

    return templates.TemplateResponse(
        request, "alerts/_monitor_signals.html",
        {"signals": signals}
    )