"""
FastAPI应用入口
"""
import asyncio
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse, RedirectResponse, Response
from loguru import logger

from .config import DASHBOARD_HOST, DASHBOARD_PORT, IS_PRIMARY, JWT_SECRET_KEY, POSTGRES_HOST
from .db import init_db, close_pool
from .db_migrate import run_migrations
from .template_setup import templates
from .routes import pages, portfolio, strategy, alerts, market, watchlist
from .routes.auth_routes import router as auth_router, api_router as wechat_api_router
from .services.sse_manager import sse_manager
from .services.scheduler import scheduler
from .services.startup_preload import start_background_preload
from .auth import is_auth_enabled

app = FastAPI(title="quant-ETF Dashboard", version="2.0.0")

# 挂载路由
app.include_router(auth_router)          # /auth/*
app.include_router(wechat_api_router)    # /api/* (微信小程序)
app.include_router(pages.router)         # /pages/*
app.include_router(portfolio.router, prefix="/api/portfolio")
app.include_router(strategy.router, prefix="/api/strategy")
app.include_router(alerts.router, prefix="/api/alerts")
app.include_router(market.router, prefix="/api/market")
app.include_router(watchlist.router, prefix="/api/watchlist")


@app.get("/")
async def root():
    """根路径重定向到总览页面"""
    return RedirectResponse(url="/pages/overview")


@app.get("/favicon.ico")
async def favicon():
    """favicon"""
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


@app.get("/health")
async def health():
    """健康检查端点"""
    health_info = {
        "status": "ok",
        "node_role": "primary" if IS_PRIMARY else "secondary",
        "auth_enabled": is_auth_enabled(),
    }
    # 检查 PostgreSQL 连接
    try:
        from .db import query_one
        result = query_one("SELECT 1")
        health_info["postgresql"] = "ok" if result else "error"
    except Exception as e:
        health_info["postgresql"] = f"error: {e}"

    return JSONResponse(content=health_info)


@app.on_event("startup")
async def startup():
    """应用启动时初始化"""
    init_db()
    run_migrations()
    if IS_PRIMARY:
        await scheduler.start_all()
    # 启动后台预加载（不阻塞主线程）
    start_background_preload()
    logger.info(
        f"Dashboard startup complete "
        f"(role={'primary' if IS_PRIMARY else 'secondary'}, "
        f"auth={is_auth_enabled()}, "
        f"postgres={POSTGRES_HOST})"
    )


@app.on_event("shutdown")
async def shutdown():
    """应用关闭时清理"""
    await scheduler.stop_all()
    await close_pool()
    logger.info("Dashboard shutdown complete")


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(status_code=500, content={"detail": str(exc)})


def main():
    """CLI入口"""
    import os
    from dotenv import load_dotenv
    import uvicorn

    load_dotenv()
    reload = os.environ.get("DASHBOARD_RELOAD", "true").lower() != "false"

    uvicorn.run(
        "quant_etf.dashboard.app:app",
        host=DASHBOARD_HOST,
        port=DASHBOARD_PORT,
        reload=reload,
        timeout_graceful_shutdown=3,
    )


if __name__ == "__main__":
    main()