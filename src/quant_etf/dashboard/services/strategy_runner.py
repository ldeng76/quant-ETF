"""
异步策略执行器
通过后台线程调用 TaskRegistry 执行策略
"""
import asyncio
import json
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
from ..db import query, execute, execute_many

_executor = ThreadPoolExecutor(max_workers=6)
_running_tasks: dict[str, dict] = {}
# 名称映射缓存（从 stock_code_name.json 加载）
_name_map_cache: dict[str, str] | None = None


def _get_csv_path(strategy_name: str, bar_interval: str, date_str: str) -> Path:
    """根据周期构造 CSV 路径。日线保持原路径，分钟周期加后缀。"""
    if bar_interval == "1d":
        return PROJECT_ROOT / "data" / "results" / date_str / f"{strategy_name}.csv"
    else:
        return PROJECT_ROOT / "data" / "results" / date_str / f"{strategy_name}_{bar_interval}.csv"


def _load_name_map() -> dict[str, str]:
    """从 data/meta/stock_code_name.json 加载 code→name 映射，用于修正 CSV 中的 Unknown"""
    global _name_map_cache
    if _name_map_cache is not None:
        return _name_map_cache
    meta_path = PROJECT_ROOT / "data" / "meta" / "stock_code_name.json"
    if meta_path.exists():
        try:
            items = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(items, list):
                _name_map_cache = {
                    item["code"]: item["name"]
                    for item in items if "code" in item and "name" in item
                }
                return _name_map_cache
        except Exception as e:
            logger.warning(f"Failed to load name map: {e}")
    _name_map_cache = {}
    return _name_map_cache


def _fix_names(records: list[dict]) -> None:
    """用 stock_code_name.json 修正 records 中 name 为 Unknown 或空的条目"""
    name_map = _load_name_map()
    if not name_map:
        return
    for r in records:
        code = r.get("code", "")
        name = r.get("name", "")
        if code in name_map and (not name or name == "Unknown"):
            r["name"] = name_map[code]



def _build_column_labels(bar_interval: str) -> dict[str, str]:
    """根据周期构建列标题映射"""
    from quant_etf.bar_interval import get_interval
    bi = get_interval(bar_interval)
    return {
        "p60": bi.unit_label(60),
        "p20": bi.unit_label(20),
        "p10": bi.unit_label(10),
        "p5": bi.unit_label(5),
    }

async def run_strategy(strategy_name: str, run_id: Optional[str] = None, bar_interval: str = "1d") -> str:
    """异步执行策略，返回 run_id"""
    if run_id is None:
        run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    _running_tasks[run_id] = {
        "status": "running",
        "strategy": strategy_name,
        "bar_interval": bar_interval,
        "started_at": datetime.now().isoformat(),
        "progress": 0,
        "column_labels": _build_column_labels(bar_interval),
    }
    # 持久化：插入执行记录
    execute("""
        INSERT INTO strategy_runs (run_id, strategy, bar_interval, status, started_at, created_by)
        VALUES (%s, %s, %s, 'running', %s, 'scheduler')
    """, [run_id, strategy_name, bar_interval, datetime.now().isoformat()])
    # 在进入线程前捕获主事件循环，供 SSE 广播使用
    main_loop = asyncio.get_event_loop()
    def _execute():
        try:
            intraday = is_intraday()
            if intraday:
                logger.info(f"Strategy {strategy_name} running in INTRADAY mode")
            task = TaskRegistry.get_task(strategy_name, intraday=intraday, bar_interval=bar_interval)
            if not task:
                raise ValueError(f"Unknown strategy: {strategy_name}")
            _running_tasks[run_id]["title"] = getattr(task, "title", strategy_name)
            _running_tasks[run_id]["progress"] = 30
            task.initialize()
            _running_tasks[run_id]["progress"] = 50
            task.run()
            _running_tasks[run_id]["task_ref"] = task
            # 存储大盘评估结果
            _running_tasks[run_id]["task_ref"] = task

            # 存储大盘评估结果
            regime = getattr(task, "_regime", None)
            if regime:
                from quant_etf.conf import INDEX_WEIGHTS
                index_names = {"510050": "沪深300", "159919": "深证300", "159949": "创业板50", "588000": "科创50"}
                _running_tasks[run_id]["market_regime"] = {
                    "market_score": round(regime.market_score * 100, 2),
                    "median_score": round(regime.median_score * 100, 2),
                    "mode": regime.mode,
                    "top_n": regime.top_n,
                    "risk_discount": regime.risk_discount,
                    "index_scores": {
                        index_names.get(c, c): round(s * 100, 2)
                        for c, s in regime.index_scores.items()
                    },
                }

            _running_tasks[run_id]["progress"] = 80

            # 读取结果
            today = datetime.now().strftime("%Y-%m-%d")
            csv_path = _get_csv_path(strategy_name, bar_interval, today)
            if csv_path.exists():
                df = pd.read_csv(csv_path, dtype={"code": str})
                records = df.to_dict("records")
                # 过滤无效行
                records = [r for r in records if r.get("code")]
                _fix_names(records)
            else:
                records = []
            _running_tasks[run_id]["result"] = records
            _running_tasks[run_id]["count"] = len(records)

            _running_tasks[run_id]["status"] = "complete"
            # column_labels already set at task init
            _running_tasks[run_id]["progress"] = 100
            _running_tasks[run_id]["finished_at"] = datetime.now().isoformat()
            # 持久化：更新执行记录 + 写入结果明细
            _persist_run_results(run_id, strategy_name, bar_interval, "complete",
                                 records, _running_tasks[run_id].get("market_regime"))
            # 清除历史汇总缓存，确保新结果立即可见

            # --- 告警引擎集成：对比上次结果，触发告警 ---
            try:
                records_for_alert = _running_tasks[run_id].get("result", [])
                prev_records = _load_prev_strategy_result(strategy_name, bar_interval)
                if records_for_alert and prev_records:
                    triggered = alert_engine.check(records_for_alert, prev_records)
                    if triggered:
                        alert_engine.save_alerts(triggered)
                        # SSE 广播告警事件
                        for a in triggered:
                            try:
                                if main_loop.is_running():
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
                                logger.warning(f"Failed to broadcast alert SSE: {sse_err}")
                        logger.info(f"Alert engine triggered {len(triggered)} alerts for {strategy_name}")
            except Exception as alert_err:
                logger.warning(f"Alert engine check failed for {strategy_name}: {alert_err}")

        except Exception as e:
            error_msg = str(e)
            # 将常见技术错误转化为用户友好的中文提示
            if "No minute data" in error_msg and "minute-backfill" in error_msg:
                error_msg = "数据库中没有分钟K线数据。请先在终端运行 `quant-etf minute-backfill` 命令回填分钟数据，然后再试。"
            elif "Unknown strategy" in error_msg:
                error_msg = f"未知策略: {error_msg.split(':')[-1].strip()}"

            logger.error(f"Strategy {strategy_name} failed: {e}")
            _running_tasks[run_id]["status"] = "error"
            _running_tasks[run_id]["error"] = error_msg
            _running_tasks[run_id]["progress"] = -1
            # 持久化：更新执行记录为失败状态
            _persist_run_results(run_id, strategy_name, bar_interval, "error",
                                 None, None, error_msg)
            # 通过 SSE 广播错误事件（与 scheduler.py 一致）
            try:
                if main_loop.is_running():
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
                else:
                    # 事件循环未运行（已关闭），跳过 SSE 广播
                    pass
            except Exception as sse_err:
                logger.warning(f"Failed to broadcast SSE error: {sse_err}")

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(_executor, _execute)
    return run_id


def _load_prev_strategy_result(strategy_name: str, bar_interval: str = "1d") -> list[dict]:
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
            csv_path = _get_csv_path(strategy_name, bar_interval, d.name)
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


# ============================================================
# 策略结果 JSON 导出（小程序用）
# ============================================================

# 百分比字段列表
_PERCENT_FIELDS = ("p60", "p20", "p10", "p5", "target_weight", "score",
                   "drawdown_from_120d_high", "bounce_from_20d_low")


def _parse_value(val):
    """解析 CSV 中的值：百分比字符串转 float，NaN/空转 None"""
    if val is None:
        return None
    if isinstance(val, float):
        import math
        if math.isnan(val):
            return None
        return val
    s = str(val).strip()
    if not s or s.lower() in ("nan", "none", ""):
        return None
    if s.endswith("%"):
        try:
            return float(s.rstrip("%"))
        except ValueError:
            return None
    # 布尔字符串
    if s in ("True", "true"):
        return True
    if s in ("False", "false"):
        return False
    # 普通数字
    try:
        return float(s)
    except ValueError:
        return val


def get_today_results(strategy_name: str, bar_interval: str = "1d") -> dict:
    """读取最近可用的策略结果 CSV，返回解析后的 JSON 数据。

    优先取今天，没有则向前最多查 7 天。
    """
    results_dir = PROJECT_ROOT / "data" / "results"
    if not results_dir.exists():
        return {"date": None, "strategy": strategy_name, "count": 0, "records": []}

    today = datetime.now().date()
    for i in range(8):  # 0=今天, 最多往前7天
        target = today - timedelta(days=i)
        date_str = target.strftime("%Y-%m-%d")
        csv_path = _get_csv_path(strategy_name, bar_interval, date_str)
        if csv_path.exists():
            break
    else:
        return {"date": None, "strategy": strategy_name, "count": 0, "records": []}

    df = pd.read_csv(csv_path, dtype={"code": str})
    records = [r for r in df.to_dict("records") if r.get("code")]
    _fix_names(records)

    # 解析百分比字段
    for r in records:
        for key in _PERCENT_FIELDS:
            if key in r:
                r[key] = _parse_value(r[key])

    return {
        "date": date_str,
        "strategy": strategy_name,
        "count": len(records),
        "records": records,
    }


def get_sell_signals(strategy_name: str = "etf", bar_interval: str = "1d") -> list[dict]:
    """检测今日掉榜的标的，返回卖出信号列表。

    严格模式：只在最新结果日掉榜的标的才触发卖出信号。
    """
    summary = get_history_summary(
        strategy_name=strategy_name, days=30, auto_backfill=False, bar_interval=bar_interval
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


def clear_history_summary_cache(strategy_name: str = "etf", bar_interval: str = "1d") -> None:
    """清除指定策略的历史汇总缓存"""
    keys_to_remove = [k for k in _history_cache if k.startswith(f"{strategy_name}_") and f"_{bar_interval}" in k]
    for k in keys_to_remove:
        del _history_cache[k]


def get_history_summary(
    strategy_name: str = "etf",
    days: int = 30,
    auto_backfill: bool = True,
    bar_interval: str = "1d"
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

    cache_key = f"{strategy_name}_{days}_{bar_interval}"
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
        csv_path = _get_csv_path(strategy_name, bar_interval, date_dir.name)
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

    _fix_names(results)
    _history_cache[cache_key] = (datetime.now(), results)
    return results

def get_drilldown_data(run_id: str, code: str, field: str) -> dict:
    """
    获取某标的在某周期字段的累计涨幅序列（用于弹窗图表）
    :param run_id: 策略执行 ID
    :param code: 标的代码
    :param field: 字段名 p60/p20/p10/p5
    :return: {code, field, label, dates, values}
    """
    if run_id not in _running_tasks:
        raise ValueError(f"Run {run_id} not found")

    task_info = _running_tasks[run_id]
    if task_info.get("status") != "complete":
        raise ValueError(f"Run {run_id} not complete")

    task = task_info.get("task_ref")
    if task is None or not hasattr(task, "_loaded_data") or not task._loaded_data:
        raise ValueError("Drilldown data not available")

    if code not in task._loaded_data:
        raise ValueError(f"Code {code} not in loaded data")

    # p60 -> 60 days, p20 -> 20 days, etc.
    day_map = {"p60": 60, "p20": 20, "p10": 10, "p5": 5}
    if field not in day_map:
        raise ValueError(f"Invalid field: {field}")
    days = day_map[field]

    from quant_etf.bar_interval import get_interval
    bi = get_interval(task._bar_interval)
    n_bars = days

    df = task._loaded_data[code]
    if df.empty or len(df) < n_bars + 1:
        # 不足则取全部
        slice_df = df.copy()
    else:
        slice_df = df.iloc[-(n_bars + 1):].copy()

    # 累计涨幅 + 原始价格
    base_price = slice_df.iloc[0]["close"]
    cum_ret = ((slice_df["close"] - base_price) / base_price).tolist()
    prices = slice_df["close"].round(4).tolist()

    # 日期/时间标签（去掉年份）
    if "date" in slice_df.columns:
        raw = slice_df["date"].astype(str)
    else:
        raw = slice_df.index.astype(str)
    fmt = "%m-%d %H:%M" if bi.is_daily is False else "%m-%d"
    dates = pd.to_datetime(raw).strftime(fmt).tolist()

    label = bi.unit_label(days)

    return {
        "code": code,
        "field": field,
        "label": label,
        "dates": dates,
        "values": cum_ret,
    }
def _persist_run_results(run_id: str, strategy: str, bar_interval: str,
                        status: str, result: list[dict] | None = None,
                        market_regime: dict | None = None, error_msg: str | None = None):
    """持久化执行记录和结果到数据库"""
    # UPDATE 执行记录
    finished_at = datetime.now().isoformat()
    execute("""
        UPDATE strategy_runs
        SET status = %s,
            finished_at = %s,
            result_count = %s,
            market_regime = %s,
            error_msg = %s
        WHERE run_id = %s
    """, [status, finished_at,
          len(result) if result else 0,
          json.dumps(market_regime) if market_regime else None,
          error_msg, run_id])
    # 批量写入结果明细
    if result and status == "complete":
        def _to_float(val) -> float | None:
            """将 '5.90%' 或 '5.90' 转换为 float"""
            if val is None:
                return None
            s = str(val).strip().rstrip("%")
            return float(s) if s else None
        params = [
            [run_id, r.get("code", ""), r.get("name", ""),
             _to_float(r.get("p60")), _to_float(r.get("p20")),
             _to_float(r.get("p10")), _to_float(r.get("p5")),
             _to_float(r.get("target_weight")), r.get("interval", ""), r.get("date", "")]
            for r in result
        ]
        execute_many("""
            INSERT INTO strategy_run_results
                (run_id, code, name, p60, p20, p10, p5, target_weight, interval_, date_)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, params)