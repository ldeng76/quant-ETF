"""
策略选择器

根据市场状态自动选择适合的策略
"""

from typing import List, Optional
from loguru import logger

from quant_etf.market_analyzer import MarketState, MarketType
from quant_etf.strategies.momentum_breakthrough import MomentumBreakthroughStrategy
from quant_etf.strategies.volume_price import VolumePriceStrategy


class StrategySelector:
    """策略选择器"""

    def __init__(self):
        """
        初始化策略选择器
        """
        self.strategies = {
            "momentum": MomentumBreakthroughStrategy(),
            "volume": VolumePriceStrategy(),
        }

    def select_strategies(self, market_state: MarketState) -> List[str]:
        """
        根据市场状态选择适合的策略
        :param market_state: 市场状态
        :return: 策略名称列表
        """
        selected = []
        market_type = market_state.market_type

        for name, strategy in self.strategies.items():
            if strategy.is_suitable_for_market(market_type):
                selected.append(name)
                logger.info(f"Strategy {name} is suitable for {market_type.value}")

        if not selected:
            logger.warning(
                f"No strategy suitable for {market_type.value}, using default"
            )
            selected = ["momentum", "volume"]

        return selected

    def get_strategy(self, name: str):
        """
        获取策略实例
        :param name: 策略名称
        :return: 策略实例
        """
        return self.strategies.get(name)

    def get_all_strategies(self):
        """
        获取所有策略
        :return: 策略字典
        """
        return self.strategies

    def get_strategy_recommendation(self, market_state: MarketState) -> str:
        """
        获取策略推荐
        :param market_state: 市场状态
        :return: 推荐策略名称
        """
        if market_state.market_type == MarketType.BULL:
            return "volume"
        elif market_state.market_type == MarketType.BEAR:
            return "momentum"
        else:
            if market_state.volatility < 0.02:
                return "volume"
            else:
                return "momentum"
