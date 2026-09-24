"""做T回测报告：摊薄曲线产物与自包含 HTML 报告。"""

from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import BacktestResult, closed_trades


def write_dilution_csv(result: BacktestResult, outdir: Path) -> Path:
    """每标的摊薄成本曲线（date, code, cost_per_share）。"""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / "dilution.csv"
    result.diluted.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def _equity_svg(equity: pd.DataFrame, width: int = 720, height: int = 200) -> str:
    """净值曲线内联 SVG（无外部依赖）。"""
    if equity.empty:
        return "<p>无净值数据</p>"
    t = equity["t_trade"].astype(float).values
    b = equity["buy_hold"].astype(float).values
    lo = min(t.min(), b.min())
    span = max(max(t.max(), b.max()) - lo, 1e-9)

    def to_points(vals: np.ndarray) -> str:
        return " ".join(
            f"{width * i / max(len(vals) - 1, 1):.1f},"
            f"{height - (height - 20) * (v - lo) / span - 10:.1f}"
            for i, v in enumerate(vals)
        )

    return (
        f'<svg width="{width}" height="{height}" '
        'style="border:1px solid #ddd;background:#fafafa">'
        f'<polyline fill="none" stroke="#c0392b" stroke-width="1.5" '
        f'points="{to_points(t)}"/>'
        f'<polyline fill="none" stroke="#2a5fd6" stroke-width="1.5" '
        f'points="{to_points(b)}"/>'
        f'<text x="8" y="16" font-size="12" fill="#c0392b">做T</text>'
        f'<text x="48" y="16" font-size="12" fill="#2a5fd6">纯持有</text>'
        "</svg>"
    )


def render_html(result: BacktestResult, artifacts: dict[str, Path]) -> str:
    """自包含 HTML 报告：摘要 + 净值曲线 + T单分解 + 池内振幅分布。"""
    closed = closed_trades(result)
    n = len(closed)
    fwd = [t for t in closed if t.get("direction") == "正T"]
    rev = [t for t in closed if t.get("direction") == "反T"]
    wins = [t for t in closed if t.get("profit", 0) > 0]

    def _line(label: str, rows: list[dict]) -> str:
        if not rows:
            return f"<tr><td>{label}</td><td>0</td><td>-</td><td>-</td><td>-</td></tr>"
        profits = [t["profit"] for t in rows]
        w = sum(1 for p in profits if p > 0)
        return (
            f"<tr><td>{label}</td><td>{len(rows)}</td>"
            f"<td>{w / len(rows):.1%}</td>"
            f"<td>{np.mean(profits):+.2f}</td><td>{np.sum(profits):+,.2f}</td></tr>"
        )

    amp_rows = "".join(
        f"<tr><td>{c}</td><td>{a:.2%}</td></tr>"
        for c, a in result.pool_amplitude[:15]
    )
    dil_last = ""
    if not result.diluted.empty:
        last = result.diluted.sort_values("date").groupby("code").tail(1)
        dil_rows = [
            f"<tr><td>{r.code}</td><td>{r.cost_per_share:.4f}</td></tr>"
            for r in last.itertuples() if r.code in result.codes
        ][:15]
        dil_last = "".join(dil_rows)

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>做T回测报告</title>
<style>
body{{font-family:"Microsoft YaHei",sans-serif;margin:24px;color:#1a1d24}}
table{{border-collapse:collapse;margin:8px 0;font-size:14px}}
td,th{{border:1px solid #ddd;padding:6px 10px}}
th{{background:#f7f8fa}}
h2{{border-left:4px solid #d9382c;padding-left:10px}}
.note{{color:#7b8393;font-size:13px}}
</style></head><body>
<h1>做T回测报告</h1>
<p class="note">样本区间: {result.start} → {result.end}
（{len(result.codes)} 标的，总资金 {result.total_cash:,.0f}；
样本与数据深度详见 data_audit.md，不外推全形态结论）</p>
<h2>主口径：做T vs 纯持有</h2>
<p>终值 {result.final_equity:,.0f} vs {result.final_bh_equity:,.0f}
（增量 {result.final_equity - result.final_bh_equity:+,.0f}）</p>
{_equity_svg(result.equity)}
<h2>T 单分解</h2>
<table><tr><th>方向</th><th>笔数</th><th>胜率</th><th>平均差价</th><th>累计差价</th></tr>
{_line("全部", closed)}
{_line("正T", fwd)}
{_line("反T", rev)}
</table>
<p>胜 {len(wins)} 笔 / 共 {n} 笔</p>
<h2>池内日均振幅分布（校准依据，前 15）</h2>
<table><tr><th>code</th><th>日均振幅</th></tr>{amp_rows}</table>
<h2>期末摊薄成本/股（前 15）</h2>
<table><tr><th>code</th><th>摊薄成本</th></tr>{dil_last}</table>
<h2>产物清单</h2>
<ul>{"".join(f"<li>{k}: {v.name}</li>" for k, v in artifacts.items())}</ul>
</body></html>"""


def write_html(result: BacktestResult, artifacts: dict[str, Path], outdir: Path) -> Path:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / "report.html"
    path.write_text(render_html(result, artifacts), encoding="utf-8")
    return path
