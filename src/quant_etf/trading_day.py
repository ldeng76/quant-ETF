from datetime import datetime, time
from pathlib import Path
import pandas as pd
from loguru import logger
from pytdx.reader import TdxDailyBarReader
from quant_etf.conf import TDX_VIPDOC_DIR


def get_tdx_path(code: str) -> Path | None:
    """
    根据代码获取通达信 .day 文件路径
    """
    if code.startswith(("5", "6")):
        market = "sh"
    elif code.startswith(("0", "1", "3")):
        market = "sz"
    else:
        market = None

    if market:
        file_path = TDX_VIPDOC_DIR / market / "lday" / f"{market}{code}.day"
        if file_path.exists():
            return file_path
    else:
        for m in ["sh", "sz"]:
            p = TDX_VIPDOC_DIR / m / "lday" / f"{m}{code}.day"
            if p.exists():
                return p

    return None


def get_available_trading_dates(code: str = "510310") -> list[datetime]:
    """
    使用 TdxDailyBarReader 读取指定 ETF 的数据，返回所有有效交易日列表（已排序）
    """
    tdx_path = get_tdx_path(code)
    if not tdx_path or not tdx_path.exists():
        logger.warning(f"No TDX data file found for {code}")
        return []

    try:
        reader = TdxDailyBarReader()
        df = reader.get_df(str(tdx_path))

        if df.empty:
            return []

        df = df.reset_index()
        df.rename(columns={"index": "date"}, inplace=True)
        df["date"] = pd.to_datetime(df["date"])
        df.sort_values("date", inplace=True)

        dates = df["date"][-10:].tolist()
        logger.info(f"Found {len(dates)} trading dates for {code}")
        return dates
    except Exception as e:
        logger.error(f"Error reading TDX data for {code}: {e}")
        return []


def get_trading_dates_between(
    start_date: str | datetime,
    end_date: str | datetime,
    code: str = "510310"
) -> list[datetime]:
    """
    返回指定日期范围内的所有有效交易日
    """
    if isinstance(start_date, str):
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    else:
        start_dt = start_date

    if isinstance(end_date, str):
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    else:
        end_dt = end_date

    all_dates = get_available_trading_dates(code)

    filtered = [
        d for d in all_dates
        if start_dt <= d <= end_dt
    ]

    logger.info(f"Trading dates between {start_date} and {end_date}: {len(filtered)} days")
    return filtered


def is_intraday() -> bool:
    """
    判断当前是否处于 A 股交易时间段
    交易时间：工作日 09:30-11:30 或 13:00-15:00
    """
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    current = now.time()
    return (time(9, 30) <= current <= time(11, 30) or
            time(13, 0) <= current <= time(15, 0))


# test code
def test1():
    code: str = "510310"
    all_dates = get_available_trading_dates(code)
    logger.info(all_dates)

if __name__ == '__main__':
    test1()