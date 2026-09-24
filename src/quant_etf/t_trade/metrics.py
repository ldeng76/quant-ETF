"""消融、回测矩阵与成本敏感性：M4a 批量实验编排（M5 决策材料）。

所有实验共用同一份预加载的 bars（内存中重复跑 run_backtest），
差异只来自参数——由 params.json 运行快照保证可复现。
表中 excess 即 CONTEXT.md 主口径"做T vs 纯持有的增量收益"（元）。
"""

import dataclasses

import pandas as pd

from .backtest import BacktestResult, closed_trades, run_backtest
from .params import TTradeParams

# 参与消融的增强开关（默认全开；逆势T默认关，消融中做"开启"方向）
ABLATION_SWITCHES = [
    "confirm_fractal",
    "confirm_volume",
    "resonance",
    "index_filter",
    "amp_filter",
    "time_window",
]


def _summarize(result: BacktestResult) -> dict:
    closed = closed_trades(result)
    n = len(closed)
    wins = sum(1 for t in closed if t["profit"] > 0)
    return {
        "trades": n,
        "win_rate": round(wins / n, 4) if n else 0.0,
        "total_profit": round(result.total_profit, 2),
        "excess": round(result.final_equity - result.final_bh_equity, 2),
    }


def _excess(result: BacktestResult) -> float:
    return result.final_equity - result.final_bh_equity


def run_ablation(
    codes: list[str],
    bars: dict[str, pd.DataFrame],
    params: TTradeParams,
    total_cash: float,
    index_bars: pd.DataFrame | None = None,
    baseline: BacktestResult | None = None,
) -> pd.DataFrame:
    """逐开关关闭重跑，输出边际贡献表（含全开基线）。

    baseline 可传入编排方已算好的全开回测结果，避免重复执行。
    """
    rows = []
    base_result = baseline or run_backtest(codes, bars, params, total_cash, index_bars)
    rows.append({"config": "baseline(全开)", **_summarize(base_result)})
    base_excess = _excess(base_result)

    for switch in ABLATION_SWITCHES:
        p = dataclasses.replace(params, **{switch: False})
        r = run_backtest(codes, bars, p, total_cash, index_bars)
        rows.append({
            "config": f"关 {switch}",
            **_summarize(r),
            # 该开关的贡献 = 基线增量 − 关掉后的增量（正=开关有正贡献）
            "switch_contribution": round(base_excess - _excess(r), 2),
        })

    # 逆势T：默认关，消融做"开启"方向
    p = dataclasses.replace(params, allow_offside_t=True)
    r = run_backtest(codes, bars, p, total_cash, index_bars)
    rows.append({
        "config": "开 allow_offside_t",
        **_summarize(r),
        "switch_contribution": round(_excess(r) - base_excess, 2),
    })

    return pd.DataFrame(rows)


def run_cost_grid(
    codes: list[str],
    bars: dict[str, pd.DataFrame],
    params: TTradeParams,
    total_cash: float,
    index_bars: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """佣金×滑点×最低佣金 敏感性网格（ETF 免印花税）。"""
    rows = []
    for comm in (5e-5, 1e-4, 2e-4):
        for slip in (0.0, 5e-4, 1e-3):
            for min_comm in (0.0, 5.0):
                p = dataclasses.replace(
                    params,
                    commission_rate=comm,
                    slippage=slip,
                    min_commission=min_comm,
                )
                r = run_backtest(codes, bars, p, total_cash, index_bars)
                rows.append({
                    "commission_bp": round(comm * 1e4, 1),
                    "slippage_bp": round(slip * 1e4, 1),
                    "min_commission": min_comm,
                    **_summarize(r),
                })
    return pd.DataFrame(rows)


def run_level_rsi_matrix(
    codes: list[str],
    bars: dict[str, pd.DataFrame],
    params: TTradeParams,
    total_cash: float,
    index_bars: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """主级别 {5m, 15m} × RSI 阈值 {20/80, 30/70} 矩阵（ETF 低波动校准）。"""
    rows = []
    for level in ("5m", "15m"):
        for lo, hi in ((20.0, 80.0), (30.0, 70.0)):
            p = dataclasses.replace(
                params,
                primary_level=level,
                rsi_buy_th=lo,
                rsi_sell_th=hi,
            )
            r = run_backtest(codes, bars, p, total_cash, index_bars)
            rows.append({
                "primary_level": level,
                "rsi_buy_th": lo,
                "rsi_sell_th": hi,
                **_summarize(r),
            })
    return pd.DataFrame(rows)


def run_eod_compare(
    codes: list[str],
    bars: dict[str, pd.DataFrame],
    params: TTradeParams,
    total_cash: float,
    index_bars: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """当日闭环对照：strict_eod 严格 vs 允许隔夜（量化"隔夜T单"代价）。"""
    rows = []
    for strict in (True, False):
        p = dataclasses.replace(params, strict_eod=strict)
        r = run_backtest(codes, bars, p, total_cash, index_bars)
        rows.append({
            "strict_eod": strict,
            **_summarize(r),
        })
    return pd.DataFrame(rows)
