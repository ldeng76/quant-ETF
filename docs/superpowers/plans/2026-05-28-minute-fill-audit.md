# 分钟K线智能补全与审计 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `minute-fill` 和 `minute-audit` 两个 CLI 命令，实现分钟K线数据的智能增量补全和缺失审计，并在 Dashboard 启动时自动补全。

**Architecture:** 新增 `minute_fill.py` 模块，封装增量补全逻辑（基于 PG 最新时间戳估算拉取量）和审计逻辑（基于交易日历对比 PG 已有数据）。修改 `cli.py` 注册两个新命令。修改 `startup_preload.py` 在 Dashboard 启动时触发补全。

**Tech Stack:** Python, psycopg2, pytdx, loguru, pytest

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `src/quant_etf/minute_fill.py` | 核心补全和审计逻辑 |
| Modify | `src/quant_etf/cli.py` | 注册 `minute-fill` / `minute-audit` 命令 |
| Modify | `src/quant_etf/dashboard/services/startup_preload.py` | 启动时调用补全 |
| Create | `tests/test_minute_fill.py` | 单元测试（mock PG 和 pytdx） |

---

### Task 1: 创建 `minute_fill.py` 核心模块

**Files:**
- Create: `src/quant_etf/minute_fill.py`

- [ ] **Step 1: 创建模块文件，实现 `_get_pool_codes` 和 `_calc_bars_to_fetch`**

```python
"""
分钟K线数据智能补全与审计模块

提供基于最新时间戳的增量补全（minute-fill）和基于交易日历的缺失审计（minute-audit）。
"""
import math
from datetime import datetime, timedelta
from loguru import logger

from quant_etf.conf import ETF_POOL, STOCK_POOL, ALL_POOL


def _get_pool_codes(pool_name: str) -> list[str]:
    """根据 pool 名称获取对应代码列表"""
    pools = {
        "etf": ETF_POOL,
        "stock": STOCK_POOL,
        "all": ALL_POOL,
    }
    return list(pools.get(pool_name, ETF_POOL))


def _calc_bars_to_fetch(latest_time: datetime | None, now: datetime, max_days: int) -> int:
    """
    估算需要从 pytdx 拉取的 K 线数量

    pytdx get_security_bars(start=0) 返回最新的 N 根 K 线，
    start=N 则跳过最新 N 根继续往前。所以拉取总量足够即可覆盖。

    :param latest_time: PG 中该代码最新一条数据的时间，None 表示无数据
    :param now: 当前时间
    :param max_days: 最大回溯天数
    :return: 需要拉取的 K 线数量
    """
    BARS_PER_DAY = 250  # 每天 240 根，留余量

    if latest_time is None:
        days_gap = max_days
    else:
        days_gap = (now - latest_time).days + 1
        days_gap = min(days_gap, max_days)

    if days_gap <= 0:
        return 0

    raw_bars = (days_gap + 1) * BARS_PER_DAY
    # 向上取整到 800 的倍数（pytdx 每次最多返回 800 根）
    return math.ceil(raw_bars / 800) * 800
```

- [ ] **Step 2: 实现 `fill_minute_gaps` 主函数**

追加到 `src/quant_etf/minute_fill.py`:

```python
def fill_minute_gaps(codes: list[str], max_days: int = 60) -> dict:
    """
    增量补全分钟 K 线数据

    对每个 code，查询 PG 最新时间戳，估算需要拉取的 K 线数量，
    调用 pytdx 获取数据后过滤新记录并 upsert 到 PG。

    :param codes: 证券代码列表
    :param max_days: 最大回溯天数
    :return: 统计结果 dict
    """
    from quant_etf.minute_collector import (
        get_minute_bars,
        get_latest_minute_time,
        save_minute_data_from_dicts,
    )

    now = datetime.now()
    stats = {
        "total": len(codes),
        "success": 0,
        "skipped": 0,
        "failed": 0,
        "total_bars": 0,
        "failures": [],
    }

    for i, code in enumerate(codes, 1):
        logger.info(f"[{i}/{len(codes)}] 补全 {code} ...")

        try:
            latest_time = get_latest_minute_time(code)
            bars_to_fetch = _calc_bars_to_fetch(latest_time, now, max_days)

            if bars_to_fetch == 0:
                logger.info(f"  数据已是最新，跳过")
                stats["skipped"] += 1
                continue

            logger.info(
                f"  最新: {latest_time}, 需拉取: {bars_to_fetch} 根"
                if latest_time
                else f"  无历史数据，需拉取: {bars_to_fetch} 根"
            )

            bars = get_minute_bars(code, count=bars_to_fetch)
            if not bars:
                stats["failed"] += 1
                stats["failures"].append((code, "无数据"))
                logger.warning(f"  无数据")
                continue

            # 过滤只保留 latest_time 之后的新记录
            if latest_time:
                new_bars = [
                    b for b in bars
                    if isinstance(b.get("time"), datetime) and b["time"] > latest_time
                ]
            else:
                new_bars = bars

            if not new_bars:
                logger.info(f"  无新数据，跳过")
                stats["skipped"] += 1
                continue

            logger.info(f"  新数据: {len(new_bars)} 条")

            if save_minute_data_from_dicts(code, new_bars):
                stats["success"] += 1
                stats["total_bars"] += len(new_bars)
            else:
                stats["failed"] += 1
                stats["failures"].append((code, "保存失败"))
                logger.warning(f"  保存失败")

        except Exception as e:
            stats["failed"] += 1
            stats["failures"].append((code, str(e)))
            logger.error(f"  错误: {e}")

    return stats
```

- [ ] **Step 3: 实现 `_detect_missing_dates` 和 `audit_minute_gaps` 函数**

追加到 `src/quant_etf/minute_fill.py`:

```python
def _detect_missing_dates(
    code: str,
    trading_dates: list[datetime],
) -> list[datetime]:
    """
    对比交易日历和 PG 数据，返回缺失的日期列表

    :param code: 证券代码
    :param trading_dates: 交易日历
    :return: 缺失的日期列表
    """
    from quant_etf.minute_collector import _get_pg_conn

    if not trading_dates:
        return []

    conn = _get_pg_conn()
    cur = conn.cursor()

    start_date = trading_dates[0].replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = trading_dates[-1].replace(hour=23, minute=59, second=59, microsecond=0)

    cur.execute(
        """
        SELECT DATE(time) AS d, COUNT(*) AS cnt
        FROM minute_bars
        WHERE code = %s AND time >= %s AND time <= %s
        GROUP BY d
        """,
        [code, start_date, end_date],
    )
    existing = {row[0]: row[1] for row in cur.fetchall()}

    missing = []
    for td in trading_dates:
        td_date = td.date() if hasattr(td, "date") else td
        # 比较日期部分
        found = False
        for existing_date in existing:
            if hasattr(existing_date, "year"):
                if existing_date == td_date:
                    found = True
                    break
            elif str(existing_date) == str(td_date):
                found = True
                break

        if not found:
            missing.append(td)
        elif existing.get(td_date, 0) < 100 if td_date in existing else True:
            # 部分缺失（少于 100 根）也算缺失
            # 重新检查：existing 字典的 key 可能是 date 对象
            for k, v in existing.items():
                if str(k) == str(td_date) and v < 100:
                    missing.append(td)
                    break

    return missing


def audit_minute_gaps(
    codes: list[str],
    max_days: int = 60,
    fix: bool = False,
) -> dict:
    """
    审计分钟 K 线数据缺失

    :param codes: 证券代码列表
    :param max_days: 审计最近 N 个交易日
    :param fix: 是否自动修复缺失
    :return: 审计结果 dict
    """
    from quant_etf.minute_collector import get_minute_bars, save_minute_data_from_dicts
    from quant_etf.trading_day import get_available_trading_dates

    now = datetime.now()
    start_dt = now - timedelta(days=max(max_days * 2, 90))

    # 获取交易日历
    all_trading_dates = get_available_trading_dates()
    if not all_trading_dates:
        logger.warning("无法获取交易日历")
        return {"error": "无法获取交易日历", "codes": codes}

    trading_dates = [d for d in all_trading_dates if start_dt <= d <= now]
    if not trading_dates:
        logger.warning("指定范围内无交易日")
        return {"error": "指定范围内无交易日", "codes": codes}

    logger.info(
        f"审计范围: {trading_dates[0].strftime('%Y-%m-%d')} ~ "
        f"{trading_dates[-1].strftime('%Y-%m-%d')} ({len(trading_dates)} 个交易日)"
    )

    results = []
    total_missing_days = 0
    fix_stats = {"fixed": 0, "fix_failed": 0, "bars_added": 0}

    for i, code in enumerate(codes, 1):
        logger.info(f"[{i}/{len(codes)}] 审计 {code} ...")
        missing = _detect_missing_dates(code, trading_dates)
        entry = {
            "code": code,
            "missing_days": len(missing),
            "missing_dates": [d.strftime("%Y-%m-%d") for d in missing],
        }

        if missing:
            total_missing_days += len(missing)
            if len(missing) == len(trading_dates):
                logger.info(f"  全部缺失 ({len(trading_dates)} 天)")
            else:
                dates_str = ", ".join(entry["missing_dates"][:5])
                suffix = f" ... 等 {len(missing)} 天" if len(missing) > 5 else ""
                logger.info(f"  缺失 {len(missing)} 天: {dates_str}{suffix}")

            if fix and missing:
                # 计算需要拉取的量覆盖缺失范围
                earliest_missing = min(missing)
                days_back = (now - earliest_missing).days
                bars_to_fetch = math.ceil((days_back + 1) / 7 * 5 * 250 / 800) * 800
                bars_to_fetch = max(bars_to_fetch, 800)

                logger.info(f"  修复中，拉取 {bars_to_fetch} 根 ...")
                try:
                    bars = get_minute_bars(code, count=bars_to_fetch)
                    if bars:
                        # 过滤只保留缺失日期范围内的记录
                        earliest = min(missing).replace(hour=0, minute=0)
                        latest = max(missing).replace(hour=23, minute=59)
                        filtered = [
                            b for b in bars
                            if isinstance(b.get("time"), datetime)
                            and earliest <= b["time"] <= latest
                        ]
                        if filtered and save_minute_data_from_dicts(code, filtered):
                            fix_stats["fixed"] += 1
                            fix_stats["bars_added"] += len(filtered)
                            logger.info(f"  修复成功，补入 {len(filtered)} 条")
                        else:
                            fix_stats["fix_failed"] += 1
                            logger.warning(f"  修复失败：无有效数据")
                    else:
                        fix_stats["fix_failed"] += 1
                        logger.warning(f"  修复失败：pytdx 无数据")
                except Exception as e:
                    fix_stats["fix_failed"] += 1
                    logger.error(f"  修复错误: {e}")
        else:
            logger.info(f"  完整")

        results.append(entry)

    return {
        "trading_dates": len(trading_dates),
        "date_range": (
            trading_dates[0].strftime("%Y-%m-%d"),
            trading_dates[-1].strftime("%Y-%m-%d"),
        ),
        "total_codes": len(codes),
        "codes_with_missing": sum(1 for r in results if r["missing_days"] > 0),
        "total_missing_days": total_missing_days,
        "results": results,
        "fix_stats": fix_stats if fix else None,
    }
```

- [ ] **Step 4: 实现 `ensure_minute_data_ready` 函数**

追加到 `src/quant_etf/minute_fill.py`:

```python
def ensure_minute_data_ready():
    """
    Dashboard 启动时调用，补全 ETF 池的分钟数据

    仅补全 ETF_POOL，失败不抛异常。
    """
    from quant_etf.dashboard.config import IS_PRIMARY

    if not IS_PRIMARY:
        logger.info("非 primary 节点，跳过分钟数据补全")
        return

    logger.info("检查 ETF 分钟数据完整性 ...")
    try:
        stats = fill_minute_gaps(codes=ETF_POOL, max_days=60)
        logger.info(
            f"分钟数据补全完成: "
            f"{stats['success']}/{stats['total']} 成功, "
            f"{stats['skipped']} 跳过, "
            f"{stats['failed']} 失败, "
            f"补入 {stats['total_bars']} 条"
        )
        if stats["failures"]:
            failed_codes = [f"{c} ({r})" for c, r in stats["failures"]]
            logger.warning(f"失败: {', '.join(failed_codes)}")
    except Exception as e:
        logger.warning(f"分钟数据补全失败（不影响启动）: {e}")
```

- [ ] **Step 5: 实现 `print_fill_report` 和 `print_audit_report` 报告函数**

追加到 `src/quant_etf/minute_fill.py`:

```python
def print_fill_report(stats: dict):
    """打印 fill 命令的简洁报告"""
    print(f"\n补全完成: {stats['success']}/{stats['total']} 成功, "
          f"{stats['skipped']} 跳过, {stats['failed']} 失败")
    print(f"  补入数据: {stats['total_bars']} 条")
    if stats["failures"]:
        failed_str = ", ".join(f"{c} ({r})" for c, r in stats["failures"])
        print(f"  失败代码: {failed_str}")


def print_audit_report(report: dict):
    """打印 audit 命令的缺失报告"""
    if "error" in report:
        print(f"\n错误: {report['error']}")
        return

    start, end = report["date_range"]
    print(f"\n审计范围: {start} ~ {end} ({report['trading_dates']} 个交易日)")
    print()
    print(f"{'代码':<10} {'缺失天数':<10} 状态")
    print("-" * 40)
    for r in report["results"]:
        if r["missing_days"] == 0:
            status = "完整"
        elif r["missing_days"] == report["trading_dates"]:
            status = "全部缺失"
        else:
            dates = ", ".join(r["missing_dates"][:5])
            suffix = f" ..." if len(r["missing_dates"]) > 5 else ""
            status = dates + suffix
        print(f"{r['code']:<10} {r['missing_days']:<10} {status}")

    print()
    print(f"汇总: {report['codes_with_missing']}/{report['total_codes']} 代码有缺失, "
          f"共 {report['total_missing_days']} 个代码天")

    if report.get("fix_stats"):
        fs = report["fix_stats"]
        print(f"\n修复: {fs['fixed']} 成功, {fs['fix_failed']} 失败, 补入 {fs['bars_added']} 条")
```

- [ ] **Step 6: 提交**

```bash
git add src/quant_etf/minute_fill.py
git commit -m "feat: add minute_fill.py module with fill and audit logic"
```

---

### Task 2: 编写单元测试

**Files:**
- Create: `tests/test_minute_fill.py`

- [ ] **Step 1: 编写 `_calc_bars_to_fetch` 和 `_get_pool_codes` 的测试**

```python
"""minute_fill 模块单元测试"""
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock

from quant_etf.minute_fill import (
    _calc_bars_to_fetch,
    _get_pool_codes,
    fill_minute_gaps,
    audit_minute_gaps,
)


class TestCalcBarsToFetch:
    def test_no_existing_data_uses_max_days(self):
        now = datetime(2025, 5, 28, 15, 0)
        result = _calc_bars_to_fetch(None, now, max_days=60)
        # 60+1 days * 250 = 15250, ceil to 800 -> 16000
        assert result == 16000

    def test_recent_data_small_gap(self):
        now = datetime(2025, 5, 28, 15, 0)
        latest = datetime(2025, 5, 28, 14, 0)
        result = _calc_bars_to_fetch(latest, now, max_days=60)
        # 1 day gap, (1+1)*250 = 500, ceil to 800 -> 800
        assert result == 800

    def test_gap_capped_by_max_days(self):
        now = datetime(2025, 5, 28, 15, 0)
        latest = datetime(2025, 1, 1, 10, 0)
        result = _calc_bars_to_fetch(latest, now, max_days=30)
        # Should be capped at 30 days, not the full ~150 day gap
        expected_raw = 31 * 250  # 7750
        expected = (expected_raw + 799) // 800 * 800  # 8000
        assert result == expected

    def test_zero_gap_returns_zero(self):
        now = datetime(2025, 5, 28, 15, 0)
        # latest in the future -> days_gap would be negative -> 0
        latest = datetime(2025, 5, 28, 16, 0)
        result = _calc_bars_to_fetch(latest, now, max_days=60)
        assert result == 0


class TestGetPoolCodes:
    def test_etf_pool(self):
        from quant_etf.conf import ETF_POOL
        codes = _get_pool_codes("etf")
        assert codes == ETF_POOL

    def test_stock_pool(self):
        from quant_etf.conf import STOCK_POOL
        codes = _get_pool_codes("stock")
        assert codes == STOCK_POOL

    def test_all_pool(self):
        from quant_etf.conf import ALL_POOL
        codes = _get_pool_codes("all")
        assert codes == ALL_POOL

    def test_unknown_defaults_to_etf(self):
        from quant_etf.conf import ETF_POOL
        codes = _get_pool_codes("unknown")
        assert codes == ETF_POOL
```

- [ ] **Step 2: 运行测试确认通过**

Run: `python -m pytest tests/test_minute_fill.py::TestCalcBarsToFetch tests/test_minute_fill.py::TestGetPoolCodes -v`
Expected: 6 passed

- [ ] **Step 3: 编写 `fill_minute_gaps` 的 mock 测试**

追加到 `tests/test_minute_fill.py`:

```python
class TestFillMinuteGaps:
    @patch("quant_etf.minute_fill.save_minute_data_from_dicts")
    @patch("quant_etf.minute_fill.get_minute_bars")
    @patch("quant_etf.minute_fill.get_latest_minute_time")
    def test_skip_when_up_to_date(self, mock_latest, mock_bars, mock_save):
        mock_latest.return_value = datetime(2025, 5, 28, 16, 0)
        result = fill_minute_gaps(["510050"], max_days=60)
        assert result["skipped"] == 1
        mock_bars.assert_not_called()

    @patch("quant_etf.minute_fill.save_minute_data_from_dicts")
    @patch("quant_etf.minute_fill.get_minute_bars")
    @patch("quant_etf.minute_fill.get_latest_minute_time")
    def test_fill_gap_success(self, mock_latest, mock_bars, mock_save):
        mock_latest.return_value = datetime(2025, 5, 28, 10, 0)
        mock_bars.return_value = [
            {"time": datetime(2025, 5, 28, 10, 1), "open": 1.0, "high": 1.0,
             "low": 1.0, "close": 1.0, "volume": 100, "amount": 100.0,
             "year": 2025, "month": 5, "day": 28, "hour": 10, "minute": 1},
            {"time": datetime(2025, 5, 28, 10, 2), "open": 1.0, "high": 1.0,
             "low": 1.0, "close": 1.0, "volume": 100, "amount": 100.0,
             "year": 2025, "month": 5, "day": 28, "hour": 10, "minute": 2},
        ]
        mock_save.return_value = True
        result = fill_minute_gaps(["510050"], max_days=60)
        assert result["success"] == 1
        assert result["total_bars"] == 2

    @patch("quant_etf.minute_fill.get_minute_bars")
    @patch("quant_etf.minute_fill.get_latest_minute_time")
    def test_fill_gap_no_data_from_tdx(self, mock_latest, mock_bars):
        mock_latest.return_value = datetime(2025, 5, 27, 10, 0)
        mock_bars.return_value = []
        result = fill_minute_gaps(["510050"], max_days=60)
        assert result["failed"] == 1

    @patch("quant_etf.minute_fill.save_minute_data_from_dicts")
    @patch("quant_etf.minute_fill.get_minute_bars")
    @patch("quant_etf.minute_fill.get_latest_minute_time")
    def test_fill_first_time_no_history(self, mock_latest, mock_bars, mock_save):
        mock_latest.return_value = None
        mock_bars.return_value = [
            {"time": datetime(2025, 5, 28, 10, 0), "open": 1.0, "high": 1.0,
             "low": 1.0, "close": 1.0, "volume": 100, "amount": 100.0,
             "year": 2025, "month": 5, "day": 28, "hour": 10, "minute": 0},
        ]
        mock_save.return_value = True
        result = fill_minute_gaps(["510050"], max_days=60)
        assert result["success"] == 1
        assert result["total_bars"] == 1
```

- [ ] **Step 4: 运行全部测试确认通过**

Run: `python -m pytest tests/test_minute_fill.py -v`
Expected: 10 passed

- [ ] **Step 5: 提交**

```bash
git add tests/test_minute_fill.py
git commit -m "test: add unit tests for minute_fill module"
```

---

### Task 3: 注册 CLI 命令 `minute-fill` 和 `minute-audit`

**Files:**
- Modify: `src/quant_etf/cli.py`

- [ ] **Step 1: 在 `build_parser()` 中添加两个子命令解析器**

在 `cli.py` 第 521 行（`return parser` 之前）插入：

```python
    p = sub.add_parser("minute-fill", help="智能增量补全分钟K线数据")
    p.add_argument("--pool", type=str, default="etf",
                   choices=["etf", "stock", "all"],
                   help="股票池 (默认: etf)")
    p.add_argument("--days", type=int, default=60, help="最大回溯天数 (默认: 60)")
    p.add_argument("--codes", type=str, help="逗号分隔的标的代码 (覆盖 --pool)")

    p = sub.add_parser("minute-audit", help="审计分钟K线数据缺失")
    p.add_argument("--pool", type=str, default="etf",
                   choices=["etf", "stock", "all"],
                   help="股票池 (默认: etf)")
    p.add_argument("--days", type=int, default=60, help="审计最近N个交易日 (默认: 60)")
    p.add_argument("--codes", type=str, help="逗号分隔的标的代码 (覆盖 --pool)")
    p.add_argument("--fix", action="store_true", help="自动修复缺失")
```

- [ ] **Step 2: 实现 `cmd_minute_fill` 和 `cmd_minute_audit` 处理函数**

在 `cli.py` 的 `cmd_clean_minute_data` 函数（第 608 行）之后插入：

```python
def cmd_minute_fill(args):
    from loguru import logger
    from quant_etf.minute_fill import (
        _get_pool_codes,
        fill_minute_gaps,
        print_fill_report,
    )

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    logger.add(log_dir / "minute_fill_{time:YYYY-MM-DD}.log", rotation="10 MB", encoding="utf-8")

    codes = args.codes.split(",") if args.codes else _get_pool_codes(args.pool)

    logger.info("=" * 60)
    logger.info("分钟K线智能补全")
    logger.info(f"标的数: {len(codes)}, 最大回溯: {args.days} 天")
    logger.info("=" * 60)

    stats = fill_minute_gaps(codes=codes, max_days=args.days)
    print_fill_report(stats)


def cmd_minute_audit(args):
    from loguru import logger
    from quant_etf.minute_fill import (
        _get_pool_codes,
        audit_minute_gaps,
        print_audit_report,
    )

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    logger.add(log_dir / "minute_audit_{time:YYYY-MM-DD}.log", rotation="10 MB", encoding="utf-8")

    codes = args.codes.split(",") if args.codes else _get_pool_codes(args.pool)

    logger.info("=" * 60)
    logger.info("分钟K线数据审计")
    logger.info(f"标的数: {len(codes)}, 审计天数: {args.days}")
    if args.fix:
        logger.info("模式: 审计 + 自动修复")
    logger.info("=" * 60)

    report = audit_minute_gaps(codes=codes, max_days=args.days, fix=args.fix)
    print_audit_report(report)
```

- [ ] **Step 3: 在 `COMMANDS` 字典中注册新命令**

在 `cli.py` 的 `COMMANDS` 字典中添加两个条目：

```python
    "minute-fill": cmd_minute_fill,
    "minute-audit": cmd_minute_audit,
```

- [ ] **Step 4: 更新 CLI docstring**

在 `cli.py` 文件顶部的 docstring（第 3-19 行）中添加：

```
    minute-fill       智能增量补全分钟K线数据
    minute-audit      审计分钟K线数据缺失
```

- [ ] **Step 5: 验证 CLI 注册正确**

Run: `uv run quant-etf --help`
Expected: 输出包含 `minute-fill` 和 `minute-audit`

Run: `uv run quant-etf minute-fill --help`
Expected: 输出包含 `--pool`, `--days`, `--codes`

- [ ] **Step 6: 提交**

```bash
git add src/quant_etf/cli.py
git commit -m "feat: register minute-fill and minute-audit CLI commands"
```

---

### Task 4: Dashboard 启动集成

**Files:**
- Modify: `src/quant_etf/dashboard/services/startup_preload.py`

- [ ] **Step 1: 在 `_preload_in_thread` 函数中添加 `ensure_minute_data_ready` 调用**

修改 `startup_preload.py` 的 `_preload_in_thread` 函数：

将:
```python
    def _preload_in_thread():
        global _preload_completed, _preload_error
        try:
            preload_market_state()
            _preload_completed = True
        except Exception as e:
            _preload_error = str(e)
            logger.error(f"Background preload failed: {e}")
```

改为:
```python
    def _preload_in_thread():
        global _preload_completed, _preload_error
        try:
            preload_market_state()
            from quant_etf.minute_fill import ensure_minute_data_ready
            ensure_minute_data_ready()
            _preload_completed = True
        except Exception as e:
            _preload_error = str(e)
            logger.error(f"Background preload failed: {e}")
```

- [ ] **Step 2: 验证语法正确**

Run: `python -c "from quant_etf.dashboard.services.startup_preload import start_background_preload; print('OK')"`
Expected: `OK`

- [ ] **Step 3: 提交**

```bash
git add src/quant_etf/dashboard/services/startup_preload.py
git commit -m "feat: integrate minute data fill into dashboard startup"
```

---

### Task 5: 修复 `_detect_missing_dates` 中的日期比较逻辑

Task 2 的审计函数中 `_detect_missing_dates` 的日期比较逻辑比较绕（PG 返回的 `DATE(time)` 是 `datetime.date` 对象，而交易日历中的是 `datetime` 对象）。需要在测试中验证并修复。

**Files:**
- Modify: `src/quant_etf/minute_fill.py`
- Modify: `tests/test_minute_fill.py`

- [ ] **Step 1: 简化 `_detect_missing_dates` 的日期比较**

将 `_detect_missing_dates` 函数替换为更简洁的实现：

```python
def _detect_missing_dates(
    code: str,
    trading_dates: list[datetime],
) -> list[datetime]:
    """
    对比交易日历和 PG 数据，返回缺失的日期列表

    :param code: 证券代码
    :param trading_dates: 交易日历（datetime 对象列表）
    :return: 缺失的日期列表
    """
    from quant_etf.minute_collector import _get_pg_conn

    if not trading_dates:
        return []

    conn = _get_pg_conn()
    cur = conn.cursor()

    start_date = trading_dates[0].replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = trading_dates[-1].replace(hour=23, minute=59, second=59, microsecond=0)

    cur.execute(
        """
        SELECT DATE(time) AS d, COUNT(*) AS cnt
        FROM minute_bars
        WHERE code = %s AND time >= %s AND time <= %s
        GROUP BY d
        """,
        [code, start_date, end_date],
    )
    rows = cur.fetchall()

    # key: 'YYYY-MM-DD' 字符串, value: count
    existing = {}
    for row in rows:
        d = row[0]
        key = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
        existing[key] = row[1]

    missing = []
    for td in trading_dates:
        key = td.strftime("%Y-%m-%d")
        count = existing.get(key, 0)
        if count < 100:
            missing.append(td)

    return missing
```

- [ ] **Step 2: 添加 `_detect_missing_dates` 的 mock 测试**

追加到 `tests/test_minute_fill.py`:

```python
class TestDetectMissingDates:
    @patch("quant_etf.minute_fill._get_pg_conn")
    def test_all_present(self, mock_conn):
        from quant_etf.minute_fill import _detect_missing_dates

        mock_cur = MagicMock()
        mock_conn.return_value.cursor.return_value = mock_cur
        # PG 返回两个日期，各有 240 根
        mock_cur.fetchall.return_value = [
            (datetime(2025, 5, 26).date(), 240),
            (datetime(2025, 5, 27).date(), 240),
        ]
        trading_dates = [datetime(2025, 5, 26), datetime(2025, 5, 27)]
        result = _detect_missing_dates("510050", trading_dates)
        assert result == []

    @patch("quant_etf.minute_fill._get_pg_conn")
    def test_one_missing(self, mock_conn):
        from quant_etf.minute_fill import _detect_missing_dates

        mock_cur = MagicMock()
        mock_conn.return_value.cursor.return_value = mock_cur
        mock_cur.fetchall.return_value = [
            (datetime(2025, 5, 26).date(), 240),
        ]
        trading_dates = [datetime(2025, 5, 26), datetime(2025, 5, 27)]
        result = _detect_missing_dates("510050", trading_dates)
        assert len(result) == 1
        assert result[0] == datetime(2025, 5, 27)

    @patch("quant_etf.minute_fill._get_pg_conn")
    def test_partial_missing_counts_as_missing(self, mock_conn):
        from quant_etf.minute_fill import _detect_missing_dates

        mock_cur = MagicMock()
        mock_conn.return_value.cursor.return_value = mock_cur
        mock_cur.fetchall.return_value = [
            (datetime(2025, 5, 26).date(), 50),  # 只有 50 根，< 100
        ]
        trading_dates = [datetime(2025, 5, 26)]
        result = _detect_missing_dates("510050", trading_dates)
        assert len(result) == 1

    @patch("quant_etf.minute_fill._get_pg_conn")
    def test_empty_trading_dates(self, mock_conn):
        from quant_etf.minute_fill import _detect_missing_dates

        result = _detect_missing_dates("510050", [])
        assert result == []
        mock_conn.assert_not_called()
```

- [ ] **Step 3: 运行全部测试**

Run: `python -m pytest tests/test_minute_fill.py -v`
Expected: 14 passed

- [ ] **Step 4: 提交**

```bash
git add src/quant_etf/minute_fill.py tests/test_minute_fill.py
git commit -m "fix: simplify date comparison in _detect_missing_dates with string keys"
```

---

### Task 6: 端到端验证

**Files:** 无新文件

- [ ] **Step 1: 验证 `minute-fill --help`**

Run: `uv run quant-etf minute-fill --help`
Expected: 显示 `--pool`, `--days`, `--codes` 参数说明

- [ ] **Step 2: 验证 `minute-audit --help`**

Run: `uv run quant-etf minute-audit --help`
Expected: 显示 `--pool`, `--days`, `--codes`, `--fix` 参数说明

- [ ] **Step 3: 用单个代码 dry-run 验证 fill（需要 PG + TDX 连接）**

Run: `uv run quant-etf minute-fill --codes 510050 --days 5`
Expected: 输出补全报告（成功/跳过/失败）

- [ ] **Step 4: 用单个代码验证 audit（需要 PG + TDX 连接）**

Run: `uv run quant-etf minute-audit --codes 510050 --days 5`
Expected: 输出审计报告

- [ ] **Step 5: 运行全部测试确保无回归**

Run: `python -m pytest tests/ -v`
Expected: 全部 passed

- [ ] **Step 6: 最终提交（如有格式修正）**

```bash
git add -A
git commit -m "chore: e2e verification and cleanup"
```
