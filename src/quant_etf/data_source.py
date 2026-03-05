import pandas as pd
import json
from pathlib import Path
from loguru import logger
from datetime import datetime, timedelta
from quant_etf.conf import DATA_DIR, ETF_POOL
from quant_etf.tdx import get_tdx_path, parse_tdx_day_file


class ETFDataSource:
    def __init__(self, data_dir: Path = DATA_DIR):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._stock_name_map: dict[str, str] | None = None
        self._etf_name_map: dict[str, str] | None = None

    def _get_meta_dir(self) -> Path:
        """
        获取元数据缓存目录（用于 name_map 等小体量数据）
        """
        meta_dir = self.data_dir / "meta"
        meta_dir.mkdir(parents=True, exist_ok=True)
        return meta_dir

    def _get_name_map_cache_path(self, map_type: str) -> Path:
        """
        获取 name_map 的本地缓存文件路径
        :param map_type: "etf" 或 "stock"
        """
        filename = f"{map_type}_name_map.json"
        return self._get_meta_dir() / filename

    def _load_cached_name_map(self, map_type: str) -> dict[str, str]:
        """
        从本地缓存读取 name_map（若不存在或损坏则返回空字典）
        """
        path = self._get_name_map_cache_path(map_type)
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            data = payload.get("data", {})
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
            return {}
        except Exception as e:
            logger.warning(f"Failed to load cached {map_type} name map from {path}: {e}")
            return {}

    def _save_cached_name_map(self, map_type: str, name_map: dict[str, str]):
        """
        将 name_map 写入本地缓存（原子写入，避免中途写坏）
        """
        path = self._get_name_map_cache_path(map_type)
        tmp_path = path.with_suffix(".json.tmp")
        payload = {
            "updated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            "data": name_map,
        }
        try:
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp_path.replace(path)
            logger.info(f"Saved cached {map_type} name map: {path}")
        except Exception as e:
            logger.warning(f"Failed to save cached {map_type} name map to {path}: {e}")

    def get_cache_path(self, code: str) -> Path:
        """
        获取缓存文件路径 (CSV格式)
        """
        etf_dir = self.data_dir / "etf"
        etf_dir.mkdir(parents=True, exist_ok=True)

        new_path = etf_dir / f"{code}.csv"
        old_path = self.data_dir / f"{code}.csv"

        if old_path.exists() and not new_path.exists():
            try:
                old_path.replace(new_path)
                logger.info(f"Migrated ETF cache file from {old_path} to {new_path}")
            except Exception as e:
                logger.warning(f"Failed to migrate ETF cache file for {code}: {e}")

        return new_path

    def get_stock_cache_path(self, code: str) -> Path:
        """
        获取股票缓存文件路径 (CSV格式)
        """
        stock_dir = self.data_dir / "stocks"
        stock_dir.mkdir(parents=True, exist_ok=True)
        return stock_dir / f"{code}.csv"

    def check_is_fresh(self, df: pd.DataFrame) -> bool:
        """
        检查数据是否足够新鲜
        判断逻辑：
        1. 如果最后日期是今天，则是新鲜的。
        2. 周末：如果是周六/周日，只要数据是本周五的，就算新鲜。
        3. 周一早上：如果是周一且未收盘，数据是上周五的就算新鲜。
        4. 工作日盘后 (16:00后)：最后日期必须是今天。
        5. 工作日盘前/盘中：最后日期至少要是昨天 (或上个交易日)。
        """
        if df.empty:
            return False

        last_date = df.index[-1].date()
        now = datetime.now()
        today = now.date()

        if last_date == today:
            return True

        if (today - last_date).days > 10:
            return False

        weekday = today.weekday()
        if weekday == 5:
            return last_date >= (today - timedelta(days=1))
        if weekday == 6:
            return last_date >= (today - timedelta(days=2))

        from datetime import time
        if now.time() > time(16, 0):
            return last_date == today
        else:
            if weekday == 0:
                return last_date >= (today - timedelta(days=3))
            else:
                return last_date >= (today - timedelta(days=1))

    def load_data(self, code: str, force_update: bool = False, check_freshness: bool = True) -> pd.DataFrame:
        """
        加载 ETF 数据（从通达信本地文件）
        :param code: ETF 代码
        :param force_update: 忽略，保留参数兼容
        :param check_freshness: 是否检查数据新鲜度
        :return: DataFrame
        """
        tdx_path = get_tdx_path(code)
        if tdx_path and tdx_path.exists():
            try:
                logger.info(f"Loading data for {code} from local TDX file: {tdx_path}")
                df = parse_tdx_day_file(tdx_path)
                if not df.empty:
                    return df
            except Exception as e:
                logger.error(f"Failed to load TDX data for {code}: {e}")

        cache_path = self.get_cache_path(code)
        if cache_path.exists():
            try:
                df = pd.read_csv(cache_path, index_col="date", parse_dates=True)
                if not df.empty:
                    if not check_freshness or self.check_is_fresh(df):
                        logger.info(f"Loaded ETF data for {code} from cache (last: {df.index[-1].date()})")
                        return df
            except Exception as e:
                logger.error(f"Error reading cache for {code}: {e}")

        raise RuntimeError(f"Failed to load ETF data for {code}. No TDX data found for {code}")

    def load_stock_data(self, code: str, force_update: bool = False, check_freshness: bool = True) -> pd.DataFrame:
        """
        加载股票数据（从通达信本地文件）
        :param code: 股票代码
        :param force_update: 忽略，保留参数兼容
        :param check_freshness: 是否检查数据新鲜度
        :return: DataFrame
        """
        tdx_path = get_tdx_path(code)
        if tdx_path and tdx_path.exists():
            try:
                logger.info(f"Loading stock data for {code} from local TDX file: {tdx_path}")
                df = parse_tdx_day_file(tdx_path)
                if not df.empty:
                    return df
            except Exception as e:
                logger.error(f"Failed to load TDX stock data for {code}: {e}")

        cache_path = self.get_stock_cache_path(code)
        if cache_path.exists():
            try:
                df = pd.read_csv(cache_path, index_col="date", parse_dates=True)
                if not df.empty:
                    if not check_freshness or self.check_is_fresh(df):
                        logger.info(f"Loaded stock data for {code} from cache (last: {df.index[-1].date()})")
                        return df
            except Exception as e:
                logger.error(f"Error reading stock cache for {code}: {e}")

        raise RuntimeError(f"Failed to load stock data for {code}. No TDX data found for {code}")

    def get_etf_name_map(self) -> dict:
        """
        获取 ETF 代码到名称的映射字典（从本地缓存）
        :return: {code: name}
        """
        if self._etf_name_map is not None:
            return self._etf_name_map

        cached_map = self._load_cached_name_map("etf")
        if cached_map:
            logger.info(f"Loaded ETF name map from local cache ({len(cached_map)} items)")
            self._etf_name_map = cached_map
            return cached_map

        logger.warning("No ETF name map cache found. Please populate data/meta/etf_name_map.json manually.")
        return {}

    def get_stock_name_map(self, force_refresh: bool = False) -> dict[str, str]:
        """
        获取 A 股股票代码到名称的映射字典（从本地缓存）
        :param force_refresh: 是否强制刷新缓存
        :return: {code: name}
        """
        if self._stock_name_map is not None and not force_refresh:
            return self._stock_name_map

        cached_map = self._load_cached_name_map("stock")
        if cached_map:
            logger.info(f"Loaded stock name map from local cache ({len(cached_map)} items)")
            self._stock_name_map = cached_map
            return cached_map

        logger.warning("No stock name map cache found. Please populate data/meta/stock_name_map.json manually.")
        return {}

    def update_all(self):
        """
        检查 ETF 池中所有 ETF 的数据是否存在（只检查通达信数据）
        """
        logger.info(f"Checking TDX data for {len(ETF_POOL)} ETFs...")
        found_count = 0
        missing_count = 0
        for code in ETF_POOL:
            tdx_path = get_tdx_path(code)
            if tdx_path and tdx_path.exists():
                found_count += 1
            else:
                missing_count += 1
                logger.warning(f"No TDX data found for {code}")
        logger.info(f"Check completed. Found: {found_count}, Missing: {missing_count}")


if __name__ == "__main__":
    ds = ETFDataSource()
    if ETF_POOL:
        test_code = ETF_POOL[0]
        df = ds.load_data(test_code)
        print(f"Data for {test_code}:")
        print(df.tail())
