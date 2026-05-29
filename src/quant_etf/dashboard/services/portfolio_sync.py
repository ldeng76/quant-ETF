"""
持仓价格同步服务
从 PostgreSQL minute_bars 获取最新收盘价，更新 holdings.current_price
"""
import asyncio
from datetime import datetime
from typing import Optional
from loguru import logger

from ..db import query, execute, get_pg_conn
from .sse_manager import sse_manager


def sync_prices() -> dict:
    """
    从 PostgreSQL minute_bars 读取最新价格，更新 holdings.current_price
    返回同步统计信息
    """
    stats = {"updated": 0, "skipped": 0, "errors": 0}

    # 获取所有需要更新价格的持仓代码
    holdings = query("SELECT DISTINCT code FROM holdings")
    if not holdings:
        return stats

    codes = [h["code"] for h in holdings]

    try:
        conn = get_pg_conn()
        cur = conn.cursor()
        for code in codes:
            try:
                # 获取最新一条分钟K线的收盘价
                cur.execute("""
                    SELECT close FROM minute_bars
                    WHERE code = %s
                    ORDER BY time DESC LIMIT 1
                """, [code])
                row = cur.fetchone()

                if row and row[0]:
                    execute(
                        "UPDATE holdings SET current_price = %s, updated_at = CURRENT_TIMESTAMP WHERE code = %s",
                        [row[0], code]
                    )
                    stats["updated"] += 1
                else:
                    stats["skipped"] += 1
            except Exception as e:
                logger.warning(f"Failed to sync price for {code}: {e}")
                stats["errors"] += 1

    except Exception as e:
        logger.error(f"Failed to connect to PostgreSQL: {e}")
        stats["errors"] += 1

    logger.info(f"Price sync completed: {stats}")
    return stats


async def sync_prices_async() -> dict:
    """异步执行价格同步（供路由层调用）"""
    loop = asyncio.get_event_loop()
    stats = await loop.run_in_executor(None, sync_prices)

    # SSE 广播价格更新事件
    if stats["updated"] > 0:
        try:
            await sse_manager.broadcast({
                "type": "portfolio_update",
                "action": "price_sync",
                "updated": stats["updated"],
                "timestamp": datetime.now().isoformat(),
            })
        except Exception as e:
            logger.warning(f"Failed to broadcast price sync SSE: {e}")

    return stats