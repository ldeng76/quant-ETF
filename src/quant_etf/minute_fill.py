"""
分钟K线数据智能补全与审计模块

提供增量补全（minute-fill）、缺失审计（minute-audit）和
Dashboard 启动自愈（ensure_minute_data_ready）功能。
"""
import math
from datetime import datetime, timedelta
from loguru import logger


def _get_pool_codes(pool_name: str) -> list[str]:
    """根据 pool 名称返回对应的代码列表。

    :param pool_name: "etf" / "stock" / "all"
    :return: 代码列表
    """
    from quant_etf.pool_loader import get_stock_pool

    pools = {
        "etf": get_stock_pool("etf"),
        "stock": get_stock_pool("stock"),
        "all": get_stock_pool("etf") + get_stock_pool("stock") + get_stock_pool("mid_term"),
    }
    return list(pools.get(pool_name.lower(), get_stock_pool("etf")))


def _calc_bars_to_fetch(
    latest_time: datetime | None,
    now: datetime,
    max_days: int = 60,
) -> int:
    """估算需要从 pytdx 拉取的分钟K线数量。

    A 股每天约 48 根 5 分钟K线，用 50 根/天作为安全余量。
    结果向上取整到 800 的倍数（pytdx 分页对齐）。

    :param latest_time: 该代码在 PG 中最新的分钟时间戳，None 表示无数据
    :param now: 当前时间
    :param max_days: 最大回补天数
    :return: 需要拉取的K线数量
    """
    if latest_time is None:
        days_gap = max_days
    else:
        days_gap = (now - latest_time).days + 1
        days_gap = min(days_gap, max_days)

    if days_gap < 0:
        return 0

    # 50 根/天 * 天数，向上取整到 800 的倍数
    bars = math.ceil(days_gap * 50 / 800) * 800
    return max(bars, 800)


def fill_minute_gaps(
    codes: list[str],
    max_days: int = 60,
) -> dict:
    """增量补全分钟K线数据的主入口。

    对每个代码：查询 PG 最新时间戳，估算需要拉取的K线数量，
    从 pytdx 拉取并过滤后写入 PG。

    :param codes: 证券代码列表
    :param max_days: 最大回补天数
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

    for code in codes:
        try:
            latest_time = get_latest_minute_time(code)
            bars_to_fetch = _calc_bars_to_fetch(latest_time, now, max_days)

            if bars_to_fetch == 0:
                stats["skipped"] += 1
                continue

            data = get_minute_bars(code, count=bars_to_fetch)
            if not data:
                stats["failed"] += 1
                stats["failures"].append((code, "no data from pytdx"))
                logger.warning(f"fill: {code} - no data returned from pytdx")
                continue

            # 只保留比 PG 中最新时间更新的记录
            if latest_time is not None:
                filtered = [b for b in data if b.get("time") and b["time"] > latest_time]
            else:
                filtered = [b for b in data if b.get("time")]

            if not filtered:
                stats["skipped"] += 1
                continue

            saved = save_minute_data_from_dicts(code, filtered)
            if saved:
                stats["success"] += 1
                stats["total_bars"] += len(filtered)
                logger.debug(f"fill: {code} - inserted {len(filtered)} bars")
            else:
                stats["failed"] += 1
                stats["failures"].append((code, "save failed"))
                logger.warning(f"fill: {code} - save failed")

        except Exception as e:
            stats["failed"] += 1
            stats["failures"].append((code, str(e)))
            logger.error(f"fill: {code} - error: {e}")

    logger.info(
        f"fill complete: {stats['success']}/{stats['total']} success, "
        f"{stats['skipped']} skipped, {stats['failed']} failed, "
        f"{stats['total_bars']} bars inserted"
    )
    return stats


def _detect_missing_dates(
    code: str,
    trading_dates: list[datetime],
) -> list[datetime]:
    """对比交易日历与 PG 数据，返回缺失的交易日列表。

    使用字符串键比较（避免 date 类型不一致问题）。
    缺失定义：该交易日无数据或K线数量 < 100。

    :param code: 证券代码
    :param trading_dates: 交易日列表
    :return: 缺失的交易日列表
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


def audit_minute_gaps(
    codes: list[str],
    max_days: int = 60,
    fix: bool = False,
) -> dict:
    """审计分钟K线数据的缺失情况，可选自动修复。

    :param codes: 证券代码列表
    :param max_days: 审计最近多少个交易日
    :param fix: 是否自动修复缺失数据
    :return: 审计报告 dict
    """
    from quant_etf.minute_collector import (
        get_minute_bars,
        save_minute_data_from_dicts,
    )
    from quant_etf.trading_day import get_available_trading_dates

    # 获取交易日历
    all_trading_dates = get_available_trading_dates()
    if not all_trading_dates:
        logger.warning("audit: no trading dates available")
        return {
            "trading_dates": 0,
            "date_range": None,
            "total_codes": len(codes),
            "codes_with_missing": 0,
            "total_missing_days": 0,
            "results": [],
            "fix_stats": None,
        }

    # 取最近 max_days 个交易日
    trading_dates = all_trading_dates[-max_days:]

    results = []
    total_missing_days = 0
    fix_stats = None

    if fix:
        fix_stats = {"success": 0, "failed": 0, "total_bars": 0}

    for code in codes:
        missing = _detect_missing_dates(code, trading_dates)

        result_entry = {
            "code": code,
            "missing_days": len(missing),
            "missing_dates": [d.strftime("%Y-%m-%d") for d in missing],
        }
        results.append(result_entry)

        if missing:
            total_missing_days += len(missing)

            if fix:
                now = datetime.now()
                min_missing = min(missing)
                days_gap = (now - min_missing).days
                bars_to_fetch = math.ceil((days_gap + 1) * 50 / 800) * 800
                bars_to_fetch = max(bars_to_fetch, 800)

                try:
                    data = get_minute_bars(code, count=bars_to_fetch)
                    if data:
                        # 只保留缺失日期范围内的记录
                        missing_keys = {d.strftime("%Y-%m-%d") for d in missing}
                        filtered = [
                            b for b in data
                            if b.get("time") and b["time"].strftime("%Y-%m-%d") in missing_keys
                        ]
                        if filtered:
                            saved = save_minute_data_from_dicts(code, filtered)
                            if saved:
                                fix_stats["success"] += 1
                                fix_stats["total_bars"] += len(filtered)
                                logger.info(
                                    f"audit-fix: {code} - fixed {len(filtered)} bars "
                                    f"across {len(missing)} dates"
                                )
                            else:
                                fix_stats["failed"] += 1
                        else:
                            fix_stats["failed"] += 1
                    else:
                        fix_stats["failed"] += 1
                except Exception as e:
                    fix_stats["failed"] += 1
                    logger.error(f"audit-fix: {code} - error: {e}")

    codes_with_missing = sum(1 for r in results if r["missing_days"] > 0)
    logger.info(
        f"audit complete: {codes_with_missing}/{len(codes)} codes with gaps, "
        f"{total_missing_days} total missing code-days"
    )
    return {
        "trading_dates": len(trading_dates),
        "date_range": (
            trading_dates[0].strftime("%Y-%m-%d"),
            trading_dates[-1].strftime("%Y-%m-%d"),
        ),
        "total_codes": len(codes),
        "codes_with_missing": codes_with_missing,
        "total_missing_days": total_missing_days,
        "results": results,
        "fix_stats": fix_stats if fix else None,
    }


def ensure_minute_data_ready() -> None:
    """Dashboard 启动入口：仅在主节点时自动补全所有池的分钟数据。

    失败不阻塞启动，仅记录日志警告。
    """
    from quant_etf.dashboard.config import IS_PRIMARY
    from quant_etf.pool_loader import get_stock_pool

    if not IS_PRIMARY:
        logger.debug("ensure_minute_data_ready: skipped (not primary)")
        return

    logger.info("ensure_minute_data_ready: starting minute fill...")
    try:
        # 动态获取所有池（ETF + 短线股票 + 中期反弹）
        all_codes = (
            get_stock_pool("etf")
            + get_stock_pool("stock")
            + get_stock_pool("mid_term")
        )
        stats = fill_minute_gaps(all_codes, max_days=60)
        logger.info(
            f"ensure_minute_data_ready: done - "
            f"{stats['success']}/{stats['total']} success, "
            f"{stats['total_bars']} bars inserted"
        )
    except Exception as e:
        logger.warning(f"ensure_minute_data_ready: fill failed (non-fatal): {e}")


def print_fill_report(stats: dict) -> None:
    """打印补全报告到终端。

    :param stats: fill_minute_gaps() 的返回值
    """
    total = stats.get("total", 0)
    success = stats.get("success", 0)
    skipped = stats.get("skipped", 0)
    failed = stats.get("failed", 0)
    bars = stats.get("total_bars", 0)
    failures = stats.get("failures", [])

    print(f"\n补全完成: {success}/{total} 成功, {skipped} 跳过, {failed} 失败")
    print(f"  补入数据: {bars:,} 条")

    if failures:
        print("  失败代码:")
        for code, reason in failures:
            print(f"    {code} ({reason})")
    print()


def print_audit_report(report: dict) -> None:
    """打印审计报告到终端。

    :param report: audit_minute_gaps() 的返回值
    """
    num_trading_dates = report.get("trading_dates", 0)
    date_range = report.get("date_range")
    results = report.get("results", [])
    total_missing = report.get("total_missing_days", 0)
    codes_with_missing = report.get("codes_with_missing", 0)
    fix_stats = report.get("fix_stats")

    if date_range:
        print(f"\n审计范围: {date_range[0]} ~ {date_range[1]} ({num_trading_dates} 个交易日)\n")
    else:
        print("\n审计范围: 无交易日数据\n")

    print(f"{'代码':<10} {'缺失天数':>8}  状态")
    print("-" * 50)

    for entry in results:
        code = entry["code"]
        missing_days = entry["missing_days"]
        missing_dates = entry["missing_dates"]

        if missing_days == 0:
            status = "完整"
        elif missing_days == num_trading_dates:
            status = "全部缺失"
        else:
            shown = missing_dates[:5]
            dates_str = ", ".join(shown)
            if len(missing_dates) > 5:
                dates_str += ", ..."
            status = dates_str

        line = f"{code:<10} {missing_days:>8}  {status}"
        print(line)

    total_codes = len(results)
    print(f"\n汇总: {codes_with_missing}/{total_codes} 代码有缺失, 共 {total_missing} 个代码天")
    if fix_stats:
        print(f"  修复: {fix_stats['success']} 成功, {fix_stats['failed']} 失败, "
              f"{fix_stats['total_bars']} 条补入")
    print()


# ---------------------------------------------------------------------------
# 深度审计与深回补（做T回测 M0）
# ---------------------------------------------------------------------------

def depth_audit(codes: list[str]) -> dict:
    """审计每只标的在 PG minute_bars 中的历史深度（起止/天数/根数）。

    与 audit_minute_gaps 的"近N日缺口"视角不同，本函数回答
    "回测窗口能覆盖多长"——做T回测的样本区间标注依据。
    """
    from quant_etf.minute_collector import _get_pg_conn as get_pg_conn

    conn = get_pg_conn()
    cur = conn.cursor()
    results = []
    for code in codes:
        cur.execute(
            """
            select count(*), min(time), max(time),
                   count(distinct date(time))
            from minute_bars where code = %s
            """,
            (code,),
        )
        n, tmin, tmax, days = cur.fetchone()
        results.append({
            "code": code,
            "bars": int(n),
            "days": int(days),
            "first": tmin.strftime("%Y-%m-%d %H:%M") if tmin else None,
            "last": tmax.strftime("%Y-%m-%d %H:%M") if tmax else None,
        })
    conn.close()

    covered = [r for r in results if r["bars"] > 0]
    return {
        "total_codes": len(codes),
        "codes_with_data": len(covered),
        "global_first": min((r["first"] for r in covered), default=None),
        "global_last": max((r["last"] for r in covered), default=None),
        "results": results,
    }


def print_depth_report(depth: dict) -> None:
    print("\n===== 分钟数据深度审计 =====")
    print(f"标的数: {depth['total_codes']}, 有数据: {depth['codes_with_data']}")
    print(f"全局覆盖: {depth['global_first']} → {depth['global_last']}")
    for r in depth["results"][:10]:
        print(f"  {r['code']}: {r['bars']}根 / {r['days']}天 "
              f"[{r['first']} → {r['last']}]")
    if len(depth["results"]) > 10:
        print(f"  ... 共 {len(depth['results'])} 只")


def save_depth_report(depth: dict, path) -> None:
    """审计报告落盘，作为回测报告样本区间标注的引用来源。"""
    from pathlib import Path

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 分钟数据深度审计（做T回测 M0）",
        "",
        f"- 审计标的数: {depth['total_codes']}",
        f"- 有数据标的: {depth['codes_with_data']}",
        f"- 全局覆盖: {depth['global_first']} → {depth['global_last']}",
        "",
        "| code | bars | days | first | last |",
        "|---|---|---|---|---|",
    ]
    for r in depth["results"]:
        lines.append(
            f"| {r['code']} | {r['bars']} | {r['days']} | {r['first']} | {r['last']} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(f"depth report saved: {path}")


def _fetch_akshare_5m(code: str, start: str, end: str,
                      retries: int = 3, backoff: float = 20.0) -> list[dict]:
    """akshare 东财源拉 5 分钟线（pytdx 不可用时的备源），带退避重试。

    返回与 save_minute_data_from_dicts 兼容的 dict 列表。
    """
    import time as time_module

    import akshare as ak
    import pandas as pd

    is_index = code in ("000300", "000016", "000905", "399001", "399006")
    for attempt in range(1, retries + 1):
        try:
            if is_index:
                df = ak.index_zh_a_hist_min_em(
                    symbol=code, period="5",
                    start_date=start, end_date=end,
                )
            else:
                df = ak.fund_etf_hist_min_em(
                    symbol=code, period="5",
                    start_date=start, end_date=end, adjust="",
                )
            if df is None or df.empty:
                return []
            out = []
            for _, row in df.iterrows():
                out.append({
                    "time": pd.to_datetime(row["时间"]),
                    "open": float(row["开盘"]),
                    "high": float(row["最高"]),
                    "low": float(row["最低"]),
                    "close": float(row["收盘"]),
                    "volume": float(row["成交量"]),
                    "amount": float(row["成交额"]) if "成交额" in row else 0.0,
                })
            return out
        except Exception as e:
            logger.warning(f"akshare {code} attempt {attempt}/{retries} failed: {e!r}")
            if attempt < retries:
                time_module.sleep(backoff * attempt)
    return []


def deep_backfill_codes(
    codes: list[str],
    total_bars: int = 14_000,
    source: str = "pytdx",
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """向更早的历史深回补 5 分钟线（fill_minute_gaps 只向前补，本函数补深处）。

    :param total_bars: pytdx 源的拉取根数（800 的倍数翻页至极限）
    :param source: "pytdx"（主源）或 "akshare"（东财备源，需 start/end）
    """
    stats = {
        "total": len(codes), "success": 0, "failed": 0,
        "total_bars": 0, "failures": [],
    }
    for code in codes:
        try:
            if source == "pytdx":
                from quant_etf.minute_collector import (
                    get_minute_bars,
                    save_minute_data_from_dicts,
                )
                data = get_minute_bars(code, count=total_bars)
            else:
                from quant_etf.minute_collector import save_minute_data_from_dicts
                data = _fetch_akshare_5m(code, start, end)
            if not data:
                stats["failed"] += 1
                stats["failures"].append((code, "no data from source"))
                logger.warning(f"deep-fill: {code} - no data returned")
                continue
            saved = save_minute_data_from_dicts(code, data)
            if saved:
                stats["success"] += 1
                stats["total_bars"] += len(data)
                times = [b["time"] for b in data if b.get("time")]
                logger.info(
                    f"deep-fill: {code} - {len(data)} bars "
                    f"[{min(times)} → {max(times)}]"
                )
            else:
                stats["failed"] += 1
                stats["failures"].append((code, "save failed"))
        except Exception as e:
            stats["failed"] += 1
            stats["failures"].append((code, str(e)))
            logger.error(f"deep-fill: {code} - error: {e}")
    return stats
