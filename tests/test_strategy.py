import pandas as pd
import pytest
from quant_etf.strategy import StrategyEngine, ETFScore

def create_mock_df(start_price, end_price, length=100):
    """
    创建一个简单的模拟 DataFrame，价格线性增长
    """
    prices = pd.Series(range(length))
    # 线性插值
    step = (end_price - start_price) / (length - 1)
    prices = [start_price + i * step for i in range(length)]
    
    df = pd.DataFrame({
        "close": prices
    }, index=pd.date_range("20230101", periods=length))
    return df

def test_calculate_returns():
    engine = StrategyEngine()
    
    # 创建一个价格翻倍的序列 (100 -> 200)
    df = create_mock_df(100, 200, length=100)
    
    returns = engine.calculate_returns(df)
    
    assert returns["p60"] > 0
    assert returns["p20"] > 0
    assert returns["p10"] > 0
    assert returns["p5"] > 0
    
    # 验证计算逻辑 (大约值)
    # 最后一天的价格是 200
    # 60天前 (index -61) 的价格大约是 100 + (39/99)*100 = 139.39
    # (200 - 139.39) / 139.39 ≈ 0.43
    # 精确计算:
    # step = 100/99 = 1.0101
    # close[-1] = 200
    # close[-61] = 100 + 39 * 1.0101 = 139.39
    # r60 = (200 - 139.39) / 139.39 = 0.4348
    
    # 我们只验证它计算出了数值，且数值合理
    assert 0.3 < returns["p60"] < 0.5

def test_calculate_returns_insufficient_data():
    engine = StrategyEngine()
    df = create_mock_df(100, 200, length=50) # 长度不足 60
    returns = engine.calculate_returns(df)
    assert returns == {}

def test_rank_etfs():
    engine = StrategyEngine()
    
    # ETF A: 涨得快 (100 -> 200)
    df_a = create_mock_df(100, 200)
    
    # ETF B: 涨得慢 (100 -> 110)
    df_b = create_mock_df(100, 110)
    
    # ETF C: 下跌 (100 -> 90)
    df_c = create_mock_df(100, 90)
    
    data = {
        "A": df_a,
        "B": df_b,
        "C": df_c
    }
    
    ranked = engine.rank_etfs(data)
    
    assert len(ranked) == 3
    assert ranked[0].code == "A"
    assert ranked[1].code == "B"
    assert ranked[2].code == "C"
    
    assert ranked[0].score > ranked[1].score
    assert ranked[1].score > ranked[2].score

def test_get_target_portfolio():
    engine = StrategyEngine()
    
    # 构造一些假分数
    scores = [
        ETFScore("A", 1.0, 0,0,0,0),
        ETFScore("B", 0.8, 0,0,0,0),
        ETFScore("C", 0.6, 0,0,0,0),
        ETFScore("D", 0.4, 0,0,0,0),
    ]
    
    # 取前2名
    target = engine.get_target_portfolio(scores, top_n=2)
    
    assert len(target) == 2
    assert "A" in target
    assert "B" in target
    assert target["A"] == 0.5
    assert target["B"] == 0.5
