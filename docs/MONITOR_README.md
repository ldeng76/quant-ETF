# ETF短线策略监控系统

基于DuckDB的ETF短线策略自动盯盘系统，支持多种策略、市场环境自动判断、技术位止损等功能。

## 📋 系统架构

### 核心模块

```
src/quant_etf/
├── minute_data_manager.py      # 15分钟K线数据管理（从1分钟生成）
├── market_analyzer.py          # 市场状态分析（牛市/熊市/震荡市）
├── strategies/
│   ├── momentum_breakthrough.py # 动量突破策略（均线突破）
│   └── volume_price.py         # 量价配合策略（量能突破）
├── strategy_selector.py        # 策略选择器（根据市场状态选择）
├── signal_generator.py         # 信号生成器（分层筛选、评分排序）
├── risk_manager.py             # 风险管理（技术位止损）
├── alert_recorder.py           # 提醒记录器（保存到数据库）
├── monitor.py                  # 监控主程序（实时监控循环）
└── init_15min_data.py          # 初始化脚本（首次加载数据）
```

### 数据库结构

```
data/
├── minute/minute_data.duckdb          # 1分钟K线数据
├── minute/minute_data_15m.duckdb      # 15分钟K线数据
└── alerts/alerts.duckdb               # 提醒记录
```

## 🚀 快速开始

### 1. 初始化数据

首次运行前需要初始化历史数据：

```bash
# 获取ETF池数据并生成15分钟K线（回溯90天）
uv run python src/quant_etf/init_15min_data.py --days 90

# 只生成15分钟数据（假设已有1分钟数据）
uv run python src/quant_etf/init_15min_data.py --skip-1min

# 指定ETF池大小（例如只初始化前20个）
uv run python src/quant_etf/init_15min_data.py --pool-size 20
```

### 2. 启动监控

```bash
# 启动实时监控（每60秒检查一次）
uv run python src/quant_etf/monitor.py

# 自定义检查间隔（例如每30秒）
uv run python src/quant_etf/monitor.py --interval 30

# 指定ETF池大小
uv run python src/quant_etf/monitor.py --pool-size 20
```

## 📊 策略说明

### 1. 动量突破策略（均线突破）

- **均线系统**：MA10（短期）、MA20（中期）、MA30（长期）
- **买入信号**：
  - MA10上穿MA20形成金叉
  - 价格站上MA10
  - 均线多头排列（MA10 > MA20 > MA30）
  - 均线向上发散
- **止损位**：MA20

### 2. 量价配合策略（量能突破）

- **成交量均线**：MA20（短期）、MA60（长期）
- **买入信号**：
  - 成交量突破均量1.5倍以上
  - 价格上涨超过0.5%
  - 价格站上MA10
  - 均量上升
- **止损位**：MA20

## 🎯 市场状态判断

系统结合以下因素判断市场状态：

1. **沪深300指数表现**（1小时收益率）
2. **ETF池整体表现**（平均收益率）
3. **波动率**（价格波动程度）

**市场类型**：
- **牛市**：指数和ETF池整体上涨，波动率适中
- **熊市**：整体下跌或波动率极高
- **震荡市**：涨跌不明显，波动率中等

## 🛡️ 风险管理

### 技术位止损

系统使用以下技术位作为止损：

1. **近期低点**：最近30根K线的最低点
2. **MA20**：20周期移动平均线
3. **MA30**：30周期移动平均线
4. **ATR止损**：2倍ATR（平均真实波幅）
5. **固定比例**：2%止损

最终止损位取以上策略的最大值。

### 止盈目标

盈亏比设置为2:1，即止盈目标是止损的2倍。

## 📈 信号流程

```
1. 更新数据
   ↓
2. 分析市场状态
   ↓
3. 选择适合的策略
   ↓
4. 生成策略信号
   ↓
5. 去重（同一代码保留最高分）
   ↓
6. 综合评分（市场环境+趋势强度）
   ↓
7. 计算止损止盈
   ↓
8. 记录提醒到数据库
   ↓
9. 显示信号
   ↓
10. 等待下一次循环
```

## 📝 提醒记录

所有交易信号都会保存到数据库，包含以下信息：

- ETF代码
- 策略名称
- 信号类型（做多/做空）
- 方向（买入/卖出）
- 综合评分
- 入场价格
- 止损价格
- 止盈价格
- 信号理由
- 市场状态
- 市场收益率
- 市场波动率
- 均线数据（MA10、MA20、MA30）

## ⚙️ 配置说明

### ETF池配置

在 `src/quant_etf/conf.py` 中配置ETF池：

```python
ETF_POOL = [
    "510050", "510310", ...  # 添加或删除ETF代码
]
```

### 策略参数调整

**动量突破策略** (`strategies/momentum_breakthrough.py`)：
- 均线周期：`ma_short=10`, `ma_mid=20`, `ma_long=30`

**量价配合策略** (`strategies/volume_price.py`)：
- 成交量均线周期：`volume_ma_short=20`, `volume_ma_long=60`
- 量能突破阈值：`volume_threshold=1.5`

**风险管理** (`risk_manager.py`)：
- ATR周期：`atr_period=14`
- 止损比例：`risk_ratio=0.02`（2%）

## 📊 查看提醒记录

```python
from quant_etf.alert_recorder import AlertRecorder

recorder = AlertRecorder()

# 查询最近24小时的提醒
alerts = recorder.query_recent_alerts(hours=24, limit=100)

# 查询指定ETF的提醒
alerts = recorder.query_alerts_by_code("510050", limit=20)

# 获取统计摘要
summary = recorder.get_alert_summary(hours=24)
print(summary)
```

## ⚠️ 注意事项

1. **数据初始化**：首次运行必须先初始化数据，否则无法正常工作
2. **交易时间**：系统只在A股交易时间内（9:30-11:30, 13:00-15:00）生成信号
3. **数据依赖**：系统需要从通达信获取实时数据，确保通达信客户端正常运行
4. **风险提示**：本系统仅供学习参考，不构成投资建议，实际交易需谨慎
5. **资源占用**：实时监控会定期更新数据，请确保服务器资源充足

## 🔄 后续优化方向

1. **添加更多策略**：均值回归、形态突破等
2. **优化止损策略**：动态止损、跟踪止损等
3. **回测功能**：验证策略历史表现
4. **提醒方式**：支持企业微信、邮件等通知方式
5. **性能优化**：数据缓存、批量处理等

## 📞 问题反馈

如有问题或建议，请联系开发者。
