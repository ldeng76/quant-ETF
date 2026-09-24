"""做T回测编排器：数据加载 → 指标预计算 → 逐bar驱动做T引擎 → 产物输出。

防未来函数的结构性保证：bar t 收盘产生决策，bar t+1 开盘价成交——
循环按"先决策、后执行"编排，决策函数看不到下一根 bar。
"""

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .account import SubAccount, TOrderRejected
from .classifier import classify_trend
from .engine import BarContext, TtAction, TtEngine
from .indicators import (
    bottom_fractal, lower_shadow_rejection, ma, rsi,
    top_fractal, upper_shadow_rejection,
)
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


def resample_intraday(df5: pd.DataFrame, rule: str = "15min") -> pd.DataFrame:
    """5m 线聚合为更高周期（纵向共振用），按 bar 收盘时间对齐。"""
    s = df5.set_index("time")
    agg = s.resample(rule, label="right", closed="right").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last",
         "volume": "sum"}
    ).dropna(subset=["close"])
    return agg


def _align_trend(trend_hi: pd.Series, times: pd.Series) -> list:
    """高级别趋势前向对齐到 5m 时间线；不可得处为 None（门控放行）。"""
    aligned = trend_hi.reindex(pd.DatetimeIndex(times), method="ffill")
    return [None if pd.isna(v) else v for v in aligned]


def _avg_daily_amp(df: pd.DataFrame) -> float:
    """全样本日均振幅：(日内高−日内低)/昨收 的逐日均值。"""
    day = df["time"].dt.date
    d_hi = df["high"].astype(float).groupby(day).max()
    d_low = df["low"].astype(float).groupby(day).min()
    d_close = df["close"].astype(float).groupby(day).last()
    return float(((d_hi - d_low) / d_close.shift(1)).dropna().mean())


def higher_trend_series(df5: pd.DataFrame, params: TTradeParams,
                        rule: str | None = None) -> pd.Series:
    """高级别三分类序列（按收盘时间索引，供 5m 时间线前向对齐）。

    15m bar 收盘时点起其分类可被同刻或之后的 5m bar 消费——无前视。
    rule 缺省按主级别自动选择：主 5m→15m，主 15m→60m。
    """
    if rule is None:
        rule = "60min" if params.primary_level == "15m" else "15min"
    hi = resample_intraday(df5, rule)
    ma_hi = ma(hi["close"].astype(float), params.ma_len)
    return classify_trend(ma_hi, slope_bars=params.slope_n,
                          threshold=params.slope_th)


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
    fractal_buy: pd.Series  # bool：底分型（近3bar）或下影线企稳
    fractal_sell: pd.Series  # bool：顶分型（近3bar）或上影线承压
    vol_shrink: pd.Series  # bool：量能收缩
    dev_above: pd.Series  # bool：价格高于 MA×(1+dev_th)
    dev_below: pd.Series  # bool：价格低于 MA×(1−dev_th)


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

    # 增强开关判定材料
    o = df5["open"].astype(float)
    h = df5["high"].astype(float)
    low_ = df5["low"].astype(float)
    vol = df5["volume"].astype(float)
    # rolling(2)：最近两根内确认过分型即算（含当前bar），贴"最近已完成bar构成"
    fractal_buy = (
        bottom_fractal(h, low_).rolling(2, min_periods=1).max().astype(bool)
        | lower_shadow_rejection(o, h, low_, close)
    )
    fractal_sell = (
        top_fractal(h, low_).rolling(2, min_periods=1).max().astype(bool)
        | upper_shadow_rejection(o, h, low_, close)
    )
    vol_shrink = vol < vol.shift(2)
    dev_above = close > ma_s * (1.0 + params.dev_th)
    dev_below = close < ma_s * (1.0 - params.dev_th)

    return SymbolFrames(
        time=df5["time"], open=o, high=h, low=low_,
        close=close, rsi=rsi_s, trend=trend, day_range=day_range,
        fractal_buy=fractal_buy, fractal_sell=fractal_sell,
        vol_shrink=vol_shrink, dev_above=dev_above, dev_below=dev_below,
    )


@dataclass
class BacktestResult:
    codes: list[str]
    total_cash: float = 0.0
    start: str = ""
    end: str = ""
    trades: list[dict] = field(default_factory=list)
    equity: pd.DataFrame = field(default_factory=pd.DataFrame)
    open_t_count: int = 0
    win_count: int = 0
    total_profit: float = 0.0
    final_equity: float = 0.0
    final_bh_equity: float = 0.0
    amp_floor: float = 0.0
    diluted: pd.DataFrame = field(default_factory=pd.DataFrame)  # date,code,cost_per_share
    pool_amplitude: list = field(default_factory=list)  # (code, 日均振幅)


def run_backtest(
    codes: list[str],
    bars: dict[str, pd.DataFrame],
    params: TTradeParams | None = None,
    total_cash: float = 1_000_000.0,
    index_bars: pd.DataFrame | None = None,
) -> BacktestResult:
    """核心回测循环：每标的独立等权子账户，逐bar决策、次bar开盘成交。"""
    params = params or TTradeParams()
    per_code_cash = total_cash / len(codes)
    result = BacktestResult(codes=[], total_cash=total_cash)
    equity_paths: dict[str, pd.Series] = {}
    bh_paths: dict[str, pd.Series] = {}
    active_codes: list[str] = []
    diluted_frames: list[pd.DataFrame] = []

    index_trend_hi = None
    if index_bars is not None and len(index_bars):
        index_frames = compute_frames(index_bars, params)
        index_trend_hi = pd.Series(
            index_frames.trend.values, index=pd.DatetimeIndex(index_frames.time)
        )

    # 振幅过滤的池内分位数复评：个股口径阈值在 ETF 池上常不可达，
    # 用池内 60 分位日均振幅封顶，避免全池被 3%式阈值一刀切停手
    amp_floor = params.amp_min
    code_amplitudes: dict[str, float] = {
        code_: _avg_daily_amp(df) for code_, df in bars.items()
    }
    result.pool_amplitude = sorted(code_amplitudes.items(), key=lambda kv: -kv[1])
    if params.amp_filter and code_amplitudes:
        pool_p60 = float(pd.Series(list(code_amplitudes.values())).quantile(0.6))
        amp_floor = min(params.amp_min, pool_p60)

    for code in codes:
        df = bars.get(code)
        if df is None or len(df) < params.ma_len + params.slope_n + params.rsi_len:
            continue
        # 主级别即交易级别：15m 主级别在聚合后的 15m bars 上驱动引擎（真·级别切换）
        drive = (
            resample_intraday(df, "15min").reset_index()
            if params.primary_level == "15m" else df
        )
        if len(drive) < params.ma_len + params.slope_n + params.rsi_len:
            continue
        frames = compute_frames(drive, params)
        hi_trend = higher_trend_series(df, params)
        hi_aligned = _align_trend(hi_trend, frames.time)
        idx_aligned = (
            _align_trend(index_trend_hi, frames.time)
            if index_trend_hi is not None else None
        )
        # 池内校准后的标的级振幅门槛（amp_filter 开启时）
        if params.amp_filter:
            code_amp = code_amplitudes[code]
            if code_amp < amp_floor:
                from loguru import logger

                logger.warning(
                    f"backtest: {code} 跳过（日均振幅 {code_amp:.2%} < 门槛 {amp_floor:.2%}）"
                )
                continue
        acc = SubAccount(
            total_cash=per_code_cash,
            commission_rate=params.commission_rate,
            min_commission=params.min_commission,
            slippage=params.slippage,
            lot_size=params.lot_size,
        )
        engine_ = TtEngine(params=params, unit_cash=acc.unit_cash)
        executor = _Executor(code=code, result=result, acc=acc, engine_=engine_)

        # 期初建满底仓（第一根 bar 开盘价）；纯持有基准用同一底仓 + 残余现金
        try:
            base_fill = acc.open_base_position(price=float(frames.open.iloc[0]))
        except TOrderRejected as e:
            # 等权资金买不满一手（高价标的）或首bar数据异常 → 跳过该标的
            from loguru import logger

            logger.warning(f"backtest: {code} 跳过（{e}）")
            continue
        active_codes.append(code)
        bh_cash = per_code_cash - (base_fill.shares * base_fill.price + base_fill.commission)

        days = frames.time.dt.date.values
        n = len(frames.time)
        pending: dict | None = None
        eq_rows: list[tuple] = []  # (date, 做T净值, 纯持有净值)——日终实点，无前视
        diluted_rows: list[tuple] = []  # (date, code, 摊薄成本/股)

        for i in range(n):
            if i == 0 or days[i] != days[i - 1]:
                # 跨日：引擎层的 LEG2_PENDING 死单清理触发前，账户层先把未对冲腿强平
                # 复位底仓（执行器已失败/无次bar可成交的场景）—— 严格闭环不允许隔夜挂单
                if i != 0 and acc.pending_t_shares is not None:
                    prev_close = float(frames.close.iloc[i - 1])
                    acc.force_close_stale(prev_close)
                acc.on_day_start()

            # ---- 执行上一bar产生的委托（本bar开盘价）----
            if pending is not None:
                executor.execute(pending, float(frames.open.iloc[i]),
                                 str(frames.time.iloc[i]))
                pending = None

            # ---- 本bar收盘：产生决策 ----
            amp_frac = frames.day_range.iloc[i]
            day_amp_ok = (
                bool(amp_frac >= params.amp_dead)
                if np.isfinite(amp_frac) else False
            ) if params.amp_filter else True
            ctx = BarContext(
                bar_time=frames.time.iloc[i].to_pydatetime(),
                close=float(frames.close.iloc[i]),
                rsi=float(frames.rsi.iloc[i]),
                primary_trend=frames.trend.iloc[i],
                available_to_sell=acc.available_to_sell,
                day_amp_ok=day_amp_ok,
                fractal_buy_ok=bool(frames.fractal_buy.iloc[i]),
                fractal_sell_ok=bool(frames.fractal_sell.iloc[i]),
                volume_shrink_ok=bool(frames.vol_shrink.iloc[i]),
                higher_trend=hi_aligned[i],
                index_trend=idx_aligned[i] if idx_aligned is not None else None,
                dev_above_ma=bool(frames.dev_above.iloc[i]),
                dev_below_ma=bool(frames.dev_below.iloc[i]),
            )
            decision = engine_.on_bar(ctx)
            is_day_end = (i == n - 1) or (days[i + 1] != days[i])

            if decision.action != TtAction.NONE:
                if i + 1 < n and days[i + 1] == days[i]:
                    pending = {
                        "action": decision.action,
                        "direction": decision.direction,
                        "shares": decision.shares,
                        "signal_time": str(frames.time.iloc[i]),
                        "reason": decision.reason,
                    }
                elif is_day_end and decision.action != TtAction.OPEN_T:
                    # 数据缺口：日末无次bar可执行——对冲类委托按当bar收盘应急成交
                    executor.execute(decision.__dict__, float(frames.close.iloc[i]),
                                     str(frames.time.iloc[i]))
                # 开仓类跨日委托直接丢弃（严格闭环模式不该出现；宽松模式由隔夜语义覆盖）

            # ---- 日终对账与净值实点 ----
            if is_day_end:
                assert acc.base_intact(), (
                    f"{code} 日终底仓对账失败: held={acc.shares_held} "
                    f"base={acc.base_shares} (bar {frames.time.iloc[i]})"
                )
                day_close = float(frames.close.iloc[i])
                eq_rows.append((
                    days[i],
                    acc.cash + acc.shares_held * day_close,
                    bh_cash + acc.base_shares * day_close,
                ))
                diluted_rows.append((days[i], code, acc.diluted_cost_per_share))

        if acc.pending_t_shares is not None:
            result.trades.append({
                "code": code, "direction": "未闭环", "profit": np.nan,
                "note": "数据结束仍有未平T单（不应在严格闭环模式下发生）",
                **({"leg1_time": executor.open_leg["leg1_time"]}
                   if executor.open_leg else {}),
            })

        eq_dates = [pd.Timestamp(d) for d, _, _ in eq_rows]
        equity_paths[code] = pd.Series(
            [e for _, e, _ in eq_rows], index=eq_dates)
        bh_paths[code] = pd.Series(
            [b for _, _, b in eq_rows], index=eq_dates)
        diluted_frames.append(pd.DataFrame(
            diluted_rows, columns=["date", "code", "cost_per_share"]))
    result.codes = active_codes
    result.amp_floor = amp_floor if params.amp_filter else 0.0
    result.diluted = (
        pd.concat(diluted_frames, ignore_index=True) if diluted_frames
        else pd.DataFrame(columns=["date", "code", "cost_per_share"])
    )
    result.final_equity = float(
        np.nansum([p.iloc[-1] for p in equity_paths.values()])
    ) if equity_paths else 0.0
    result.final_bh_equity = float(
        np.nansum([p.iloc[-1] for p in bh_paths.values()])
    ) if bh_paths else 0.0
    result.equity = _combine_daily(equity_paths, bh_paths)
    profits = [t["profit"] for t in closed_trades(result)]
    result.total_profit = float(np.nansum(profits)) if profits else 0.0
    result.win_count = sum(1 for p in profits if p > 0)
    if not result.equity.empty:
        result.start = str(result.equity["date"].iloc[0].date())
        result.end = str(result.equity["date"].iloc[-1].date())
    return result


@dataclass
class _Executor:
    """单标的委托执行器：decision → 账户成交 + 引擎确认 + T单台账。

    契约（对应 engine.py 模块 docstring）：eod_force_leg1_unconfirmed 的
    FORCE_CLOSE 到来时，若开仓腿实际已成交则正常对冲；未成交（无在场T单）
    则拒绝会沿 resolve_stale_order 复位状态机。
    """

    code: str
    result: BacktestResult
    acc: SubAccount
    engine_: TtEngine
    open_leg: dict | None = None

    def execute(self, order: dict, price: float, exec_time: str) -> None:
        try:
            if order["action"] == TtAction.OPEN_T:
                self._open(order, price, exec_time)
            else:
                try:
                    self._close(order, price, exec_time)
                except TOrderRejected as e:
                    if "no pending" in str(e):
                        raise
                    # 对冲腿是铁律：正常路径被拒（如现金不足）→ 强平重试
                    self.result.trades.append({
                        "code": self.code, "direction": "强平重试", "note": str(e),
                        "signal_time": order["signal_time"],
                    })
                    self._close(order, price, exec_time, force=True)
        except TOrderRejected as e:
            self.result.trades.append({
                "code": self.code, "direction": "被拒", "note": str(e),
                "signal_time": order["signal_time"],
            })
            if order["action"] == TtAction.OPEN_T or "no pending" in str(e):
                self.engine_.resolve_stale_order()
            else:
                self.engine_.on_fill_leg()

    def _open(self, order: dict, price: float, exec_time: str) -> None:
        fill = self.acc.open_t(
            direction=order["direction"], shares=order["shares"], price=price
        )
        self.result.open_t_count += 1
        self.engine_.on_fill_leg()  # 开仓腿成交确认 → LEG2_PENDING
        self.open_leg = {
            "code": self.code,
            "direction": order["direction"].label,  # 正T / 反T
            "leg1_time": exec_time,
            "leg1_price": fill.price,
            "leg1_shares": fill.shares,
            "signal_time": order["signal_time"],
            "trigger": order["reason"],
        }

    def _close(self, order: dict, price: float, exec_time: str,
               force: bool = False) -> None:
        fill, profit = self.acc.close_t(price=price, force=force)
        row = dict(self.open_leg or {"direction": "未知", "code": self.code})
        leg1_price = row.get("leg1_price")
        holding_bars = None
        if row.get("leg1_time"):
            delta = pd.Timestamp(exec_time) - pd.Timestamp(row["leg1_time"])
            holding_bars = int(delta / pd.Timedelta(minutes=5))
        profit_bp = (
            profit / (fill.shares * leg1_price) * 10_000.0
            if leg1_price and fill.shares else np.nan
        )
        row.update({
            "leg2_time": exec_time,
            "leg2_price": fill.price,
            "leg2_shares": fill.shares,
            "profit": profit,
            "profit_bp": round(float(profit_bp), 2) if np.isfinite(profit_bp) else np.nan,
            "holding_bars": holding_bars,
            "hedge_reason": order["reason"],
        })
        self.result.trades.append(row)
        self.open_leg = None
        self.engine_.on_fill_leg()


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
    """产物集：trades / equity / summary / params / dilution / html + 深度审计副本。"""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    # 深度审计报告拷入产物目录，避免"详见 data_audit.md"指针悬空
    audit_src = outdir.parent / "data_audit.md"
    if audit_src.exists() and not (outdir / "data_audit.md").exists():
        (outdir / "data_audit.md").write_text(
            audit_src.read_text(encoding="utf-8"), encoding="utf-8"
        )
    paths = {}
    trades_path = outdir / "trades.csv"
    pd.DataFrame(result.trades).to_csv(trades_path, index=False, encoding="utf-8-sig")
    paths["trades"] = trades_path
    equity_path = outdir / "equity.csv"
    result.equity.to_csv(equity_path, index=False, encoding="utf-8-sig")
    paths["equity"] = equity_path
    import json as _json

    params_path = outdir / "params.json"
    snapshot = {
        "params": _json.loads(params.to_json()),
        "run": {
            "codes": result.codes,
            "total_cash": result.total_cash,
            "start": result.start,
            "end": result.end,
        },
    }
    params_path.write_text(
        _json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    paths["params"] = params_path
    summary_path = outdir / "summary.md"
    summary_path.write_text(render_summary(result, params), encoding="utf-8")
    paths["summary"] = summary_path
    # 完整化产物（#8）：摊薄曲线 + 自包含 HTML 报告
    from quant_etf.t_trade.report import write_dilution_csv, write_html

    paths["dilution"] = write_dilution_csv(result, outdir)
    paths["html"] = write_html(result, paths, outdir)
    return paths


def closed_trades(result: BacktestResult) -> list[dict]:
    """已闭环（有合法差价）的 T 单。"""
    return [
        t for t in result.trades
        if isinstance(t.get("profit"), (int, float)) and np.isfinite(t["profit"])
    ]


def render_summary(result: BacktestResult, params: TTradeParams) -> str:
    closed = closed_trades(result)
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
    avg_profit = result.total_profit / n_trades if n_trades else 0.0
    return (
        "# 做T回测摘要（核心层）\n\n"
        f"- 样本区间: {result.start} → {result.end}"
        f"（样本较短，详见 data_audit.md 深度审计）\n"
        f"- 标的数: {len(result.codes)}，主级别: {params.primary_level}\n"
        f"- 主口径 做T vs 纯持有: {result.final_equity:,.0f} vs {result.final_bh_equity:,.0f}"
        f"（增量 {excess:+,.0f}，年化差 {ann:+.2%}）\n"
        f"- T单: {n_trades} 笔（正T {fwd} / 反T {rev}），胜率 {win_rate:.1%}，"
        f"平均差价 {avg_profit:+,.0f}，累计 {result.total_profit:+,.0f}\n"
    )
