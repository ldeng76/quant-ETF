import pandas as pd

from quant_etf.strategy import StrategyEngine


def _make_df(closes, volumes):
    df = pd.DataFrame(
        {
            "close": closes,
            "volume": volumes,
        },
        index=pd.date_range("2024-01-01", periods=len(closes), freq="D"),
    )
    return df


def test_rank_stocks_for_short_term_picks_high_momentum():
    engine = StrategyEngine()

    base_close = list(range(1, 101))
    base_volume = [1000] * 100

    df_strong = _make_df(base_close, base_volume[:-1] + [4000])
    df_weak = _make_df(list(reversed(base_close)), base_volume)

    stock_data = {
        "000001": df_weak,
        "000002": df_strong,
    }

    picks = engine.rank_stocks_for_short_term(stock_data, top_n=1)
    assert len(picks) == 1
    assert picks[0].code == "000002"


def test_calculate_short_term_stock_score_requires_min_length():
    engine = StrategyEngine()
    df_short = _make_df(list(range(1, 30)), [1000] * 29)
    score = engine.calculate_short_term_stock_score("000001", df_short)
    assert score is None

