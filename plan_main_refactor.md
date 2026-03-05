# 重构 main.py 任务模式计划

## 1. 问题分析

当前 `main.py` 使用 `if "--pick-stocks" in sys.argv` 这种硬编码的字符串匹配来区分不同任务模式，存在以下问题：
- 代码耦合：所有逻辑（ETF选股、短线选股、中期反弹）全堆在 main.py 的 if-elif 分支里
- 难以扩展：新增任务类型需要修改 main.py 主函数
- 难以测试：无法单独测试某个任务的逻辑
- 命令行不直观：参数名过长（`--pick-stocks`），缺乏统一入口

## 2. 重构方案

### 2.1 设计思路：任务模式（Task Pattern）

```
┌─────────────────────────────────────────────────────────────┐
│                        main.py                               │
│  - 解析命令行参数                                           │
│  - 根据子命令选择任务                                        │
│  - 调用 Task.run() 执行                                     │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     tasks.py                                 │
│  ┌─────────────────┐  ┌─────────────────┐                  │
│  │   BaseTask      │  │  TaskRegistry   │  (字典注册)       │
│  │   (抽象基类)    │  │                 │                  │
│  └────────┬────────┘  └─────────────────┘                  │
│           │                                                  │
│  ┌────────┼────────┐                                        │
│  ▼        ▼        ▼                                         │
│ ETFTask  ShortTerm  MidTermRebound                          │
│          StockTask  Task                                     │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 核心类设计

**BaseTask（抽象基类）**
```python
class BaseTask(ABC):
    name: str           # 任务名称，如 "etf", "short", "mid"
    description: str   # 任务描述

    @abstractmethod
    def load_data(self, ds: ETFDataSource) -> Dict[str, pd.DataFrame]:
        """加载数据"""
        pass

    @abstractmethod
    def run_strategy(self, data: Dict[str, pd.DataFrame]) -> List[ScoreItem]:
        """执行策略打分"""
        pass

    @abstractmethod
    def export_results(self, results: List[ScoreItem], ds: ETFDataSource) -> None:
        """导出结果"""
        pass

    def run(self) -> None:
        """主流程：初始化 → 加载 → 策略 → 导出"""
        ds = ETFDataSource()
        data = self.load_data(ds)
        results = self.run_strategy(data)
        self.export_results(results, ds)
```

**具体任务实现**
- `ETFTask`: 使用 `ETF_POOL`，调用 `rank_etfs()`，导出 TDX 板块
- `ShortTermStockTask`: 使用 `STOCK_POOL`，调用 `rank_stocks_for_short_term()`，导出短线选股结果
- `MidTermReboundTask`: 使用 `MID_TERM_STOCK_POOL`，调用 `rank_stocks_for_mid_term_rebound()`，导出中期反弹结果

### 2.3 命令行改进

| 原方式 | 新方式 | 说明 |
|--------|--------|------|
| `python main.py` | `python main.py etf` | ETF 组合（默认） |
| `python main.py --pick-stocks` | `python main.py short` | 短线选股 Top5 |
| `python main.py --pick-mid-term-stocks` | `python main.py mid` | 中期反弹 Top15 |
| - | `python main.py --list` | 列出所有可用任务 |

## 3. 实施步骤

1. **创建 `quant_etf/tasks.py`**：
   - 定义 `BaseTask` 抽象基类
   - 实现 `ETFTask`, `ShortTermStockTask`, `MidTermReboundTask`
   - 实现 `TaskRegistry` 任务注册表

2. **重构 `main.py`**：
   - 使用 argparse 定义子命令
   - 通过 TaskRegistry 获取任务实例
   - 调用 `task.run()` 执行

3. **测试验证**：
   - 运行 `python main.py --help` 检查帮助信息
   - 运行 `python main.py etf` 验证 ETF 流程
   - 运行 `python main.py short` 验证短线选股
   - 运行 `python main.py mid` 验证中期反弹

## 4. 预期效果

- main.py 从 ~230 行精简到 ~50 行
- 每个任务逻辑独立封装，易于维护和测试
- 命令行更直观，支持子命令补全
- 新增任务只需在 tasks.py 中添加类，无需修改 main.py
