"""
FastAPI应用入口
"""
import asyncio
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
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


@app.on_event("startup")
async def startup():
    """应用启动时初始化"""
    logger.info("Dashboard starting...")
    if is_auth_enabled():
        logger.info("Authentication: enabled (JWT)")
    else:
        logger.warning("Authentication: DISABLED (no JWT_SECRET_KEY)")

    # 初始化数据库
    init_db()
    run_migrations()

    # 启动后台预加载任务
    try:
        await start_background_preload()
    except Exception as e:
        logger.warning(f"Background preload skipped: {e}")

    logger.info(f"Dashboard ready on http://{DASHBOARD_HOST}:{DASHBOARD_PORT}")


@app.on_event("shutdown")
async def shutdown():
    """应用关闭时清理"""
    logger.info("Dashboard shutting down...")
    scheduler.stop_all()
    await sse_manager.close()
    await close_pool()
    logger.info("Dashboard shutdown complete")


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(status_code=500, content={"detail": str(exc)})


@app.exception_handler(HTTPException)
async def http_exception_redirect_handler(request: Request, exc: HTTPException):
    """处理 HTTP 异常，返回 JSON 或重定向"""
    # API 路径或 HTMX 请求返回 JSON
    if request.url.path.startswith("/api") or request.headers.get("HX-Request"):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    # 浏览器页面：认证失败重定向到登录页
    if exc.status_code == 401:
        return RedirectResponse(url="/auth/login", status_code=302)

    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/events")
async def sse_events(request: Request):
    """SSE 事件流端点"""
    from .deps import get_current_user
    await get_current_user(request)
    return StreamingResponse(
        sse_manager.subscribe(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/health")
async def health():
    """健康检查端点"""
    from .services.scheduler import scheduler
    health_info = {
        "status": "ok",
        "scheduler": "running" if scheduler else "unavailable",
    }
    return health_info


@app.get("/")
async def root():
    """根路径 -> 重定向到看板"""
    return RedirectResponse(url="/pages/overview")


@app.get("/favicon.ico")
async def favicon():
    """返回空响应消除404"""
    return Response(status_code=204)


def main():
    import uvicorn
    import os

    host = os.environ.get("DASHBOARD_HOST", DASHBOARD_HOST)
    port = int(os.environ.get("DASHBOARD_PORT", DASHBOARD_PORT))

    uvicorn.run(
        "quant_etf.dashboard.app:app",
        host=host,
        port=port,
        reload=False,
        factory=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()