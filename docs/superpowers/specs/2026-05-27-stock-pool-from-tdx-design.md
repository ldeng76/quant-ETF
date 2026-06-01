# STOCK_POOL 动态化：从通达信板块读取

## 背景

当前 `conf.py` 中 `STOCK_POOL`（49 只短线股票）和 `MID_TERM_STOCK_POOL`（90 只中期反弹股票）为硬编码列表。每次调整股票池都需要修改源码并重启，不够灵活。

本设计将这两个列表改为**每天从通达信自定义板块文件中动态读取**，板块名可在 conf.py 中配置。

## 关键决策

| 项目 | 决策 |
|------|------|
| 板块配置方式 | 在 `conf.py` 中维护一个 `dict[pool_type → block_name]` |
| 降级策略 | **无 fallback**：读不到直接抛错，强制暴露问题 |
| 刷新时机 | **惰性刷新**：每次任务 `get_pool()` 调用时重新读取 |
| ETF_POOL | **保持硬编码**：ETF 池稳定且不在通达信板块里 |
| 数据结构 | **保持 `list[str]`**：pool 仍只存代码，name 仅在展示时查 |

## 架构变更

### 1. 配置层：`conf.py`

新增：

```python
# 通达信板块名映射：pool_type → 板块文件名（不含 .blk 后缀）
TDX_STOCK_BLOCKS = {
    "stock": "TDXRG",       # 短线股票池 → 通达信热股
    "mid_term": "MIDTERM",  # 中期反弹池 → 自定义板块（用户需在通达信中创建）
}
```

保留 `STOCK_POOL` 和 `MID_TERM_STOCK_POOL` 硬编码列表作为**参考文档**（加注释说明它们不再被运行时使用），便于回滚或查阅历史池子。

### 2. 新建模块：`src/quant_etf/pool_loader.py`

负责解析通达信 `.blk` 文件：

```python
def parse_blk_file(blk_path: Path) -> list[str]:
    """
    解析 .blk 文件，返回股票代码列表。

    文件格式（GBK 纯文本）：每行 7 位数字
    - 第 1 位：市场代码（0=SZ, 1=SH）
    - 后 6 位：股票代码
    """

def load_pool_from_tdx(block_name: str) -> list[str]:
    """读取 TDX_BLOCK_DIR/{block_name}.blk，返回代码列表。"""

def get_stock_pool(pool_type: str) -> list[str]:
    """
    主入口：按 pool_type 读取对应板块。

    - pool_type="stock"  → 读 TDX_STOCK_BLOCKS["stock"] 板块
    - pool_type="mid_term" → 读 TDX_STOCK_BLOCKS["mid_term"] 板块
    - pool_type="etf"    → 直接返回 ETF_POOL（硬编码）
    - 其他 pool_type     → 返回空列表
    """
```

**错误处理**：板块文件不存在 / 解析失败 / 代码为空 → 抛 `RuntimeError`，由调用方（任务）决定如何响应。日志中打印板块路径以便排查。

### 3. 任务层：`tasks.py`

- `ShortTermStockTask.get_pool()` → 调 `get_stock_pool("stock")`
- `MidTermReboundTask.get_pool()` → 调 `get_stock_pool("mid_term")`
- `ETFTask.get_pool()` → **保持不变**，仍读 `ETF_POOL`

### 4. 调度引擎：`scheduler_engine.py`

`PUBLIC_POOLS` 字典在模块加载时仍构建初始值（用于类型推断和快速回退），但实际执行时由各 Task 的 `get_pool()` 动态读取，不依赖模块级常量。

具体改动：将 `PUBLIC_POOLS["stock"]` 和 `PUBLIC_POOLS["mid_term"]` 的初始值改为空列表或占位值，让运行时完全依赖 task 的 `get_pool()`。

### 5. 其他消费方

- `data_source.py`：`_build_code_name_cache()` 和 `ensure_all_codes_in_db()` 中读取 `STOCK_POOL`/`MID_TERM_STOCK_POOL` 的地方改为调 `get_stock_pool()`
- `minute_fill.py`：`POOL_CODES["stock"]` 改为动态调用
- `collect_info/missing_code_finder.py`：保留对硬编码常量的引用（它是做历史对比用的）

## 测试策略

- 单元测试：mock `TDX_BLOCK_DIR`，构造示例 `.blk` 文件，验证 `parse_blk_file` 输出正确
- 冒烟测试：在通达信软件已启动、板块已创建的环境下，跑 `python -c "from quant_etf.pool_loader import get_stock_pool; print(get_stock_pool('stock'))"`
- 回归测试：确认 `scheduler_engine.run_job_for_interval()` 能正常完成一轮

## 用户前置操作

实施后用户需要在通达信中：
1. 创建名为 `MIDTERM` 的自定义板块（或在 `conf.py` 中将 `TDX_STOCK_BLOCKS["mid_term"]` 改为自己已有的板块名）
2. 将当前 `MID_TERM_STOCK_POOL` 中的 90 只股票加入该板块
3. 确认 `TDXRG` 板块（已有）包含当前短线池的股票

## 不在范围

- ETF_POOL 动态化（保持硬编码）
- name 字段升级（保持 `list[str]`，展示时临时查 `stock_code_name.json`）
- 板块文件写入 / 反向同步
- 多用户私有板块（已有 scheduler_db 的 user_pool 机制处理）
