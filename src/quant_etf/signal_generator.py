"""
信号生成器

分层筛选、评分排序，生成最终的交易信号
"""

import pandas as pd
from typing import List, Dict
from loguru import logger
from datetime import datetime

from quant_etf.market_analyzer import MarketState
from quant_etf.strategy_selector import StrategySelector
from quant_etf.strategies.momentum_breakthrough import StrategySignal


class SignalGenerator:
    """信号生成器"""

    def __init__(self, top_n: int = 10):
        """
        初始化信号生成器
        :param top_n: 最多返回的信号数量
        """
        self.top_n = top_n
        self.strategy_selector = StrategySelector()

    def generate_signals(
        self, pool_data: Dict[str, pd.DataFrame], market_state: MarketState
    ) -> List[StrategySignal]:
        """
        生成信号（分层筛选）
        :param pool_data: ETF池数据 {code: DataFrame}
        :param market_state: 市场状态
        :return: 信号列表
        """
        selected_strategies = self.strategy_selector.select_strategies(market_state)

        logger.info(
            f"Market: {market_state.market_type.value}, Selected strategies: {selected_strategies}"
        )

        all_signals = []

        for strategy_name in selected_strategies:
            strategy = self.strategy_selector.get_strategy(strategy_name)
            if not strategy:
                logger.warning(f"Strategy {strategy_name} not found")
                continue

            try:
                signals = strategy.analyze_pool(pool_data)
                all_signals.extend(signals)
                logger.info(
                    f"Strategy {strategy_name} generated {len(signals)} signals"
                )
            except Exception as e:
                logger.error(f"Failed to run strategy {strategy_name}: {e}")

        deduplicated_signals = self._deduplicate_signals(all_signals)

        scored_signals = self._score_signals(deduplicated_signals, market_state)

        scored_signals.sort(key=lambda x: x.score, reverse=True)

        final_signals = scored_signals[: self.top_n]

        logger.info(
            f"Generated {len(final_signals)} signals (from {len(all_signals)} raw signals)"
        )

        return final_signals

    def _deduplicate_signals(
        self, signals: List[StrategySignal]
    ) -> List[StrategySignal]:
        """
        去重（同一代码的多个信号保留最高分的）
        """
        if not signals:
            return []

        code_signals = {}
        for signal in signals:
            if signal.code not in code_signals:
                code_signals[signal.code] = signal
            else:
                if signal.score > code_signals[signal.code].score:
                    code_signals[signal.code] = signal

        return list(code_signals.values())

    def _score_signals(
        self, signals: List[StrategySignal], market_state: MarketState
    ) -> List[StrategySignal]:
        """
        对信号进行综合评分
        """
        for signal in signals:
            base_score = signal.score

            market_bonus = 0
            if market_state.market_type.value == "牛市":
                market_bonus = 0.1
            elif market_state.market_type.value == "震荡市":
                market_bonus = 0.05

            trend_bonus = 0
            if signal.ma10 > signal.ma20 > signal.ma30:
                trend_bonus = 0.1

            signal.score = min(base_score + market_bonus + trend_bonus, 1.0)

        return signals

    def generate_signals_simple(
        self,
        pool_data: Dict[str, pd.DataFrame],
        market_state: MarketState,
        strategy_name: str = None,
    ) -> List[StrategySignal]:
        """
        简化版信号生成（使用指定策略或推荐策略）
        :param pool_data: ETF池数据
        :param market_state: 市场状态
        :param strategy_name: 策略名称，如果为None则使用推荐策略
        :return: 信号列表
        """
        if strategy_name is None:
            strategy_name = self.strategy_selector.get_strategy_recommendation(
                market_state
            )

        logger.info(f"Using strategy: {strategy_name}")

        strategy = self.strategy_selector.get_strategy(strategy_name)
        if not strategy:
            logger.error(f"Strategy {strategy_name} not found")
            return []

        try:
            signals = strategy.analyze_pool(pool_data)

            deduplicated_signals = self._deduplicate_signals(signals)

            scored_signals = self._score_signals(deduplicated_signals, market_state)

            scored_signals.sort(key=lambda x: x.score, reverse=True)

            final_signals = scored_signals[: self.top_n]

            return final_signals

        except Exception as e:
            logger.error(f"Failed to generate signals: {e}")
            return []
