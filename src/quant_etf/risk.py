from enum import Enum
import pandas as pd
import numpy as np
from loguru import logger
from dataclasses import dataclass

class RiskLevel(Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL" # 严重风险，建议清仓

@dataclass
class RiskStatus:
    level: RiskLevel
    reason: str
    suggested_action: str # "KEEP", "REDUCE", "CLEAR"

class RiskManager:
    def __init__(self):
        # 风险参数配置
        self.high_percentile_threshold = 0.85 # 历史分位数阈值
        self.ma_fast = 20
        self.ma_slow = 60
        self.rsi_period = 14
        self.rsi_threshold = 80 # 超买阈值

    def calculate_rsi(self, series: pd.Series, period: int = 14) -> float:
        """
        计算 RSI 指标
        """
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.iloc[-1] if not np.isnan(rsi.iloc[-1]) else 50.0

    def check_risk(self, df: pd.DataFrame) -> RiskStatus:
        """
        检查单只 ETF 的风险状态
        :param df: ETF 历史数据 (必须包含 'close')
        :return: RiskStatus
        """
        if df.empty or len(df) < 60:
            return RiskStatus(RiskLevel.NORMAL, "Insufficient data", "KEEP")

        close_prices = df["close"]
        current_price = close_prices.iloc[-1]
        
        # 1. 监测“高位风险区”
        # 方法A: 历史分位数
        # 计算过去 250 天 (约一年) 的分位数
        lookback_window = min(len(df), 250)
        recent_history = close_prices.iloc[-lookback_window:]
        percentile = (recent_history < current_price).mean()
        
        is_high_position = percentile > self.high_percentile_threshold
        
        # 方法B: RSI 超买
        rsi = self.calculate_rsi(close_prices, self.rsi_period)
        is_overbought = rsi > self.rsi_threshold
        
        # 2. 监测“开始下跌”
        # 方法: 跌破 MA20
        ma20 = close_prices.rolling(window=self.ma_fast).mean()
        # ma60 = close_prices.rolling(window=self.ma_slow).mean()
        
        # 当前价格跌破 MA20，且前一天在 MA20 之上 (向下穿越)
        # 或者简单点：当前价格 < MA20
        is_below_ma20 = current_price < ma20.iloc[-1]
        
        # 3. 综合判断
        # 规则：如果处于高位 (分位数高 或 RSI超买) 且 趋势破坏 (跌破MA20)，则触发严重风险
        if (is_high_position or is_overbought) and is_below_ma20:
            reason = []
            if is_high_position: reason.append(f"High Percentile ({percentile:.2%})")
            if is_overbought: reason.append(f"High RSI ({rsi:.2f})")
            reason.append("Breaking MA20")
            
            return RiskStatus(
                level=RiskLevel.CRITICAL,
                reason=", ".join(reason),
                suggested_action="CLEAR" # 建议清仓或大幅减仓
            )
            
        # 预警：仅高位但未跌破
        if is_high_position or is_overbought:
             return RiskStatus(
                level=RiskLevel.WARNING,
                reason=f"High Position (Pct: {percentile:.2%}, RSI: {rsi:.2f})",
                suggested_action="REDUCE" # 建议不加仓或适当止盈
            )

        return RiskStatus(RiskLevel.NORMAL, "Normal", "KEEP")
