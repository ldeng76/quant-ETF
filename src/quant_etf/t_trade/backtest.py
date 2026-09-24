"""做T回测编排器：数据加载 → 指标预计算 → 逐bar驱动做T引擎 → 产物输出。

防未来函数的结构性保证：bar t 收盘产生决策，bar t+1 开盘价成交——
循环按"先决策、后执行"编排，决策函数看不到下一根 bar。
"""

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .account import SubAccount, TOrderRejected
from .classifier import Direction, classify_trend
from .engine import BarContext, TtAction, TtEngine
from .indicators import ma, rsi
from .params import TTradeParams


def load_pool_bars(
    codes: list[str], start: str | None = None, end: str | None = None
) -> dict[str, pd.DataFrame]:
    """从 PG minute_bars 加载每只标的的 5m 线，按时间升序。"""
    from quant_etf.minute_collector import query_minute_data

    out = {}
    for code in codes:
        df = query_minute_data(code, start=start, end=end, limit=500_000)
        if df is not None and len(df):
            df = df.reset_index().sort_values("time").reset_index(drop=True)
            out[code] = df
    return out


@dataclass
class SymbolFrames:
    """单标的预计算好的指标序列（与 5m bars 逐行对齐）。"""

    time: pd.Series
    open: pd.Series
    high: pd.Series
    low: pd.Series
    close: pd.Series
    rsi: pd.Series
    trend: pd.Series
    day_range: pd.Series  # 当日截至当前bar的已实现振幅（相对昨收）


def compute_frames(df5: pd.DataFrame, params: TTradeParams) -> SymbolFrames:
    """向量化预计算全部指标（不逐bar调 pandas，快且无隐藏状态）。"""
    close = df5["close"].astype(float)
    rsi_s = rsi(close, params.rsi_len)
    ma_s = ma(close, params.ma_len)
    trend = classify_trend(ma_s, slope_bars=params.slope_n, threshold=params.slope_th)

    # 当日已实现振幅 = (日内累计high − 日内累计low) / 昨日收盘
    day = df5["time"].dt.date
    day_close = close.groupby(day).last()          # 每日收盘
    prev_day_close = day.map(day_close.shift(1))   # 每根bar对应的昨收
    intraday_high = df5["high"].astype(float).groupby(day).cummax()
    intraday_low = df5["low"].astype(float).groupby(day).cummin()
    day_range = (intraday_high - intraday_low) / prev_day_close

    return SymbolFrames(
        time=df5["time"], open=df5["open"].astype(float),
        high=df5["high"].astype(float), low=df5["low"].astype(float),
        close=close, rsi=rsi_s, trend=trend, day_range=day_range,
    )


@dataclass
class BacktestResult:
    codes: list[str]
    start: str = ""
    end: str = ""
    trades: list[dict] = field(default_factory=list)
    equity: pd.DataFrame = field(default_factory=pd.DataFrame)
    open_t_count: int = 0
    win_count: int = 0
    total_profit: float = 0.0
    final_equity: float = 0.0
    final_bh_equity: float = 0.0


def run_backtest(
    codes: list[str],
    bars: dict[str, pd.DataFrame],
    params: TTradeParams | None = None,
    total_cash: float = 1_000_000.0,
) -> BacktestResult:
    """核心回测循环：每标的独立等权子账户，逐bar决策、次bar开盘成交。"""
    params = params or TTradeParams()
    per_code_cash = total_cash / len(codes)
    result = BacktestResult(codes=codes)
    equity_paths: dict[str, pd.Series] = {}
    bh_paths: dict[str, pd.Series] = {}

    for code in codes:
        df = bars.get(code)
        if df is None or len(df) < params.ma_len + params.slope_n + params.rsi_len:
            continue
        frames = compute_frames(df, params)
        acc = SubAccount(
            total_cash=per_code_cash,
            commission_rate=params.commission_rate,
            min_commission=params.min_commission,
            slippage=params.slippage,
            lot_size=params.lot_size,
        )
        engine_ = TtEngine(params=params, unit_cash=acc.unit_cash)

        # 期初建满底仓（第一根 bar 开盘价）；纯持有基准用同一底仓 + 残余现金
        try:
            acc.open_base_position(price=float(frames.open.iloc[0]))
        except TOrderRejected as e:
            # 等权资金买不满一手（高价标的）或首bar数据异常 → 跳过该标的
            from loguru import logger

            logger.warning(f"backtest: {code} 跳过（{e}）")
            continue
        bh_cash = per_code_cash - (
            acc.base_shares * float(frames.open.iloc[0]) * (1.0 + params.slippage)
            + max(
                acc.base_shares * float(frames.open.iloc[0]) * params.commission_rate,
                params.min_commission,
            )
        )

        days = frames.time.dt.date.values
        n = len(frames.time)
        pending: dict | None = None
        open_leg: dict | None = None  # 在场T单的开仓腿记录，对冲后合成一行T单

        for i in range(n):
            if i == 0 or days[i] != days[i - 1]:
                acc.on_day_start()

            # ---- 执行上一bar产生的委托（本bar开盘价）----
            if pending is not None:
                open_leg = _execute(
                    pending, code, float(frames.open.iloc[i]),
                    str(frames.time.iloc[i]), result, acc, engine_, open_leg,
                )
                pending = None

            # ---- 本bar收盘：产生决策 ----
            rng = frames.day_range.iloc[i]
            ctx = BarContext(
                bar_time=frames.time.iloc[i].to_pydatetime(),
                close=float(frames.close.iloc[i]),
                rsi=float(frames.rsi.iloc[i]),
                primary_trend=frames.trend.iloc[i],
                available_to_sell=acc.available_to_sell,
                day_amp_ok=bool(rng >= params.amp_dead) if np.isfinite(rng) else False,
            )
            decision = engine_.on_bar(ctx)
            if decision.action != TtAction.NONE and i + 1 < n and days[i + 1] == days[i]:
                pending = {
                    "action": decision.action,
                    "direction": decision.direction,
                    "shares": decision.shares,
                    "signal_time": str(frames.time.iloc[i]),
                    "reason": decision.reason,
                }

            # ---- 日终对账：等量铁律（底仓股数恒等于期初）----
            is_day_end = (i == n - 1) or (days[i + 1] != days[i])
            if is_day_end:
                assert acc.base_intact(), (
                    f"{code} 日终底仓对账失败: held={acc.shares_held} "
                    f"base={acc.base_shares} (bar {frames.time.iloc[i]})"
                )

        if acc.pending_t_shares is not None:
            result.trades.append({
                "code": code, "direction": "未闭环", "profit": np.nan,
                "note": "数据结束仍有未平T单（不应在严格闭环模式下发生）",
                **({"leg1_time": open_leg["leg1_time"]} if open_leg else {}),
            })

        equity_paths[code] = _daily_equity(acc, df)
        bh_paths[code] = _daily_bh(acc, bh_cash, df)

    result.final_equity = float(
        np.nansum([p.iloc[-1] for p in equity_paths.values()])
    ) if equity_paths else 0.0
    result.final_bh_equity = float(
        np.nansum([p.iloc[-1] for p in bh_paths.values()])
    ) if bh_paths else 0.0
    result.equity = _combine_daily(equity_paths, bh_paths)
    profits = [
        t["profit"] for t in result.trades
        if isinstance(t.get("profit"), (int, float)) and np.isfinite(t["profit"])
    ]
    result.total_profit = float(np.nansum(profits)) if profits else 0.0
    result.win_count = sum(1 for p in profits if p > 0)
    if not result.equity.empty:
        result.start = str(result.equity["date"].iloc[0].date())
        result.end = str(result.equity["date"].iloc[-1].date())
    return result


def _execute(
    order: dict, code: str, price: float, exec_time: str,
    result: BacktestResult, acc: SubAccount, engine_: TtEngine,
    open_leg: dict | None,
) -> dict | None:
    """按委托执行（price 为决策次bar开盘价）。返回更新后的开仓腿记录。"""
    try:
        if order["action"] == TtAction.OPEN_T:
            fill = acc.open_t(
                direction=order["direction"], shares=order["shares"], price=price
            )
            result.open_t_count += 1
            engine_.on_fill_leg()  # 开仓腿成交确认 → LEG2_PENDING
            return {
                "code": code,
                "direction": order["direction"].label,  # 正T / 反T
                "leg1_time": exec_time,
                "leg1_price": fill.price,
                "leg1_shares": fill.shares,
                "signal_time": order["signal_time"],
                "trigger": order["reason"],
            }
        fill, profit = acc.close_t(price=price)
        row = dict(open_leg or {"direction": "未知", "code": code})
        row.update({
            "leg2_time": exec_time,
            "leg2_price": fill.price,
            "leg2_shares": fill.shares,
            "profit": profit,
            "hedge_reason": order["reason"],
        })
        result.trades.append(row)
        engine_.on_fill_leg()
        return None
    except TOrderRejected as e:
        result.trades.append({
            "code": code, "direction": "被拒", "note": str(e),
            "signal_time": order["signal_time"],
        })
        if order["action"] == TtAction.OPEN_T:
            engine_.resolve_stale_order()
        else:
            engine_.on_fill_leg()
        return open_leg


def _daily_equity(acc: SubAccount, df: pd.DataFrame) -> pd.Series:
    """每日收盘净资产 = 现金 + 持仓×收盘价。"""
    s = df.set_index("time")["close"].astype(float).resample("1D").last().dropna()
    return s.map(lambda c: acc.cash + acc.shares_held * float(c))


def _daily_bh(acc: SubAccount, bh_cash: float, df: pd.DataFrame) -> pd.Series:
    """纯持有基准：同一底仓一动不动 + 残余现金（与做T账户同起点）。"""
    s = df.set_index("time")["close"].astype(float).resample("1D").last().dropna()
    return s.map(lambda c: bh_cash + acc.base_shares * float(c))


def _combine_daily(equity_paths: dict, bh_paths: dict) -> pd.DataFrame:
    """组合层：各标的等权求和。"""
    frames = []
    for code in equity_paths:
        f = pd.DataFrame({
            "date": equity_paths[code].index,
            f"eq_{code}": equity_paths[code].values,
            f"bh_{code}": bh_paths[code].values,
        })
        frames.append(f)
    if not frames:
        return pd.DataFrame(columns=["date", "t_trade", "buy_hold"])
    out = frames[0]
    for f in frames[1:]:
        out = out.merge(f, on="date", how="outer")
    out = out.sort_values("date").reset_index(drop=True)
    eq_cols = [c for c in out.columns if c.startswith("eq_")]
    bh_cols = [c for c in out.columns if c.startswith("bh_")]
    out["t_trade"] = out[eq_cols].sum(axis=1)
    out["buy_hold"] = out[bh_cols].sum(axis=1)
    return out[["date", "t_trade", "buy_hold"]]


def write_outputs(result: BacktestResult, params: TTradeParams, outdir: Path) -> dict[str, Path]:
    """最小产物集：trades.csv / equity.csv / summary.md / params.json。"""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    paths = {}
    trades_path = outdir / "trades.csv"
    pd.DataFrame(result.trades).to_csv(trades_path, index=False, encoding="utf-8-sig")
    paths["trades"] = trades_path
    equity_path = outdir / "equity.csv"
    result.equity.to_csv(equity_path, index=False, encoding="utf-8-sig")
    paths["equity"] = equity_path
    params_path = outdir / "params.json"
    params.save(params_path)
    paths["params"] = params_path
    summary_path = outdir / "summary.md"
    summary_path.write_text(render_summary(result, params), encoding="utf-8")
    paths["summary"] = summary_path
    return paths


def render_summary(result: BacktestResult, params: TTradeParams) -> str:
    closed = [
        t for t in result.trades
        if isinstance(t.get("profit"), (int, float)) and np.isfinite(t["profit"])
    ]
    n_trades = len(closed)
    win_rate = result.win_count / n_trades if n_trades else 0.0
    excess = result.final_equity - result.final_bh_equity
    days = max(
        (result.equity["date"].iloc[-1] - result.equity["date"].iloc[0]).days, 1
    ) if not result.equity.empty else 1
    ann = (
        (result.final_equity / result.final_bh_equity) ** (365.0 / days) - 1.0
        if result.final_bh_equity > 0 and result.final_equity > 0 else 0.0
    )
    fwd = len([t for t in closed if t.get("direction") == "正T"])
    rev = len([t for t in closed if t.get("direction") == "反T"])
    return (
        "# 做T回测摘要（核心层）\n\n"
        f"- 样本区间: {result.start} → {result.end}"
        f"（样本较短，详见 data_audit.md 深度审计）\n"
        f"- 标的数: {len(result.codes)}，主级别: {params.primary_level}\n"
        f"- 主口径 做T vs 纯持有: {result.final_equity:,.0f} vs {result.final_bh_equity:,.0f}"
        f"（增量 {excess:+,.0f}，年化差 {ann:+.2%}）\n"
        f"- T单: {n_trades} 笔（正T {fwd} / 反T {rev}），胜率 {win_rate:.1%}，"
        f"累计差价 {result.total_profit:+,.0f}\n"
    )
