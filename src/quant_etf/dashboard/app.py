"""
FastAPI应用入口
"""
import asyncio
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse, RedirectResponse, Response
from loguru import logger

from .config import DASHBOARD_HOST, DASHBOARD_PORT
from .db import init_db
from .template_setup import templates
from .routes import pages, portfolio, strategy, alerts, market
from .services.sse_manager import sse_manager
from .services.scheduler import scheduler

app = FastAPI(title="quant-ETF Dashboard", version="1.0.0")

# 挂载路由
app.include_router(pages.router)
app.include_router(portfolio.router, prefix="/api/portfolio")
app.include_router(strategy.router, prefix="/api/strategy")
app.include_router(alerts.router, prefix="/api/alerts")
app.include_router(market.router, prefix="/api/market")


@app.get("/")
async def root():
    """根路径重定向到总览页面"""
    return RedirectResponse(url="/pages/overview")


@app.get("/favicon.ico")
async def favicon():
    """favicon - 返回空内容避免404"""
    return Response(status_code=204)


@app.get("/events")
async def sse_events(request: Request):
    """SSE 事件流端点"""
    return StreamingResponse(
        sse_manager.subscribe(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@app.on_event("startup")
async def startup():
    """应用启动时初始化"""
    init_db()
    await scheduler.start_all()
    logger.info("Dashboard startup complete")


@app.on_event("shutdown")
async def shutdown():
    """应用关闭时清理"""
    await scheduler.stop_all()
    logger.info("Dashboard shutdown complete")


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(status_code=500, content={"detail": str(exc)})


def main():
    """CLI入口"""
    import os
    import uvicorn

    reload = os.environ.get("DASHBOARD_RELOAD", "true").lower() != "false"

    uvicorn.run(
        "quant_etf.dashboard.app:app",
        host=DASHBOARD_HOST,
        port=DASHBOARD_PORT,
        reload=reload,
        timeout_graceful_shutdown=3,  # 优雅关闭超时3秒，防止 SSE 长连接阻塞 CTRL+C 退出
    )


if __name__ == "__main__":
    main()
