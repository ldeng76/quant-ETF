"""
调度系统数据库操作层

复用 dashboard/db.py 的 psycopg2 同步连接接口。
表：scheduler_users / user_pools / strategy_rankings / job_runs
"""
import json
from datetime import datetime
from typing import List, Dict, Any

from loguru import logger

from quant_etf.dashboard.db import get_pg_conn

# 所有 K 线周期
ALL_INTERVALS = ("1d", "60m", "30m", "15m")
# 所有池子类型
ALL_POOL_TYPES = ("etf", "stock")


# ============================================================
# 用户 & 池子
# ============================================================

def get_all_users(enabled_only: bool = True) -> List[Dict[str, Any]]:
    """返回所有用户列表。"""
    sql = "SELECT id, name, enabled, created_at FROM scheduler_users"
    if enabled_only:
        sql += " WHERE enabled = TRUE"
    sql += " ORDER BY id"
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in rows]


def get_user_pool(user_id: int, pool_type: str) -> List[str]:
    """返回用户指定类型私有证券池。返回空列表表示无私有池（只用公共池）。"""
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT codes FROM user_pools
                WHERE user_id = %s AND pool_type = %s AND enabled = TRUE
                """,
                (user_id, pool_type),
            )
            row = cur.fetchone()
            if row is None:
                return []
            # codes 存为 JSONB，Python 读取后是 list
            codes = row[0]
            if isinstance(codes, list):
                return codes
            # 防御：JSONB 可能返回为 str
            return json.loads(codes) if isinstance(codes, str) else []


def upsert_user_pool(user_id: int, pool_type: str, codes: List[str]) -> None:
    """upsert 用户私有证券池（追加/覆盖模式）。"""
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_pools (user_id, pool_type, codes, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (user_id, pool_type)
                    DO UPDATE SET codes = EXCLUDED.codes,
                                  enabled = TRUE,
                                  updated_at = NOW()
                """,
                (user_id, pool_type, json.dumps(codes)),
            )
        conn.commit()


# ============================================================
# 策略结果
# ============================================================

def insert_strategy_rankings(rankings: List[Dict[str, Any]]) -> None:
    """
    批量插入策略排名结果。

    ranking dict 字段：
        user_id, interval_, task_type, code, score, rank_pos,
        p60, p20, p10, p5, volume_ratio, trend_ok
    """
    if not rankings:
        return
    computed_at = datetime.now()
    rows = [
        (
            r["user_id"],
            r["interval_"],
            r["task_type"],
            r["code"],
            r["score"],
            r["rank_pos"],
            r.get("p60"),
            r.get("p20"),
            r.get("p10"),
            r.get("p5"),
            r.get("volume_ratio"),
            r.get("trend_ok"),
            computed_at,
        )
        for r in rankings
    ]
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO strategy_rankings
                    (user_id, interval_, task_type, code, score, rank_pos,
                     p60, p20, p10, p5, volume_ratio, trend_ok, computed_at)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                rows,
            )
        conn.commit()
    logger.debug(f"Inserted {len(rankings)} ranking rows")


def get_latest_rankings(
    user_id: int,
    interval_: str,
    task_type: str,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """查询某用户最近一次计算的排名结果。"""
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""  # noqa: F541 (SQL, not f-string)
                SELECT * FROM strategy_rankings
                WHERE user_id = %s AND interval_ = %s AND task_type = %s
                ORDER BY computed_at DESC, rank_pos ASC
                LIMIT %s
                """,
                (user_id, interval_, task_type, limit),
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in rows]


# ============================================================
# Job 运行记录
# ============================================================

def insert_job_run(
    interval_: str,
    started_at: datetime,
) -> int:
    """插入一条 job_runs 记录，返回新记录 id。"""
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO job_runs (interval_, started_at, status)
                VALUES (%s, %s, 'running')
                RETURNING id
                """,
                (interval_, started_at),
            )
            row = cur.fetchone()
        conn.commit()
        return row[0] if row else 0


def update_job_run(
    run_id: int,
    finished_at: datetime,
    status: str,
    user_count: int | None = None,
    security_count: int | None = None,
    error_msg: str | None = None,
) -> None:
    """更新 job_runs 记录（结束时调用）。"""
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE job_runs
                SET finished_at = %s,
                    status = %s,
                    user_count = COALESCE(%s, user_count),
                    security_count = COALESCE(%s, security_count),
                    error_msg = %s
                WHERE id = %s
                """,
                (finished_at, status, user_count, security_count, error_msg, run_id),
            )
        conn.commit()


def get_recent_job_runs(interval_: str | None = None, limit: int = 10) -> List[Dict[str, Any]]:
    """查询最近 job 运行记录。"""
    sql = "SELECT * FROM job_runs"
    params: list = []
    if interval_:
        sql += " WHERE interval_ = %s"
        params.append(interval_)
    sql += " ORDER BY started_at DESC LIMIT %s"
    params.append(limit)
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in rows]