"""
监控主程序

实时监控ETF池，生成交易信号并记录提醒
"""

import time
import pandas as pd
from datetime import datetime
from typing import Optional
from loguru import logger

from quant_etf.minute_collector import (
    is_trading_time,
    save_minute_data_from_dicts,
    get_minute_bars,
)
from quant_etf.minute_data_manager import get_pool_15min_bars, update_15min_data
from quant_etf.market_analyzer import MarketAnalyzer, get_market_state
from quant_etf.signal_generator import SignalGenerator
from quant_etf.risk_manager import RiskManager
from quant_etf.alert_recorder import AlertRecorder
from quant_etf.strategies.momentum_breakthrough import StrategySignal
from quant_etf.conf import ETF_POOL


class ETFMonitor:
    """ETF监控器"""

    def __init__(self, etf_pool: list[str] = None, check_interval: int = 60):
        """
        初始化监控器
        :param etf_pool: ETF代码列表
        :param check_interval: 检查间隔（秒）
        """
        self.etf_pool = etf_pool or ETF_POOL
        self.check_interval = check_interval

        self.market_analyzer = MarketAnalyzer()
        self.signal_generator = SignalGenerator(top_n=10)
        self.risk_manager = RiskManager()
        self.alert_recorder = AlertRecorder()

        self.running = False

        logger.info(
            f"ETFMonitor initialized with {len(self.etf_pool)} ETFs, interval={check_interval}s"
        )

    def fetch_realtime_data(self) -> dict[str, pd.DataFrame]:
        """
        获取实时5分钟数据并保存
        """
        pool_data = {}
        for code in self.etf_pool:
            try:
                bars = get_minute_bars(code, count=10)
                if bars:
                    save_minute_data_from_dicts(code, bars)
                    logger.debug(f"Fetched and saved 1min data for {code}")
            except Exception as e:
                logger.error(f"Failed to fetch 1min data for {code}: {e}")

        return pool_data

    def update_15min_data(self) -> dict[str, pd.DataFrame]:
        """
        更新15分钟K线数据
        """
        for code in self.etf_pool:
            try:
                count = update_15min_data(code)
                if count > 0:
                    logger.debug(f"Updated 15min data for {code}: {count} bars")
            except Exception as e:
                logger.error(f"Failed to update 15min data for {code}: {e}")

        pool_data = get_pool_15min_bars(self.etf_pool, count=200)
        logger.info(f"Loaded 15min data for {len(pool_data)} ETFs")
        return pool_data

    def analyze_market(self):
        """
        分析市场状态
        """
        market_state = self.market_analyzer.analyze_market(self.etf_pool)
        logger.info(
            f"Market State: {market_state.market_type.value}, "
            f"ETF Pool Return: {market_state.etf_pool_return:.2%}, "
            f"Volatility: {market_state.volatility:.4f}"
        )
        return market_state

    def generate_signals(
        self, pool_data: dict[str, pd.DataFrame], market_state
    ) -> list[StrategySignal]:
        """
        生成交易信号
        """
        signals = self.signal_generator.generate_signals(pool_data, market_state)

        for signal in signals:
            df = pool_data.get(signal.code)
            if df is not None:
                self.risk_manager.update_signal_risk(signal, df)

        return signals

    def record_alerts(self, signals: list[StrategySignal], market_state):
        """
        记录提醒
        """
        if not signals:
            logger.info("No signals to record")
            return 0

        count = self.alert_recorder.record_signals(signals, market_state)
        return count

    def display_signals(self, signals: list[StrategySignal]):
        """
        显示信号
        """
        if not signals:
            return

        print("\n" + "=" * 80)
        print(
            f"{'代码':<8} {'策略':<12} {'方向':<6} {'评分':<6} {'入场价':<10} "
            f"{'止损':<10} {'止盈':<10} {'理由'}"
        )
        print("=" * 80)

        for signal in signals:
            print(
                f"{signal.code:<8} {signal.strategy_name:<12} {signal.direction:<6} "
                f"{signal.score:.3f}   {signal.entry_price:<10.4f} "
                f"{signal.stop_loss or 0:<10.4f} {signal.take_profit or 0:<10.4f} "
                f"{signal.reason[:30]}"
            )

        print("=" * 80 + "\n")

    def run_cycle(self) -> bool:
        """
        运行一次监控循环
        :return: 是否继续运行
        """
        logger.info(f"\n{'=' * 60}")
        logger.info(f"Starting monitoring cycle at {datetime.now()}")
        logger.info(f"{'=' * 60}\n")

        if not is_trading_time():
            logger.info("Not in trading hours, skipping...")
            return True

        try:
            pool_data = self.update_15min_data()

            if not pool_data:
                logger.warning("No 15min data available, skipping this cycle")
                return True

            market_state = self.analyze_market()

            signals = self.generate_signals(pool_data, market_state)

            self.display_signals(signals)

            self.record_alerts(signals, market_state)

            logger.info(f"Cycle completed at {datetime.now()}\n")

            return True

        except Exception as e:
            logger.exception(f"Error in monitoring cycle: {e}")
            return True

    def start(self):
        """
        启动监控
        """
        logger.info("Starting ETF Monitor...")
        self.running = True

        while self.running:
            try:
                should_continue = self.run_cycle()

                if not should_continue:
                    break

                time.sleep(self.check_interval)

            except KeyboardInterrupt:
                logger.info("Received interrupt signal, stopping...")
                self.running = False
                break

        logger.info("ETF Monitor stopped")

    def stop(self):
        """
        停止监控
        """
        logger.info("Stopping ETF Monitor...")
        self.running = False


def main():
    """
    主函数
    """
    from argparse import ArgumentParser

    parser = ArgumentParser(description="ETF短线策略监控程序")
    parser.add_argument(
        "--interval", type=int, default=60, help="检查间隔（秒），默认60秒"
    )
    parser.add_argument(
        "--pool-size",
        type=int,
        default=len(ETF_POOL),
        help=f"ETF池大小，默认{len(ETF_POOL)}",
    )

    args = parser.parse_args()

    pool = ETF_POOL[: args.pool_size]

    monitor = ETFMonitor(etf_pool=pool, check_interval=args.interval)

    try:
        monitor.start()
    except KeyboardInterrupt:
        monitor.stop()


if __name__ == "__main__":
    main()
