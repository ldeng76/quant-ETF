# 每日自动化运行与结果对比计划

## 目标
1. 每日收盘后自动运行 etf、short、mid 三个策略。
2. 将策略结果保存为 CSV 文件，便于历史追溯。
3. 自动对比今日结果与前 3 日（或最近一次）结果，生成调仓建议报告。

## 详细设计

### 1. 数据持久化 (CSV)
修改 `src/quant_etf/tasks.py` 中的任务类，在 `export_results` 阶段增加 CSV 保存逻辑。

- **保存路径**: `data/results/{YYYY-MM-DD}/{task_name}.csv`
- **文件格式**: CSV，包含表头。
- **字段内容**:
    - **ETF**: `date`, `code`, `name`, `score`, `r60`, `r20`, `r10`, `r5`, `target_weight`
    - **Short**: `date`, `code`, `name`, `score`, `r5`, `r10`, `r20`, `volume_ratio`, `trend_ok`
    - **Mid**: `date`, `code`, `name`, `score`, `drawdown`, `bounce`, `stabilization`, `rebound`

### 2. 对比逻辑 (Comparison)
新建模块 `src/quant_etf/comparison.py`。

- **功能**:
    - 查找指定日期及之前的最近一次历史记录。
    - 加载两个 CSV 文件。
    - 对比差异：
        - **新增 (New Entry)**: 今日在榜，昨日不在。
        - **退出 (Exit)**: 今日不在，昨日在榜。
        - **变化 (Change)**: 都在榜，但权重/评分发生显著变化。
- **输出**:
    - 控制台打印简报。
    - (可选) 保存为 `data/results/{YYYY-MM-DD}/comparison_report.txt`。

### 3. 主程序 (Runner)
新建脚本 `run_daily.py`。

- **流程**:
    1. 获取今日日期。
    2. 依次运行 `ETFTask`, `ShortTermStockTask`, `MidTermReboundTask`。
    3. 运行完毕后，调用 `comparison` 模块生成报告。
    4. 确保日志清晰，便于排查错误。

## 实施步骤

1. **修改 `src/quant_etf/tasks.py`**:
    - 引入 `dataclasses.asdict` 和 `pandas`。
    - 在 `BaseTask` 或各子类中实现 CSV 保存功能。
    
2. **创建 `src/quant_etf/comparison.py`**:
    - 实现 `ResultComparator` 类。
    
3. **创建 `run_daily.py`**:
    - 编写调度逻辑。
    
4. **测试**:
    - 运行一次，生成今日数据。
    - 模拟（或手动修改日期）生成昨日数据。
    - 运行对比逻辑验证输出。
