import numpy as np
import pandas as pd

from quant_etf.strategy import StrategyEngine


def _make_rebound_df(high_120: float, low_20: float, current: float, last_volume: float = 2000.0) -> pd.DataFrame:
    """
    构造满足“前期高位回撤 -> 近期止跌 -> 重新回升”筛选条件的模拟数据。
    """
    closes: list[float] = []
    closes += [70.0] * 40
    closes += list(np.linspace(70.0, high_120, 40, endpoint=True))
    closes += list(np.linspace(high_120, low_20 + 5.0, 60, endpoint=True))

    last20_head = [
        low_20 + 2.0,
        low_20 + 1.0,
        low_20,
        low_20 + 1.0,
        low_20 + 2.0,
        low_20 + 3.0,
        low_20 + 4.0,
        low_20 + 5.0,
        low_20 + 6.0,
        low_20 + 7.0,
    ]
    last10_tail = list(np.linspace(last20_head[-1], current, 10, endpoint=True))
    closes += last20_head + last10_tail

    volumes = [1000.0] * (len(closes) - 1) + [float(last_volume)]

    df = pd.DataFrame(
        {"close": closes, "volume": volumes},
        index=pd.date_range("2024-01-01", periods=len(closes), freq="D"),
    )
    return df


def test_calculate_rebound_stock_score_passes_filters():
    engine = StrategyEngine()
    df = _make_rebound_df(high_120=100.0, low_20=80.0, current=88.0, last_volume=2500.0)

    score = engine.calculate_rebound_stock_score("000001", df)
    assert score is not None
    assert score.stabilization_ok is True
    assert score.rebound_ok is True
    assert score.drawdown_from_120d_high <= -0.12
    assert score.bounce_from_20d_low >= 0.04


def test_calculate_rebound_stock_score_fails_when_drawdown_too_small():
    engine = StrategyEngine()
    df = _make_rebound_df(high_120=100.0, low_20=90.0, current=95.0, last_volume=2500.0)

    score = engine.calculate_rebound_stock_score("000001", df)
    assert score is None


def test_rank_stocks_for_mid_term_rebound_sorts_by_score_desc():
    engine = StrategyEngine()

    df_weaker = _make_rebound_df(high_120=100.0, low_20=80.0, current=88.0, last_volume=1200.0)
    df_stronger = _make_rebound_df(high_120=110.0, low_20=70.0, current=80.0, last_volume=3000.0)

    stock_data = {"000001": df_weaker, "000002": df_stronger}
    picks = engine.rank_stocks_for_mid_term_rebound(stock_data, top_n=1)

    assert len(picks) == 1
    assert picks[0].code == "000002"
