from .minute_collector_service import start_minute_collector_service
"""
启动时后台预加载核心数据

在 Dashboard 启动时异步预加载市场状态等核心数据，
避免首次访问时的漫长等待。
"""
import asyncio
import threading
from loguru import logger
from .scheduler import scheduler
from ..config import IS_PRIMARY

# 预加载状态
_preload_completed = False
_preload_error: str | None = None


def preload_market_state():
    """
    预加载市场状态（在后台线程执行）

    调用 get_market_state_cached() 会自动触发缓存填充
    """
    try:
        from quant_etf.market_analyzer import get_market_state_cached
        logger.info("Preloading market state...")
        state = get_market_state_cached()
        logger.info(
            f"Market state preloaded: {state.market_type.value}, "
            f"return={state.index_return:.3f}, volatility={state.volatility:.3f}"
        )
    except Exception as e:
        logger.warning(f"Market state preload failed: {e}")


async def start_background_preload():
    """
    启动后台预加载任务（不阻塞主线程）

    在 Dashboard 启动后立即调用，不影响启动速度
    """
    global _preload_completed, _preload_error

    def _preload_in_thread():
        global _preload_completed, _preload_error
        try:
            preload_market_state()
            if IS_PRIMARY:
                asyncio.run(scheduler.start_all())
            start_minute_collector_service()
            _preload_completed = True
        except Exception as e:
            _preload_error = str(e)
            logger.error(f"Background preload failed: {e}")

    thread = threading.Thread(
        target=_preload_in_thread,
        daemon=True,
        name="market-state-preload"
    )
    thread.start()
    logger.info("Background preload thread started")


def is_preload_completed() -> bool:
    """检查预加载是否完成"""
    return _preload_completed


def get_preload_error() -> str | None:
    """获取预加载错误信息"""
    return _preload_error