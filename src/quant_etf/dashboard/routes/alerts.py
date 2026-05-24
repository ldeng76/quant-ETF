"""
告警管理API
"""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from ..template_setup import templates
from ..db import query, query_one, execute
from ..models import AlertRuleCreate, AlertUpdate
from ..config import ALERTS_DUCKDB_PATH
from loguru import logger

router = APIRouter(tags=["alerts"])


@router.get("/rules", response_class=JSONResponse)
async def list_rules():
    """列出告警规则"""
    return query("SELECT * FROM alert_rules ORDER BY name")


@router.post("/rules")
async def create_rule(data: AlertRuleCreate):
    """创建告警规则"""
    rid = execute(
        "INSERT INTO alert_rules (name, rule_type, config) VALUES (?, ?, ?)",
        [data.name, data.rule_type, data.config]
    )
    return {"id": rid}


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: int):
    """删除告警规则"""
    execute("DELETE FROM alert_rules WHERE id = ?", [rule_id])
    return {"message": "Deleted"}


@router.get("/dashboard", response_class=HTMLResponse)
async def list_dashboard_alerts(request: Request):
    """告警列表片段"""
    alerts = query("""
        SELECT * FROM alerts_dashboard
        ORDER BY
            CASE status WHEN 'active' THEN 0 WHEN 'acknowledged' THEN 1 ELSE 2 END,
            created_at DESC
        LIMIT 100
    """)
    return templates.TemplateResponse(
        request, "alerts/_alert_list.html",
        {"alerts": alerts}
    )


@router.put("/dashboard/{alert_id}/status")
async def update_alert_status(alert_id: int, data: AlertUpdate):
    """更新告警状态"""
    if data.status == "resolved":
        execute(
            "UPDATE alerts_dashboard SET status = ?, resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
            [data.status, alert_id]
        )
    else:
        execute(
            "UPDATE alerts_dashboard SET status = ? WHERE id = ?",
            [data.status, alert_id]
        )
    return {"message": "Updated"}


@router.get("/dashboard/stats", response_class=JSONResponse)
async def alert_stats():
    """告警统计数据"""
    total = query_one("SELECT COUNT(*) as cnt FROM alerts_dashboard")["cnt"]
    active = query_one("SELECT COUNT(*) as cnt FROM alerts_dashboard WHERE status = 'active'")["cnt"]
    return {"total": total, "active": active}


@router.get("/monitor-signals", response_class=HTMLResponse)
async def monitor_signals(request: Request, limit: int = 50):
    """读取 DuckDB 中 ETFMonitor 产生的监控信号"""
    signals = []
    try:
        if ALERTS_DUCKDB_PATH.exists():
            import duckdb
            conn = duckdb.connect(str(ALERTS_DUCKDB_PATH), read_only=True)
            try:
                rows = conn.execute(
                    "SELECT id, time, code, strategy_name, signal_type, direction, "
                    "score, entry_price, reason, market_state "
                    "FROM alerts ORDER BY time DESC LIMIT ?",
                    [limit]
                ).fetchall()
                cols = [desc[0] for desc in conn.description]
                signals = [dict(zip(cols, row)) for row in rows]
            finally:
                conn.close()
    except Exception as e:
        logger.warning(f"Failed to read monitor signals from DuckDB: {e}")

    return templates.TemplateResponse(
        request, "alerts/_monitor_signals.html",
        {"signals": signals}
    )
