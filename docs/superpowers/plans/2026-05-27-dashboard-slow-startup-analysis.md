# Dashboard 启动缓慢分析与缓存优化方案

> 生成时间: 2026-05-27
> 项目: quant-ETF Dashboard

---

## 一、问题描述

执行 `uv run quant-etf dashboard --no-reload` 启动看板时，发现数据加载耗时过长，影响用户体验。

---

## 二、瓶颈定位

### 2.1 `market_analyzer.py` - 无缓存的数据库查询（主要瓶颈）

**文件位置**: `src/quant_etf/market_analyzer.py`

`market_status` API 调用 `get_market_state()` → `analyzer.analyze_market()`，执行大量数据库查询：

```python
get_index_1min_bars(days=5)           # 1次查询（000300指数5天分钟数据）
get_etf_pool_performance(codes)        # N次查询（ETF_POOL 中每个标的）
  └─ for code in codes:
       pd.read_sql(minute_bars WHERE code=xxx)  # 逐个标的查询
```

当 `ETF_POOL` 有 66 个标的时，每次访问 `/api/market/status` 执行 **67+ 次数据库查询**。

**问题**：每次请求都重新查询原始分钟数据，没有缓存。

---

### 2.2 `strategy_runner.py` - `get_history_summary()` 无缓存

**文件位置**: `src/quant_etf/dashboard/services/strategy_runner.py`

```python
def get_history_summary(strategy_name, days=30, auto_backfill=True):
    # 每次请求都读取所有CSV文件
    for date_dir in all_date_dirs:
        df = pd.read_csv(csv_path)  # 30+ 个 CSV 文件
```

虽然有 `_history_cache`（5分钟TTL），但首次访问时需要读取30+个CSV文件。

---

### 2.3 连接池管理 - psycopg2 同步连接开销

**文件位置**: `src/quant_etf/dashboard/db.py`

```python
def get_pg_conn():
    return psycopg2.connect(...)  # 每次查询都新建连接

def query(sql, params):
    with get_pg_conn() as conn:  # 用完即关闭
        cur = conn.cursor()
        cur.execute(sql, params)
```

`market_analyzer.py` 的 `pd.read_sql()` 也是每次创建新连接，连接开销累积。

---

### 2.4 启动时 Scheduler 加载

**文件位置**: `src/quant_etf/dashboard/app.py`

```python
async def startup():
    init_db()           # 执行 schema 初始化
    run_migrations()    # 执行迁移
    if IS_PRIMARY:
        await scheduler.start_all()  # 启动所有定时任务
```

Scheduler 首次执行时会触发策略运行，产生大量数据加载。

---

## 三、缓存解决方案

### 架构设计

```
┌─────────────────────────────────────────────────────────────────┐
│                      缓存层级架构                                │
├─────────────────────────────────────────────────────────────────┤
│  L1: 内存缓存 (TTL)          L2: PostgreSQL 缓存表               │
│  ─────────────────          ────────────────────────────        │
│  • market_state: 60s        • minute_bars 聚合数据              │
│  • etf_pool_perf: 60s       • market_daily 汇总                │
│  • history_summary: 5min     • 策略运行结果                     │
│  • name_map: 常驻            • 用户会话信息                    │
└─────────────────────────────────────────────────────────────────┘
```

---

### 方案 A：内存缓存（推荐，轻量级）

**文件**: `src/quant_etf/dashboard/services/market_cache.py`

```python
"""市场数据内存缓存"""
import time
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime
from loguru import logger

@dataclass
class MarketCache:
    """市场状态缓存（TTL 60秒）"""
    _state: Optional[dict] = None
    _fetched_at: float = 0
    _ttl: int = 60  # 秒

    def get(self) -> Optional[dict]:
        if self._state and (time.time() - self._fetched_at) < self._ttl:
            return self._state
        return None

    def set(self, state: dict) -> None:
        self._state = state
        self._fetched_at = time.time()

    def invalidate(self) -> None:
        self._state = None
        self._fetched_at = 0

# 全局缓存实例
market_cache = MarketCache()
```

---

### 方案 B：异步预加载 + 后台刷新

**文件**: `src/quant_etf/dashboard/services/startup_preload.py`

```python
"""启动时预加载核心数据"""
import asyncio
import threading
from loguru import logger

async def preload_core_data():
    """启动时后台预加载核心数据"""
    from .market_cache import market_cache
    from ...market_analyzer import get_market_state
    from .strategy_runner import get_history_summary

    async def _preload():
        try:
            # 1. 市场状态预加载
            state = await asyncio.to_thread(get_market_state)
            market_cache.set(state)
            logger.info("Market state preloaded")
        except Exception as e:
            logger.warning(f"Market state preload failed: {e}")

    # 启动后台预加载（不阻塞启动）
    asyncio.create_task(_preload())
```

---

### 方案 C：market_analyzer 添加缓存装饰器

**文件**: `src/quant_etf/market_analyzer.py` 添加缓存

```python
import time
from functools import lru_cache
from typing import Optional

class CachedMarketAnalyzer(MarketAnalyzer):
    """带缓存的市场分析器"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cache: dict = {}
        self._cache_ttl = 60  # 60秒TTL

    def _get_cached(self, key: str) -> Optional[MarketState]:
        if key in self._cache:
            state, timestamp = self._cache[key]
            if time.time() - timestamp < self._cache_ttl:
                return state
        return None

    def _set_cached(self, key: str, state: MarketState) -> None:
        self._cache[key] = (state, time.time())

    def analyze_market_cached(self, codes: list[str]) -> MarketState:
        cache_key = f"market_{hash(tuple(sorted(codes)))}"
        cached = self._get_cached(cache_key)
        if cached:
            logger.debug("Using cached market state")
            return cached

        state = self.analyze_market(codes)
        self._set_cached(cache_key, state)
        return state
```

---

### 方案 D：分钟数据聚合表（可选，PostgreSQL层面优化）

```sql
-- 预计算最近1小时的聚合数据
CREATE TABLE IF NOT EXISTS market_snapshot (
    id              SERIAL PRIMARY KEY,
    snapshot_time   TIMESTAMP NOT NULL,
    index_code      VARCHAR(20),
    index_close     NUMERIC(18, 4),
    index_return_1h  NUMERIC(10, 4),
    pool_return_1h  NUMERIC(10, 4),
    volatility      NUMERIC(10, 4),
    ma_short        NUMERIC(18, 4),
    ma_long         NUMERIC(18, 4),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 后台任务每分钟更新一次
CREATE OR REPLACE FUNCTION update_market_snapshot()
RETURNS void AS $$
BEGIN
    INSERT INTO market_snapshot (snapshot_time, index_code, ...)
    SELECT NOW(), ...
    ON CONFLICT DO NOTHING;
END;
$$ LANGUAGE plpgsql;
```

---

## 四、推荐实施步骤

### Phase 1: 快速优化（30分钟）

- [ ] 1.1 添加 market_state 内存缓存（TTL 60s）
- [ ] 1.2 添加 get_market_state 缓存检查
- [ ] 1.3 启动时后台异步预加载市场数据

### Phase 2: 中期优化（2小时）

- [ ] 2.1 重构 market_analyzer 使用缓存分析器
- [ ] 2.2 为 get_etf_pool_performance 添加批量查询
- [ ] 2.3 添加 market_snapshot 聚合表

### Phase 3: 长期优化（可选）

- [ ] 3.1 引入 Redis 作为分布式缓存
- [ ] 3.2 实现缓存失效策略
- [ ] 3.3 添加监控指标

---

## 五、相关文件

| 文件 | 说明 | 优先级 |
|------|------|--------|
| `src/quant_etf/market_analyzer.py` | 市场分析器，无缓存 | P0 |
| `src/quant_etf/dashboard/services/strategy_runner.py` | 策略运行器，历史汇总无缓存 | P1 |
| `src/quant_etf/dashboard/db.py` | 数据库连接池管理 | P2 |
| `src/quant_etf/dashboard/app.py` | 启动时初始化 | P2 |
| `src/quant_etf/dashboard/services/auto_backfill.py` | 自动补算服务 | P3 |
| `src/quant_etf/dashboard/routes/market.py` | 市场API路由 | P1 |

---

## 六、监控指标建议

```python
# 添加启动性能监控
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(round(process_time, 3))
    return response
```

关键指标：
- `/api/market/status` 响应时间
- `/api/market/overview` 响应时间
- 数据库查询次数/请求
- 缓存命中率