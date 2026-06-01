"""
调度引擎：多用户并行策略执行核心

职责：
1. 管理 public pool 映射 (pool_type → codes)
2. 合并 public + user-private pools
3. 计算所有用户证券 union
4. Job 流程：prefetch → 并行 user 执行 → 写入结果

注意：scheduler_db 延迟导入，避免测试时触发 asyncpg 依赖。
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from threading import Timer
from typing import Dict, List

from loguru import logger

from quant_etf.conf import ETF_POOL
from quant_etf.pool_loader import get_stock_pool
from quant_etf.scheduler_cache import get_cache
from quant_etf.tasks import TaskRegistry


# ============================================================
# Module-level constants
# ============================================================

# Pool type → default codes (public pools, shared by all users)
# stock / mid_term 留空：Task 会自己从通达信动态读取
PUBLIC_POOLS: Dict[str, List[str]] = {
    "etf": list(ETF_POOL),
    "stock": [],
    "mid_term": [],
}

ALL_POOL_TYPES = ("etf", "stock", "mid_term")


# ============================================================
# Pool helpers
# ============================================================

def get_user_codes(user_id: int, pool_type: str) -> List[str]:
    """
    返回用户的私有证券池（仅 private）。
    public 部分由 Task.get_pool() 动态从通达信读取，不在这里合并。
    """
    from quant_etf.scheduler_db import get_user_pool

    private_codes = get_user_pool(user_id, pool_type)
    return list(dict.fromkeys(private_codes)) if private_codes else []


def get_all_codes(interval: str) -> set[str]:
    """
    构建所有用户在所有 pool_type 下的证券 union。
    用于 prefetch 阶段。
    """
    from quant_etf.scheduler_db import get_all_users

    users = get_all_users()
    all_codes: set[str] = set()
    # 先把公共池的全集加进去（stock/mid_term 动态读取）
    for pool_type in ALL_POOL_TYPES:
        all_codes.update(get_stock_pool(pool_type))
    # 再加各用户的私有池
    for user in users:
        for pool_type in ALL_POOL_TYPES:
            all_codes.update(get_user_codes(user["id"], pool_type))
    return all_codes


# ============================================================
# Single-user strategy runner
# ============================================================

def _score_to_rank_pos(index: int, total: int) -> int:
    """将 0-based index 转为 1-based rank_pos。"""
    return index + 1


def run_single_user_strategy(user: dict, interval: str) -> List[dict]:
    """
    为单个用户在指定 interval 运行所有 3 个策略。

    Args:
        user: 用户字典，包含 {"id": int, "name": str, ...}
        interval: K 线周期

    Returns:
        所有策略 ranking 字典列表，准备 upsert
    """
    user_id = user["id"]
    username = user.get("name", str(user_id))

    # 1. 获取该用户的私有证券池
    etf_private = get_user_codes(user_id, "etf")
    stock_private = get_user_codes(user_id, "stock")
    mid_private = get_user_codes(user_id, "mid_term")

    # 2. 构建 per-task pool override（仅在有私有池时注入）
    override_pool: Dict[str, List[str]] = {}
    if etf_private:
        # 用户私有 ETF 池需要和公共 ETF 池合并后覆盖
        override_pool["etf"] = list(dict.fromkeys(list(ETF_POOL) + etf_private))
    if stock_private:
        # 有私有池时：动态公共池 + 私有池
        override_pool["stock"] = list(dict.fromkeys(get_stock_pool("stock") + stock_private))
    if mid_private:
        override_pool["mid_term"] = list(dict.fromkeys(get_stock_pool("mid_term") + mid_private))

    rankings: List[dict] = []
    task_names = ["etf", "short", "mid"]

    for task_name in task_names:
        task = TaskRegistry.get_task(task_name, bar_interval=interval)
        if task is None:
            logger.warning(f"[Engine] Unknown task: {task_name}, skipping")
            continue

        # 注入用户私有池（BaseTask.get_pool() 会检查 _override_pool）
        task._override_pool = override_pool

        try:
            task.run()
        except Exception as e:
            logger.exception(f"[Engine] User {username} task {task_name} failed: {e}")
            continue

        # 3. 转换 task 结果为 ranking 字典
        if not hasattr(task, "_results") or not task._results:
            logger.debug(f"[Engine] No results for user {username} task {task_name}")
            continue

        results = task._results
        total = len(results)

        for rank_idx, result in enumerate(results, start=1):
            rank_pos_1based = _score_to_rank_pos(rank_idx - 1, total)

            if task_name == "etf":
                rankings.append({
                    "user_id": user_id,
                    "interval_": interval,
                    "task_type": "etf",
                    "code": result.code,
                    "score": result.score,
                    "rank_pos": rank_pos_1based,
                    "p60": result.p60,
                    "p20": result.p20,
                    "p10": result.p10,
                    "p5": result.p5,
                })
            elif task_name == "short":
                rankings.append({
                    "user_id": user_id,
                    "interval_": interval,
                    "task_type": "short",
                    "code": result.code,
                    "score": result.score,
                    "rank_pos": rank_pos_1based,
                    "p60": result.p60,
                    "p20": result.p20,
                    "p10": result.p10,
                    "p5": result.p5,
                    "volume_ratio": result.volume_ratio_1d_20d,
                    "trend_ok": result.trend_ok,
                })
            elif task_name == "mid":
                rankings.append({
                    "user_id": user_id,
                    "interval_": interval,
                    "task_type": "mid_term",
                    "code": result.code,
                    "score": result.score,
                    "rank_pos": rank_pos_1based,
                    "p20": result.p20,
                    "p10": result.p10,
                    "p5": result.p5,
                    "volume_ratio": result.volume_ratio_1d_20d,
                    "trend_ok": result.trend_ok,
                })

    logger.info(f"[Engine] User {username} -> {len(rankings)} ranking records")
    return rankings


# ============================================================
# Job runners
# ============================================================

def run_job_for_interval(interval: str, run_id: int) -> None:
    """
    执行单个周期的 Job 流程：

    1. 获取全局证券并集
    2. 预热共享缓存
    3. 获取所有用户
    4. ThreadPoolExecutor 并行执行用户策略
    5. 批量写入 ranking 结果
    """
    from quant_etf.scheduler_db import (
        get_all_users,
        insert_strategy_rankings,
        update_job_run,
    )

    logger.info(f"[Engine] Job started: interval={interval} run_id={run_id}")
    t0 = datetime.now()

    try:
        # 1. 获取全局证券并集
        all_codes = get_all_codes(interval)

        # 2. 预热缓存
        cache = get_cache()
        cached_count = cache.prefetch(all_codes, interval)
        logger.info(f"[Engine] Cache prefetched {cached_count}/{len(all_codes)} securities")

        # 3. 获取所有用户
        users = get_all_users()
        if not users:
            logger.warning("[Engine] No enabled users found, skipping")
            return

        # 4. 并行执行（按用户隔离）
        all_rankings: List[dict] = []
        with ThreadPoolExecutor(max_workers=min(len(users), 8)) as executor:
            futures = {
                executor.submit(run_single_user_strategy, user, interval): user
                for user in users
            }
            for future in as_completed(futures):
                user = futures[future]
                try:
                    rankings = future.result()
                    all_rankings.extend(rankings)
                except Exception as e:
                    logger.exception(f"[Engine] User {user.get('name')} failed: {e}")

        # 5. 批量写入
        if all_rankings:
            insert_strategy_rankings(all_rankings)
            logger.info(f"[Engine] Wrote {len(all_rankings)} ranking records")

        elapsed = (datetime.now() - t0).total_seconds()
        update_job_run(run_id, datetime.now(), "success", len(users), len(all_codes))
        logger.info(f"[Engine] Job finished: interval={interval} ({elapsed:.1f}s, {len(all_rankings)} rankings)")

    except Exception:
        logger.exception(f"[Engine] Job error: interval={interval}")
        update_job_run(run_id, datetime.now(), "failed", error_msg="Job execution failed")
        raise


def run_job_for_interval_with_timeout(
    interval: str, run_id: int, timeout: float = 150.0
) -> None:
    """
    带超时保护的 Job 执行（Windows 兼容，使用 threading.Timer）。
    超时后强制结束进程。
    """
    logger.info(f"[Engine] Job with timeout: interval={interval} timeout={timeout}s")

    def timeout_fire() -> None:
        logger.error("[Engine] Timeout reached, exiting process...")
        import sys
        sys.exit(1)

    timer = Timer(timeout, timeout_fire)
    timer.daemon = True
    timer.start()

    try:
        run_job_for_interval(interval, run_id)
    except Exception:
        raise
    finally:
        timer.cancel()
        logger.debug(f"[Engine] Job finished within timeout for interval={interval}")