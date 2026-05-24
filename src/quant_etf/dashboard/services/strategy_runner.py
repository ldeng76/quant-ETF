"""
异步策略执行器
通过后台线程调用 TaskRegistry 执行策略
"""
import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from loguru import logger

from quant_etf.tasks import TaskRegistry
from quant_etf.conf import PROJECT_ROOT
from .sse_manager import sse_manager
from .alert_engine import alert_engine
from ..db import query

_executor = ThreadPoolExecutor(max_workers=2)
_running_tasks: dict[str, dict] = {}


async def run_strategy(strategy_name: str, run_id: Optional[str] = None) -> str:
    """异步执行策略，返回 run_id"""
    if run_id is None:
        run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    _running_tasks[run_id] = {
        "status": "running",
        "strategy": strategy_name,
        "started_at": datetime.now().isoformat(),
        "progress": 0,
    }

    # 在进入线程前捕获主事件循环，供 SSE 广播使用
    main_loop = asyncio.get_event_loop()

    def _execute():
        try:
            task = TaskRegistry.get_task(strategy_name)
            if not task:
                raise ValueError(f"Unknown strategy: {strategy_name}")

            _running_tasks[run_id]["progress"] = 30
            task.initialize()

            _running_tasks[run_id]["progress"] = 50
            task.run()

            _running_tasks[run_id]["progress"] = 80

            # 读取结果
            today = datetime.now().strftime("%Y-%m-%d")
            csv_path = PROJECT_ROOT / "data" / "results" / today / f"{strategy_name}.csv"
            if csv_path.exists():
                df = pd.read_csv(csv_path, dtype={"code": str})
                records = df.to_dict("records")
                # 过滤无效行
                records = [r for r in records if r.get("code")]
                _running_tasks[run_id]["result"] = records
                _running_tasks[run_id]["count"] = len(records)
            else:
                _running_tasks[run_id]["result"] = []
                _running_tasks[run_id]["count"] = 0

            _running_tasks[run_id]["status"] = "complete"
            _running_tasks[run_id]["progress"] = 100
            _running_tasks[run_id]["finished_at"] = datetime.now().isoformat()

            # --- 告警引擎集成：对比上次结果，触发告警 ---
            try:
                records_for_alert = _running_tasks[run_id].get("result", [])
                prev_records = _load_prev_strategy_result(strategy_name)
                if records_for_alert and prev_records:
                    triggered = alert_engine.check(records_for_alert, prev_records)
                    if triggered:
                        alert_engine.save_alerts(triggered)
                        # SSE 广播告警事件
                        for a in triggered:
                            try:
                                asyncio.run_coroutine_threadsafe(
                                    sse_manager.broadcast({
                                        "type": "alert",
                                        "alert_type": a.get("alert_type", ""),
                                        "severity": a.get("severity", "info"),
                                        "title": a.get("title", ""),
                                        "message": a.get("message", ""),
                                        "strategy": strategy_name,
                                        "run_id": run_id,
                                        "timestamp": datetime.now().isoformat(),
                                    }),
                                    main_loop
                                )
                            except Exception as sse_err:
                                logger.error(f"Failed to broadcast alert SSE: {sse_err}")
                        logger.info(f"Alert engine triggered {len(triggered)} alerts for {strategy_name}")
            except Exception as alert_err:
                logger.warning(f"Alert engine check failed for {strategy_name}: {alert_err}")

        except Exception as e:
            logger.error(f"Strategy {strategy_name} failed: {e}")
            _running_tasks[run_id]["status"] = "error"
            _running_tasks[run_id]["error"] = str(e)
            _running_tasks[run_id]["progress"] = -1

            # 通过 SSE 广播错误事件（与 scheduler.py 一致）
            try:
                asyncio.run_coroutine_threadsafe(
                    sse_manager.broadcast({
                        "type": "strategy_error",
                        "run_id": run_id,
                        "strategy": strategy_name,
                        "error": str(e),
                        "timestamp": datetime.now().isoformat(),
                    }),
                    main_loop
                )
            except Exception as sse_err:
                logger.error(f"Failed to broadcast SSE error: {sse_err}")

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(_executor, _execute)
    return run_id


def _load_prev_strategy_result(strategy_name: str) -> list[dict]:
    """加载上一次策略执行结果，用于告警对比"""
    try:
        import pandas as pd
        from pathlib import Path as P
        results_dir = PROJECT_ROOT / "data" / "results"
        if not results_dir.exists():
            return []
        # 按日期降序获取最近两次结果目录
        date_dirs = sorted([d for d in results_dir.iterdir() if d.is_dir()], reverse=True)
        if len(date_dirs) < 2:
            return []
        # 跳过今天（当前结果），取上一次
        for d in date_dirs:
            csv_path = d / f"{strategy_name}.csv"
            if csv_path.exists():
                today_str = datetime.now().strftime("%Y-%m-%d")
                if d.name == today_str:
                    continue  # 跳过今天的结果
                df = pd.read_csv(csv_path, dtype={"code": str})
                records = df.to_dict("records")
                return [r for r in records if r.get("code")]
        return []
    except Exception as e:
        logger.warning(f"Failed to load prev result for {strategy_name}: {e}")
        return []


def get_task_status(run_id: str) -> Optional[dict]:
    """获取任务状态"""
    return _running_tasks.get(run_id)


def list_available_strategies() -> list[dict]:
    """列出可用策略"""
    return TaskRegistry.list_tasks()
