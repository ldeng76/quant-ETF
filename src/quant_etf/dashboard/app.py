"""
FastAPI应用入口
"""
import asyncio
from contextlib import asynccontextmanager
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
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager（替换 @app.on_event）"""
    # Startup
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
    yield  # 应用运行在这里
    # Shutdown
    print(">>> Dashboard shutting down...", flush=True)
    logger.info("Dashboard shutting down...")
    from .services.minute_collector_service import stop_minute_collector_service
    # 先停止 collector（设置 stop event，唤醒 timer）
    stop_minute_collector_service()
    print(">>> collector stopped", flush=True)
    # 让 I/O 完成一轮
    await asyncio.sleep(0)
    print(">>> I/O processed", flush=True)
    # scheduler.stop_all() 有超时保护，最多等 2 秒
    try:
        await asyncio.wait_for(scheduler.stop_all(), timeout=2.0)
    except asyncio.TimeoutError:
        logger.warning("scheduler.stop_all() timed out, forcing shutdown")
    print(">>> scheduler stopped", flush=True)
    await sse_manager.close()
    await close_pool()
    logger.info("Dashboard shutdown complete")
    print(">>> Dashboard shutdown complete", flush=True)
# FastAPI 应用实例（必须在模块级，供 uvicorn 引用）
app = FastAPI(
    title="Quant ETF Dashboard",
    lifespan=lifespan,
)
# 挂载路由
app.include_router(auth_router)          # /auth/*
app.include_router(wechat_api_router)    # /api/* (微信小程序)
app.include_router(pages.router)         # /pages/*
app.include_router(portfolio.router, prefix="/api/portfolio")
app.include_router(strategy.router, prefix="/api/strategy")
app.include_router(alerts.router, prefix="/api/alerts")
app.include_router(market.router, prefix="/api/market")
app.include_router(watchlist.router, prefix="/api/watchlist")


# ---- 关闭期 CancelledError 静默中间件 ----
# uvicorn timeout_graceful_shutdown 到期后会 cancel SSE 等长连接任务，
# CancelledError 从 Starlette StreamingResponse.listen_for_disconnect 传播上来
# 会被 uvicorn 记录为 "Exception in ASGI application"，非常难看。
# 此中间件在最外层拦截该异常，使其不产生错误日志。
from starlette.types import ASGIApp, Receive, Scope, Send


class _SuppressCancelMiddleware:
    """ASGI middleware: 静默关闭期间的 CancelledError"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await self.app(scope, receive, send)
        except asyncio.CancelledError:
            pass  # 优雅关闭期间的正常行为，无需记录


app.add_middleware(_SuppressCancelMiddleware)  # type: ignore[arg-type]


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    # 关闭期间的 CancelledError 不记录为错误
    if isinstance(exc, asyncio.CancelledError):
        return Response(status_code=204)
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
    import signal
    import uvicorn
    import os
    import threading

    host = os.environ.get("DASHBOARD_HOST", DASHBOARD_HOST)
    port = int(os.environ.get("DASHBOARD_PORT", DASHBOARD_PORT))

    # Windows + uvicorn: Ctrl+C 无法可靠停止服务器
    # 原因：uvicorn.run(str) 会重新导入模块并覆盖自定义 SIGINT handler，
    #       且 SSE 长连接导致优雅关闭阶段卡住
    # 解决：传 app 对象 + 安全定时器兜底强退
    _sigint_count = 0

    def _sigint_handler(signum, frame):
        nonlocal _sigint_count
        _sigint_count += 1
        if _sigint_count == 1:
            print("\n>>> Ctrl+C received, shutting down... (press Ctrl+C again to force quit)", flush=True)
            _install_safety_timer()
            raise SystemExit(0)
        else:
            print("\n>>> Force quitting...", flush=True)
            os._exit(0)

    signal.signal(signal.SIGINT, _sigint_handler)

    # 安全定时器：如果 uvicorn graceful shutdown 5 秒内未完成，强制退出
    def _force_exit_after_timeout():
        print("\n>>> Graceful shutdown timed out (5s), force quitting...", flush=True)
        os._exit(1)

    def _install_safety_timer():
        timer = threading.Timer(5.0, _force_exit_after_timeout)
        timer.daemon = True
        timer.start()

    # 传 app 对象（而非字符串），避免 uvicorn 重新导入模块覆盖信号处理器
    uvicorn.run(
        app,
        host=host,
        port=port,
        reload=False,
        log_level="info",
        timeout_graceful_shutdown=3,  # 3秒后强制关闭所有连接
    )


if __name__ == "__main__":
    main()