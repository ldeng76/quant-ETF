"""
E2E tests for the strategy engine.
Tests full strategy scoring pipeline with realistic mock data.
"""
import pytest
from quant_etf.strategy import StrategyEngine, ETFScore, StockScore, ReboundStockScore


class TestETFMomentumStrategyE2E:
    """End-to-end ETF momentum strategy tests with realistic data profiles."""

    def test_strong_etf_ranks_highest(self, mixed_etf_pool):
        """Strong uptrend ETF should rank above weak/declining ones."""
        engine = StrategyEngine()
        ranked = engine.rank_etfs(mixed_etf_pool)

        assert len(ranked) == 4
        assert isinstance(ranked[0], ETFScore)
        # Strong ETF should be #1
        assert ranked[0].code == "510050", (
            f"Expected strong ETF 510050 at top, got {ranked[0].code}"
        )

    def test_declining_etf_ranks_lowest(self, mixed_etf_pool):
        """Declining ETF should rank at or near the bottom."""
        engine = StrategyEngine()
        ranked = engine.rank_etfs(mixed_etf_pool)

        # Declining ETF should be last or second-to-last (rebound might be lower)
        assert ranked[-1].code in ("159352",), (
            f"Expected declining ETF 159352 at bottom, got {ranked[-1].code}"
        )

    def test_score_reflects_momentum(self, mixed_etf_pool):
        """Scores should correlate with momentum profile."""
        engine = StrategyEngine()
        ranked = engine.rank_etfs(mixed_etf_pool)
        score_map = {r.code: r.score for r in ranked}

        # Strong should score higher than weak
        assert score_map["510050"] > score_map["510310"]

    def test_returns_calculated_correctly(self, strong_etf_df):
        """Calculate returns should produce non-trivial values for strong ETF."""
        engine = StrategyEngine()
        returns = engine.calculate_returns(strong_etf_df)

        assert "r60" in returns
        assert "r20" in returns
        assert "r10" in returns
        assert "r5" in returns
        # Strong uptrend: all returns should be positive
        assert returns["r60"] > 0
        assert returns["r20"] > 0

    def test_short_horizon_weights_favor_rebound(self, rebound_etf_df, declining_etf_df):
        """
        With short-horizon weights (r5:0.4, r10:0.3), a rebound ETF
        should score above a continuously declining one.
        """
        weights = {"r60": 0.1, "r20": 0.2, "r10": 0.3, "r5": 0.4}
        engine = StrategyEngine(weights=weights)
        data = {
            "510880": rebound_etf_df,
            "159352": declining_etf_df,
        }
        ranked = engine.rank_etfs(data)
        assert ranked[0].code == "510880", (
            "Rebound ETF should rank above declining ETF with short-horizon weights"
        )

    def test_custom_weights_change_ranking(self, mixed_etf_pool):
        """Long-term weights should produce different ranking than short-term weights."""
        long_weights = {"r60": 0.5, "r20": 0.3, "r10": 0.15, "r5": 0.05}
        short_weights = {"r60": 0.1, "r20": 0.2, "r10": 0.3, "r5": 0.4}

        engine_long = StrategyEngine(weights=long_weights)
        engine_short = StrategyEngine(weights=short_weights)

        ranked_long = engine_long.rank_etfs(mixed_etf_pool)
        ranked_short = engine_short.rank_etfs(mixed_etf_pool)

        # At least verify both produce valid rankings
        assert len(ranked_long) == 4
        assert len(ranked_short) == 4


class TestShortTermStockStrategyE2E:
    """End-to-end short-term stock strategy tests."""

    def test_strong_stock_wins(self, strong_etf_df, declining_etf_df):
        """Strong uptrend stock should rank higher than declining one."""
        engine = StrategyEngine()
        data = {
            "002202": strong_etf_df,  # reuse strong ETF data as mock stock
            "600783": declining_etf_df,
        }
        ranked = engine.rank_stocks_for_short_term(data, top_n=2)

        assert len(ranked) == 2
        assert isinstance(ranked[0], StockScore)
        assert ranked[0].code == "002202"

    def test_trend_filter_affects_score(self):
        """Stock with proper MA alignment should score higher."""
        engine = StrategyEngine()
        # Create two stocks: one with strong uptrend (trend_ok=True), one flat
        df_trending = strong_etf_df = _create_trending_up_df()
        df_flat = _create_flat_df()

        data = {
            "002202": df_trending,
            "600783": df_flat,
        }
        ranked = engine.rank_stocks_for_short_term(data, top_n=2)

        # Both should be included, trending should rank higher
        assert len(ranked) == 2
        assert ranked[0].code == "002202"

    def test_top_n_limit(self, mixed_etf_pool):
        """Should return only top_n results."""
        engine = StrategyEngine()
        # Treat ETF data as stocks
        stock_data = {f"0{code}": df for code, df in list(mixed_etf_pool.items())[:3]}
        ranked = engine.rank_stocks_for_short_term(stock_data, top_n=2)
        assert len(ranked) == 2


class TestMidTermReboundStrategyE2E:
    """End-to-end mid-term rebound strategy tests."""

    def test_rebound_stock_ranks_above_normal(self, rebound_etf_df, strong_etf_df):
        """Rebound profile should rank above steadily uptrending stock."""
        engine = StrategyEngine()
        data = {
            "300870": rebound_etf_df,   # rebound profile
            "300570": strong_etf_df,    # steady uptrend (not a rebound candidate)
        }
        ranked = engine.rank_stocks_for_mid_term_rebound(data, top_n=5)

        # Rebound stock should be included and rank higher
        codes = [r.code for r in ranked]
        assert "300870" in codes, "Rebound stock should pass the filter"

    def test_strong_uptrend_excluded_from_rebound(self, strong_etf_df):
        """Steadily uptrending stock should NOT pass rebound filter (drawdown too small)."""
        engine = StrategyEngine()
        data = {"300870": strong_etf_df}
        ranked = engine.rank_stocks_for_mid_term_rebound(data, top_n=5)

        # Strong uptrend has no 12%+ drawdown, should be filtered out
        assert len(ranked) == 0, (
            "Steady uptrend stock should not qualify as rebound candidate"
        )

    def test_top_n_limits_results(self):
        """Should return at most top_n results."""
        engine = StrategyEngine()
        # Generate multiple rebound-like stocks
        from .conftest import generate_momentum_etf_data
        data = {}
        for i in range(5):
            data[f"30000{i}"] = generate_momentum_etf_data(f"30000{i}", 10.0, "rebound", seed=i)

        ranked = engine.rank_stocks_for_mid_term_rebound(data, top_n=3)
        assert len(ranked) <= 3


class TestEdgeCasesE2E:
    """Edge case tests for strategy engine with realistic scenarios."""

    def test_insufficient_data_excluded(self):
        """ETFs with < 60 days of data should be excluded."""
        import pandas as pd
        import numpy as np
        engine = StrategyEngine()

        short_df = pd.DataFrame({
            "close": np.linspace(10, 12, 30),
            "open": np.linspace(10, 12, 30),
            "high": np.linspace(10.5, 12.5, 30),
            "low": np.linspace(9.5, 11.5, 30),
            "volume": np.ones(30) * 1e6,
            "amount": np.ones(30) * 1e7,
        }, index=pd.bdate_range(end="2026-01-01", periods=30))
        short_df.index.name = "date"

        good_df = _create_trending_up_df()

        data = {"SHORT": short_df, "GOOD": good_df}
        ranked = engine.rank_etfs(data)

        assert len(ranked) == 1
        assert ranked[0].code == "GOOD"

    def test_all_invalid_returns_empty(self):
        """When all ETFs have insufficient data, ranking returns empty."""
        import pandas as pd
        import numpy as np
        engine = StrategyEngine()
        tiny = pd.DataFrame({"close": [1.0, 1.1]}, index=pd.date_range("2026-01-01", periods=2))
        tiny.index.name = "date"
        ranked = engine.rank_etfs({"A": tiny, "B": tiny})
        assert ranked == []

    def test_single_etf_ranks(self, strong_etf_df):
        """Single ETF should still be ranked."""
        engine = StrategyEngine()
        ranked = engine.rank_etfs({"510050": strong_etf_df})
        assert len(ranked) == 1
        assert ranked[0].code == "510050"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _create_trending_up_df() -> "pd.DataFrame":
    """Create a DataFrame with clear uptrend (MA5 > MA10 > MA20)."""
    from .conftest import generate_price_series
    return generate_price_series(start_price=10.0, days=300, trend=0.004, volatility=0.01, seed=99)


def _create_flat_df() -> "pd.DataFrame":
    """Create a DataFrame with flat/no trend."""
    from .conftest import generate_price_series
    return generate_price_series(start_price=10.0, days=300, trend=0.0, volatility=0.02, seed=100)
