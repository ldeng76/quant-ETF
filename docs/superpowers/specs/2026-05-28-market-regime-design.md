# 大盘状态评估与自适应策略

## 背景

当前 ETF 策略按风控调整后的 `weight` 排序（等权分配后的值），导致排名与动量 score 无关。需要：

1. 修复排序 bug（始终按动量 score 排序）
2. 新增大盘状态评估，牛市激进/弱市防守

## 设计

### 1. MarketRegime — 大盘状态评估器

**职责**：计算大盘综合动量分数，判断牛/熊状态。

**输入**：4 只指数 ETF 的 K 线数据（复用现有 `load_data_batch`）

```python
INDEX_WEIGHTS = {
    "510050": 0.2,   # 沪深300
    "159919": 0.2,   # 深证300
    "159949": 0.4,   # 创业板50
    "588000": 0.2,   # 科创50
}
```

**计算**：
- 对每只指数 ETF 调用 `calculate_returns()` 得到 p60/p20/p10/p5
- 用 `MOMENTUM_WEIGHTS` 加权得到每只指数分数
- 再用 `INDEX_WEIGHTS` 加权得到大盘总分 `market_score`
- 与所有指数分数的中位数比较：`is_bullish = market_score > median`

**位置**：`src/quant_etf/market_regime.py`，独立模块

### 2. conf.py 新增配置

```python
# 大盘指数权重
INDEX_WEIGHTS = {
    "510050": 0.2, "159919": 0.2,
    "159949": 0.4, "588000": 0.2,
}

# 策略模式参数
MARKET_REGIME_CONFIG = {
    "aggressive": {"top_n": 15, "risk_discount": 1.0},
    "defensive": {"top_n": 8, "risk_discount": 0.5},
}
```

`risk_discount` 用于弱市时风控折扣翻倍（WARNING 从 0.5x 降到 0.25x）。

### 3. ETFTask.run_strategy() 改动

当前流程：
```
rank_etfs → get_target_portfolio → 风控 → 按 weight 排序（bug）
```

改为：
```
1. 加载大盘指数数据（4 只 INDEX_WEIGHTS 中的 ETF）
2. MarketRegime.assess(index_data, bar_interval) → regime
3. 确定模式参数：aggressive 或 defensive
4. rank_etfs(data) → 按 score 降序（已排序）
5. 取 top_n（根据模式）
6. 风控调整权重（defensive 时折扣更大）
7. 输出保持 score 降序
```

**排序修复**：`output_results.sort(key=lambda x: x[2])` 改为按动量 score 排序。ETFScore.score 字段恢复为动量分数而非权重。

### 4. 数据加载

大盘指数数据与 ETF 池数据共用同一次 `load_data_batch` 调用。在 `BaseTask.run()` 中将指数代码加入加载列表，避免额外查询。

如果指数 ETF 不在 ETF_POOL 中，需要额外加载。最简方案：`ETFTask.load_data()` 把 INDEX_WEIGHTS 的 key 合并进 codes 列表。

### 5. 结果输出

新增 `market_score` 和 `regime` 字段到 `_running_tasks` 状态中，供前端展示（可选，后续扩展）。

## 不变部分

- `StrategyEngine` — 不改，`calculate_returns` 和 `rank_etfs` 逻辑不变
- `RiskManager` — 不改，只调参数
- 其他 Task（ShortTask, ReboundTask）— 不受影响
- 前端展示 — 暂不改，排序自然变正确

## 改动文件清单

| 文件 | 改动 |
|---|---|
| `src/quant_etf/market_regime.py` | **新增**：MarketRegime 类 |
| `src/quant_etf/conf.py` | 新增 INDEX_WEIGHTS, MARKET_REGIME_CONFIG |
| `src/quant_etf/tasks.py` | ETFTask.run_strategy() 重构排序+注入大盘评估 |
