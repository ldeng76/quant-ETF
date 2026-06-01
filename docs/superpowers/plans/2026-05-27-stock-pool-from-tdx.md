# STOCK_POOL 通达信动态化 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `STOCK_POOL` 和 `MID_TERM_STOCK_POOL` 从 `conf.py` 硬编码改为每次运行策略时从通达信 `.blk` 板块文件动态读取。

**Architecture:** 新建 `pool_loader.py` 作为板块读取的统一入口；各 Task 的 `get_pool()` 和 `data_source.py` / `minute_fill.py` 等消费方全部通过该入口获取代码列表；`scheduler_engine.py` 只负责注入用户私有池，公共池由 Task 动态读取。

**Tech Stack:** Python 3.11+, pathlib, loguru, pytest（mock TDX_BLOCK_DIR）

**Spec:** `docs/superpowers/specs/2026-05-27-stock-pool-from-tdx-design.md`

---

## File Structure

| 文件 | 动作 | 职责 |
|------|------|------|
| `src/quant_etf/pool_loader.py` | **新建** | 解析 `.blk` 文件 + 提供 `get_stock_pool(pool_type)` 主入口 |
| `src/quant_etf/conf.py` | 修改 | 新增 `TDX_STOCK_BLOCKS` 映射；保留硬编码列表作为参考 |
| `src/quant_etf/tasks.py` | 修改 | `ShortTermStockTask.get_pool()` / `MidTermReboundTask.get_pool()` 改调 `get_stock_pool()` |
| `src/quant_etf/scheduler_engine.py` | 修改 | `PUBLIC_POOLS` 的 stock/mid_term 项不再用模块级常量；override 只注入 user-private 部分 |
| `src/quant_etf/data_source.py` | 修改 | 两处读取 `STOCK_POOL`/`MID_TERM_STOCK_POOL` 改为调 `get_stock_pool()` |
| `src/quant_etf/minute_fill.py` | 修改 | `POOL_CODES["stock"]` 改为动态调用 |
| `src/collect_info/missing_code_finder.py` | 不动 | 保留对硬编码常量的引用（做历史对比用） |
| `tests/test_pool_loader.py` | **新建** | 单元测试（mock blocknew 目录） |

---

## Task 1: 新建 `pool_loader.py` 并写单元测试

**Files:**
- Create: `src/quant_etf/pool_loader.py`
- Create: `tests/test_pool_loader.py`

- [ ] **Step 1: 写单元测试（先红）**

```python
# tests/test_pool_loader.py
"""pool_loader 单元测试：使用临时目录模拟通达信 blocknew"""
from pathlib import Path
import pytest
from quant_etf import pool_loader


@pytest.fixture
def fake_block_dir(tmp_path, monkeypatch):
    """创建临时 blocknew 目录，并让 pool_loader.TDX_BLOCK_DIR 指向它"""
    blk_dir = tmp_path / "blocknew"
    blk_dir.mkdir()
    monkeypatch.setattr(pool_loader, "TDX_BLOCK_DIR", blk_dir)
    return blk_dir


def _write_blk(directory: Path, name: str, lines: list[str]) -> Path:
    p = directory / f"{name}.blk"
    # GBK + \r\n 与通达信一致
    p.write_bytes("\r\n".join(lines).encode("gbk"))
    return p


def test_parse_blk_file_returns_codes_only(fake_block_dir):
    _write_blk(fake_block_dir, "TDXRG", ["0000063", "1600030", "0300750"])
    codes = pool_loader.load_pool_from_tdx("TDXRG")
    assert codes == ["000063", "600030", "300750"]


def test_parse_blk_file_skips_blank_and_invalid_lines(fake_block_dir):
    _write_blk(fake_block_dir, "TDXRG", ["0000063", "", "abc", "1600030"])
    codes = pool_loader.load_pool_from_tdx("TDXRG")
    assert codes == ["000063", "600030"]


def test_load_pool_from_tdx_missing_file_raises(fake_block_dir):
    with pytest.raises(RuntimeError, match="TDX block file not found"):
        pool_loader.load_pool_from_tdx("NOT_EXIST")


def test_load_pool_from_tdx_empty_block_raises(fake_block_dir):
    _write_blk(fake_block_dir, "EMPTY", [])
    with pytest.raises(RuntimeError, match="TDX block is empty"):
        pool_loader.load_pool_from_tdx("EMPTY")


def test_get_stock_pool_stock_uses_tdx(fake_block_dir):
    _write_blk(fake_block_dir, "TDXRG", ["0000063"])
    # conf.TDX_STOCK_BLOCKS["stock"] 默认为 "TDXRG"
    codes = pool_loader.get_stock_pool("stock")
    assert codes == ["000063"]


def test_get_stock_pool_etf_returns_hardcoded(monkeypatch):
    """etf 必须走硬编码，不能读板块"""
    from quant_etf.conf import ETF_POOL
    monkeypatch.delenv("TDX_DATA_PATH", raising=False)
    codes = pool_loader.get_stock_pool("etf")
    assert codes == list(ETF_POOL)
    assert len(codes) > 10


def test_get_stock_pool_unknown_type_returns_empty():
    assert pool_loader.get_stock_pool("whatever") == []
```

- [ ] **Step 2: 跑测试确认红**

```bash
uv run pytest tests/test_pool_loader.py -v
```

预期：全部 FAIL（`ImportError: cannot import name 'pool_loader'`）。

- [ ] **Step 3: 实现 `pool_loader.py`**

```python
# src/quant_etf/pool_loader.py
"""
股票池动态加载器

职责：
- 解析通达信自定义板块文件（.blk）
- 按 pool_type 返回对应股票池代码列表

数据源：
- stock / mid_term：从 TDX_BLOCK_DIR/<block_name>.blk 读取
- etf：直接返回 conf.ETF_POOL（硬编码）
"""
from pathlib import Path
from loguru import logger

from quant_etf.conf import (
    ETF_POOL,
    TDX_BLOCK_DIR,
    TDX_STOCK_BLOCKS,
)


def parse_blk_file(blk_path: Path) -> list[str]:
    """
    解析 .blk 文件，返回股票代码列表。

    文件格式（GBK 纯文本，\r\n 分隔）：
    每行 7 位数字：第 1 位市场代码（0=SZ, 1=SH），后 6 位股票代码。
    返回的 code 仅包含 6 位代码（去掉市场码前缀）。
    """
    if not blk_path.exists():
        raise RuntimeError(f"TDX block file not found: {blk_path}")

    content = blk_path.read_text(encoding="gbk", errors="ignore")
    codes: list[str] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        if not line.isdigit() or len(line) != 7:
            logger.debug(f"[pool_loader] skip invalid line: {line!r} in {blk_path}")
            continue
        codes.append(line[1:])  # 去市场码
    return codes


def load_pool_from_tdx(block_name: str) -> list[str]:
    """读取 TDX_BLOCK_DIR/<block_name>.blk，返回代码列表。"""
    blk_path = TDX_BLOCK_DIR / f"{block_name}.blk"
    codes = parse_blk_file(blk_path)
    if not codes:
        raise RuntimeError(f"TDX block is empty: {blk_path}")
    logger.info(f"[pool_loader] Loaded {len(codes)} codes from TDX block '{block_name}'")
    return codes


def get_stock_pool(pool_type: str) -> list[str]:
    """
    按 pool_type 返回股票池代码列表（运行时主入口）。

    - "etf"      → conf.ETF_POOL（硬编码）
    - "stock"    → TDX_STOCK_BLOCKS["stock"] 板块
    - "mid_term" → TDX_STOCK_BLOCKS["mid_term"] 板块
    - 其他       → []
    """
    if pool_type == "etf":
        return list(ETF_POOL)

    block_name = TDX_STOCK_BLOCKS.get(pool_type)
    if not block_name:
        logger.warning(f"[pool_loader] Unknown pool_type: {pool_type}")
        return []

    return load_pool_from_tdx(block_name)
```

- [ ] **Step 4: 跑测试确认绿**

```bash
uv run pytest tests/test_pool_loader.py -v
```

预期：全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add src/quant_etf/pool_loader.py tests/test_pool_loader.py
git commit -m "feat(pool_loader): 通达信板块动态读取股票池"
```

---

## Task 2: 在 `conf.py` 新增 `TDX_STOCK_BLOCKS` 映射

**Files:**
- Modify: `src/quant_etf/conf.py:114-131`

- [ ] **Step 1: 在通达信配置区块增加板块映射**

找到 `TDX_CUSTOM_BLOCK_NAME = "高分etf"` 那一行附近，新增：

```python
# 各策略的股票池对应的通达信板块文件名（不含 .blk 后缀）
# - stock:    短线股票池（ShortTermStockTask 使用）
# - mid_term: 中期反弹池（MidTermReboundTask 使用）
TDX_STOCK_BLOCKS = {
    "stock": "TDXRG",
    "mid_term": "MIDTERM",
}
```

- [ ] **Step 2: 给保留的硬编码列表加注释**

在 `STOCK_POOL = [...]` 上方加注释：

```python
# [参考] 短线股票池的历史硬编码列表，运行时不再使用。
# 实际股票池由 pool_loader.get_stock_pool("stock") 从通达信板块 TDXRG 读取。
STOCK_POOL = [...]
```

同理在 `MID_TERM_STOCK_POOL = [...]` 上方加：

```python
# [参考] 中期反弹池的历史硬编码列表，运行时不再使用。
# 实际股票池由 pool_loader.get_stock_pool("mid_term") 从通达信板块 MIDTERM 读取。
MID_TERM_STOCK_POOL = [...]
```

- [ ] **Step 3: 跑 pool_loader 测试确认映射正确**

```bash
uv run pytest tests/test_pool_loader.py -v
```

预期：PASS。

- [ ] **Step 4: 提交**

```bash
git add src/quant_etf/conf.py
git commit -m "chore(conf): 新增 TDX_STOCK_BLOCKS，标注硬编码池为参考"
```

---

## Task 3: 改造 `tasks.py` 中两个 Task 的 `get_pool()`

**Files:**
- Modify: `src/quant_etf/tasks.py`（`ShortTermStockTask.get_pool` 和 `MidTermReboundTask.get_pool` 所在位置）

- [ ] **Step 1: 更新 imports**

在 `tasks.py` 顶部 import 区：
- 新增：`from quant_etf.pool_loader import get_stock_pool`
- 保留：`from quant_etf.conf import ETF_POOL, STOCK_POOL, MID_TERM_STOCK_POOL, TOP_N, PROJECT_ROOT`（STOCK_POOL/MID_TERM_STOCK_POOL 用于 fallback 注释，不删除；如果后续想彻底清理可以再删）

- [ ] **Step 2: 改 `ShortTermStockTask.get_pool()`**

定位 `class ShortTermStockTask` 的 `get_pool` 方法（约 line 355），改为：

```python
def get_pool(self) -> List[str]:
    """短线股票池：优先 override，其次动态从通达信读取"""
    if self._override_pool is not None and "stock" in self._override_pool:
        return self._override_pool["stock"]
    return get_stock_pool("stock")
```

- [ ] **Step 3: 改 `MidTermReboundTask.get_pool()`**

定位 `class MidTermReboundTask` 的 `get_pool` 方法（约 line 418），改为：

```python
def get_pool(self) -> List[str]:
    """中期反弹池：优先 override，其次动态从通达信读取"""
    if self._override_pool is not None and "mid_term" in self._override_pool:
        return self._override_pool["mid_term"]
    return get_stock_pool("mid_term")
```

- [ ] **Step 4: 跑相关测试**

```bash
uv run pytest tests/test_stock_strategy.py tests/test_mid_term_rebound_strategy.py -v
```

预期：PASS（若通达信板块未就绪，预期到 `RuntimeError`，属于环境问题非代码问题；此时用 `test_pool_loader.py` 的 mock 测试覆盖即可）。

- [ ] **Step 5: 提交**

```bash
git add src/quant_etf/tasks.py
git commit -m "feat(tasks): ShortTerm/MidTerm 的 get_pool 改走通达信动态读取"
```

---

## Task 4: 改造 `scheduler_engine.py` 的 `PUBLIC_POOLS`

**Files:**
- Modify: `src/quant_etf/scheduler_engine.py:19-56`

- [ ] **Step 1: 理解当前逻辑**

阅读 `scheduler_engine.py`：
- L19：`from quant_etf.conf import ETF_POOL, STOCK_POOL, MID_TERM_STOCK_POOL`
- L29-33：`PUBLIC_POOLS` 字典
- L42-56：`get_user_codes(user_id, pool_type)` 把 PUBLIC + private 合并
- L120：`task._override_pool = override_pool`

当前问题：`override_pool` 把 public+private 合并后塞给 Task，会覆盖 Task 对 TDX 的动态读取。

- [ ] **Step 2: 修改 imports 和 PUBLIC_POOLS**

替换：

```python
from quant_etf.conf import ETF_POOL, STOCK_POOL, MID_TERM_STOCK_POOL
```

为：

```python
from quant_etf.conf import ETF_POOL
from quant_etf.pool_loader import get_stock_pool
```

替换 `PUBLIC_POOLS`：

```python
# Pool type → default codes (public pools, shared by all users)
# stock / mid_term 留空：Task 会自己从通达信动态读取
PUBLIC_POOLS: Dict[str, List[str]] = {
    "etf": list(ETF_POOL),
    "stock": [],
    "mid_term": [],
}
```

- [ ] **Step 3: 修改 `get_user_codes` 只返回 private 部分**

将 `get_user_codes` 改为：

```python
def get_user_codes(user_id: int, pool_type: str) -> List[str]:
    """
    返回用户的私有证券池（仅 private）。
    public 部分由 Task.get_pool() 动态从通达信读取，不在这里合并。
    """
    from quant_etf.scheduler_db import get_user_pool

    private_codes = get_user_pool(user_id, pool_type)
    return list(dict.fromkeys(private_codes)) if private_codes else []
```

- [ ] **Step 4: 修改 `run_single_user_strategy` 中的 override_pool 构造**

找到 `override_pool = {...}` 那段（约 L104-108），只保留 etf 的 override（因为 ETF 池是硬编码且无 user-private 需求时可省略），stock/mid_term 仅在有 private 时才注入：

```python
override_pool: Dict[str, List[str]] = {}
etf_private = get_user_codes(user_id, "etf")
if etf_private:
    # 用户私有 ETF 池需要和公共 ETF 池合并后覆盖
    override_pool["etf"] = list(dict.fromkeys(list(ETF_POOL) + etf_private))

stock_private = get_user_codes(user_id, "stock")
if stock_private:
    # 有私有池时：动态公共池 + 私有池
    override_pool["stock"] = list(dict.fromkeys(get_stock_pool("stock") + stock_private))

mid_private = get_user_codes(user_id, "mid_term")
if mid_private:
    override_pool["mid_term"] = list(dict.fromkeys(get_stock_pool("mid_term") + mid_private))

task._override_pool = override_pool if override_pool else None
```

- [ ] **Step 5: 修改 `get_all_codes` 确保 prefetch 仍能拿到全集**

当前 `get_all_codes` 遍历 `PUBLIC_POOLS` + user-private。因为 `PUBLIC_POOLS["stock"]/["mid_term"]` 现在为空，必须主动调 `get_stock_pool()` 补齐：

```python
def get_all_codes(interval: str) -> set[str]:
    from quant_etf.scheduler_db import get_all_users

    users = get_all_users()
    all_codes: set[str] = set()
    # 先把公共池的全集加进去（stock/mid_term 动态读取）
    for pool_type in ALL_POOL_TYPES:
        all_codes.update(get_stock_pool(pool_type))
    # 再加各用户的私有池
    for user in users:
        for pool_type in ALL_POOL_TYPES:
            all_codes.update(get_user_codes(user["id"], pool_type))
    return all_codes
```

- [ ] **Step 6: 跑 scheduler_engine 相关测试**

```bash
uv run pytest tests/test_scheduler_engine.py -v
```

预期：PASS。

- [ ] **Step 7: 提交**

```bash
git add src/quant_etf/scheduler_engine.py
git commit -m "refactor(engine): PUBLIC_POOLS 剥离硬编码，override 仅注入私有池"
```

---

## Task 5: 改造 `data_source.py` 和 `minute_fill.py` 的消费方

**Files:**
- Modify: `src/quant_etf/data_source.py:182-183`, `src/quant_etf/data_source.py:756-763`
- Modify: `src/quant_etf/minute_fill.py:18-22`

- [ ] **Step 1: 改 `data_source.py` 第 182-183 行**

定位 `_build_code_name_cache()` 或相关函数（约 line 182）：

```python
from quant_etf.conf import ETF_POOL, STOCK_POOL, MID_TERM_STOCK_POOL
all_codes = {normalize_code(c) for c in (list(ETF_POOL) + list(STOCK_POOL) + list(MID_TERM_STOCK_POOL))}
```

改为：

```python
from quant_etf.pool_loader import get_stock_pool
all_codes = {
    normalize_code(c)
    for c in (get_stock_pool("etf") + get_stock_pool("stock") + get_stock_pool("mid_term"))
}
```

- [ ] **Step 2: 改 `data_source.py` 第 756-763 行**

定位 `ensure_all_codes_in_db()` 或相关函数（约 line 756）：

```python
from quant_etf.conf import ETF_POOL, STOCK_POOL, MID_TERM_STOCK_POOL
...
all_codes = sorted({normalize_code(c) for c in (list(ETF_POOL) + list(STOCK_POOL) + list(MID_TERM_STOCK_POOL))})
```

改为：

```python
from quant_etf.pool_loader import get_stock_pool
...
all_codes = sorted({
    normalize_code(c)
    for c in (get_stock_pool("etf") + get_stock_pool("stock") + get_stock_pool("mid_term"))
})
```

- [ ] **Step 3: 改 `minute_fill.py` 的 POOL_CODES**

定位 `POOL_CODES`（约 line 18-22）：

```python
from quant_etf.conf import ETF_POOL, STOCK_POOL, ALL_POOL
...
"stock": STOCK_POOL,
```

改为：

```python
from quant_etf.conf import ETF_POOL
from quant_etf.pool_loader import get_stock_pool
...
# 注意：POOL_CODES["stock"] 改为动态函数，调用方需在运行时求值
```

然后搜索 `POOL_CODES["stock"]` 的调用点，将读取改为 `get_stock_pool("stock")`。如果 `POOL_CODES` 是个 dict 常量被多处引用，则改为：

```python
def get_pool_codes() -> dict:
    return {
        "etf": ETF_POOL,
        "stock": get_stock_pool("stock"),
        "mid_term": get_stock_pool("mid_term"),
    }
```

并将所有 `POOL_CODES[...]` 调用改为 `get_pool_codes()[...]`。

- [ ] **Step 4: 跑相关测试**

```bash
uv run pytest tests/test_data_source.py tests/test_minute_fill.py -v
```

预期：PASS。

- [ ] **Step 5: 提交**

```bash
git add src/quant_etf/data_source.py src/quant_etf/minute_fill.py
git commit -m "refactor: data_source/minute_fill 改走 pool_loader 动态读取"
```

---

## Task 6: 端到端冒烟测试

**Files:**
- 无新代码，仅运行验证

- [ ] **Step 1: 确保通达信板块已创建**

在通达信软件中确认：
- `T0002/blocknew/TDXRG.blk` 存在且包含短线股票
- `T0002/blocknew/MIDTERM.blk` 存在且包含中期反弹股票

- [ ] **Step 2: 命令行验证**

```bash
uv run python -c "from quant_etf.pool_loader import get_stock_pool; print('stock:', len(get_stock_pool('stock'))); print('mid_term:', len(get_stock_pool('mid_term'))); print('etf:', len(get_stock_pool('etf')))"
```

预期：分别打印出非空数字（和通达信板块实际数量一致）。

- [ ] **Step 3: 跑一次短线策略验证闭环**

```bash
uv run python -c "from quant_etf.tasks import ShortTermStockTask; t = ShortTermStockTask(bar_interval='1d'); print('pool size:', len(t.get_pool()))"
```

预期：`pool size` 等于通达信 TDXRG 板块的股票数。

- [ ] **Step 4: 跑全量测试套件**

```bash
uv run pytest tests/ -x --ignore=tests/e2e -q
```

预期：无新增 FAIL。

- [ ] **Step 5: 合并提交（若有文档更新）**

```bash
git add -A
git status
git commit -m "chore: 股票池动态化收尾"  # 仅在有额外改动时
```

---

## 不在范围（避免蔓延）

- 不改 `collect_info/missing_code_finder.py`（保留历史硬编码对比）
- 不升级 `list[str]` → `list[dict]`
- 不做 ETF_POOL 动态化
- 不做板块文件写入/反向同步
- 不修改 Dashboard UI
