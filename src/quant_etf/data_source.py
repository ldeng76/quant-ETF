import pandas as pd
import akshare as ak
import json
import random
import time as time_module
from pathlib import Path
from loguru import logger
from datetime import datetime, time, timedelta
from quant_etf.conf import DATA_DIR, ETF_POOL

class ETFDataSource:
    def __init__(self, data_dir: Path = DATA_DIR):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._stock_name_map: dict[str, str] | None = None
        self._last_request_at: float | None = None
        self._min_request_interval_s: float = 5.1

    def _throttle(self):
        """
        频率控制：确保任意两次 AkShare 请求之间间隔 >= self._min_request_interval_s，降低被限频/封禁风险
        """
        now = time_module.monotonic()
        if self._last_request_at is None:
            self._last_request_at = now
            return

        elapsed = now - self._last_request_at
        if elapsed < self._min_request_interval_s:
            delay = (self._min_request_interval_s - elapsed) + random.random() * 0.2
            logger.info(f"Throttling AkShare requests: sleeping {delay:.2f}s")
            time_module.sleep(delay)
            now = time_module.monotonic()

        self._last_request_at = now

    def _call_with_retry(self, func, *args, retries: int = 2, base_delay_s: float = 0.8, jitter_s: float = 0.2, **kwargs):
        """
        对可能发生网络抖动的调用增加重试，降低 RemoteDisconnected/Connection aborted 等瞬时错误的影响
        :param func: 可调用对象
        :param retries: 重试次数（不含首次调用）
        :param base_delay_s: 基础等待时间（指数退避）
        :param jitter_s: 抖动时间，避免并发请求同步重试
        :return: func 的返回值
        """
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                self._throttle()
                return func(*args, **kwargs)
            except Exception as e:
                last_exc = e
                if attempt >= retries:
                    raise
                delay = base_delay_s * (2 ** attempt) + (random.random() * jitter_s if jitter_s > 0 else 0.0)
                logger.warning(f"Call failed: {getattr(func, '__name__', str(func))} ({e}); retry {attempt + 1}/{retries} in {delay:.2f}s")
                time_module.sleep(delay)
        raise last_exc if last_exc else RuntimeError("Unknown error in _call_with_retry")

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

    def fetch_from_akshare(self, code: str, start_date: str = "20000101", end_date: str = "20991231") -> pd.DataFrame:
        """
        从 AkShare 获取 ETF 历史行情 (前复权)
        """
        logger.info(f"Fetching data for {code} from AkShare...")
        # 针对东方财富接口的不稳定性，增加重试次数和等待时间
        df = self._call_with_retry(
            ak.fund_etf_hist_em,
            symbol=code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
            retries=5,         # 增加重试次数
            base_delay_s=2.5,  # 增加基础等待时间
        )

        if df is None or df.empty:
            raise RuntimeError(f"AkShare returned empty ETF data for {code}")

        rename_map = {
            "日期": "date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
            "涨跌幅": "pct_chg",
        }
        df = df.rename(columns=rename_map)

        required_cols = ["date", "open", "close", "high", "low", "volume"]
        if not all(col in df.columns for col in required_cols):
            raise RuntimeError(f"Missing columns in ETF data for {code}. Available: {list(df.columns)}")

        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        df = df.sort_index()
        return df

    def get_stock_cache_path(self, code: str) -> Path:
        """
        获取股票缓存文件路径 (CSV格式)
        """
        stock_dir = self.data_dir / "stocks"
        stock_dir.mkdir(parents=True, exist_ok=True)
        return stock_dir / f"{code}.csv"

    def fetch_stock_from_akshare(self, code: str, start_date: str = "20000101", end_date: str = "20991231") -> pd.DataFrame:
        """
        从 AkShare 获取 A 股历史行情 (前复权)
        """
        logger.info(f"Fetching stock data for {code} from AkShare...")
        df = self._call_with_retry(
            ak.stock_zh_a_hist,
            symbol=code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
        )

        if df is None or df.empty:
            raise RuntimeError(f"AkShare returned empty stock data for {code}")

        rename_map = {
            "日期": "date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
            "涨跌幅": "pct_chg",
        }
        df = df.rename(columns=rename_map)

        required_cols = ["date", "open", "close", "high", "low", "volume"]
        if not all(col in df.columns for col in required_cols):
            raise RuntimeError(f"Missing columns in stock data for {code}. Available: {list(df.columns)}")

        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        df = df.sort_index()
        return df

    def update_stock_data(self, code: str) -> bool:
        """
        更新指定股票的数据并缓存
        """
        df_new = self.fetch_stock_from_akshare(code)
        if df_new.empty:
            raise RuntimeError(f"Empty stock data for {code}")

        cache_path = self.get_stock_cache_path(code)
        df_new.to_csv(cache_path)
        logger.info(f"Saved stock data for {code} to {cache_path}")
        return True

    def load_stock_data(self, code: str, force_update: bool = False, check_freshness: bool = True) -> pd.DataFrame:
        """
        加载股票数据
        :param code: 股票代码
        :param force_update: 是否强制从网络更新
        :param check_freshness: 是否检查数据新鲜度 (防止递归死循环)
        :return: DataFrame
        """
        cache_path = self.get_stock_cache_path(code)

        if not force_update and cache_path.exists():
            try:
                df = pd.read_csv(cache_path, index_col="date", parse_dates=True)
                if not df.empty:
                    if not check_freshness or self.check_is_fresh(df):
                        return df
                    logger.info(f"Stock data for {code} is outdated (Last: {df.index[-1].date()}). Updating...")
            except Exception as e:
                logger.error(f"Error reading stock cache for {code}: {e}")

        if self.update_stock_data(code):
            return self.load_stock_data(code, force_update=False, check_freshness=False)

        raise RuntimeError(f"Failed to load stock data for {code}")

    def update_data(self, code: str) -> bool:
        """
        更新指定 ETF 的数据并缓存
        """
        df_new = self.fetch_from_akshare(code)
        if df_new.empty:
            raise RuntimeError(f"Empty ETF data for {code}")
        
        cache_path = self.get_cache_path(code)
        
        # 保存为 CSV
        df_new.to_csv(cache_path)
        logger.info(f"Saved data for {code} to {cache_path}")
        return True

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
        
        # 1. 如果最后日期就是今天，肯定新鲜
        if last_date == today:
            return True
            
        # 2. 如果最后日期比昨天还早很多(比如超过10天)，肯定不新鲜
        if (today - last_date).days > 10: 
             return False
             
        # 3. 处理周末情况
        if today.weekday() == 5: # Saturday
             # 期望是周五 (昨天)
             return last_date >= (today - timedelta(days=1))
        if today.weekday() == 6: # Sunday
             # 期望是周五 (前天)
             return last_date >= (today - timedelta(days=2))
             
        # 4. 处理工作日
        if now.time() > time(16, 0):
             # 盘后，期望是今天的数据
             # 但如果今天是节假日（非周末），我们可能无法判断。
             # 这里严格一点，如果是盘后，就尝试更新。
             # 如果更新后还是旧的，load_data 的逻辑会防止死循环。
             return last_date == today
        else:
             # 盘前/盘中，期望是昨天（如果是周一则是上周五）
             if today.weekday() == 0: # Monday
                 return last_date >= (today - timedelta(days=3))
             else:
                 return last_date >= (today - timedelta(days=1))

    def load_data(self, code: str, force_update: bool = False, check_freshness: bool = True) -> pd.DataFrame:
        """
        加载 ETF 数据
        :param code: ETF 代码
        :param force_update: 是否强制从网络更新
        :param check_freshness: 是否检查数据新鲜度 (防止递归死循环)
        :return: DataFrame
        """
        cache_path = self.get_cache_path(code)
        
        if not force_update and cache_path.exists():
            try:
                # 读取缓存
                df = pd.read_csv(cache_path, index_col="date", parse_dates=True)
                if not df.empty:
                    # 检查新鲜度，如果不新鲜则自动更新
                    # 如果 check_freshness 为 False，说明刚更新过，直接信任
                    if not check_freshness or self.check_is_fresh(df):
                        return df
                    else:
                        logger.info(f"Data for {code} is outdated (Last: {df.index[-1].date()}). Updating...")
            except Exception as e:
                logger.error(f"Error reading cache for {code}: {e}")
        
        # 如果没有缓存、强制更新或自动检查发现过期，则下载
        if self.update_data(code):
             # 更新后重新加载，避免递归死循环
             # 设置 check_freshness=False，表示信任刚更新的数据
             return self.load_data(code, force_update=False, check_freshness=False)

        raise RuntimeError(f"Failed to load ETF data for {code}")

    def get_etf_name_map(self) -> dict:
        """
        获取 ETF 代码到名称的映射字典
        :return: {code: name}
        """
        logger.info("Fetching ETF name map from AkShare...")
        df = self._call_with_retry(ak.fund_etf_spot_em)
        if df is None or df.empty:
            raise RuntimeError("AkShare returned empty ETF name map")

        df["代码"] = df["代码"].astype(str)
        name_map = dict(zip(df["代码"], df["名称"]))
        self._save_cached_name_map("etf", {str(k): str(v) for k, v in name_map.items()})
        return name_map

    def get_stock_name_map(self, force_refresh: bool = False) -> dict[str, str]:
        """
        获取 A 股股票代码到名称的映射字典
        :param force_refresh: 是否强制刷新缓存
        :return: {code: name}
        """
        if self._stock_name_map is not None and not force_refresh:
            return self._stock_name_map

        logger.info("Fetching stock name map from AkShare...")
        df = self._call_with_retry(ak.stock_info_a_code_name)
        if df is None or df.empty:
            raise RuntimeError("AkShare returned empty stock name map")

        code_col_candidates = ["code", "证券代码", "股票代码"]
        name_col_candidates = ["name", "证券简称", "股票简称", "名称"]

        code_col = next((c for c in code_col_candidates if c in df.columns), None)
        name_col = next((c for c in name_col_candidates if c in df.columns), None)
        if code_col is None or name_col is None:
            raise RuntimeError(f"Unexpected columns in stock name map: {list(df.columns)}")

        df[code_col] = df[code_col].astype(str).str.zfill(6)
        self._stock_name_map = {str(k): str(v) for k, v in dict(zip(df[code_col], df[name_col])).items()}
        self._save_cached_name_map("stock", self._stock_name_map)
        return self._stock_name_map

    def update_all(self):
        """
        更新 ETF 池中所有 ETF 的数据
        """
        logger.info(f"Starting update for {len(ETF_POOL)} ETFs...")
        success_count = 0
        for code in ETF_POOL:
            if self.update_data(code):
                success_count += 1
        logger.info(f"Update completed. Success: {success_count}/{len(ETF_POOL)}")

if __name__ == "__main__":
    # 简单测试
    ds = ETFDataSource()
    # 测试获取第一个 ETF
    if ETF_POOL:
        test_code = ETF_POOL[0]
        df = ds.load_data(test_code)
        print(f"Data for {test_code}:")
        print(df.tail())
