"""
提醒记录器

将策略信号和提醒信息保存到数据库
"""

import duckdb
from pathlib import Path
from loguru import logger
from datetime import datetime
from typing import List, Optional

from quant_etf.market_analyzer import MarketState
from quant_etf.strategies.momentum_breakthrough import StrategySignal
from quant_etf.conf import DATA_DIR


def get_alert_db_path() -> Path:
    """
    获取提醒数据库文件路径
    """
    data_dir = DATA_DIR / "alerts"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "alerts.duckdb"


def init_alert_db() -> duckdb.DuckDBPyConnection:
    """
    初始化提醒数据库
    """
    db_path = get_alert_db_path()
    conn = duckdb.connect(str(db_path))

    conn.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TIMESTAMP,
            code VARCHAR,
            strategy_name VARCHAR,
            signal_type VARCHAR,
            direction VARCHAR,
            score DOUBLE,
            entry_price DOUBLE,
            stop_loss DOUBLE,
            take_profit DOUBLE,
            reason TEXT,
            market_state VARCHAR,
            market_return DOUBLE,
            market_volatility DOUBLE,
            ma10 DOUBLE,
            ma20 DOUBLE,
            ma30 DOUBLE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_time ON alerts(time)
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_code ON alerts(code)
    """)

    logger.info(f"Initialized alert database: {db_path}")
    return conn


def get_alert_db_connection() -> duckdb.DuckDBPyConnection:
    """
    获取提醒数据库连接（单例模式）
    """
    if not hasattr(get_alert_db_connection, "_conn"):
        get_alert_db_connection._conn = init_alert_db()
    return get_alert_db_connection._conn


class AlertRecorder:
    """提醒记录器"""

    def __init__(self):
        """
        初始化提醒记录器
        """
        self.conn = get_alert_db_connection()

    def record_signal(self, signal: StrategySignal, market_state: MarketState) -> bool:
        """
        记录单个信号
        :param signal: 策略信号
        :param market_state: 市场状态
        :return: 是否成功
        """
        try:
            self.conn.execute(
                """
                INSERT INTO alerts
                (time, code, strategy_name, signal_type, direction, score,
                 entry_price, stop_loss, take_profit, reason, market_state,
                 market_return, market_volatility, ma10, ma20, ma30)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    signal.code,
                    signal.code,
                    signal.strategy_name,
                    signal.signal_type.value,
                    signal.direction,
                    signal.score,
                    signal.entry_price,
                    signal.stop_loss,
                    signal.take_profit,
                    signal.reason,
                    market_state.market_type.value,
                    market_state.etf_pool_return,
                    market_state.volatility,
                    signal.ma10,
                    signal.ma20,
                    signal.ma30,
                ),
            )

            logger.info(f"Recorded alert for {signal.code}: {signal.reason[:50]}...")
            return True

        except Exception as e:
            logger.error(f"Failed to record alert for {signal.code}: {e}")
            return False

    def record_signals(
        self, signals: List[StrategySignal], market_state: MarketState
    ) -> int:
        """
        批量记录信号
        :param signals: 信号列表
        :param market_state: 市场状态
        :return: 成功记录的数量
        """
        success_count = 0
        for signal in signals:
            if self.record_signal(signal, market_state):
                success_count += 1

        logger.info(f"Recorded {success_count}/{len(signals)} alerts")
        return success_count

    def query_recent_alerts(self, hours: int = 24, limit: int = 100) -> List[dict]:
        """
        查询最近的提醒
        :param hours: 最近多少小时
        :param limit: 限制数量
        :return: 提醒列表
        """
        try:
            query = f"""
                SELECT * FROM alerts
                WHERE time >= datetime('now', '-{hours} hours')
                ORDER BY time DESC
                LIMIT {limit}
            """
            result = self.conn.execute(query).fetchall()
            columns = [desc[0] for desc in self.conn.description]

            alerts = []
            for row in result:
                alert_dict = dict(zip(columns, row))
                alerts.append(alert_dict)

            return alerts

        except Exception as e:
            logger.error(f"Failed to query recent alerts: {e}")
            return []

    def query_alerts_by_code(self, code: str, limit: int = 20) -> List[dict]:
        """
        查询指定代码的提醒
        :param code: ETF代码
        :param limit: 限制数量
        :return: 提醒列表
        """
        try:
            query = f"""
                SELECT * FROM alerts
                WHERE code = '{code}'
                ORDER BY time DESC
                LIMIT {limit}
            """
            result = self.conn.execute(query).fetchall()
            columns = [desc[0] for desc in self.conn.description]

            alerts = []
            for row in result:
                alert_dict = dict(zip(columns, row))
                alerts.append(alert_dict)

            return alerts

        except Exception as e:
            logger.error(f"Failed to query alerts for {code}: {e}")
            return []

    def get_alert_summary(self, hours: int = 24) -> dict:
        """
        获取提醒统计摘要
        :param hours: 最近多少小时
        :return: 统计字典
        """
        try:
            query = f"""
                SELECT
                    COUNT(*) as total_alerts,
                    COUNT(DISTINCT code) as unique_codes,
                    AVG(score) as avg_score,
                    COUNT(CASE WHEN direction = 'buy' THEN 1 END) as buy_signals,
                    COUNT(CASE WHEN direction = 'sell' THEN 1 END) as sell_signals
                FROM alerts
                WHERE time >= datetime('now', '-{hours} hours')
            """
            result = self.conn.execute(query).fetchone()

            return {
                "total_alerts": result[0],
                "unique_codes": result[1],
                "avg_score": result[2],
                "buy_signals": result[3],
                "sell_signals": result[4],
            }

        except Exception as e:
            logger.error(f"Failed to get alert summary: {e}")
            return {}
