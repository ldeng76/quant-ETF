import pandas as pd
import json
import sys
from pathlib import Path
from loguru import logger
from datetime import datetime, timedelta
from quant_etf.conf import DATA_DIR, ETF_POOL
from quant_etf.tdx import (
    get_tdx_path, parse_tdx_day_file, get_security_bars, get_xdxr_info,
    adjust_price_qfq, get_realtime_quote_single,
)
from quant_etf.trading_day import is_intraday

_collect_info_path = Path(__file__).parent.parent / "collect_info"
if str(_collect_info_path) not in sys.path:
    sys.path.insert(0, str(_collect_info_path))


def build_intraday_bar(code: str, df_history: pd.DataFrame) -> pd.DataFrame | None:
    """
    用实时行情数据构造一条"今日"日K线
    :param code: 证券代码
    :param df_history: 历史日线数据（用于获取昨天的收盘价计算 pct_chg）
    :return: 单行 DataFrame，格式与 load_data 返回值一致；失败返回 None
    """
    quote = get_realtime_quote_single(code)
    if quote is None:
        logger.warning(f"Failed to get realtime quote for {code}, skipping intraday bar")
        return None

    today = datetime.now().date()
    today_dt = datetime.combine(today, datetime.min.time())

    # 提取实时行情字段 (pytdx 使用 price 而非 close)
    close = quote.get("price") or quote.get("close", 0)
    if not close or close <= 0:
        logger.warning(f"Invalid close price from realtime quote for {code}: {close}")
        return None

    open_price = quote.get("open", close)
    high = quote.get("high", close)
    low = quote.get("low", close)
    volume = quote.get("vol", quote.get("volume", 0))
    amount = quote.get("amount", 0)

    # 计算 pct_chg（基于历史数据的最后一个收盘价）
    pct_chg = 0.0
    if not df_history.empty:
        prev_close = df_history.iloc[-1]["close"]
        if prev_close and prev_close > 0:
            pct_chg = (close - prev_close) / prev_close * 100

    # 构造单行 DataFrame
    row = pd.DataFrame(
        {
            "open": [open_price],
            "high": [high],
            "low": [low],
            "close": [close],
            "amount": [amount],
            "volume": [volume],
            "pct_chg": [pct_chg],
        },
        index=[today_dt],
    )
    row.index.name = "date"

    logger.info(f"Built intraday bar for {code}: close={close}, pct_chg={pct_chg:.2f}%")
    return row


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

    def load_data(self, code: str, force_update: bool = False, check_freshness: bool = True, allow_online: bool = True, adjust_qfq: bool = True, intraday: bool = False) -> pd.DataFrame:
        """
        加载 ETF 数据
        优先级：本地 TDX 文件 > 缓存 > 在线获取
        :param code: ETF 代码
        :param force_update: 忽略，保留参数兼容
        :param check_freshness: 是否检查数据新鲜度
        :param allow_online: 是否允许在线获取数据（默认 True）
        :param adjust_qfq: 是否进行前复权处理（默认 True）
        :param intraday: 是否在交易时段内构造今日临时日K线（默认 False）
        :return: DataFrame
        """
        # 1. 尝试从本地 TDX 文件加载
        tdx_path = get_tdx_path(code)
        if tdx_path and tdx_path.exists():
            try:
                logger.info(f"Loading data for {code} from local TDX file: {tdx_path}")
                df = parse_tdx_day_file(tdx_path)
                if not df.empty:
                    # 应用前复权处理
                    if adjust_qfq:
                        df = self._apply_qfq(code, df)
                    return self._append_intraday_if_needed(code, df, intraday, adjust_qfq)
            except Exception as e:
                logger.error(f"Failed to load TDX data for {code}: {e}")

        # 2. 尝试从缓存加载
        cache_path = self.get_cache_path(code)
        if cache_path.exists():
            try:
                df = pd.read_csv(cache_path, index_col="date", parse_dates=True)
                if not df.empty:
                    if not check_freshness or self.check_is_fresh(df):
                        logger.info(f"Loaded ETF data for {code} from cache (last: {df.index[-1].date()})")
                        # 应用前复权处理
                        if adjust_qfq:
                            df = self._apply_qfq(code, df)
                        return self._append_intraday_if_needed(code, df, intraday, adjust_qfq)
            except Exception as e:
                logger.error(f"Error reading cache for {code}: {e}")

        # 3. 尝试在线获取
        if allow_online:
            try:
                logger.info(f"Fetching ETF data for {code} from online TDX server...")
                df = get_security_bars(code)
                if not df.empty:
                    # 应用前复权处理
                    if adjust_qfq:
                        df = self._apply_qfq(code, df)
                    # 保存到缓存
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    df.to_csv(cache_path)
                    logger.info(f"Saved online data to cache: {cache_path} (last: {df.index[-1].date()})")
                    return self._append_intraday_if_needed(code, df, intraday, adjust_qfq)
            except Exception as e:
                logger.error(f"Failed to fetch online data for {code}: {e}")

        raise RuntimeError(f"Failed to load ETF data for {code}. No TDX data found for {code}")

    def _append_intraday_if_needed(self, code: str, df: pd.DataFrame, intraday: bool, adjust_qfq: bool) -> pd.DataFrame:
        """
        如果处于 intraday 模式且当前是交易时段，构造今日临时日K线并拼接
        注意: 实时行情返回的是未复权价格，所以 intraday bar 需要基于未复权数据构建
        """
        if not intraday or not is_intraday():
            return df

        # 构造 intraday bar
        if adjust_qfq:
            # 重新加载未复权数据用于 intraday bar 计算
            df_unadjusted = self._load_unadjusted_data(code)
            if df_unadjusted is not None and not df_unadjusted.empty:
                intraday_bar = build_intraday_bar(code, df_unadjusted)
            else:
                logger.warning(f"Failed to load unadjusted data for {code}, skipping intraday bar")
                return df
        else:
            intraday_bar = build_intraday_bar(code, df)

        if intraday_bar is not None:
            # 对 intraday bar 应用相同的前复权因子
            if adjust_qfq and not df.empty:
                last_adjusted_close = df.iloc[-1]["close"]
                # 计算复权因子 (复权后价格 / 未复权价格)
                if intraday_bar.iloc[0]["close"] > 0:
                    # 将 intraday bar 的价格调整到与历史数据同一量级
                    adjustment_ratio = last_adjusted_close / intraday_bar.iloc[0]["close"]
                    for col in ["open", "high", "low", "close"]:
                        intraday_bar[col] *= adjustment_ratio
                    # 重新计算 pct_chg
                    if len(df) > 0:
                        prev_close = df.iloc[-1]["close"]
                        intraday_bar["pct_chg"] = (intraday_bar.iloc[0]["close"] - prev_close) / prev_close * 100

            # 修复：在拼接前，先删除 df 中已存在的今日数据（避免重复追加）
            today = datetime.now().date()
            today_mask = df.index.date == today
            if today_mask.any():
                df = df[~today_mask]
                logger.info(f"Removed existing intraday bar for {code} before appending new one")

            df = pd.concat([df, intraday_bar])
            df.sort_index(inplace=True)
            logger.info(f"Appended intraday bar for {code}, data now spans to {df.index[-1].date()}")

            # 将包含 intraday bar 的数据保存到 CSV 缓存，确保后续读取能获取到最新数据
            # adjust_qfq=False 表示是股票数据
            self._save_with_intraday_to_cache(code, df, is_stock=not adjust_qfq)

        return df

    def _save_with_intraday_to_cache(self, code: str, df: pd.DataFrame, is_stock: bool = False) -> None:
        """
        将包含 intraday bar 的数据保存到 CSV 缓存
        :param code: ETF代码或股票代码
        :param df: 包含 intraday bar 的 DataFrame
        :param is_stock: 是否为股票数据
        """
        try:
            if is_stock:
                cache_path = self.get_stock_cache_path(code)
            else:
                cache_path = self.get_cache_path(code)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(cache_path)
            logger.info(f"Saved intraday bar to cache: {cache_path} (date: {df.index[-1].date()})")
        except Exception as e:
            logger.warning(f"Failed to save intraday bar to cache for {code}: {e}")

    def _load_unadjusted_data(self, code: str) -> pd.DataFrame | None:
        """
        加载未复权的原始数据(用于 intraday bar 计算)
        """
        # 尝试从本地 TDX 文件加载
        tdx_path = get_tdx_path(code)
        if tdx_path and tdx_path.exists():
            try:
                return parse_tdx_day_file(tdx_path)
            except Exception as e:
                logger.debug(f"Failed to load unadjusted TDX data for {code}: {e}")

        # 尝试从缓存加载
        cache_path = self.get_cache_path(code)
        if cache_path.exists():
            try:
                df = pd.read_csv(cache_path, index_col="date", parse_dates=True)
                if not df.empty:
                    return df
            except Exception as e:
                logger.debug(f"Failed to load unadjusted cache data for {code}: {e}")

        return None

    def _apply_qfq(self, code: str, df: pd.DataFrame) -> pd.DataFrame:
        """
        对 ETF 数据应用前复权处理
        :param code: ETF 代码
        :param df: 原始日线数据
        :return: 前复权后的数据
        """
        try:
            # 获取除权除息信息
            xdxr_df = get_xdxr_info(code)
            if not xdxr_df.empty:
                logger.info(f"Applying forward adjustment (前复权) for {code}")
                df = adjust_price_qfq(df, xdxr_df)
            else:
                logger.info(f"No xdxr info available for {code}, skipping qfq adjustment")
        except Exception as e:
            logger.warning(f"Failed to apply qfq adjustment for {code}: {e}, using original data")
        
        return df

    def load_stock_data(self, code: str, force_update: bool = False, check_freshness: bool = True, allow_online: bool = True, intraday: bool = False) -> pd.DataFrame:
        """
        加载股票数据
        优先级：本地 TDX 文件 > 缓存 > 在线获取
        :param code: 股票代码
        :param force_update: 忽略，保留参数兼容
        :param check_freshness: 是否检查数据新鲜度
        :param allow_online: 是否允许在线获取数据（默认 True）
        :param intraday: 是否在交易时段内构造今日临时日K线（默认 False）
        :return: DataFrame
        """
        # 1. 尝试从本地 TDX 文件加载
        tdx_path = get_tdx_path(code)
        if tdx_path and tdx_path.exists():
            try:
                logger.info(f"Loading stock data for {code} from local TDX file: {tdx_path}")
                df = parse_tdx_day_file(tdx_path)
                if not df.empty:
                    return self._append_intraday_if_needed(code, df, intraday, adjust_qfq=False)
            except Exception as e:
                logger.error(f"Failed to load TDX stock data for {code}: {e}")

        # 2. 尝试从缓存加载
        cache_path = self.get_stock_cache_path(code)
        if cache_path.exists():
            try:
                df = pd.read_csv(cache_path, index_col="date", parse_dates=True)
                if not df.empty:
                    if not check_freshness or self.check_is_fresh(df):
                        logger.info(f"Loaded stock data for {code} from cache (last: {df.index[-1].date()})")
                        return self._append_intraday_if_needed(code, df, intraday, adjust_qfq=False)
            except Exception as e:
                logger.error(f"Error reading stock cache for {code}: {e}")

        # 3. 尝试在线获取
        if allow_online:
            try:
                logger.info(f"Fetching stock data for {code} from online TDX server...")
                df = get_security_bars(code)
                if not df.empty:
                    # 保存到缓存
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    df.to_csv(cache_path)
                    logger.info(f"Saved online data to cache: {cache_path} (last: {df.index[-1].date()})")
                    return self._append_intraday_if_needed(code, df, intraday, adjust_qfq=False)
            except Exception as e:
                logger.error(f"Failed to fetch online stock data for {code}: {e}")

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

    @staticmethod
    def _market_for_code(code: str) -> str:
        """
        统一的市场判定逻辑：5/6 -> sh, 0/1/3 -> sz。
        与 quant_etf.tdx.code_to_market 对齐，避免再出现 1/5 误判为 sz 的历史问题。
        """
        code = str(code).strip().zfill(6)
        if code.startswith(("5", "6")):
            return "sh"
        if code.startswith(("0", "1", "3")):
            return "sz"
        return "sz"

    def refresh_stock_names(
        self,
        target_file: str | Path | None = None,
        dry_run: bool = False,
    ) -> dict:
        """
        强制全量校准 stock_code_name.json：
        - 取 ETF_POOL ∪ STOCK_POOL ∪ MID_TERM_STOCK_POOL 全集
        - 用 SimpleStockAPI（新浪/腾讯/网易/东财级联）逐一查询权威名称
        - 与现 JSON 比对，覆盖错误条目；market 字段统一用 _market_for_code 判定
        - 查询失败的代码：保留 JSON 中已有条目，返回 failed 列表

        :param target_file: 目标 JSON 文件路径 (默认: data/meta/stock_code_name.json)
        :param dry_run: 仅生成报告，不写文件
        :return: {"new": [...], "updated": [...], "unchanged": [...], "failed": [...]}
                 其中 updated 元素为 {"code","old_name","new_name","old_market","new_market"}
        """
        from missing_code_finder import normalize_code
        from simple_stock_api import SimpleStockAPI
        from quant_etf.conf import ETF_POOL, STOCK_POOL, MID_TERM_STOCK_POOL

        if target_file is None:
            target_file = DATA_DIR / "meta" / "stock_code_name.json"
        else:
            target_file = Path(target_file)

        all_codes = sorted({normalize_code(c) for c in (list(ETF_POOL) + list(STOCK_POOL) + list(MID_TERM_STOCK_POOL))})
        logger.info(f"开始全量校准股票代码名称，目标文件: {target_file}，共 {len(all_codes)} 个代码 (dry_run={dry_run})")

        # 加载现有 JSON
        existing_items: list[dict] = []
        if target_file.exists():
            try:
                loaded = json.loads(target_file.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    existing_items = loaded
            except Exception as e:
                logger.warning(f"现有 JSON 解析失败，将视为空: {e}")
                existing_items = []
        existing_map: dict[str, dict] = {}
        for it in existing_items:
            code = it.get("code")
            if code:
                existing_map[normalize_code(code)] = it

        # 在线查询
        api = SimpleStockAPI()
        results = api.batch_query(all_codes, delay=0.3)

        new_list: list[dict] = []
        updated_list: list[dict] = []
        unchanged_list: list[str] = []
        failed_list: list[str] = []

        # 以 code 为 key 汇总最终条目
        final_map: dict[str, dict] = dict(existing_map)  # 先继承旧值

        for r in results:
            code = normalize_code(r.get("code", ""))
            if not code:
                continue
            new_name = r.get("name")
            new_market = self._market_for_code(code)

            if not new_name:
                failed_list.append(code)
                logger.warning(f"查询失败，保留旧条目: {code}")
                continue

            old = existing_map.get(code)
            new_item = {"code": code, "name": new_name, "market": new_market}

            if old is None:
                new_list.append(code)
                final_map[code] = new_item
                logger.info(f"新增: {code} -> {new_name} ({new_market})")
            else:
                old_name = old.get("name", "")
                old_market = old.get("market", "")
                if old_name != new_name or old_market != new_market:
                    updated_list.append({
                        "code": code,
                        "old_name": old_name,
                        "new_name": new_name,
                        "old_market": old_market,
                        "new_market": new_market,
                    })
                    final_map[code] = new_item
                    logger.warning(
                        f"覆盖: {code}  name: '{old_name}' -> '{new_name}'  market: '{old_market}' -> '{new_market}'"
                    )
                else:
                    unchanged_list.append(code)

        report = {
            "new": new_list,
            "updated": updated_list,
            "unchanged": unchanged_list,
            "failed": failed_list,
        }
        logger.info(
            f"校准报告: new={len(new_list)} updated={len(updated_list)} "
            f"unchanged={len(unchanged_list)} failed={len(failed_list)}"
        )

        if dry_run:
            logger.info("dry_run=True，未写入文件")
            return report

        # 原子写入
        all_items = sorted(final_map.values(), key=lambda x: x.get("code", ""))
        target_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target_file.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(all_items, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(target_file)
        logger.info(f"已写入 {len(all_items)} 条记录到 {target_file}")

        return report

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
