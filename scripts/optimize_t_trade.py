"""做T策略「选股 + 参数优化」一体化脚本。

阶段（各自可独立重跑，产物落盘可续跑）：
  rank    全池逐只基线回测 → 每只标的的增量指标表（选股依据）
  search  在候选子集上做坐标下降参数搜索（贪心 hill climbing）
  holdout 用搜索得到的参数在样本外时间段复验，量化过拟合落差
  final   用最终参数跑组合回测，落标准产物（trades/equity/summary/html）

设计要点：
  - bars 只从 PG 拉一次并 pickle 缓存到 data/cache/，后续阶段秒级启动
  - 搜索目标 = 子集组合总增量（元）；每轮只动一个参数维度，坐标下降
  - 成本假设（commission/slippage）是回测输入，不参与搜索——它是既定事实，
    改它等于自欺；但会在 holdout 阶段额外做敏感性体检

用法：
  uv run python scripts/optimize_t_trade.py --stage rank
  uv run python scripts/optimize_t_trade.py --stage search --codes <csv>
  uv run python scripts/optimize_t_trade.py --stage holdout
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import pickle
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_etf.conf import ETF_POOL  # noqa: E402
from quant_etf.t_trade.backtest import (  # noqa: E402
    _avg_daily_amp,
    closed_trades,
    load_pool_bars,
    run_backtest,
    write_outputs,
)
from quant_etf.t_trade.params import TTradeParams  # noqa: E402

CACHE_DIR = ROOT / "data" / "cache"
OUT_DIR = ROOT / "data" / "results" / "2026-09-28" / "t_trade"
BASE_PARAMS = (
    OUT_DIR / "params-realistic.json"
)  # 万二佣金 + 5bp 单边滑点（真实成本）


# ---------------------------------------------------------------- 数据层


def load_bars_cached(codes: list[str], start: str, end: str,
                     tag: str) -> dict[str, pd.DataFrame]:
    """从 PG 载入并 pickle 缓存；命中缓存则直接反序列化。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"tbars_{tag}.pkl"
    if cache_file.exists():
        with open(cache_file, "rb") as f:
            payload = pickle.load(f)
        print(f"[data] 命中缓存 {cache_file.name}: {len(payload)} 只", flush=True)
        return payload

    t0 = time.time()
    bars = load_pool_bars(codes, start=start, end=end)
    with open(cache_file, "wb") as f:
        pickle.dump(bars, f, protocol=pickle.HIGHEST_PROTOCOL)
    mb = cache_file.stat().st_size / 1e6
    print(f"[data] PG 载入 {len(bars)} 只 {time.time()-t0:.0f}s → {cache_file.name} "
          f"({mb:.0f}MB)", flush=True)
    return bars


def eligible_codes(bars: dict[str, pd.DataFrame], params: TTradeParams,
                   min_bars: int = 2000) -> list[str]:
    """深度门槛：剔除只有几十根 bar 的伪标的（001389 类非 ETF）。"""
    return sorted(
        c for c, df in bars.items()
        if len(df) >= max(min_bars, params.ma_len * 3)
    )


# ---------------------------------------------------------------- 评估层


def cost_bp(params: TTradeParams) -> float:
    return (params.commission_rate * 2 + params.slippage * 2) * 1e4


# 时间分段：IS = 样本内（选股+调参），OOS = 样本外（只验证，不参与任何决策）
SEGMENTS = {
    "is": ("2024-09-09", "2025-12-31"),
    "oos": ("2026-01-01", "2026-09-28"),
}


def segment_excess(result, start: str, end: str) -> float:
    """从逐日净值曲线切段求增量。段末 t_trade − buy_hold 即该段内做T累计增量。"""
    eq = result.equity
    if eq is None or not len(eq):
        return float("nan")
    d = pd.to_datetime(eq["date"])
    sub = eq[(d >= start) & (d <= end)]
    if len(sub) < 2:
        return float("nan")
    return float(sub["t_trade"].iloc[-1] - sub["buy_hold"].iloc[-1])


def eval_one(code: str, bars: dict, params: TTradeParams, cash: float,
             index_bars) -> dict:
    """单标的回测 + 指标。excess=做T净值−纯持有净值。"""
    df = bars.get(code)
    if df is None:
        return {"code": code, "ok": False, "excess": 0.0, "trades": 0}
    r = run_backtest([code], {code: df}, params, total_cash=cash,
                     index_bars=index_bars)
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
    """子集评估：返回组合汇总；detail=True 时附带逐只明细。"""
    rows = [eval_one(c, bars, params, cash, index_bars) for c in codes]
    summary = summarize(rows, params)
    return (summary, pd.DataFrame(rows)) if detail else (summary, None)


# ---------------------------------------------------------------- 参数空间


# 坐标下降：每个维度给候选值，从当前最优出发逐维贪心替换
PARAM_GRID: dict[str, list] = {
    # RSI 阈值：IS 实测呈单调趋势——越极端越好（放宽到 40/60 笔数涨 6 倍但增量亏 12 倍），
    # 说明价差只存在于极端超买超卖处，故向更极端延伸而非向中性收
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
    "long_t_only": [True, False],
}


# 零假设随机化起点：出厂默认值（非最优解附近）
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
    """基线参数：--params 指定则用它，否则用真实成本默认。"""
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
    bars = load_bars_cached(codes, args.start, args.end, tag)
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


# ---- 并行：参数搜索要评估数百组，逐bar 循环是纯 Python，多进程才有意义 ----

_PAR_WORKERS = max(2, min(8, (os.cpu_count() or 4)))
_W: dict = {}  # worker 进程侧全局


def _init_worker(payload_file: str) -> None:
    with open(payload_file, "rb") as f:
        d = pickle.load(f)
    _W.update(bars=d["bars"], index_bars=d["index_bars"], cash=d["cash"])


def _eval_worker(task: tuple) -> dict:
    code, pj = task
    data = json.loads(pj)
    data["buy_window"] = tuple(data["buy_window"])
    data["sell_window"] = tuple(data["sell_window"])
    return eval_one(code, _W["bars"], TTradeParams(**data), _W["cash"],
                    _W["index_bars"])


def _params_json(p: TTradeParams) -> str:
    d = json.loads(p.to_json())
    d["buy_window"] = list(d["buy_window"])
    d["sell_window"] = list(d["sell_window"])
    return json.dumps(d)


def build_payload(bars: dict, index_bars, cash: float, tag: str) -> Path:
    """搜索专用的小缓存：只含候选标的，worker 进程加载快。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f = CACHE_DIR / f"tsearch_{tag}.pkl"
    if not f.exists():
        with open(f, "wb") as fh:
            pickle.dump({"bars": bars, "index_bars": index_bars, "cash": cash},
                        fh, protocol=pickle.HIGHEST_PROTOCOL)
    return f


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


def _slice_period(full: dict, index_code: str, codes: list[str],
                  start: str, end: str):
    """按时间窗切出子集 bars 与同期指数，避免跨段污染。"""
    def cut(df):
        d = df["time"]
        return df[(d >= start) & (d <= end)].reset_index(drop=True)

    bars = {}
    for c in codes:
        if c in full and c != index_code:
            sub = cut(full[c])
            if len(sub):
                bars[c] = sub
    idx = full.get(index_code)
    return (cut(idx) if idx is not None else None), bars


def stage_search(args) -> None:
    """坐标下降参数搜索（严格样本内：只用 IS 段数据）。"""
    params = _load_base_params(args)
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    if not codes:
        raise SystemExit("--codes 必填（用 rank 结果挑选的候选子集）")

    full = load_bars_cached(list(dict.fromkeys(ETF_POOL)), None, None, "full2y")
    index_bars, bars = _slice_period(
        full, args.index, codes, args.is_start, args.is_end
    )
    if args.resume:
        prev = OUT_DIR / "opt" / "search_best.json"
        if not prev.exists():
            raise SystemExit("--resume 需要已有 search_best.json")
        params = _params_from(json.loads(prev.read_text(encoding="utf-8")))
        print(f"[search] 从上次最优续搜（每日上限 "
              f"{params.max_trades_per_day} 笔）", flush=True)
    if not bars:
        raise SystemExit("IS 段切片为空，检查 --is-start/--is-end")
    print(f"[search] IS {args.is_start}→{args.is_end}，候选 {len(bars)} 只 "
          f"（{sum(len(v) for v in bars.values()):,} 根 5m bar）", flush=True)

    # 振幅门槛与池内分位对齐（组合口径）
    amps = {c: _avg_daily_amp(df) for c, df in bars.items()}
    p60 = float(pd.Series(list(amps.values())).quantile(0.6))
    params = dataclasses.replace(params, amp_min=min(params.amp_min, p60),
                                 amp_dead=min(params.amp_dead, p60))
    print(f"[search] 候选 {len(bars)} 只，振幅门槛 {params.amp_min*100:.3f}%", flush=True)

    dims = args.dims.split(",") if args.dims else list(PARAM_GRID)
    codes = list(bars)
    payload = build_payload(bars, index_bars, args.cash,
                            f"{len(codes)}sym_is{args.is_start[:4]}")
    print(f"[search] 并行 worker {_PAR_WORKERS}，候选缓存 {payload.name}", flush=True)

    history = []
    best = params
    with ProcessPoolExecutor(
        max_workers=min(len(codes), _PAR_WORKERS),
        initializer=_init_worker, initargs=(str(payload),),
    ) as pool:
        def run(p: TTradeParams):
            pj = _params_json(p)
            return summarize(list(pool.map(_eval_worker, [(c, pj) for c in codes])), p)

        base = run(best)
        best_score = base["total_excess"]
        print(f"[search] 基线增量 {best_score:.0f} 元 / {base['trades']} 笔 "
              f"胜率 {base['win_rate_pct']:.1f}%", flush=True)
        history.append({"round": 0, "dim": "baseline", "value": "-",
                        "excess": best_score, "trades": base["trades"],
                        "win_rate_pct": base["win_rate_pct"], "adopted": True})

        for rnd in range(1, args.rounds + 1):
            improved = False
            print(f"\n[search] ===== Round {rnd}/{args.rounds} =====", flush=True)
            for dim in dims:
                for value in PARAM_GRID[dim]:
                    cand = apply_dim(best, dim, value)
                    if cand == best:
                        continue
                    s = run(cand)
                    mark = ""
                    if s["total_excess"] > best_score:
                        best_score, best, improved = s["total_excess"], cand, True
                        mark = "  ← 新最优"
                    print(f"[search] {dim}={value!r:>10} → 增量 {s['total_excess']:>9.0f} "
                          f"笔数 {s['trades']:>4} 胜率 {s['win_rate_pct']:>5.1f}%{mark}",
                          flush=True)
                    history.append({"round": rnd, "dim": dim, "value": repr(value),
                                    "excess": s["total_excess"], "trades": s["trades"],
                                    "win_rate_pct": s["win_rate_pct"],
                                    "pos_codes": s["pos_codes"],
                                    "adopted": mark != ""})
            print(f"[search] Round {rnd} 结束，最优 {best_score:.0f} 元", flush=True)
            if not improved:
                print("[search] 本轮无改进，收敛", flush=True)
                break
        final = run(best)

    outdir = OUT_DIR / "opt"
    outdir.mkdir(parents=True, exist_ok=True)
    n_trials = len(history) - 1
    result = {
        "codes": codes,
        "rounds_done": max((h["round"] for h in history), default=0),
        "trials": n_trials,
        "best_excess": best_score,
        "params": param_desc(best),
        "params_full": json.loads(_params_json(best)),
        "in_sample": {**final, "start": args.is_start, "end": args.is_end},
    }
    (outdir / "search_best.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.DataFrame(history).to_csv(outdir / "search_history.csv", index=False,
                                 encoding="utf-8-sig")
    print(f"\n[search] 共评估 {n_trials} 组参数，最优增量 {best_score:.0f} 元 "
          f"/ {final['trades']} 笔 / 胜率 {final['win_rate_pct']:.1f}%")
    print(f"[search] 最优参数：{json.dumps(param_desc(best), ensure_ascii=False)}")


def _params_from(blob: dict) -> TTradeParams:
    """从 search_best.json 还原参数（优先完整快照，回退到摘要字段）。"""
    pj = blob.get("params_full") or blob["params"]
    return TTradeParams(**{
        k: (tuple(v) if k in ("buy_window", "sell_window") else v)
        for k, v in pj.items()
    })


def stage_nulltest(args) -> None:
    """零假设检验：随机参数在 IS 段能达到的最大增量，判定搜索最优是否为运气。

    从同一参数空间独立随机采样 N 组，与坐标下降的最优比。若随机组里也有大量
    样本达到同等增量，说明 4088 元来自挑选偏差而非参数本身有效。
    """
    optdir = OUT_DIR / "opt"
    best_json = optdir / "search_best.json"
    if not best_json.exists():
        raise SystemExit("缺少 search_best.json，先跑 --stage search")
    blob = json.loads(best_json.read_text(encoding="utf-8"))
    observed = blob["best_excess"]
    codes = blob["codes"]

    full = load_bars_cached(list(dict.fromkeys(ETF_POOL)), None, None, "full2y")
    index_bars, bars = _slice_period(full, args.index, codes,
                                     args.is_start, args.is_end)
    base = _params_from(blob)
    # 回到出厂默认作为随机化的起点，避免从最优解附近采样（那不是零假设）
    base = dataclasses.replace(base, **_DEFAULTS)

    payload = build_payload(bars, index_bars, args.cash,
                            f"{len(codes)}sym_is{args.is_start[:4]}")
    rng = np.random.default_rng(args.seed)
    n = args.samples
    print(f"[null] IS 段随机采样 {n} 组，观测最优 {observed:.0f} 元", flush=True)

    samples = []
    with ProcessPoolExecutor(
        max_workers=min(len(codes), _PAR_WORKERS),
        initializer=_init_worker, initargs=(str(payload),),
    ) as pool:
        for i in range(1, n + 1):
            p = base
            for dim in PARAM_GRID:
                v = rng.choice(PARAM_GRID[dim])
                if isinstance(v, np.ndarray):
                    v = tuple(float(x) for x in v.tolist())
                elif isinstance(v, np.generic):
                    v = v.item()  # numpy 标量 → 原生类型，否则 json 序列化失败
                p = apply_dim(p, dim, v)
            pj = _params_json(p)
            s = summarize(list(pool.map(_eval_worker, [(c, pj) for c in codes])), p)
            samples.append({"i": i, "excess": s["total_excess"],
                            "trades": s["trades"]})
            if i % 10 == 0:
                print(f"[null] {i}/{n} 当前随机最优 "
                      f"{max(x['excess'] for x in samples):.0f} 元", flush=True)

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


def stage_holdout(args) -> None:
    """样本外复验 + 成本敏感性。"""
    optdir = OUT_DIR / "opt"
    best_json = optdir / "search_best.json"
    if not best_json.exists():
        raise SystemExit("缺少 search_best.json，先跑 --stage search")
    blob = json.loads(best_json.read_text(encoding="utf-8"))
    best = _params_from(blob)
    codes = blob["codes"]
    bars_full = load_bars_cached(list(dict.fromkeys(ETF_POOL)), None, None, "full2y")
    index_full = bars_full.get(args.index)

    def slice_by(start, end):
        ib, b = _slice_period(bars_full, args.index, codes, start, end)
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

    # 成本敏感性：真实成本是既定事实，但要知道结论有多脆
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
    bars_full = load_bars_cached(list(dict.fromkeys(ETF_POOL)), None, None, "full2y")
    index_full = bars_full.get(args.index)
    bars = {c: bars_full[c] for c in codes}
    amps = {c: _avg_daily_amp(df) for c, df in bars.items()}
    p60 = float(pd.Series(list(amps.values())).quantile(0.6))
    best = dataclasses.replace(best, amp_min=min(best.amp_min, p60),
                               amp_dead=min(best.amp_dead, p60))

    # 默认全区间（start/end 皆 None → 不切片）；传参则只跑该窗口
    start, end = args.start, args.end
    if start or end:
        bars = {c: df[(df["time"] >= (start or df["time"].min()))
                      & (df["time"] <= (end or df["time"].max()))].reset_index(drop=True)
                for c, df in bars.items()}
        if index_full is not None:
            index_full = index_full[(index_full["time"] >= (start or index_full["time"].min()))
                                    & (index_full["time"] <= (end or index_full["time"].max()))]

    result = run_backtest(codes, bars, best, total_cash=args.cash,
                          index_bars=index_full)
    outdir = OUT_DIR / f"opt_final_{len(codes)}sym"
    paths = write_outputs(result, best, outdir)
    print(paths["summary"].read_text(encoding="utf-8"))
    print(f"[final] 产物：{outdir}")


def main() -> None:
    ap = argparse.ArgumentParser(description="做T选股与参数优化")
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
    ap.add_argument("--samples", type=int, default=60, help="nulltest 随机采样组数")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--params", type=str, default="",
                    help="基线参数 JSON，缺省用真实成本默认")
    ap.add_argument("--resume", action="store_true",
                    help="从既有 search_best.json 继续搜索")
    args = ap.parse_args()
    {"rank": stage_rank, "search": stage_search, "holdout": stage_holdout,
     "nulltest": stage_nulltest, "final": stage_final}[args.stage](args)


if __name__ == "__main__":
    main()
