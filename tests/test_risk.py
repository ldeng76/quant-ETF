import pandas as pd
import numpy as np
from quant_etf.risk import RiskManager, RiskLevel

def create_mock_df(prices):
    """
    辅助函数：创建 DataFrame
    """
    return pd.DataFrame({
        "close": prices
    }, index=pd.date_range("20230101", periods=len(prices)))

def test_risk_normal():
    rm = RiskManager()
    # 价格平稳，无风险
    prices = [100] * 100
    df = create_mock_df(prices)
    status = rm.check_risk(df)
    assert status.level == RiskLevel.NORMAL
    assert status.suggested_action == "KEEP"

def test_risk_high_position_warning():
    rm = RiskManager()
    # 价格一路由于，处于历史高位 (100 -> 200)
    # 此时未跌破均线，应该是 WARNING
    prices = list(np.linspace(100, 200, 100))
    df = create_mock_df(prices)
    
    # 确保当前价格(200) > MA20
    # MA20 约等于 (190+200)/2 = 195 < 200
    
    status = rm.check_risk(df)
    assert status.level == RiskLevel.WARNING
    assert "High Position" in status.reason

def test_risk_critical_breakdown():
    rm = RiskManager()
    # 1. 先涨到高位
    prices = list(np.linspace(100, 200, 80))
    # 2. 然后急跌
    prices += [190, 180, 170, 160, 150] # 跌破均线
    
    df = create_mock_df(prices)
    
    status = rm.check_risk(df)
    
    # 此时分位数依然很高 (因为大部分历史价格都在100-200之间，150依然可能属于高位区间或者RSI刚下来)
    # 但更重要的是跌破了均线。
    # 为了触发 High Percentile，我们需要让 150 依然处于 > 85% 的分位。
    # 如果 100-200 均匀分布，150 是 50% 分位。
    # 所以我们需要构造一个大部分时间在低位，最近暴涨然后回调的形态。
    
    # 重新构造：
    # 前80天在 10-20 震荡
    prices = list(np.linspace(10, 20, 80))
    # 后15天暴涨到 100
    prices += list(np.linspace(20, 100, 15))
    # 最后5天回调到 90
    prices += [98, 96, 94, 92, 90]
    
    df = create_mock_df(prices)
    
    # MA20 会包含部分暴涨期的数据，数值较高。
    # 20天前是 index 80 (price 20) 到 index 100 (price 90)
    # MA20 大概在 (20+100)/2 = 60 左右? 不对，是最近20天的均值。
    # 最近20天是 15天暴涨 + 5天回调。
    # 均值肯定低于当前价格吗？
    # 让我们打印一下看看
    
    ma20 = df["close"].rolling(20).mean().iloc[-1]
    # print(f"Current: {df['close'].iloc[-1]}, MA20: {ma20}")
    
    # 如果 Current < MA20，且 Percentile 高，就是 Critical
    
    # 我们直接 mock check_risk 里的逻辑可能比较复杂，不如直接构造一个简单的数学模型
    # 场景：高位下跌
    
    # 强制让 check_risk 里的判定成立
    # 1. Percentile > 0.85
    # 2. Current < MA20
    
    # 构造数据：
    # 历史: [1, 1, ..., 1] (200个)
    # 最近: [100, 100, ..., 100] (25个)
    # 今天: 90
    
    # Percentile: 90 比 200个1都大，肯定 > 85%
    # MA20: 最近20天包含 19个100 和 1个90 -> 均值接近 100
    # Current(90) < MA20(接近100) -> True
    
    prices = [1] * 200 + [100] * 25 + [90]
    df = create_mock_df(prices)
    
    status = rm.check_risk(df)
    assert status.level == RiskLevel.CRITICAL
    assert status.suggested_action == "CLEAR"

def test_rsi_calculation():
    rm = RiskManager()
    # 构造 RSI
    # 连续上涨 -> RSI 100
    prices = list(range(20))
    df = create_mock_df(prices)
    rsi = rm.calculate_rsi(df["close"])
    assert rsi > 90
