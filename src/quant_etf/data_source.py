import pandas as pd
import json
import sys
from pathlib import Path
from loguru import logger
from datetime import datetime, timedelta
from quant_etf.conf import DATA_DIR, ETF_POOL
from quant_etf.tdx import get_tdx_path, parse_tdx_day_file

_collect_info_path = Path(__file__).parent.parent / "collect_info"
if str(_collect_info_path) not in sys.path:
    sys.path.insert(0, str(_collect_info_path))


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

    def _load_name_map_from_meta(self) -> dict[str, str]:
        """
        从 data/meta/stock_code_name.json 加载名称映射
        :return: {code: name}
        """
        meta_path = self.data_dir / "meta" / "stock_code_name.json"
        if not meta_path.exists():
            return {}
        try:
            items = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(items, list):
                return {item["code"]: item["name"] for item in items if "code" in item and "name" in item}
            return {}
        except Exception as e:
            logger.warning(f"Failed to load name map from {meta_path}: {e}")
            return {}

    def get_etf_name_map(self) -> dict:
        """
        获取 ETF 代码到名称的映射字典（从 data/meta/stock_code_name.json）
        :return: {code: name}
        """
        if self._etf_name_map is not None:
            return self._etf_name_map

        meta_map = self._load_name_map_from_meta()
        if meta_map:
            logger.info(f"Loaded ETF name map from data/meta/stock_code_name.json ({len(meta_map)} items)")
            self._etf_name_map = meta_map
            return meta_map

        logger.warning("No ETF name map found in data/meta/stock_code_name.json")
        return {}

    def get_stock_name_map(self, force_refresh: bool = False) -> dict[str, str]:
        """
        获取 A 股股票代码到名称的映射字典（从 data/meta/stock_code_name.json）
        :param force_refresh: 是否强制刷新缓存
        :return: {code: name}
        """
        if self._stock_name_map is not None and not force_refresh:
            return self._stock_name_map

        meta_map = self._load_name_map_from_meta()
        if meta_map:
            logger.info(f"Loaded stock name map from data/meta/stock_code_name.json ({len(meta_map)} items)")
            self._stock_name_map = meta_map
            return meta_map

        logger.warning("No stock name map found in data/meta/stock_code_name.json")
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

    def backfill_stock_names(self, target_file: str | Path | None = None) -> dict:
        """
        补齐 stock_code_name.json 中缺失的股票代码名称
        :param target_file: 目标 JSON 文件路径 (默认: data/meta/stock_code_name.json)
        :return: 统计信息 {"missing": int, "filled": int, "failed": list}
        """
        from missing_code_finder import find_missing_codes, get_all_missing_codes, normalize_code
        from simple_stock_api import SimpleStockAPI

        if target_file is None:
            target_file = DATA_DIR / "meta" / "stock_code_name.json"
        else:
            target_file = Path(target_file)

        logger.info(f"开始补齐股票代码名称，目标文件: {target_file}")

        missing = find_missing_codes(target_file)
        all_missing_codes = get_all_missing_codes(target_file)

        if not all_missing_codes:
            logger.info("没有缺失的代码，无需补齐")
            return {"missing": 0, "filled": 0, "failed": []}

        total_missing = sum(len(v) for v in missing.values())
        logger.info(f"缺失代码总数: {total_missing} (ETF: {len(missing['etf'])}, 短线: {len(missing['stock'])}, 中线: {len(missing['mid_term_stock'])})")

        api = SimpleStockAPI()
        results = api.batch_query(all_missing_codes, delay=0.3)

        existing_items = []
        if target_file.exists():
            try:
                existing_items = json.loads(target_file.read_text(encoding="utf-8"))
                if not isinstance(existing_items, list):
                    existing_items = []
            except Exception:
                existing_items = []

        existing_codes = {normalize_code(item["code"]) for item in existing_items if "code" in item}

        new_items = []
        failed_codes = []
        for result in results:
            code = normalize_code(result.get("code", ""))
            name = result.get("name")
            if name and code and code not in existing_codes:
                new_items.append({
                    "code": code,
                    "name": name,
                    "market": result.get("market", ""),
                })
                existing_codes.add(code)
                logger.info(f"补齐成功: {code} -> {name}")
            elif not name:
                failed_codes.append(code)
                logger.warning(f"补齐失败: {code} (查询无结果)")

        all_items = existing_items + new_items
        all_items.sort(key=lambda x: x.get("code", ""))

        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(json.dumps(all_items, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"补齐完成，已写入 {len(new_items)} 条新记录到 {target_file}")

        return {
            "missing": total_missing,
            "filled": len(new_items),
            "failed": failed_codes,
        }


if __name__ == "__main__":
    ds = ETFDataSource()
    if ETF_POOL:
        test_code = ETF_POOL[0]
        df = ds.load_data(test_code)
        print(f"Data for {test_code}:")
        print(df.tail())
