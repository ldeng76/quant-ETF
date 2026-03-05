import pandas as pd
import numpy as np
from loguru import logger
from typing import Dict, List, Optional
from dataclasses import dataclass

from quant_etf.conf import MOMENTUM_WEIGHTS

@dataclass
class ETFScore:
    code: str
    score: float
    r60: float
    r20: float
    r10: float
    r5: float
    # 可以添加更多字段，如排名等

@dataclass
class StockScore:
    code: str
    score: float
    r60: float
    r20: float
    r10: float
    r5: float
    volume_ratio_1d_20d: float
    trend_ok: bool

@dataclass
class ReboundStockScore:
    code: str
    score: float
    drawdown_from_120d_high: float
    bounce_from_20d_low: float
    r20: float
    r10: float
    r5: float
    volume_ratio_1d_20d: float
    stabilization_ok: bool
    rebound_ok: bool

class StrategyEngine:
    def __init__(self, weights: Optional[Dict[str, float]] = None):
        """
        初始化策略引擎
        :param weights: 动量因子权重，如果为 None 则使用 conf.py 中的默认配置
        """
        if weights:
            self.weights = weights
        else:
            self.weights = MOMENTUM_WEIGHTS
        
        # 归一化权重，确保和为 1 (可选，但推荐)
        total = sum(self.weights.values())
        if abs(total - 1.0) > 1e-6:
             logger.warning(f"Weights do not sum to 1.0: {total}, re-normalizing...")
             for k in self.weights:
                 self.weights[k] /= total

    def calculate_returns(self, df: pd.DataFrame) -> Dict[str, float]:
        """
        计算单只 ETF 的各周期涨幅
        """
        if df.empty or len(df) < 60:
            return {}

        # 获取最新价格
        current_price = df.iloc[-1]["close"]
        
        # 计算各周期前的价格 (使用 shift 或者 iloc)
        # 注意：这里假设数据是连续的日线。如果有停牌，shift 可能不准确，但在 ETF 中影响较小
        # 更严谨的做法是按交易日历查找，这里简化处理
        
        try:
            p60 = df.iloc[-61]["close"]
            p20 = df.iloc[-21]["close"]
            p10 = df.iloc[-11]["close"]
            p5 = df.iloc[-6]["close"]
        except IndexError:
            # 数据长度不足
            return {}

        r60 = (current_price - p60) / p60
        r20 = (current_price - p20) / p20
        r10 = (current_price - p10) / p10
        r5 = (current_price - p5) / p5

        return {
            "r60": r60,
            "r20": r20,
            "r10": r10,
            "r5": r5
        }

    def normalize_scores(self, scores: List[ETFScore]) -> List[ETFScore]:
        """
        (可选) 对分数进行归一化处理，或者直接使用原始加权分
        这里我们暂时直接使用加权分
        """
        return scores

    def rank_etfs(self, etf_data: Dict[str, pd.DataFrame]) -> List[ETFScore]:
        """
        对 ETF 池进行打分和排序
        :param etf_data: 字典，Key为ETF代码，Value为DataFrame
        :return: 排序后的 ETFScore 列表
        """
        scores = []
        
        for code, df in etf_data.items():
            returns = self.calculate_returns(df)
            if not returns:
                logger.warning(f"Insufficient data for {code}, skipping.")
                continue
                
            # 计算加权得分
            # 这里简单直接加权。
            # 进阶优化：可以先对每个因子在所有ETF中进行 Rank (0-100)，再加权 Rank。
            # 这样可以避免某个极端涨幅扭曲结果。
            # 为保持简单，第一版先用原始涨幅加权。
            final_score = (
                returns["r60"] * self.weights["r60"] +
                returns["r20"] * self.weights["r20"] +
                returns["r10"] * self.weights["r10"] +
                returns["r5"] * self.weights["r5"]
            )
            
            scores.append(ETFScore(
                code=code,
                score=final_score,
                r60=returns["r60"],
                r20=returns["r20"],
                r10=returns["r10"],
                r5=returns["r5"]
            ))
            
        # 按分数降序排列
        scores.sort(key=lambda x: x.score, reverse=True)
        return scores

    def get_target_portfolio(self, ranked_scores: List[ETFScore], top_n: int = 5) -> Dict[str, float]:
        """
        根据排名生成目标持仓
        :param ranked_scores: 排序后的分数列表
        :param top_n: 持仓支数
        :return: 目标持仓权重 {code: weight}
        """
        target = {}
        if not ranked_scores:
            return target
            
        # 取前 N 名
        selected = ranked_scores[:top_n]
        
        # 简单等权分配
        # 也可以根据分数分配权重
        weight = 1.0 / len(selected)
        for item in selected:
            target[item.code] = weight
            
        return target

    def calculate_short_term_stock_score(self, code: str, df: pd.DataFrame) -> Optional[StockScore]:
        """
        计算单只股票的短线评分
        """
        returns = self.calculate_returns(df)
        if not returns:
            return None

        close_prices = df["close"]
        volume = df["volume"]

        if len(close_prices) < 60:
            return None

        ma5 = close_prices.rolling(window=5).mean().iloc[-1]
        ma10 = close_prices.rolling(window=10).mean().iloc[-1]
        ma20 = close_prices.rolling(window=20).mean().iloc[-1]
        current_close = close_prices.iloc[-1]

        trend_ok = bool(current_close > ma20 and ma5 > ma10 and ma10 > ma20)

        vol20 = volume.rolling(window=20).mean().iloc[-1]
        vol_last = volume.iloc[-1]
        if pd.isna(vol20) or vol20 <= 0:
            volume_ratio = 1.0
        else:
            volume_ratio = float(vol_last / vol20)

        momentum_score = (
            returns["r60"] * self.weights["r60"] +
            returns["r20"] * self.weights["r20"] +
            returns["r10"] * self.weights["r10"] +
            returns["r5"] * self.weights["r5"]
        )

        final_score = float(momentum_score + 0.03 * (volume_ratio - 1.0) + 0.02 * (1.0 if trend_ok else 0.0))

        return StockScore(
            code=code,
            score=final_score,
            r60=returns["r60"],
            r20=returns["r20"],
            r10=returns["r10"],
            r5=returns["r5"],
            volume_ratio_1d_20d=volume_ratio,
            trend_ok=trend_ok,
        )

    def rank_stocks_for_short_term(self, stock_data: Dict[str, pd.DataFrame], top_n: int = 5) -> List[StockScore]:
        """
        从股票池中挑选适合短线的股票
        """
        scores: List[StockScore] = []
        for code, df in stock_data.items():
            score = self.calculate_short_term_stock_score(code, df)
            if score is None:
                logger.warning(f"Insufficient data for stock {code}, skipping.")
                continue
            scores.append(score)

        scores.sort(key=lambda x: x.score, reverse=True)
        return scores[:top_n]

    def calculate_rebound_stock_score(self, code: str, df: pd.DataFrame) -> Optional[ReboundStockScore]:
        """
        计算“前期高位回撤 -> 近期止跌 -> 出现回升迹象”的评分
        """
        if df.empty or len(df) < 140:
            return None

        if "close" not in df.columns or "volume" not in df.columns:
            return None

        close_prices = df["close"].astype(float)
        volume = df["volume"].astype(float)

        current_close = float(close_prices.iloc[-1])
        if not np.isfinite(current_close) or current_close <= 0:
            return None

        high_120 = float(close_prices.iloc[-120:].max())
        if not np.isfinite(high_120) or high_120 <= 0:
            return None

        drawdown = current_close / high_120 - 1.0
        if drawdown > -0.12:
            return None

        low_20 = float(close_prices.iloc[-20:].min())
        if not np.isfinite(low_20) or low_20 <= 0:
            return None

        bounce_from_low = current_close / low_20 - 1.0
        if bounce_from_low < 0.04:
            return None

        min_idx_20 = int(close_prices.iloc[-20:].idxmin().toordinal())
        last_idx = int(close_prices.index[-1].toordinal())
        stabilization_ok = (last_idx - min_idx_20) >= 4

        returns = self.calculate_returns(df)
        if not returns:
            return None

        if returns["r5"] <= 0 or returns["r10"] < -0.01:
            return None

        ma5 = float(close_prices.rolling(window=5).mean().iloc[-1])
        ma10 = float(close_prices.rolling(window=10).mean().iloc[-1])
        rebound_ok = bool(current_close > ma5 and ma5 > ma10)

        vol20 = volume.rolling(window=20).mean().iloc[-1]
        vol_last = volume.iloc[-1]
        if pd.isna(vol20) or vol20 <= 0:
            volume_ratio = 1.0
        else:
            volume_ratio = float(vol_last / vol20)

        score = (
            (min(abs(drawdown), 0.6)) * 0.35
            + (min(bounce_from_low, 0.6)) * 0.40
            + max(returns["r5"], 0.0) * 0.15
            + max(returns["r10"], 0.0) * 0.05
            + max(volume_ratio - 1.0, 0.0) * 0.05
        )

        if not stabilization_ok:
            score *= 0.7
        if not rebound_ok:
            score *= 0.8

        return ReboundStockScore(
            code=code,
            score=float(score),
            drawdown_from_120d_high=float(drawdown),
            bounce_from_20d_low=float(bounce_from_low),
            r20=float(returns["r20"]),
            r10=float(returns["r10"]),
            r5=float(returns["r5"]),
            volume_ratio_1d_20d=float(volume_ratio),
            stabilization_ok=bool(stabilization_ok),
            rebound_ok=bool(rebound_ok),
        )

    def rank_stocks_for_mid_term_rebound(self, stock_data: Dict[str, pd.DataFrame], top_n: int = 10) -> List[ReboundStockScore]:
        """
        从 MID_TERM_STOCK_POOL 中挑出“回撤后止跌回升”的股票
        """
        scores: List[ReboundStockScore] = []
        for code, df in stock_data.items():
            item = self.calculate_rebound_stock_score(code, df)
            if item is None:
                continue
            scores.append(item)

        scores.sort(key=lambda x: x.score, reverse=True)
        return scores[:top_n]
