"""逐标的做T回测排名：回答「哪些标的最赚钱」。

核心层 run_backtest 天然支持组合回测（等权子账户），但组合口径下无法做
单标的归因。本脚本把同一套 params 逐标的独立回测，产出可排序的指标表，
用于筛选做T策略的标的子集。

用法：
  uv run python scripts/rank_t_trade_symbols.py --level 15m --top 10
  uv run python scripts/rank_t_trade_symbols.py --level 15m --amp-mode single

口径说明：
  - amp-mode pool60（默认）：振幅门槛取「全池 60 分位」，与组合回测一致，
    保证排名口径和 data/results 下的组合产物可比。
  - amp-mode single：单只回测原生口径（分位数退化为自身振幅），更宽松。
  - 每只标的独立账户、同一份 params、同一份沪深300 指数过滤。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
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
    resample_intraday,
    run_backtest,
)
from quant_etf.t_trade.params import TTradeParams  # noqa: E402


def _round_trip_cost_bp(params: TTradeParams) -> float:
    """一进一出双腿的摩擦（bp），与 executor 计费口径一致。"""
    return (params.commission_rate * 2.0 + params.slippage * 2.0) * 10_000.0


def _summarize(code: str, result, params: TTradeParams, cash: float,
               n_bars_5m: int, n_days: int, amp: float,
               cost_bp: float) -> dict:
    closed = closed_trades(result)
    n = len(closed)
    excess = result.final_equity - result.final_bh_equity

    span_days = 0.0
    if result.start and result.end:
        span_days = max((pd.Timestamp(result.end) - pd.Timestamp(result.start)).days, 1)
    years = span_days / 365.25

    excess_ret = excess / cash if cash else 0.0
    ann_excess = excess_ret / years if years > 0 else 0.0

    if n:
        bp = np.array([t["profit_bp"] for t in closed], dtype=float)
        profits = np.array([t["profit"] for t in closed], dtype=float)
        win_rate = float((profits > 0).mean())
        gross = float(profits[profits > 0].sum())
        loss = float(-profits[profits <= 0].sum())
        pf = gross / loss if loss > 0 else np.inf
        med_bp, mean_bp = float(np.median(bp)), float(bp.mean())
        std_bp = float(bp.std(ddof=1)) if n > 1 else 0.0
        # 单笔净bp 的 t 值：判断「是否只是运气」
        t_stat = mean_bp / (std_bp / np.sqrt(n)) if std_bp > 0 else 0.0
        hold = float(np.mean([t.get("holding_bars", 0) for t in closed]))
    else:
        win_rate = pf = med_bp = mean_bp = t_stat = hold = float("nan")

    pos_t = sum(1 for t in closed if t.get("direction") == "正T")
    neg_t = sum(1 for t in closed if t.get("direction") == "反T")

    return {
        "code": code,
        "bars_5m": n_bars_5m,
        "days": n_days,
        "amp_pct": round(amp * 100, 3),
        "trades": n,
        "trades_per_year": round(n / years, 1) if years > 0 else 0.0,
        "excess_cny": round(excess, 2),
        "excess_ret_pct": round(excess_ret * 100, 4),
        "ann_excess_pct": round(ann_excess * 100, 4),
        "win_rate_pct": round(win_rate * 100, 1),
        "profit_factor": round(pf, 2) if np.isfinite(pf) else None,
        "mean_net_bp": round(mean_bp, 2),
        "median_net_bp": round(med_bp, 2),
        "gross_bp_per_trade": round(mean_bp + cost_bp, 2),
        "t_stat": round(t_stat, 2),
        "avg_hold_bars": round(hold, 1),
        "pos_t": pos_t,
        "neg_t": neg_t,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="逐标的做T回测排名")
    ap.add_argument("--codes", type=str, default="", help="逗号分隔标的，默认 ETF_POOL")
    ap.add_argument("--level", type=str, default="5m", choices=["5m", "15m"])
    ap.add_argument("--start", type=str, default=None)
    ap.add_argument("--end", type=str, default=None)
    ap.add_argument("--cash", type=float, default=1_000_000.0)
    ap.add_argument("--params", type=str, default=None, help="参数 JSON，缺省用内置默认")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--min-bars", type=int, default=2000,
                    help="5m bar 数门槛，剔除深度不足的标的")
    ap.add_argument("--index", type=str, default="000300", help="指数过滤基准")
    ap.add_argument("--amp-mode", type=str, default="pool60", choices=["pool60", "single"])
    ap.add_argument("--outdir", type=str, default=None)
    args = ap.parse_args()

    codes = [c.strip() for c in args.codes.split(",") if c.strip()] or list(
        dict.fromkeys(ETF_POOL)
    )
    end_arg = args.end + " 23:59:59" if args.end and len(args.end) == 10 else args.end

    params = (
        TTradeParams.from_json(Path(args.params).read_text(encoding="utf-8"))
        if args.params
        else TTradeParams()
    )
    params = replace(
        params,
        primary_level=args.level,
        resonance_level="60m" if args.level == "15m" else "15m",
    )
    cost_bp = _round_trip_cost_bp(params)

    print(f"[rank] 载入 {len(codes)} 只标的 {args.start} → {args.end}", flush=True)
    bars = load_pool_bars(codes, start=args.start, end=end_arg)

    index_bars = None
    idx = load_pool_bars([args.index], start=args.start, end=end_arg)
    if idx:
        index_bars = idx[args.index]

    # 深度门槛 + 振幅门槛（pool60 与组合回测同口径）
    amps = {c: _avg_daily_amp(df) for c, df in bars.items()}
    pool_p60 = float(pd.Series(list(amps.values())).quantile(0.6)) if amps else 0.0
    if args.amp_mode == "pool60":
        params = replace(params, amp_min=min(params.amp_min, pool_p60))

    eligible = {
        c: df for c, df in bars.items()
        if len(df) >= args.min_bars
        and len(resample_intraday(df, "15min")) >= params.ma_len * 4
    }
    skipped = sorted(set(codes) - set(eligible))
    print(f"[rank] 有效 {len(eligible)} 只，剔除 {len(skipped)} 只（数据不足/非ETF）"
          f"；振幅门槛 {params.amp_min*100:.3f}%（pool_p60={pool_p60*100:.3f}%）",
          flush=True)

    rows = []
    for i, (code, df) in enumerate(sorted(eligible.items()), 1):
        res = run_backtest([code], {code: df}, params,
                           total_cash=args.cash, index_bars=index_bars)
        rows.append(_summarize(
            code, res, params, args.cash, len(df),
            int(pd.DatetimeIndex(df["time"]).normalize().nunique()),
            amps.get(code, 0.0), cost_bp,
        ))
        print(f"[rank] {i}/{len(eligible)} {code} "
              f"增量 {rows[-1]['excess_cny']:>10.2f} 元 "
              f"({rows[-1]['ann_excess_pct']:>7.3f}%/年) "
              f"笔数 {rows[-1]['trades']:>4}", flush=True)

    df_all = pd.DataFrame(rows).sort_values("ann_excess_pct", ascending=False)
    outdir = Path(args.outdir) if args.outdir else (
        Path("data/results") / pd.Timestamp.today().date().isoformat() / "t_trade"
        / f"rank_{args.level}_{args.amp_mode}"
    )
    outdir.mkdir(parents=True, exist_ok=True)
    df_all.to_csv(outdir / f"per_symbol_{args.level}.csv", index=False,
                  encoding="utf-8-sig")
    df_all.head(args.top).to_csv(outdir / f"top{args.top}_{args.level}.csv",
                                 index=False, encoding="utf-8-sig")

    meta = {
        "level": args.level,
        "amp_mode": args.amp_mode,
        "amp_min_pct": round(params.amp_min * 100, 3),
        "cash": args.cash,
        "cost_round_trip_bp": round(cost_bp, 2),
        "params": json.loads(params.to_json()),
        "eligible": len(eligible),
        "skipped": skipped,
    }
    (outdir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    cols = ["code", "ann_excess_pct", "excess_cny", "trades", "win_rate_pct",
            "profit_factor", "mean_net_bp", "gross_bp_per_trade", "t_stat",
            "amp_pct", "avg_hold_bars", "pos_t", "neg_t"]
    print(f"\n[rank] 产出：{outdir}")
    print(f"\n[rank] 前 {args.top} 名（按年化超额%）")
    print(df_all.head(args.top)[cols].to_string(index=False))
    print(f"\n[rank] 摩擦 {cost_bp:.0f} bp/往返；净bp 均值需 > {-cost_bp:.0f} 才覆盖成本")
    pos = int((df_all["ann_excess_pct"] > 0).sum())
    print(f"[rank] 全池 {len(df_all)} 只中 {pos} 只年化超额为正")


if __name__ == "__main__":
    main()
