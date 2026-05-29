# TDX 服务器发现结果缓存设计

**日期：** 2026-05-29
**状态：** 已批准实施

## 背景

`get_local_tdx_server()` 通过扫描 `tdxw.exe` 进程 + `netstat` 命令查找本地通达信连接到的行情服务器。当前每次调用都执行完整发现流程，在 dashboard 运行时产生大量重复日志（`INFO` 级别），且执行 netstat 耗时。

`tdx.py` 已有进程级服务器缓存（`_cached_server`），但 `minute_collector.py` 未使用。

## 设计目标

- TDX 服务器发现结果缓存到进程级，避免重复扫描
- `minute_collector.py` 和 `tdx.py` 共用同一缓存
- 首次发现后直接命中缓存，不再触发 netstat
- 移除 `minute_collector.py` 中重复的发现逻辑

## 实现方案

### 改动 1：minute_collector 发现后写入缓存

`minute_collector.py` 的 `get_local_tdx_server()` 改为：

```python
# 发现后写入共享缓存
from quant_etf.tdx import _set_cached_server, _get_cached_server

def get_local_tdx_server() -> tuple[str, int] | None:
    # 先查缓存
    cached = _get_cached_server()
    if cached:
        return cached

    # 原有的发现逻辑...
    if discovered:
        _set_cached_server(ip, port)  # 写入缓存
        return (ip, port)
    return None
```

### 改动 2：tdx.py 优先从缓存读取

`tdx.py` 的 `get_default_server()` 已有优先级逻辑，只需确保 local_server 发现后写入缓存，后续调用直接命中 `_get_cached_server()`。

### 改动 3：移除重复日志

前序修复已将 `logger.info` 改为 `logger.debug`，本次无需额外改动。

### 缓存失效策略

缓存不设 TTL，在以下情况失效：
- 连接失败后 `tdx.py` 已有的 `_failed_servers` 机制标记该服务器不可用
- 程序重启时缓存自动清空

## 文件改动清单

| 文件 | 改动 |
|------|------|
| `src/quant_etf/minute_collector.py` | 导入 `_set_cached_server`, `_get_cached_server`；`get_local_tdx_server()` 先查缓存，发现后写入缓存 |

## 风险评估

- **低风险**：逻辑简单，仅增加缓存读写
- **兼容**：原有错误处理和回退逻辑保持不变
- **无破坏性变更**：API 接口不变，返回值不变