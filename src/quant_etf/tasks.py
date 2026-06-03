"""
任务模块：定义各类选股任务的抽象基类和具体实现
"""
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import asdict

import pandas as pd
from loguru import logger

from quant_etf.bar_interval import DEFAULT_INTERVAL
from quant_etf.conf import (
    ETF_POOL,
    TOP_N,
    PROJECT_ROOT,
)
from quant_etf.pool_loader import get_stock_pool
from quant_etf.data_source import ETFDataSource
from quant_etf.trading_day import is_intraday
from quant_etf.strategy import StrategyEngine, ETFScore, StockScore, ReboundStockScore
from quant_etf.risk import RiskManager, RiskLevel
from quant_etf.export import (
    export_to_tdx_block,
    generate_tdx_formula_file,
    export_to_tdx_custom_block_auto,
)


class BaseTask(ABC):
    """
    任务抽象基类，定义任务的标准执行流程
    """

    name: str = "base"
    description: str = "Base task"

    def __init__(
        self,
        target_date: str | None = None,
        intraday: bool = False,
        bar_interval: str = DEFAULT_INTERVAL,
    ):
        """
        初始化任务
        :param target_date: 目标日期，格式 YYYY-MM-DD。默认为 None，表示使用当前日期。
        :param intraday: 是否使用盘中实时行情构造今日临时日K线
        :param bar_interval: K线周期 ("1d"/"5m"/"15m"/"30m"/"60m")
        """
        self.target_date = target_date
        self._intraday = intraday
        self._bar_interval = bar_interval
        self.ds: Optional[ETFDataSource] = None
        self.strategy: Optional[StrategyEngine] = None
        self.risk_manager: Optional[RiskManager] = None
        # Pool override set by scheduler_engine (per-user merged public+private pools)
        # Dict mapping pool_type → list of codes, e.g. {"etf": [...], "stock": [...], "mid_term": [...]}
        self._override_pool: Optional[Dict[str, List[str]]] = None

    @property
    def intraday(self) -> bool:
        return self._intraday

    def initialize(self) -> None:
        """
        初始化数据源和策略引擎
        """
        self.ds = ETFDataSource()
        self.strategy = StrategyEngine(bar_interval=self._bar_interval)
        self.risk_manager = RiskManager(bar_interval=self._bar_interval)

        mode = "INTRADAY" if (self._intraday and is_intraday()) else "standard"
        logger.info(f"[{self.name}] Data mode: {mode}")

    def save_results_to_csv(self, data: List[Dict[str, Any]], filename_prefix: str) -> None:
        """
        将结果保存为 CSV 文件
        """
        if not data:
            logger.warning("No data to save.")
            return

        date_str = self.target_date if self.target_date else datetime.now().strftime("%Y-%m-%d")
        output_dir = PROJECT_ROOT / "data" / "results" / date_str
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # 日线保持原路径，分钟周期加后缀
        if self._bar_interval == "1d":
            output_path = output_dir / f"{filename_prefix}.csv"
        else:
            output_path = output_dir / f"{filename_prefix}_{self._bar_interval}.csv"
        
        df = pd.DataFrame(data)
        
        # 确保 date 列存在
        if "date" not in df.columns:
            df.insert(0, "date", date_str)
        
        # 新增 interval 列标识周期
        df.insert(1, "interval", self._bar_interval)
        
        # 调整列顺序：name列移到第3个位置（在code之后）
        if "name" in df.columns:
            cols = df.columns.tolist()
            cols.remove("name")
            # 找到code列的位置，在其后插入name
            if "code" in cols:
                code_idx = cols.index("code")
                cols.insert(code_idx + 1, "name")
            else:
                cols.insert(2, "name")  # 默认放在第3个位置
            df = df[cols]
        
        # 涨幅和权重字段转换为百分比格式（保留2位小数的百分比字符串）
        # ETF策略字段：p60, p20, p10, p5, target_weight
        # Short策略字段：score, p60, p20, p10, p5
        # Mid策略字段：score, drawdown_from_120d_high, bounce_from_20d_low, p20, p10, p5
        pct_cols = ["p60", "p20", "p10", "p5", "target_weight", "score",
                    "drawdown_from_120d_high", "bounce_from_20d_low"]
        for col in pct_cols:
            if col in df.columns:
                df[col] = df[col].apply(lambda x: f"{x*100:.2f}%")
            
        df.to_csv(output_path, index=False, encoding="utf-8")
        logger.info(f"Results saved to CSV: {output_path}")

    @abstractmethod
    def get_pool(self) -> List[str]:
        """
        获取任务使用的证券池
        """
        pass

    @abstractmethod
    def load_data(self, pool: List[str]) -> Dict[str, pd.DataFrame]:
        """
        加载证券数据
        """
        pass

    @abstractmethod
    def run_strategy(self, data: Dict[str, pd.DataFrame]) -> List[Any]:
        """
        执行策略打分
        """
        pass

    @abstractmethod
    def format_result(self, result: Any, name_map: Dict[str, str]) -> str:
        """
        格式化结果输出
        """
        pass

    @abstractmethod
    def export_results(self, results: List[Any]) -> None:
        """
        导出结果到文件
        """
        pass

    def run(self) -> None:
        """
        任务主流程（带超时保护，防止网络操作卡死导致进程挂起）
        """
        logger.info(f"Starting task: {self.name}")
        logger.info(f"Description: {self.description}")

        self.initialize()

        pool = self.get_pool()
        logger.info(f"Loading data for {len(pool)} securities...")

        # 使用线程池 + 超时保护数据加载，防止 pytdx 网络操作无限阻塞
        LOAD_TIMEOUT = 300  # 5分钟超时
        with ThreadPoolExecutor(max_workers=1) as _load_executor:
            future = _load_executor.submit(self.load_data, pool)
            try:
                data = future.result(timeout=LOAD_TIMEOUT)
            except TimeoutError:
                logger.error(
                    f"Data loading timed out after {LOAD_TIMEOUT}s for {len(pool)} securities. "
                    f"Possible cause: TDX server unresponsive. Skipping this run."
                )
                future.cancel()
                return
            except Exception as e:
                logger.error(f"Data loading failed with exception: {e}")
                return
        if not data:
            logger.error("No data loaded. Exiting.")
            return

        # 根据 target_date 对数据进行切片过滤
        if self.target_date:
            target_dt = datetime.strptime(self.target_date, "%Y-%m-%d")
            data = {
                code: df[df.index <= target_dt] for code, df in data.items() if not df.empty
            }
            logger.info(f"Filtered data to before {self.target_date}")

        logger.info(f"Running strategy on {len(data)} securities...")
        self._loaded_data = data
        results = self.run_strategy(data)

        # Store results so scheduler_engine can read them
        self._results = results

        if not results:
            logger.warning("No results from strategy. Exiting.")
            return

        name_map = {}
        if self.name == "etf":
            name_map = self.ds.get_etf_name_map()
        elif self.name in ("short", "mid"):
            name_map = self.ds.get_stock_name_map()

        logger.info("=" * 30)
        logger.info(f"TOP {len(results)} {self.name.upper()} RESULTS")
        logger.info("=" * 30)
        for i, item in enumerate(results, start=1):
            logger.info(self.format_result(item, name_map))

        self.export_results(results)
        logger.info(f"Task {self.name} completed successfully.")


class ETFTask(BaseTask):
    """
    ETF 组合任务：根据动量因子筛选 ETF 并生成目标持仓
    """

    name = "etf"
    description = "ETF 组合选股任务"
    title = "ETF 组合"

    def get_pool(self) -> List[str]:
        if self._override_pool is not None and "etf" in self._override_pool:
            return self._override_pool["etf"]
        return ETF_POOL

    def load_data(self, pool: List[str]) -> Dict[str, pd.DataFrame]:
        """
        批量加载 ETF 数据（一次 PG 查询）
        """
        return self.ds.load_data_batch(pool, intraday=self.intraday, interval=self._bar_interval)

    def _load_previous_etf_codes(self) -> set:
        """从最近一次 CSV 结果加载昨日在榜 ETF 代码集合"""
        results_dir = PROJECT_ROOT / "data" / "results"
        if not results_dir.exists():
            return set()

        target_dt = None
        if self.target_date:
            target_dt = datetime.strptime(self.target_date, "%Y-%m-%d")

        latest_dir = None
        for d in sorted(results_dir.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            try:
                dir_date = datetime.strptime(d.name, "%Y-%m-%d")
            except ValueError:
                continue
            if target_dt and dir_date >= target_dt:
                continue
            if self._bar_interval == "1d":
                csv_path = d / "etf.csv"
            else:
                csv_path = d / f"etf_{self._bar_interval}.csv"
            if csv_path.exists():
                latest_dir = d
                break

        if not latest_dir:
            return set()

        if self._bar_interval == "1d":
            csv_path = latest_dir / "etf.csv"
        else:
            csv_path = latest_dir / f"etf_{self._bar_interval}.csv"
        try:
            df = pd.read_csv(csv_path, dtype={"code": str})
            codes = set(df["code"].dropna().str.strip().tolist())
            logger.info(f"Loaded {len(codes)} previous ETF codes from {latest_dir.name}")
            return codes
        except Exception as e:
            logger.warning(f"Failed to load previous ETF results: {e}")
            return set()

    def run_strategy(self, data: Dict[str, pd.DataFrame]) -> List[ETFScore]:
        from quant_etf.market_regime import assess_market
        from quant_etf.conf import INDEX_WEIGHTS, MARKET_REGIME_CONFIG

        # 1. 大盘状态评估
        index_data = {c: data[c] for c in INDEX_WEIGHTS if c in data}
        regime = assess_market(index_data, bar_interval=self._bar_interval)
        self._regime = regime  # 供 runner 读取

        # 2. 按动量 score 排序
        ranked = self.strategy.rank_etfs(data)
        ranked_map = {item.code: item for item in ranked}

        # 3. 取 top_n（根据大盘状态）
        top_n = regime.top_n

        # 4. 加载昨日持仓，检测下榜（排除主动从池子中删除的标的）
        prev_codes = self._load_previous_etf_codes()
        pool_codes = set(data.keys())  # 当前池中所有标的
        self._pool_codes = pool_codes  # 供 runner 读取
        prev_in_pool = prev_codes & pool_codes  # 只考虑仍在池中的昨日持仓
        current_codes = {item.code for item in ranked[:top_n]}
        self._delisted_codes = prev_in_pool - current_codes  # 在池中但跌出 top_n → 下榜
        removed_from_pool = prev_codes - pool_codes
        if removed_from_pool:
            logger.info(f"REMOVED FROM POOL (not delist): {removed_from_pool}")
        for code in self._delisted_codes:
            logger.info(f"DELISTED: {code} rank>{top_n}, removed from portfolio")

        # 5. 构建组合：top_n 内正常入选
        portfolio = self.strategy.get_target_portfolio(ranked, top_n=top_n)

        # 6. 风控调整（defensive 时折扣更大）
        etf_name_map = self.ds.get_etf_name_map()
        final_portfolio = {}
        warning_factor = 0.5 * regime.risk_discount

        for code, weight in portfolio.items():
            df = data.get(code)
            if df is None:
                continue
            risk_status = self.risk_manager.check_risk(df)
            etf_name = etf_name_map.get(code, "Unknown")

            if risk_status.level == RiskLevel.CRITICAL:
                logger.critical(
                    f"RISK ALERT for {code} ({etf_name}): {risk_status.reason}. "
                    f"Action: {risk_status.suggested_action}"
                )
                final_portfolio[code] = 0.0
            elif risk_status.level == RiskLevel.WARNING:
                logger.warning(
                    f"RISK WARNING for {code} ({etf_name}): {risk_status.reason}. "
                    f"Action: {risk_status.suggested_action}"
                )
                final_portfolio[code] = weight * warning_factor
            else:
                logger.info(f"Risk Check {code} ({etf_name}): PASSED")
                final_portfolio[code] = weight

        # 7. 输出：保持动量 score 降序
        output_results = []
        for code, adj_weight in final_portfolio.items():
            if adj_weight > 0:
                original = ranked_map.get(code)
                if not original:
                    continue
                item = ETFScore(
                    code=code,
                    score=original.score,
                    p60=original.p60,
                    p20=original.p20,
                    p10=original.p10,
                    p5=original.p5,
                )
                output_results.append((item, adj_weight))

        output_results.sort(key=lambda x: x[0].score, reverse=True)
        return [item[0] for item in output_results]

    def format_result(self, result: ETFScore, name_map: Dict[str, str]) -> str:
        etf_name = name_map.get(result.code, "Unknown")
        return f"Rank: {result.code} ({etf_name}) | Target Weight: {result.score:.2%}"

    def export_results(self, results: List[ETFScore]) -> None:
        etf_name_map = self.ds.get_etf_name_map()
        
        # 保存 CSV
        csv_data = []
        for item in results:
            row = asdict(item)
            row["name"] = etf_name_map.get(item.code, "Unknown")
            # 重命名 score 为 target_weight 以免歧义
            row["target_weight"] = row.pop("score")
            csv_data.append(row)
        self.save_results_to_csv(csv_data, "etf")

        codes = [r.code for r in results]

        logger.info("=" * 30)
        logger.info("FINAL PORTFOLIO TARGETS")
        logger.info("=" * 30)
        for code in codes:
            df = self._loaded_data.get(code)
            if df is None or df.empty:
                continue
            weight = self.strategy.get_target_portfolio(
                self.strategy.rank_etfs({code: df}), top_n=1
            ).get(code, 0)
            etf_name = etf_name_map.get(code, "Unknown")
            logger.info(f"ETF: {code} ({etf_name}) | Target Weight: {weight:.2%}")

        if codes:
            export_path = export_to_tdx_block(codes)
            if export_path:
                logger.info(f"TDX Import File created: {export_path}")

            auto_export_path = export_to_tdx_custom_block_auto(codes)
            if auto_export_path:
                logger.info(f"Auto-exported to TDX Block: {auto_export_path}")

        formula_path = generate_tdx_formula_file()
        if formula_path:
            logger.info(f"TDX Formula File created: {formula_path}")


class ShortTermStockTask(BaseTask):
    """
    短线股票任务：从股票池中筛选短线强势股
    """

    name = "short"
    description = "短线股票选股任务"
    title = "短线股票"

    def get_pool(self) -> List[str]:
        """短线股票池：优先 override，其次动态从通达信读取"""
        if self._override_pool is not None and "stock" in self._override_pool:
            return self._override_pool["stock"]
        return get_stock_pool("stock")

    def load_data(self, pool: List[str]) -> Dict[str, pd.DataFrame]:
        """
        批量加载股票数据（一次 PG 查询）
        """
        return self.ds.load_stock_data_batch(pool, intraday=self.intraday, interval=self._bar_interval)

    def run_strategy(self, data: Dict[str, pd.DataFrame]) -> List[StockScore]:
        return self.strategy.rank_stocks_for_short_term(data, top_n=5)

    def format_result(self, result: StockScore, name_map: Dict[str, str]) -> str:
        stock_name = name_map.get(result.code, "Unknown")
        return (
            f"Rank: {result.code} ({stock_name}) | Score: {result.score:.4f} "
            f"(P5: {result.p5:.2%}, P10: {result.p10:.2%}, P20: {result.p20:.2%}, "
            f"VolRatio: {result.volume_ratio_1d_20d:.2f}, TrendOK: {result.trend_ok})"
        )

    def export_results(self, results: List[StockScore]) -> None:
        stock_name_map = self.ds.get_stock_name_map()
        
        # 保存 CSV
        csv_data = []
        for item in results:
            row = asdict(item)
            row["name"] = stock_name_map.get(item.code, "Unknown")
            csv_data.append(row)
        self.save_results_to_csv(csv_data, "short")

        codes = [r.code for r in results]

        logger.info("=" * 30)
        logger.info("PICKED STOCK LIST (CODE + NAME)")
        logger.info("=" * 30)
        for code in codes:
            logger.info(f"{code}\t{stock_name_map.get(code, 'Unknown')}")

        out_dir = PROJECT_ROOT / "output"
        out_dir.mkdir(exist_ok=True)
        out_path = out_dir / f"TDX_Stock_Pick_{datetime.now().strftime('%Y%m%d_%H%M%S')}.blk"

        lines = []
        for code in codes:
            prefix = "1" if str(code).startswith(("5", "6")) else "0"
            lines.append(f"{prefix}{code}\n")

        out_path.write_text("".join(lines), encoding="utf-8")
        logger.info(f"Saved TDX stock pick block file: {out_path}")


class MidTermReboundTask(BaseTask):
    """
    中期反弹股票任务：从股票池中筛选中期反弹股
    """

    name = "mid"
    description = "中期反弹股票选股任务"
    title = "中期反弹"

    def get_pool(self) -> List[str]:
        """中期反弹池：优先 override，其次动态从通达信读取"""
        if self._override_pool is not None and "mid_term" in self._override_pool:
            return self._override_pool["mid_term"]
        return get_stock_pool("mid_term")

    def load_data(self, pool: List[str]) -> Dict[str, pd.DataFrame]:
        """
        批量加载股票数据（一次 PG 查询）
        """
        return self.ds.load_stock_data_batch(pool, intraday=self.intraday, interval=self._bar_interval)

    def run_strategy(self, data: Dict[str, pd.DataFrame]) -> List[ReboundStockScore]:
        return self.strategy.rank_stocks_for_mid_term_rebound(data, top_n=15)

    def format_result(self, result: ReboundStockScore, name_map: Dict[str, str]) -> str:
        stock_name = name_map.get(result.code, "Unknown")
        return (
            f"Rank: {result.code} ({stock_name}) | Score: {result.score:.4f} "
            f"(Drawdown120: {result.drawdown_from_120d_high:.2%}, "
            f"Bounce20: {result.bounce_from_20d_low:.2%}, "
            f"P5: {result.p5:.2%}, P10: {result.p10:.2%}, P20: {result.p20:.2%}, "
            f"VolRatio: {result.volume_ratio_1d_20d:.2f}, "
            f"Stabilized: {result.stabilization_ok}, ReboundOK: {result.rebound_ok})"
        )

    def export_results(self, results: List[ReboundStockScore]) -> None:
        stock_name_map = self.ds.get_stock_name_map()
        
        # 保存 CSV
        csv_data = []
        for item in results:
            row = asdict(item)
            row["name"] = stock_name_map.get(item.code, "Unknown")
            csv_data.append(row)
        self.save_results_to_csv(csv_data, "mid")

        codes = [r.code for r in results]

        logger.info("=" * 30)
        logger.info("PICKED STOCK LIST (CODE + NAME)")
        logger.info("=" * 30)
        for code in codes:
            logger.info(f"{code}\t{stock_name_map.get(code, 'Unknown')}")

        out_dir = PROJECT_ROOT / "output"
        out_dir.mkdir(exist_ok=True)
        out_path = out_dir / f"TDX_MidTerm_Rebound_Pick_{datetime.now().strftime('%Y%m%d_%H%M%S')}.blk"

        lines = []
        for code in codes:
            prefix = "1" if str(code).startswith(("5", "6")) else "0"
            lines.append(f"{prefix}{code}\n")

        out_path.write_text("".join(lines), encoding="utf-8")
        logger.info(f"Saved TDX mid-term rebound pick block file: {out_path}")


class TaskRegistry:
    """
    任务注册表：管理所有可用任务
    """

    _tasks: Dict[str, type] = {
        "etf": ETFTask,
        "short": ShortTermStockTask,
        "mid": MidTermReboundTask,
    }

    @classmethod
    def get_task(
        cls,
        name: str,
        target_date: str | None = None,
        intraday: bool = False,
        bar_interval: str = DEFAULT_INTERVAL,
    ) -> Optional[BaseTask]:
        """
        根据任务名称获取任务实例
        :param name: 任务名称
        :param target_date: 目标日期，格式 YYYY-MM-DD
        :param intraday: 是否使用盘中实时行情
        :param bar_interval: K线周期 ("1d"/"5m"/"15m"/"30m"/"60m")
        """
        task_class = cls._tasks.get(name)
        if task_class is None:
            return None
        return task_class(target_date=target_date, intraday=intraday, bar_interval=bar_interval)

    @classmethod
    def list_tasks(cls) -> List[Dict[str, str]]:
        """
        列出所有可用任务
        """
        return [
            {"name": name, "description": task_class.description, "title": getattr(task_class, "title", task_class.description)}
            for name, task_class in cls._tasks.items()
        ]

    @classmethod
    def register(cls, name: str, task_class: type) -> None:
        """
        注册新任务
        """
        cls._tasks[name] = task_class
