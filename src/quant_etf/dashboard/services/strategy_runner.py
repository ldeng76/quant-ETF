"""
异步策略执行器
通过后台线程调用 TaskRegistry 执行策略
"""
import asyncio
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from loguru import logger

from quant_etf.tasks import TaskRegistry
from quant_etf.conf import PROJECT_ROOT
from quant_etf.trading_day import is_intraday
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
            intraday = is_intraday()
            if intraday:
                logger.info(f"Strategy {strategy_name} running in INTRADAY mode")
            task = TaskRegistry.get_task(strategy_name, intraday=intraday)
            if not task:
                raise ValueError(f"Unknown strategy: {strategy_name}")

            _running_tasks[run_id]["title"] = getattr(task, "title", strategy_name)

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

            # 清除历史汇总缓存，确保新结果立即可见
            clear_history_summary_cache(strategy_name)

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
                                        "strategy_title": _running_tasks[run_id].get("title", strategy_name),
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
                        "strategy_title": _running_tasks[run_id].get("title", strategy_name),
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


def get_sell_signals(strategy_name: str = "etf") -> list[dict]:
    """检测今日掉榜的标的，返回卖出信号列表。

    严格模式：只在最新结果日掉榜的标的才触发卖出信号。
    """
    summary = get_history_summary(
        strategy_name=strategy_name, days=30, auto_backfill=False
    )
    if not summary:
        return []

    # 找到最新结果日期（当前仍在榜的标的的 last_on_date 最大值）
    latest_date = max((d["last_on_date"] for d in summary if d["is_active"]), default=None)
    if not latest_date:
        return []

    # 严格模式：off_date == latest_date 表示今天刚掉榜
    signals = []
    for item in summary:
        if not item["is_active"] and item["off_date"] == latest_date:
            signals.append({
                "code": item["code"],
                "name": item["name"],
                "last_on_date": item["last_on_date"],
                "on_days": item["on_days"],
            })
    return signals


_history_cache: dict[str, tuple] = {}


def clear_history_summary_cache(strategy_name: str = "etf") -> None:
    """清除指定策略的历史汇总缓存"""
    keys_to_remove = [k for k in _history_cache if k.startswith(f"{strategy_name}_")]
    for k in keys_to_remove:
        del _history_cache[k]


def get_history_summary(
    strategy_name: str = "etf",
    days: int = 30,
    auto_backfill: bool = True
) -> list[dict]:
    """获取最近N天策略历史标的汇总
    
    Args:
        strategy_name: 策略名称
        days: 最近天数
        auto_backfill: 是否自动检测并补算缺失的CSV文件
    
    Returns:
        历史标的汇总列表
    """
    # 在获取历史汇总前，自动检测并补算缺失的CSV
    if auto_backfill:
        try:
            from .auto_backfill import auto_backfill_history
            auto_backfill_history(strategy_name=strategy_name, days=days)
        except Exception as e:
            logger.warning(f"Auto-backfill failed for {strategy_name}: {e}")
            # 补算失败不影响返回现有数据

    cache_key = f"{strategy_name}_{days}"
    if cache_key in _history_cache:
        ts, data = _history_cache[cache_key]
        if (datetime.now() - ts).total_seconds() < 300:  # 5分钟TTL
            return data

    results_dir = PROJECT_ROOT / "data" / "results"
    if not results_dir.exists():
        return []

    today = datetime.now().date()
    cutoff = today - timedelta(days=days)

    # 收集日期目录
    all_date_dirs = []
    for d in results_dir.iterdir():
        if d.is_dir():
            try:
                dir_date = datetime.strptime(d.name, "%Y-%m-%d").date()
                if dir_date >= cutoff:
                    all_date_dirs.append(d)
            except ValueError:
                continue
    all_date_dirs.sort()

    if not all_date_dirs:
        return []

    # 读取所有CSV，构建 code -> set(dates) 映射
    code_dates: dict[str, set] = defaultdict(set)
    code_names: dict[str, str] = {}

    for date_dir in all_date_dirs:
        csv_path = date_dir / f"{strategy_name}.csv"
        if not csv_path.exists():
            continue
        df = pd.read_csv(csv_path, dtype={"code": str})
        for row in df.to_dict("records"):
            code = row.get("code")
            if not code:
                continue
            date_str = row.get("date", date_dir.name)
            code_dates[code].add(date_str)
            code_names[code] = row.get("name", "")

    # 计算汇总指标
    latest_date = all_date_dirs[-1].name

    def _compute_off_date(last_on: str, all_dates: list) -> str:
        for d in all_dates:
            if d > last_on:
                return d
        return "-"

    results = []
    date_strs = [d.name for d in all_date_dirs]

    for code, dates in code_dates.items():
        sorted_dates = sorted(dates)
        last_on_date = sorted_dates[-1]
        on_days = len(dates)
        is_active = (last_on_date == latest_date)

        if is_active:
            off_date = "-"
        else:
            off_date = _compute_off_date(last_on_date, date_strs)

        results.append({
            "code": code,
            "name": code_names.get(code, ""),
            "last_on_date": last_on_date,
            "off_date": off_date,
            "on_days": on_days,
            "first_on_date": sorted_dates[0],
            "is_active": is_active,
        })

    # 排序：在榜天数降序，最晚上榜日期降序
    results.sort(key=lambda x: (-x["on_days"], x["last_on_date"]))

    _history_cache[cache_key] = (datetime.now(), results)
    return results
