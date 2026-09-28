"""_align_trend 的陈旧状态回归测试。

真实 bug（2026-09-28 发现）：指数数据只到 2026-06-03，但 ffill 无上限，
把 6-03 的大盘方向一路沿用到 9-28。结果是：
  - 用三个月前的市场状态去拦/放当天的交易（误拦合法信号）
  - 给 trades.csv 打上并不成立的 `index` 标签
"""
import pandas as pd

from quant_etf.t_trade.backtest import _align_trend
from quant_etf.t_trade.classifier import Trend


def _series(index, values):
    return pd.Series(values, index=pd.DatetimeIndex(index))


def test_ffill_within_gaps_is_preserved():
    """数据范围内的间隙（午休/隔夜）仍应正常前向填充。"""
    trend = _series(
        ["2026-06-03 10:00", "2026-06-03 10:05"],
        [Trend.UP, Trend.UP],
    )
    times = pd.Series(pd.to_datetime(
        ["2026-06-03 10:00", "2026-06-03 11:30", "2026-06-03 13:00"]
    ))
    out = _align_trend(trend, times)
    assert out == [Trend.UP, Trend.UP, Trend.UP]


def test_no_until_keeps_legacy_behavior():
    """不传 until 时保持原有行为（自身数据序列跨末端继续填充）。"""
    trend = _series(["2026-06-03 10:00"], [Trend.UP])
    times = pd.Series(pd.to_datetime(["2026-06-03 10:00", "2026-09-28 10:00"]))
    out = _align_trend(trend, times)
    assert out == [Trend.UP, Trend.UP]


def test_beyond_until_becomes_none():
    """
    核心回归：超过指数数据末端后必须置 None，
    不能把陈旧的大盘方向当成当下行情。
    """
    trend = _series(
        ["2026-06-03 14:00", "2026-06-03 14:05"],
        [Trend.UP, Trend.DOWN],
    )
    times = pd.Series(pd.to_datetime(
        ["2026-06-03 14:00", "2026-06-03 14:05", "2026-09-11 09:50", "2026-09-28 13:00"]
    ))
    until = pd.Timestamp("2026-06-03 14:05")

    out = _align_trend(trend, times, until=until)

    assert out[0] == Trend.UP
    assert out[1] == Trend.DOWN
    # 越界后为 None —— 门控放行，而非用 6-03 的方向
    assert out[2] is None
    assert out[3] is None


def test_before_first_observation_is_none():
    """数据起点之前也应为 None（ffill 不会回填未来值，这一点不能被破坏）。"""
    trend = _series(["2026-06-03 14:00"], [Trend.UP])
    times = pd.Series(pd.to_datetime(["2026-05-01 10:00", "2026-06-03 14:00"]))
    out = _align_trend(trend, times, until=pd.Timestamp("2026-06-03 14:00"))
    assert out[0] is None
    assert out[1] == Trend.UP


def test_stale_value_would_have_been_misused():
    """
    反向断言：若没有 until 界，末点会被填成 3 个月前的方向——
    证明这个修复确实堵住了误用路径。
    """
    trend = _series(["2026-06-03 14:00"], [Trend.UP])
    times = pd.Series(pd.to_datetime(["2026-09-11 09:50"]))
    until = pd.Timestamp("2026-06-03 14:00")

    assert _align_trend(trend, times)[0] == Trend.UP      # 旧行为：陈旧方向
    assert _align_trend(trend, times, until=until)[0] is None  # 新行为：不可得
