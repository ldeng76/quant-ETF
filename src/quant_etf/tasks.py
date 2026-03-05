"""
任务模块：定义各类选股任务的抽象基类和具体实现
"""
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

import pandas as pd
from loguru import logger

from quant_etf.conf import (
    ETF_POOL,
    STOCK_POOL,
    MID_TERM_STOCK_POOL,
    TOP_N,
    PROJECT_ROOT,
)
from quant_etf.data_source import ETFDataSource
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

    def __init__(self):
        self.ds: Optional[ETFDataSource] = None
        self.strategy: Optional[StrategyEngine] = None
        self.risk_manager: Optional[RiskManager] = None

    def initialize(self) -> None:
        """
        初始化数据源和策略引擎
        """
        self.ds = ETFDataSource()
        self.strategy = StrategyEngine()
        self.risk_manager = RiskManager()

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
        任务主流程
        """
        logger.info(f"Starting task: {self.name}")
        logger.info(f"Description: {self.description}")

        self.initialize()

        pool = self.get_pool()
        logger.info(f"Loading data for {len(pool)} securities...")

        data = self.load_data(pool)
        if not data:
            logger.error("No data loaded. Exiting.")
            return

        logger.info(f"Running strategy on {len(data)} securities...")
        results = self.run_strategy(data)

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

    def get_pool(self) -> List[str]:
        return ETF_POOL

    def load_data(self, pool: List[str]) -> Dict[str, pd.DataFrame]:
        data = {}
        for code in pool:
            df = self.ds.load_data(code)
            if df.empty:
                logger.error(f"Failed to load data for {code}. Skipping.")
                continue
            data[code] = df
        return data

    def run_strategy(self, data: Dict[str, pd.DataFrame]) -> List[ETFScore]:
        ranked = self.strategy.rank_etfs(data)
        portfolio = self.strategy.get_target_portfolio(ranked, top_n=TOP_N)

        etf_name_map = self.ds.get_etf_name_map()
        final_portfolio = {}

        for code, weight in portfolio.items():
            df = data[code]
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
                final_portfolio[code] = weight * 0.5
            else:
                logger.info(f"Risk Check {code} ({etf_name}): PASSED")
                final_portfolio[code] = weight

        output_results = []
        etf_name_map = self.ds.get_etf_name_map()
        for code, weight in final_portfolio.items():
            if weight > 0:
                item = ETFScore(
                    code=code,
                    score=weight,
                    r60=0,
                    r20=0,
                    r10=0,
                    r5=0,
                )
                output_results.append((item, etf_name_map.get(code, "Unknown"), weight))

        output_results.sort(key=lambda x: x[2], reverse=True)
        return [item[0] for item in output_results]

    def format_result(self, result: ETFScore, name_map: Dict[str, str]) -> str:
        etf_name = name_map.get(result.code, "Unknown")
        return f"Rank: {result.code} ({etf_name}) | Target Weight: {result.score:.2%}"

    def export_results(self, results: List[ETFScore]) -> None:
        etf_name_map = self.ds.get_etf_name_map()
        codes = [r.code for r in results]

        logger.info("=" * 30)
        logger.info("FINAL PORTFOLIO TARGETS")
        logger.info("=" * 30)
        for code in codes:
            weight = self.strategy.get_target_portfolio(
                self.strategy.rank_etfs({code: self.ds.load_data(code)}), top_n=1
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

    def get_pool(self) -> List[str]:
        return STOCK_POOL

    def load_data(self, pool: List[str]) -> Dict[str, pd.DataFrame]:
        data = {}
        for code in pool:
            df = self.ds.load_stock_data(code)
            if df.empty:
                logger.error(f"Failed to load stock data for {code}. Skipping.")
                continue
            data[code] = df
        return data

    def run_strategy(self, data: Dict[str, pd.DataFrame]) -> List[StockScore]:
        return self.strategy.rank_stocks_for_short_term(data, top_n=5)

    def format_result(self, result: StockScore, name_map: Dict[str, str]) -> str:
        stock_name = name_map.get(result.code, "Unknown")
        return (
            f"Rank: {result.code} ({stock_name}) | Score: {result.score:.4f} "
            f"(R5: {result.r5:.2%}, R10: {result.r10:.2%}, R20: {result.r20:.2%}, "
            f"VolRatio: {result.volume_ratio_1d_20d:.2f}, TrendOK: {result.trend_ok})"
        )

    def export_results(self, results: List[StockScore]) -> None:
        stock_name_map = self.ds.get_stock_name_map()
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

    def get_pool(self) -> List[str]:
        return MID_TERM_STOCK_POOL

    def load_data(self, pool: List[str]) -> Dict[str, pd.DataFrame]:
        data = {}
        for code in pool:
            df = self.ds.load_stock_data(code)
            if df.empty:
                logger.error(f"Failed to load stock data for {code}. Skipping.")
                continue
            data[code] = df
        return data

    def run_strategy(self, data: Dict[str, pd.DataFrame]) -> List[ReboundStockScore]:
        return self.strategy.rank_stocks_for_mid_term_rebound(data, top_n=15)

    def format_result(self, result: ReboundStockScore, name_map: Dict[str, str]) -> str:
        stock_name = name_map.get(result.code, "Unknown")
        return (
            f"Rank: {result.code} ({stock_name}) | Score: {result.score:.4f} "
            f"(Drawdown120: {result.drawdown_from_120d_high:.2%}, "
            f"Bounce20: {result.bounce_from_20d_low:.2%}, "
            f"R5: {result.r5:.2%}, R10: {result.r10:.2%}, R20: {result.r20:.2%}, "
            f"VolRatio: {result.volume_ratio_1d_20d:.2f}, "
            f"Stabilized: {result.stabilization_ok}, ReboundOK: {result.rebound_ok})"
        )

    def export_results(self, results: List[ReboundStockScore]) -> None:
        stock_name_map = self.ds.get_stock_name_map()
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
    def get_task(cls, name: str) -> Optional[BaseTask]:
        """
        根据任务名称获取任务实例
        """
        task_class = cls._tasks.get(name)
        if task_class is None:
            return None
        return task_class()

    @classmethod
    def list_tasks(cls) -> List[Dict[str, str]]:
        """
        列出所有可用任务
        """
        return [
            {"name": name, "description": task_class.description}
            for name, task_class in cls._tasks.items()
        ]

    @classmethod
    def register(cls, name: str, task_class: type) -> None:
        """
        注册新任务
        """
        cls._tasks[name] = task_class
