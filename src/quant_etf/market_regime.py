"""
大盘状态评估模块

通过 4 只指数 ETF 的动量分数评估大盘状态，
与滚动历史中位数比较判断牛市/弱市。
"""

from dataclasses import dataclass
from typing import Dict

import pandas as pd
import numpy as np
from loguru import logger

from quant_etf.conf import INDEX_WEIGHTS, MARKET_REGIME_CONFIG
from quant_etf.strategy import StrategyEngine


@dataclass
class MarketAssessment:
    """大盘评估结果"""
    market_score: float       # 当前大盘综合分数
    median_score: float       # 滚动中位数
    is_bullish: bool          # 是否牛市
    mode: str                 # "aggressive" or "defensive"
    top_n: int                # 建议持仓数
    risk_discount: float      # 风控折扣系数
    index_scores: Dict[str, float]  # 各指数分数


def assess_market(
    index_data: Dict[str, pd.DataFrame],
    bar_interval: str = "1d",
    lookback: int = 20,
) -> MarketAssessment:
    """
    评估大盘状态。

    对每只指数 ETF，回算最近 lookback 个交易日的动量分数，
    得到 market_score 序列，与中位数比较判断牛/熊。

    Args:
        index_data: 指数 ETF 的 K 线数据 {code: DataFrame}
        bar_interval: K 线周期
        lookback: 回算天数（用于计算滚动中位数）
    """
    engine = StrategyEngine(bar_interval=bar_interval)
    weights = engine.weights  # 归一化后的 MOMENTUM_WEIGHTS

    # 对每只指数计算每日滚动 score
    # 需要 61 根 K 线才能算 p60，所以每只指数至少需要 61 + lookback 根
    daily_scores = {}  # {code: [score_day1, score_day2, ...]}

    for code, w in INDEX_WEIGHTS.items():
        if code not in index_data or index_data[code].empty:
            logger.warning(f"Index {code} data missing, skipping")
            continue

        df = index_data[code]
        close = df["close"].values
        n = len(close)

        # 需要至少 61 根才能算一次 p60
        if n < 61:
            logger.warning(f"Index {code} has only {n} bars, need 61")
            continue

        scores = []
        # 从最新开始往前回算 lookback 个交易日
        for i in range(lookback):
            end = n - i
            start = max(0, end - 61 - 1)
            if end - start < 62:  # 61 bars + 1 anchor
                scores.append(None)
                continue

            slice_close = close[start:end]
            cur = slice_close[-1]
            try:
                p60 = slice_close[-(60 + 1)]
                p20 = slice_close[-(20 + 1)]
                p10 = slice_close[-(10 + 1)]
                p5 = slice_close[-(5 + 1)]
            except IndexError:
                scores.append(None)
                continue

            r60 = (cur - p60) / p60
            r20 = (cur - p20) / p20
            r10 = (cur - p10) / p10
            r5 = (cur - p5) / p5

            score = (r60 * weights["p60"] + r20 * weights["p20"]
                     + r10 * weights["p10"] + r5 * weights["p5"])
            scores.append(score)

        daily_scores[code] = scores

    if not daily_scores:
        # 无数据，默认保守
        config = MARKET_REGIME_CONFIG["defensive"]
        return MarketAssessment(
            market_score=0.0, median_score=0.0, is_bullish=False,
            mode="defensive", top_n=config["top_n"],
            risk_discount=config["risk_discount"],
            index_scores={},
        )

    # 计算每日 market_score 序列
    n_days = lookback
    market_scores = []
    for i in range(n_days):
        daily_total = 0.0
        daily_weight = 0.0
        for code, scores in daily_scores.items():
            if i < len(scores) and scores[i] is not None:
                idx_w = INDEX_WEIGHTS.get(code, 0)
                daily_total += scores[i] * idx_w
                daily_weight += idx_w
        if daily_weight > 0:
            market_scores.append(daily_total / daily_weight)
        else:
            market_scores.append(None)

    # 过滤 None
    valid_scores = [s for s in market_scores if s is not None]
    if not valid_scores:
        config = MARKET_REGIME_CONFIG["defensive"]
        return MarketAssessment(
            market_score=0.0, median_score=0.0, is_bullish=False,
            mode="defensive", top_n=config["top_n"],
            risk_discount=config["risk_discount"],
            index_scores={},
        )

    current_score = valid_scores[0]  # 最新一天
    median = float(np.median(valid_scores))
    is_bullish = current_score > median

    # 各指数当前分数
    index_current = {}
    for code, scores in daily_scores.items():
        if scores and scores[0] is not None:
            index_current[code] = scores[0]

    mode = "aggressive" if is_bullish else "defensive"
    config = MARKET_REGIME_CONFIG[mode]

    logger.info(
        f"Market regime: score={current_score:.4f}, median={median:.4f}, "
        f"mode={mode}, top_n={config['top_n']}"
    )

    return MarketAssessment(
        market_score=current_score,
        median_score=median,
        is_bullish=is_bullish,
        mode=mode,
        top_n=config["top_n"],
        risk_discount=config["risk_discount"],
        index_scores=index_current,
    )
