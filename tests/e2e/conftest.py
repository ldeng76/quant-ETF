"""
Shared pytest fixtures for E2E tests
"""
import json
import struct
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Mock data generation helpers
# ---------------------------------------------------------------------------

def generate_price_series(
    start_price: float = 3.0,
    days: int = 300,
    trend: float = 0.0,       # daily drift (e.g. 0.001 = +0.1%/day)
    volatility: float = 0.02, # daily vol
    seed: int = 42,
) -> pd.DataFrame:
    """
    Generate a realistic OHLCV price series for E2E tests.
    Returns a DataFrame with columns: open, high, low, close, volume, amount.
    Indexed by date (DatetimeIndex).
    """
    rng = np.random.default_rng(seed)
    n = days

    # Generate close prices via geometric Brownian motion
    daily_returns = rng.normal(trend, volatility, n)
    close = start_price * np.cumprod(1 + daily_returns)

    # Derive OHLCV from close
    intraday_range = close * rng.uniform(0.005, 0.03, n)
    open_ = close + rng.uniform(-intraday_range * 0.3, intraday_range * 0.3, n)
    high = np.maximum(open_, close) + rng.uniform(0, intraday_range * 0.5, n)
    low = np.minimum(open_, close) - rng.uniform(0, intraday_range * 0.5, n)
    volume = rng.integers(1_000_000, 50_000_000, n).astype(float)
    amount = volume * close

    dates = pd.bdate_range(end=datetime.now().date(), periods=n, freq="B")
    df = pd.DataFrame(
        {
            "open": np.round(open_, 3),
            "high": np.round(high, 3),
            "low": np.round(low, 3),
            "close": np.round(close, 3),
            "volume": volume,
            "amount": np.round(amount, 2),
        },
        index=dates,
    )
    df.index.name = "date"
    return df


def generate_momentum_etf_data(
    code: str,
    base_price: float,
    momentum_type: str = "strong",  # strong | weak | declining | rebound
    days: int = 300,
    seed: int = 42,
) -> pd.DataFrame:
    """
    Generate ETF data with a specific momentum profile.
    - strong:    sustained uptrend (positive trend, very low vol)
    - weak:      flat/slightly up (near-zero trend)
    - declining: downtrend (negative trend)
    - rebound:   dropped significantly recently, now bouncing back
    """
    if momentum_type == "strong":
        # High trend, very low vol to ensure consistent uptrend
        return generate_price_series(base_price, days, trend=0.008, volatility=0.008, seed=seed)
    elif momentum_type == "weak":
        return generate_price_series(base_price, days, trend=0.0005, volatility=0.02, seed=seed)
    elif momentum_type == "declining":
        # Negative trend, low vol to ensure consistent decline
        return generate_price_series(base_price, days, trend=-0.008, volatility=0.008, seed=seed)
    elif momentum_type == "rebound":
        # First 250 days: up, then 50 days: sharp drop + bounce
        early = generate_price_series(base_price, days - 30, trend=0.002, volatility=0.015, seed=seed)
        high_point = early["close"].iloc[-1]
        # Drop 25% over 20 days
        drop_days = 20
        drop_returns = np.linspace(0, -0.015, drop_days)
        drop_close = high_point * np.cumprod(1 + drop_returns)
        # Then bounce 8% over 10 days
        bounce_days = 10
        bounce_returns = np.linspace(0.01, 0.005, bounce_days)
        bounce_close = drop_close[-1] * np.cumprod(1 + bounce_returns)

        combined_close = np.concatenate([early["close"].values, drop_close, bounce_close])
        n_total = len(combined_close)
        rng = np.random.default_rng(seed + 1)
        intraday = combined_close * rng.uniform(0.01, 0.025, n_total)
        open_ = combined_close + rng.uniform(-intraday * 0.3, intraday * 0.3, n_total)
        high = np.maximum(open_, combined_close) + rng.uniform(0, intraday * 0.3, n_total)
        low = np.minimum(open_, combined_close) - rng.uniform(0, intraday * 0.3, n_total)
        volume = rng.integers(1_000_000, 30_000_000, n_total).astype(float)
        amount = volume * combined_close

        all_dates = pd.bdate_range(end=datetime.now().date(), periods=n_total, freq="B")
        return pd.DataFrame(
            {
                "open": np.round(open_, 3),
                "high": np.round(high, 3),
                "low": np.round(low, 3),
                "close": np.round(combined_close, 3),
                "volume": volume,
                "amount": np.round(amount, 2),
            },
            index=all_dates,
        )
    else:
        raise ValueError(f"Unknown momentum_type: {momentum_type}")


def write_tdx_day_file(df: pd.DataFrame, path: Path) -> Path:
    """
    Write a DataFrame to a TDX .day binary file.
    TDX daily bar format (32 bytes per record):
    - date (uint32): YYYYMMDD
    - open (uint32): price * 100
    - high (uint32): price * 100
    - low (uint32): price * 100
    - close (uint32): price * 100
    - amount (float32)
    - volume (uint32)
    - reserved (uint32)
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    records = []
    for date, row in df.iterrows():
        date_int = int(date.strftime("%Y%m%d"))
        records.append(struct.pack(
            "IIIIIIfI",
            date_int,
            int(row["open"] * 100),
            int(row["high"] * 100),
            int(row["low"] * 100),
            int(row["close"] * 100),
            int(row["amount"]),
            int(row["volume"]),
            0,  # reserved
        ))

    path.write_bytes(b"".join(records))
    return path


def create_mock_name_map(codes: list[str]) -> list[dict]:
    """Create a mock stock_code_name.json content."""
    names = {
        "510050": "50ETF",
        "510310": "沪深300ETF",
        "159352": "A500ETF",
        "510880": "红利ETF",
        "561280": "A500ETF基金",
        "159957": "创业板50ETF",
        "159949": "创业板50",
        "159991": "创大盘ETF",
        "159780": "双创ETF",
        "159811": "中证500ETF",
        "512480": "半导体ETF",
        "159560": "云计算ETF",
        "159516": "机器人ETF",
        "562820": "农业ETF",
        "159590": "芯片ETF",
        "562920": "智能汽车ETF",
        "159819": "人工智能ETF",
        "159363": "A50ETF",
        "159526": "消费50ETF",
        "159206": "港股科技ETF",
        "561220": "矿业ETF",
        "159667": "新能源ETF",
        "159638": "电池ETF",
        "516390": "光伏ETF",
        "159565": "传媒ETF",
        "159261": "游戏ETF",
        "560980": "军工ETF",
        "159775": "国证2000ETF",
        "561380": "稀土ETF",
        "561700": "工业母机ETF",
        "159713": "光伏产业ETF",
        "516020": "新能源车ETF",
        "159652": "科创板50ETF",
        "512660": "军工ETF",
        "515220": "煤炭ETF",
        "588010": "科创50ETF",
        "159886": "机械ETF",
        "512070": "券商ETF",
        "515020": "银行ETF",
        "513090": "香港证券ETF",
        "517520": "有色金属ETF",
        "516130": "消费ETF",
        "512690": "酒ETF",
        "560080": "旅游ETF",
        "159859": "生物医药ETF",
        "159567": "中药ETF",
        "159265": "饲料ETF",
        "159869": "游戏动漫ETF",
        "159856": "社交媒体ETF",
        "159202": "两年期国债ETF",
        "159742": "港股创新药ETF",
        "159605": "中概互联ETF",
        "159750": "证券ETF",
        "159712": "500ETF",
        "159312": "2000ETF",
        "513100": "纳指ETF",
        "513500": "标普500ETF",
        "159941": "广发金融ETF",
        "513130": "日经ETF",
        "513330": "恒生ETF",
        "518880": "黄金ETF",
        "159001": "保证金ETF",
        "511160": "短融ETF",
        "510170": "大宗商品ETF",
        "159985": "豆粕ETF",
        "159697": "有色50ETF",
    }
    result = []
    for code in codes:
        result.append({"code": code, "name": names.get(code, f"标的{code}")})
    return result


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_project(tmp_path: Path) -> Path:
    """
    Create a temporary project-like directory with data/output/logs subdirs.
    """
    (tmp_path / "data").mkdir()
    (tmp_path / "output").mkdir()
    (tmp_path / "logs").mkdir()
    return tmp_path


@pytest.fixture()
def mock_tdx_dir(tmp_path: Path) -> Path:
    """
    Create a mock TDX data directory structure:
    {tmp}/vipdoc/sh/lday/  and  {tmp}/vipdoc/sz/lday/
    """
    sh_lday = tmp_path / "vipdoc" / "sh" / "lday"
    sz_lday = tmp_path / "vipdoc" / "sz" / "lday"
    sh_lday.mkdir(parents=True)
    sz_lday.mkdir(parents=True)
    return tmp_path / "vipdoc"


@pytest.fixture()
def strong_etf_df() -> pd.DataFrame:
    """ETF with strong upward momentum."""
    return generate_momentum_etf_data("510050", 3.5, "strong", seed=42)


@pytest.fixture()
def weak_etf_df() -> pd.DataFrame:
    """ETF with weak/flat momentum."""
    return generate_momentum_etf_data("510310", 4.0, "weak", seed=43)


@pytest.fixture()
def declining_etf_df() -> pd.DataFrame:
    """ETF in decline."""
    return generate_momentum_etf_data("159352", 1.2, "declining", seed=44)


@pytest.fixture()
def rebound_etf_df() -> pd.DataFrame:
    """ETF that dropped then bounced back (for mid-term rebound test)."""
    return generate_momentum_etf_data("510880", 2.5, "rebound", seed=45)


@pytest.fixture()
def mixed_etf_pool(
    strong_etf_df: pd.DataFrame,
    weak_etf_df: pd.DataFrame,
    declining_etf_df: pd.DataFrame,
    rebound_etf_df: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """A small pool of 4 ETFs with different momentum profiles."""
    return {
        "510050": strong_etf_df,
        "510310": weak_etf_df,
        "159352": declining_etf_df,
        "510880": rebound_etf_df,
    }


@pytest.fixture()
def mock_tdx_data(
    mock_tdx_dir: Path,
    mixed_etf_pool: dict[str, pd.DataFrame],
) -> Path:
    """
    Write mock TDX .day files for the ETF pool.
    Returns the vipdoc directory Path (for patching TDX_VIPDOC_DIR).
    """
    for code, df in mixed_etf_pool.items():
        if code.startswith(("5", "6")):
            day_path = mock_tdx_dir / "sh" / "lday" / f"sh{code}.day"
        else:
            day_path = mock_tdx_dir / "sz" / "lday" / f"sz{code}.day"
        write_tdx_day_file(df, day_path)
    return mock_tdx_dir


@pytest.fixture()
def mock_meta_dir(tmp_path: Path) -> Path:
    """Create data/meta/ directory with stock_code_name.json."""
    meta_dir = tmp_path / "data" / "meta"
    meta_dir.mkdir(parents=True)
    return meta_dir
