import pandas as pd
from pathlib import Path
from loguru import logger
from pytdx.reader import TdxDailyBarReader

from quant_etf.conf import TDX_VIPDOC_DIR


def get_tdx_path(code: str) -> Path | None:
    """
    根据代码获取通达信 .day 文件路径
    :param code: 证券代码 (e.g. "510050", "000001")
    :return: .day 文件的绝对路径，如果未找到则返回 None
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


def parse_tdx_day_file(file_path: Path | str) -> pd.DataFrame:
    """
    使用 pytdx 解析通达信 .day 文件
    :param file_path: 文件路径
    :return: DataFrame (columns: date, open, high, low, close, amount, volume)
    """
    path = Path(file_path)

    if not path.exists():
        logger.warning(f"TDX file not found: {path}")
        return pd.DataFrame()

    try:
        reader = TdxDailyBarReader()
        df = reader.get_df(str(path))

        if df.empty:
            logger.warning(f"TDX file is empty: {path}")
            return df

        df = df.reset_index()
        df.rename(columns={"index": "date"}, inplace=True)
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        df.sort_index(inplace=True)

        df["pct_chg"] = df["close"].pct_change() * 100
        df["pct_chg"] = df["pct_chg"].fillna(0.0)

        return df

    except Exception as e:
        logger.error(f"Failed to parse TDX file {path}: {e}")
        return pd.DataFrame()
