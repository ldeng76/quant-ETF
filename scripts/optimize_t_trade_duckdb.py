"""做T策略「选股 + 参数优化」—— DuckDB 版。

与 optimize_t_trade.py 等价的产物；I/O / 切片 / 15m 重采样 / worker 通讯统一走 DuckDB + parquet。

瓶颈来源（原版）：
  - load_pool_bars：70 只标的 → 70 次 PG 顺序查询
  - 候选 bars pickle：字典序列化后 ~150 MB / 70 标的 × 全区间
  - 多进程 worker：每次启动反序列化整份 bars（per-call floor cost）
  - 15m 重采样：循环内 pandas resample（仅 primary_level=="15m" 触发）

DuckDB 版换掉什么：
  - 全池 minute bars → 一次性 parquet（data/cache/ttrade_minute_bars_5m.parquet）；
    后续所有 stage 都通过 DuckDB read_parquet 按 code/time 即时切片
  - 15m 重采样 → 一次性 DuckDB time_bucket 预计算（按需触发）
  - worker payload：每进程一只 DuckDB 连接 + (parquet, 时间窗, codes)；按 code 即时 query，
    跳过整份 bars 反序列化。热循环内没有 pickle round-trip

保持不变：
  - run_backtest / params / account / engine 全部原样（pandas 消费）
  - 各 stage 输出的 CSV / JSON schema 字段（与原版可直接 diff）
  - CLI 表面（--stage / --codes / --start / --is-start / --rounds / --dims ...）

用法：
  uv run python scripts/optimize_t_trade_duckdb.py --stage rank
  uv run python scripts/optimize_t_trade_duckdb.py --stage search --codes <csv>
  uv run python scripts/optimize_t_trade_duckdb.py --stage holdout
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import duckdb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_etf.conf import ETF_POOL  # noqa: E402
from quant_etf.minute_collector import query_minute_data  # noqa: E402
from quant_etf.t_trade.backtest import (  # noqa: E402
    _avg_daily_amp,
    closed_trades,
    run_backtest,
    write_outputs,
)
from quant_etf.t_trade.params import TTradeParams  # noqa: E402

CACHE_DIR = ROOT / "data" / "cache"
OUT_DIR = ROOT / "data" / "results" / "2026-09-28" / "t_trade"
BASE_PARAMS = (
    OUT_DIR / "params-realistic.json"
)  # 万二佣金 + 5bp 单边滑点（真实成本）

# 全池 5m parquet：与 bench_parquet_duckdb.py / minute_collector.py 口径一致
BARS_PARQUET = CACHE_DIR / "ttrade_minute_bars_5m.parquet"
# 15m 预计算 parquet：按主级别 15m 时由 stage_search/final 按需重建
BARS_15M_PARQUET = CACHE_DIR / "ttrade_minute_bars_15m.parquet"

# ---------------------------------------------------------------- 数据层


def export_pool_bars(codes: list[str], start: Optional[str],
                     end: Optional[str]) -> int:
    """PG → parquet（一次性，全池共享）。psycopg2 返回 Decimal，
    先转 float 否则 DuckDB 会推断成 DECIMAL(11,2) 触发期溢出。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    frames = []
    for code in codes:
        df = query_minute_data(code, start=start, end=end, limit=500_000)
        if df is not None and len(df):
            df = (df.reset_index().sort_values("time").reset_index(drop=True)
                  .assign(code=code))
            frames.append(df[["code", "time", "open", "high", "low",
                              "close", "volume", "amount"]])
    if not frames:
        return 0
    all_df = pd.concat(frames, ignore_index=True)
    for col in ("open", "high", "low", "close", "volume", "amount"):
        all_df[col] = all_df[col].astype(float)
    con = duckdb.connect()
    con.register("all_df", all_df)
    con.execute(
        f"COPY (SELECT * FROM all_df ORDER BY code, time) "
        f"TO '{BARS_PARQUET.as_posix()}' (FORMAT parquet)"
    )
    return len(all_df)


def ensure_pool_bars(codes: list[str], start: Optional[str],
                     end: Optional[str]) -> duckdb.DuckDBPyConnection:
    """保证 parquet 存在并包含请求的 code 子集；返回绑定 parquet 的 DuckDB 连接。

    不直接返回 dict[code, df]——callsite 用 DuckDB SQL 按 code / time 即时切片，
    用完即弃，worker 端彻底跳过 150MB pickle round-trip。
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    need_export = True
    if BARS_PARQUET.exists():
        # 完整性检查：parquet 内 code 集合是否覆盖请求的 codes
        con = duckdb.connect()
        have = set(con.execute(
            f"SELECT DISTINCT code FROM read_parquet('{BARS_PARQUET.as_posix()}')"
        ).fetchdf()["code"].astype(str).tolist())
        missing = [c for c in codes if c not in have]
        if not missing:
            need_export = False
            print(f"[data] 命中 {BARS_PARQUET.name}，覆盖全部 {len(codes)} 个 code",
                  flush=True)
        else:
            print(f"[data] {BARS_PARQUET.name} 缺 {len(missing)} 个 code → 重导出",
                  flush=True)
    if need_export:
        t0 = time.time()
        n = export_pool_bars(codes, start, end)
        print(f"[data] PG → parquet {n:,} 行 / {len(codes)} 个 code "
              f"{time.time()-t0:.0f}s → {BARS_PARQUET.name}", flush=True)
    con = duckdb.connect()
    return con


def fetch_bars_dict(con: duckdb.DuckDBPyConnection, codes: list[str],
                    start: Optional[str], end: Optional[str],
                    parquet_path: Path = BARS_PARQUET,
                    label: str = "5m") -> dict[str, pd.DataFrame]:
    """DuckDB 单次扫表 → pandas 切分；等同 load_pool_bars 的输出接口。"""
    con.execute("SET TimeZone = 'UTC'")
    where = ["code = ANY(?)"]
    params = [list(codes)]
    if start:
        where.append("time >= CAST(? AS TIMESTAMP)")
        params.append(_to_ts(start))
    if end:
        where.append("time <= CAST(? AS TIMESTAMP)")
        params.append(_to_ts(end))
    sql = (
        f"SELECT code, time, open, high, low, close, volume, amount "
        f"FROM read_parquet('{parquet_path.as_posix()}') "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY code, time"
    )
    df = con.execute(sql, params).df()
    if df.empty:
        return {c: pd.DataFrame() for c in codes}
    df["time"] = pd.to_datetime(df["time"])
    return {c: g.drop(columns=["code"]).reset_index(drop=True)
            for c, g in df.groupby("code", sort=False)}


def _to_ts(s: str) -> str:
    """'2024-09-09' / '2024-09-09 09:30:00' 都接受；转成 DuckDB TIMESTAMP 字面量。"""
    if " " in s:
        return s if len(s) > 10 else s + " 00:00:00"
    return s + " 00:00:00"


def fetch_one(con: duckdb.DuckDBPyConnection, code: str,
              start: Optional[str], end: Optional[str],
              parquet_path: Path = BARS_PARQUET) -> pd.DataFrame:
    """单 code 拉取（worker 热循环专用，避免扫全表后再 groupby）。"""
    con.execute("SET TimeZone = 'UTC'")
    where = ["code = ?"]
    params: list = [code]
    if start:
        where.append("time >= CAST(? AS TIMESTAMP)")
        params.append(_to_ts(start))
    if end:
        where.append("time <= CAST(? AS TIMESTAMP)")
        params.append(_to_ts(end))
    sql = (
        f"SELECT time, open, high, low, close, volume, amount "
        f"FROM read_parquet('{parquet_path.as_posix()}') "
        f"WHERE {' AND '.join(where)} ORDER BY time"
    )
    df = con.execute(sql, params).df()
    if df.empty:
        return df
    df["time"] = pd.to_datetime(df["time"])
    return df.reset_index(drop=True)


# 15m 重采样：A 股午休 11:30-13:00 不跨时段聚合；与 minute_resampler.py 一致，
# 用 DuckDB time_bucket 窗口分桶，AM/PM 独立计数 → 重采样无间隙、无错位。
_AGG_15M_SQL = """
WITH ordered AS (
    SELECT code, time, open, high, low, close, volume, amount,
           CASE WHEN EXTRACT(HOUR FROM time) < 12 THEN 0 ELSE 1 END AS session,
           ROW_NUMBER() OVER (
               PARTITION BY code, DATE(time),
                            CASE WHEN EXTRACT(HOUR FROM time) < 12 THEN 0 ELSE 1 END
               ORDER BY time
           ) - 1 AS bar_seq
    FROM {src}
    WHERE code = ANY(?)
      {time_clause}
),
grouped AS (
    SELECT code, MAX(time) AS time,
           FIRST(open ORDER BY time) AS open,
           MAX(high) AS high,
           MIN(low) AS low,
           LAST(close ORDER BY time) AS close,
           SUM(volume) AS volume,
           SUM(amount) AS amount
    FROM ordered
    GROUP BY code, DATE(time), session, bar_seq // 3
)
SELECT code, time, open, high, low, close, volume, amount
FROM grouped
ORDER BY code, time
"""


def build_15m_parquet(codes: list[str], start: Optional[str],
                      end: Optional[str]) -> None:
    """由 5m parquet 一次性聚合出 15m parquet（AM/PM session 拆分，按通达信口径）。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    clauses = ["code = ANY(?)"]
    params: list = [list(codes)]
    if start:
        clauses.append("time >= CAST(? AS TIMESTAMP)")
        params.append(_to_ts(start))
    if end:
        clauses.append("time <= CAST(? AS TIMESTAMP)")
        params.append(_to_ts(end))
    time_clause = " AND " + " AND ".join(clauses[1:]) if len(clauses) > 1 else ""
    # src 只给纯表引用：code 与时间过滤由 _AGG_15M_SQL 的 WHERE 子句统一提供。
    # 原实现在 src 里已带 WHERE，模板又接了一个 WHERE code = ANY(?)，拼出双 WHERE
    # 直接 ParserException。
    src = f"read_parquet('{BARS_PARQUET.as_posix()}')"
    sql = _AGG_15M_SQL.format(src=src, time_clause=time_clause)
    con.execute(
        f"COPY ({sql}) TO '{BARS_15M_PARQUET.as_posix()}' (FORMAT parquet)",
        params,
    )


def ensure_15m_bars(codes: list[str], start: Optional[str],
                    end: Optional[str]) -> None:
    """按 codes / 时间窗生成或复用 15m parquet。

    复用判定必须同时看 code 集合与时间覆盖：只比 code 会让「上次跑的是更窄的
    时间窗」被误判为命中，worker 随后读到残缺区间且不报错。
    """
    if BARS_15M_PARQUET.exists():
        con = duckdb.connect()
        stats = con.execute(
            f"SELECT count(DISTINCT code) AS c, min(time) AS t0, max(time) AS t1 "
            f"FROM read_parquet('{BARS_15M_PARQUET.as_posix()}')"
        ).fetchdf().iloc[0]
        have = set(con.execute(
            f"SELECT DISTINCT code FROM read_parquet('{BARS_15M_PARQUET.as_posix()}')"
        ).fetchdf()["code"].astype(str).tolist())
        covers = all(c in have for c in codes)
        if covers and start:
            covers = pd.Timestamp(stats["t0"]) <= pd.Timestamp(_to_ts(start))
        if covers and end:
            covers = pd.Timestamp(stats["t1"]) >= pd.Timestamp(_to_ts(end))
        if covers:
            print(f"[data] 命中 {BARS_15M_PARQUET.name}（{stats['t0']} ~ "
                  f"{stats['t1']}）", flush=True)
            return
        print(f"[data] {BARS_15M_PARQUET.name} 未覆盖 code/时间窗 → 重建", flush=True)
    t0 = time.time()
    build_15m_parquet(codes, start, end)
    print(f"[data] 5m → 15m 预聚合 {time.time()-t0:.0f}s → "
          f"{BARS_15M_PARQUET.name}", flush=True)


def eligible_codes(bars: dict[str, pd.DataFrame], params: TTradeParams,
                   min_bars: int = 2000) -> list[str]:
    return sorted(
        c for c, df in bars.items()
        if len(df) >= max(min_bars, params.ma_len * 3)
    )


# ---------------------------------------------------------------- 评估层


def cost_bp(params: TTradeParams) -> float:
    return (params.commission_rate * 2 + params.slippage * 2) * 1e4


SEGMENTS = {
    "is": ("2024-09-09", "2025-12-31"),
    "oos": ("2026-01-01", "2026-09-28"),
}


def segment_excess(result, start: str, end: str) -> float:
    eq = result.equity
    if eq is None or not len(eq):
        return float("nan")
    d = pd.to_datetime(eq["date"])
    sub = eq[(d >= start) & (d <= end)]
    if len(sub) < 2:
        return float("nan")
    return float(sub["t_trade"].iloc[-1] - sub["buy_hold"].iloc[-1])


def eval_one(code: str, bars: dict, params: TTradeParams, cash: float,
             index_bars, pre_aggregated: bool = False) -> dict:
    """单标的回测 + 指标。excess=做T净值−纯持有净值。

    pre_aggregated：bars 已是主级别聚合结果，跳过主级别重采样。
    """
    df = bars.get(code)
    if df is None:
        return {"code": code, "ok": False, "excess": 0.0, "trades": 0}
    r = run_backtest([code], {code: df}, params, total_cash=cash,
                     index_bars=index_bars, pre_aggregated=pre_aggregated)
    closed = closed_trades(r)
    n = len(closed)
    excess = r.final_equity - r.final_bh_equity
    span = max((pd.Timestamp(r.end) - pd.Timestamp(r.start)).days, 1)
    years = span / 365.25
    if n:
        bp = np.array([t["profit_bp"] for t in closed], dtype=float)
        pf_arr = np.array([t["profit"] for t in closed], dtype=float)
        win_rate = float((pf_arr > 0).mean())
        gains, losses = pf_arr[pf_arr > 0].sum(), -pf_arr[pf_arr <= 0].sum()
        pf = float(gains / losses) if losses > 0 else float("inf")
        mean_bp, med_bp = float(bp.mean()), float(np.median(bp))
        std_bp = float(bp.std(ddof=1)) if n > 1 else 0.0
        t_stat = mean_bp / (std_bp / np.sqrt(n)) if std_bp > 0 else 0.0
        hold = float(np.mean([t.get("holding_bars", 0) for t in closed]))
    else:
        win_rate = pf = mean_bp = med_bp = t_stat = hold = float("nan")
    out = {
        "code": code, "ok": True, "excess": float(excess), "trades": n,
        "excess_ret_pct": excess / cash * 100,
        "ann_excess_pct": (excess / cash) / years * 100 if years else 0.0,
        "win_rate_pct": (win_rate * 100) if n else 0.0,
        "profit_factor": (pf if np.isfinite(pf) else None) if n else None,
        "mean_net_bp": mean_bp, "median_net_bp": med_bp,
        "gross_bp": mean_bp + cost_bp(params), "t_stat": t_stat,
        "avg_hold_bars": hold,
        "pos_t": sum(1 for t in closed if t.get("direction") == "正T"),
        "neg_t": sum(1 for t in closed if t.get("direction") == "反T"),
        "amp_pct": _avg_daily_amp(df) * 100,
        "bars": len(df), "days": int(pd.DatetimeIndex(df["time"]).normalize().nunique()),
    }
    for name, (s, e) in SEGMENTS.items():
        out[f"ex_{name}"] = segment_excess(r, s, e)
    return out


def eval_set(codes: list[str], bars: dict, params: TTradeParams, cash: float,
             index_bars, detail: bool = False):
    rows = [eval_one(c, bars, params, cash, index_bars) for c in codes]
    summary = summarize(rows, params)
    return (summary, pd.DataFrame(rows)) if detail else (summary, None)


def summarize(rows: list[dict], params: TTradeParams) -> dict:
    live = [r for r in rows if r.get("ok")]
    n_trades = sum(r["trades"] for r in live)
    wins = sum(r["trades"] * r["win_rate_pct"] / 100 for r in live)
    total = sum(r["excess"] for r in live)
    return {
        "codes": len(live),
        "total_excess": total,
        "trades": n_trades,
        "win_rate_pct": (wins / n_trades * 100) if n_trades else 0.0,
        "excess_per_trade": (total / n_trades) if n_trades else 0.0,
        "pos_codes": sum(1 for r in live if r["excess"] > 0),
        "round_trip_cost_bp": cost_bp(params),
    }


# ---------------------------------------------------------------- 参数空间


PARAM_GRID: dict[str, list] = {
    "rsi_pair": [(8.0, 92.0), (10.0, 90.0), (15.0, 85.0), (20.0, 80.0),
                 (25.0, 75.0)],
    "rsi_len": [2, 3, 6, 9, 14],
    "ma_len": [48, 96, 144, 240],
    "slope_th": [0.0005, 0.001, 0.002, 0.004],
    "amp_dead": [0.005, 0.008, 0.010, 0.015, 0.020],
    "dev_th": [0.005, 0.010, 0.015, 0.030],
    "max_trades_per_day": [1, 2, 3, 5, 8],
    "confirm_fractal": [True, False],
    "confirm_volume": [True, False],
    "resonance": [True, False],
    "index_filter": [True, False],
    "amp_filter": [True, False],
    "time_window": [True, False],
    "allow_offside_t": [True, False],
}

_DEFAULTS = {f.name: getattr(TTradeParams(), f.name)
             for f in dataclasses.fields(TTradeParams)}


def apply_dim(base: TTradeParams, dim: str, value) -> TTradeParams:
    if dim == "rsi_pair":
        lo, hi = value
        return dataclasses.replace(base, rsi_buy_th=lo, rsi_sell_th=hi)
    if dim in ("amp_dead",):
        return dataclasses.replace(base, amp_dead=value, amp_min=value)
    return dataclasses.replace(base, **{dim: value})


def param_desc(p: TTradeParams) -> dict:
    return {
        "rsi_buy_th": p.rsi_buy_th, "rsi_sell_th": p.rsi_sell_th,
        "rsi_len": p.rsi_len, "ma_len": p.ma_len, "slope_th": p.slope_th,
        "amp_dead": p.amp_dead, "dev_th": p.dev_th,
        "max_trades_per_day": p.max_trades_per_day,
        "confirm_fractal": p.confirm_fractal, "confirm_volume": p.confirm_volume,
        "resonance": p.resonance, "index_filter": p.index_filter,
        "amp_filter": p.amp_filter, "time_window": p.time_window,
        "allow_offside_t": p.allow_offside_t,
        "commission_rate": p.commission_rate, "slippage": p.slippage,
        "strict_eod": p.strict_eod,
    }


# ---------------------------------------------------------------- 阶段


def _load_base_params(args) -> TTradeParams:
    """基线参数：--params 指定则用它（primary_level 等由此切换），否则用真实成本默认。"""
    if getattr(args, "params", None):
        return TTradeParams.from_json(Path(args.params).read_text(encoding="utf-8"))
    return TTradeParams.from_json(BASE_PARAMS.read_text(encoding="utf-8"))


def stage_rank(args) -> None:
    """全池逐只基线排名。"""
    params = _load_base_params(args)
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        tag = f"full2y_n{len(codes)}"
    else:
        codes = list(dict.fromkeys(ETF_POOL))
        tag = "full2y"
    con = ensure_pool_bars(codes, args.start, args.end)
    bars = fetch_bars_dict(con, codes, args.start, args.end)
    index_bars = bars.get(args.index)
    bars = {c: v for c, v in bars.items() if c != args.index}
    live = eligible_codes(bars, params)

    print(f"[rank] 有效标的 {len(live)} / {len(codes)}", flush=True)
    rows = []
    for i, c in enumerate(live, 1):
        t0 = time.time()
        rows.append(eval_one(c, bars, params, args.cash, index_bars))
        r = rows[-1]
        print(f"[rank] {i:>2}/{len(live)} {c} 增量 {r['excess']:>9.2f} "
              f"笔数 {r['trades']:>4} 胜率 {r['win_rate_pct']:>5.1f}% "
              f"净bp {r['mean_net_bp']:>7.2f} 振幅 {r['amp_pct']:>5.2f}% "
              f"({time.time()-t0:.0f}s)", flush=True)

    df = pd.DataFrame(rows).sort_values("excess", ascending=False)
    out = OUT_DIR / "opt" / "rank_baseline_5m.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")

    cols = ["code", "excess", "ex_is", "ex_oos", "trades", "win_rate_pct",
            "profit_factor", "mean_net_bp", "gross_bp", "t_stat", "amp_pct",
            "avg_hold_bars", "pos_t", "neg_t", "days"]
    print(f"\n[rank] 产出 {out}")
    print(f"[rank] 摩擦 {cost_bp(params):.0f}bp/往返")
    print(df[cols].head(20).to_string(index=False))
    pos = int((df["excess"] > 0).sum())
    both = int(((df["ex_is"] > 0) & (df["ex_oos"] > 0)).sum())
    print(f"\n[rank] {pos}/{len(df)} 只全区间增量为正；"
          f"{both}/{len(df)} 只 IS 与 OOS 同为正")


# ---- 并行 ----

_PAR_WORKERS = max(2, min(8, (os.cpu_count() or 4)))
_W_CON: Optional[duckdb.DuckDBPyConnection] = None
_W_PARQUET: Path = BARS_PARQUET
_W_CASH: float = 0.0


def _init_worker(parquet_path: str, cash: float) -> None:
    """worker 进程一次性绑定 DuckDB 连接 + 时间窗不绑（每 task 各自传）。

    拿掉原版的整份 bars pickle dict：从 _W_PARQUET 即时 read_parquet WHERE code=?
    ETF_POOL 不含 000300，index_bars 始终 None——与原版 effective 行为一致。
    """
    global _W_CON, _W_PARQUET, _W_CASH
    _W_CON = duckdb.connect()
    _W_CON.execute("SET TimeZone = 'UTC'")
    _W_PARQUET = Path(parquet_path)
    _W_CASH = cash


def _eval_worker(task: tuple) -> dict:
    """(code, params_json, start, end, pre_aggregated) → 单标的回测 + 指标。"""
    code, pj, start, end, pre_agg = task
    data = json.loads(pj)
    data["buy_window"] = tuple(data["buy_window"])
    data["sell_window"] = tuple(data["sell_window"])
    params = TTradeParams(**data)
    df = fetch_one(_W_CON, code, start, end, _W_PARQUET)
    if df.empty:
        return {"code": code, "ok": False, "excess": 0.0, "trades": 0}
    return eval_one(code, {code: df}, params, _W_CASH, index_bars=None,
                    pre_aggregated=pre_agg)


def _params_json(p: TTradeParams) -> str:
    d = json.loads(p.to_json())
    d["buy_window"] = list(d["buy_window"])
    d["sell_window"] = list(d["sell_window"])
    return json.dumps(d)


def stage_search(args) -> None:
    """坐标下降参数搜索（样本内）。"""
    params = _load_base_params(args)
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    if not codes:
        raise SystemExit("--codes 必填（用 rank 结果挑选的候选子集）")

    full_pool = list(dict.fromkeys(ETF_POOL))
    con = ensure_pool_bars(full_pool, None, None)

    # IS 段切片：把 DuckDB 已能 where 切片的活，留在 SQL 这层
    is_start, is_end = args.is_start, args.is_end
    src_parquet = BARS_PARQUET
    if params.primary_level == "15m":
        ensure_15m_bars(codes, is_start, is_end)
        src_parquet = BARS_15M_PARQUET
        print(f"[search] 主级别 15m → 数据源 {src_parquet.name}"
              f"（预聚合，跳过 pandas resample）", flush=True)
    bars = fetch_bars_dict(con, codes, is_start, is_end, parquet_path=src_parquet)
    if not bars:
        raise SystemExit("IS 段切片为空，检查 --is-start/--is-end")
    print(f"[search] IS {is_start}→{is_end}，候选 {len(bars)} 只 "
          f"（{sum(len(v) for v in bars.values()):,} 根 "
          f"{params.primary_level} bar）", flush=True)

    amps = {c: _avg_daily_amp(df) for c, df in bars.items()}
    p60 = float(pd.Series(list(amps.values())).quantile(0.6))
    params = dataclasses.replace(params, amp_min=min(params.amp_min, p60),
                                 amp_dead=min(params.amp_dead, p60))
    print(f"[search] 候选 {len(bars)} 只，振幅门槛 {params.amp_min*100:.3f}%",
          flush=True)

    dims = args.dims.split(",") if args.dims else list(PARAM_GRID)
    payload_codes = list(bars.keys())
    pre_agg = params.primary_level == "15m"
    print(f"[search] 并行 worker {_PAR_WORKERS}，"
          f"数据源 {src_parquet.name}（worker 直查）", flush=True)

    history = []
    best = params
    with ProcessPoolExecutor(
        max_workers=min(len(payload_codes), _PAR_WORKERS),
        initializer=_init_worker,
        initargs=(str(src_parquet), args.cash),
    ) as pool:

        def run(p: TTradeParams):
            pj = _params_json(p)
            # 单组参数 × 候选集：每个 task 是一笔单标的回测
            tasks = [(c, pj, is_start, is_end, pre_agg) for c in payload_codes]
            rows = list(pool.map(_eval_worker, tasks))
            return summarize(rows, p)

        base = run(best)
        best_score = base["total_excess"]
        print(f"[search] 基线增量 {best_score:.0f} 元 / {base['trades']} 笔 "
              f"胜率 {base['win_rate_pct']:.1f}%", flush=True)
        history.append({"round": 0, "dim": "baseline", "value": "-",
                        "excess": best_score, "trades": base["trades"],
                        "win_rate_pct": base["win_rate_pct"], "adopted": True})

        for rnd in range(1, args.rounds + 1):
            improved = False
            print(f"\n[search] ===== Round {rnd}/{args.rounds} =====",
                  flush=True)
            for dim in dims:
                for value in PARAM_GRID[dim]:
                    cand = apply_dim(best, dim, value)
                    if cand == best:
                        continue
                    s = run(cand)
                    mark = ""
                    if s["total_excess"] > best_score:
                        best_score, best, improved = (
                            s["total_excess"], cand, True)
                        mark = "  ← 新最优"
                    print(f"[search] {dim}={value!r:>10} → 增量 "
                          f"{s['total_excess']:>9.0f} 笔数 {s['trades']:>4} "
                          f"胜率 {s['win_rate_pct']:>5.1f}%{mark}", flush=True)
                    history.append({"round": rnd, "dim": dim,
                                    "value": repr(value),
                                    "excess": s["total_excess"],
                                    "trades": s["trades"],
                                    "win_rate_pct": s["win_rate_pct"],
                                    "pos_codes": s["pos_codes"],
                                    "adopted": mark != ""})
            print(f"[search] Round {rnd} 结束，最优 {best_score:.0f} 元",
                  flush=True)
            if not improved:
                print("[search] 本轮无改进，收敛", flush=True)
                break
        final = run(best)

    outdir = OUT_DIR / "opt"
    outdir.mkdir(parents=True, exist_ok=True)
    n_trials = len(history) - 1
    result = {
        "codes": payload_codes,
        "rounds_done": max((h["round"] for h in history), default=0),
        "trials": n_trials,
        "best_excess": best_score,
        "params": param_desc(best),
        "params_full": json.loads(_params_json(best)),
        "in_sample": {**final, "start": is_start, "end": is_end},
    }
    (outdir / "search_best.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.DataFrame(history).to_csv(outdir / "search_history.csv", index=False,
                                 encoding="utf-8-sig")
    print(f"\n[search] 共评估 {n_trials} 组参数，最优增量 {best_score:.0f} 元 "
          f"/ {final['trades']} 笔 / 胜率 {final['win_rate_pct']:.1f}%")
    print(f"[search] 最优参数：{json.dumps(param_desc(best), ensure_ascii=False)}")


def _params_from(blob: dict) -> TTradeParams:
    pj = blob.get("params_full") or blob["params"]
    return TTradeParams(**{
        k: (tuple(v) if k in ("buy_window", "sell_window") else v)
        for k, v in pj.items()
    })


def stage_nulltest(args) -> None:
    """零假设检验：从同一参数空间独立随机采样 N 组，与坐标下降的最优比。"""
    optdir = OUT_DIR / "opt"
    best_json = optdir / "search_best.json"
    if not best_json.exists():
        raise SystemExit("缺少 search_best.json，先跑 --stage search")
    blob = json.loads(best_json.read_text(encoding="utf-8"))
    observed = blob["best_excess"]
    codes = blob["codes"]

    full_pool = list(dict.fromkeys(ETF_POOL))
    con = ensure_pool_bars(full_pool, None, None)
    is_start, is_end = args.is_start, args.is_end

    base = _params_from(blob)
    base = dataclasses.replace(base, **_DEFAULTS)

    print(f"[null] IS 段随机采样 {args.samples} 组，"
          f"观测最优 {observed:.0f} 元", flush=True)

    rng = np.random.default_rng(args.seed)
    pre_agg = base.primary_level == "15m"
    samples = []
    with ProcessPoolExecutor(
        max_workers=min(len(codes), _PAR_WORKERS),
        initializer=_init_worker,
        initargs=(str(BARS_PARQUET), args.cash),
    ) as pool:
        for i in range(1, args.samples + 1):
            p = base
            for dim in PARAM_GRID:
                v = rng.choice(PARAM_GRID[dim])
                # numpy 标量（np.int64 / np.bool_）直接进 dataclass 会让
                # to_json() 的 json.dumps 抛 TypeError，必须回落原生类型
                if isinstance(v, np.ndarray):
                    v = tuple(float(x) for x in v.tolist())
                elif isinstance(v, np.generic):
                    v = v.item()
                p = apply_dim(p, dim, v)
            pj = _params_json(p)
            tasks = [(c, pj, is_start, is_end, pre_agg) for c in codes]
            s = summarize(list(pool.map(_eval_worker, tasks)), p)
            samples.append({"i": i, "excess": s["total_excess"],
                            "trades": s["trades"]})
            if i % 10 == 0:
                print(f"[null] {i}/{args.samples} 当前随机最优 "
                      f"{max(x['excess'] for x in samples):.0f} 元",
                      flush=True)

    df = pd.DataFrame(samples)
    ge = int((df["excess"] >= observed).sum())
    p_value = ge / len(df)
    df.sort_values("excess", ascending=False).to_csv(
        optdir / "nulltest_samples.csv", index=False, encoding="utf-8-sig")
    summary = {
        "observed": observed, "n_samples": len(df),
        "null_max": float(df["excess"].max()),
        "null_mean": float(df["excess"].mean()),
        "null_p95": float(df["excess"].quantile(0.95)),
        "n_ge_observed": ge, "empirical_p": p_value,
    }
    (optdir / "nulltest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[null] 随机组：最高 {summary['null_max']:.0f} 元，"
          f"均值 {summary['null_mean']:.0f} 元，P95 {summary['null_p95']:.0f} 元")
    print(f"[null] ≥ 观测最优的随机组 {ge}/{len(df)} → 经验 p = {p_value:.3f}")
    print(f"[null] 判定：{'无法拒绝随机性（疑似过拟合）' if p_value > 0.05 else '显著优于随机参数'}")


def _slice_period_ddb(con: duckdb.DuckDBPyConnection, codes: list[str],
                      start: str, end: str,
                      parquet_path: Path = BARS_PARQUET
                      ) -> tuple[Optional[pd.DataFrame], dict[str, pd.DataFrame]]:
    """SQL 即时切片（worker 不用再为子集做内存过滤）。"""
    full = fetch_bars_dict(con, codes, start, end, parquet_path)
    idx = fetch_one(con, "000300", start, end, parquet_path)
    idx = idx if not idx.empty else None
    return idx, full


def stage_holdout(args) -> None:
    """样本外复验 + 成本敏感性。"""
    optdir = OUT_DIR / "opt"
    best_json = optdir / "search_best.json"
    if not best_json.exists():
        raise SystemExit("缺少 search_best.json，先跑 --stage search")
    blob = json.loads(best_json.read_text(encoding="utf-8"))
    best = _params_from(blob)
    codes = blob["codes"]

    full_pool = list(dict.fromkeys(ETF_POOL))
    con = ensure_pool_bars(full_pool, None, None)

    def slice_by(start, end):
        ib, b = _slice_period_ddb(con, codes, start, end)
        b = {c: df for c, df in b.items() if len(df) > best.ma_len * 3}
        return b, ib

    segments = [
        ("样本内", args.is_start, args.is_end),
        ("样本外", args.oos_start, args.oos_end),
        ("全区间", "0000-01-01", "9999-12-31"),
    ]
    rows = []
    for name, s, e in segments:
        b, ib = slice_by(s, e)
        if not b:
            continue
        summary, detail = eval_set(list(b), b, best, args.cash, ib, detail=True)
        rows.append({"segment": name, "start": s or "min", "end": e or "max",
                     **summary})
        if detail is not None and len(detail):
            detail.to_csv(optdir / f"holdout_{name}_detail.csv", index=False,
                          encoding="utf-8-sig")

    for mult, label in ((0.5, "成本减半"), (1.0, "真实成本"), (2.0, "成本翻倍")):
        p2 = dataclasses.replace(best, commission_rate=best.commission_rate * mult,
                                 slippage=best.slippage * mult)
        b, ib = slice_by(args.oos_start, args.oos_end)
        s, _ = eval_set(list(b), b, p2, args.cash, ib)
        rows.append({"segment": f"样本外/{label}", "start": args.oos_start,
                     "end": args.oos_end, **s})

    df = pd.DataFrame(rows)
    df.to_csv(optdir / "holdout_summary.csv", index=False, encoding="utf-8-sig")
    print(f"[holdout] 产出 {optdir/'holdout_summary.csv'}")
    print(df[["segment", "start", "end", "codes", "total_excess", "trades",
              "win_rate_pct", "pos_codes"]].to_string(index=False))


def stage_final(args) -> None:
    """最终组合回测（标准产物）。"""
    optdir = OUT_DIR / "opt"
    blob = json.loads((optdir / "search_best.json").read_text(encoding="utf-8"))
    best = _params_from(blob)
    codes = blob["codes"]

    # 默认全区间（start/end 皆 None → 不切片）；传参则只跑该窗口。
    # 勿退回 oos_start 默认值：那会让 final 产物只剩样本外一段，
    # 与 search/holdout 的全区间口径对不上。
    start, end = args.start, args.end

    full_pool = list(dict.fromkeys(ETF_POOL))
    con = ensure_pool_bars(full_pool, None, None)
    src_parquet = BARS_PARQUET
    if best.primary_level == "15m":
        ensure_15m_bars(codes, start, end)
        src_parquet = BARS_15M_PARQUET
        print(f"[final] 主级别 15m → 数据源 {src_parquet.name}", flush=True)
    bars = fetch_bars_dict(con, codes, start, end, parquet_path=src_parquet)
    index_bars = None  # ETF_POOL 不含 000300，与原版 effective 行为一致

    amps = {c: _avg_daily_amp(df) for c, df in bars.items()}
    if amps:
        p60 = float(pd.Series(list(amps.values())).quantile(0.6))
        best = dataclasses.replace(best, amp_min=min(best.amp_min, p60),
                                   amp_dead=min(best.amp_dead, p60))

    result = run_backtest(codes, bars, best, total_cash=args.cash,
                          index_bars=index_bars,
                          pre_aggregated=(best.primary_level == "15m"))
    outdir = OUT_DIR / f"opt_final_{len(codes)}sym"
    paths = write_outputs(result, best, outdir)
    print(paths["summary"].read_text(encoding="utf-8"))
    print(f"[final] 产物：{outdir}")


# ---------------------------------------------------------------- CLI


def main() -> None:
    ap = argparse.ArgumentParser(description="做T选股与参数优化（DuckDB 版）")
    ap.add_argument("--stage", required=True,
                    choices=["rank", "search", "holdout", "nulltest", "final"])
    ap.add_argument("--codes", type=str, default="")
    ap.add_argument("--start", type=str, default=None)
    ap.add_argument("--end", type=str, default=None)
    ap.add_argument("--is-start", type=str, default="2024-09-09")
    ap.add_argument("--is-end", type=str, default="2025-12-31")
    ap.add_argument("--oos-start", type=str, default="2026-01-01")
    ap.add_argument("--oos-end", type=str, default="2026-09-28")
    ap.add_argument("--cash", type=float, default=1_000_000.0)
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--dims", type=str, default="")
    ap.add_argument("--index", type=str, default="000300")
    ap.add_argument("--samples", type=int, default=60)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--params", type=str, default="",
                    help="基线参数 JSON，缺省用真实成本默认"
                         "（primary_level=15m 时自动走 15m 预聚合）")
    args = ap.parse_args()
    {"rank": stage_rank, "search": stage_search, "holdout": stage_holdout,
     "nulltest": stage_nulltest, "final": stage_final}[args.stage](args)


if __name__ == "__main__":
    main()
