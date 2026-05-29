"""
提醒记录器

将策略信号和提醒信息保存到 PostgreSQL 数据库
"""
import psycopg2
from pathlib import Path
from loguru import logger
from datetime import datetime
from typing import List, Optional

from quant_etf.market_analyzer import MarketState
from quant_etf.strategies.momentum_breakthrough import StrategySignal


_pg_conn = None


def _get_pg_conn():
    """获取 PG 同步连接（单例）"""
    global _pg_conn
    if _pg_conn is None:
        from quant_etf.dashboard.config import (
            POSTGRES_HOST, POSTGRES_PORT,
            POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB,
        )
        _pg_conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            database=POSTGRES_DB,
        )
        logger.info("PostgreSQL connection created for alert recorder")
    return _pg_conn


def close_pg_conn():
    """关闭 PG 连接"""
    global _pg_conn
    if _pg_conn:
        _pg_conn.close()
        _pg_conn = None
        logger.info("PostgreSQL connection closed for alert recorder")


def init_alert_db():
    """初始化提醒数据库"""
    conn = _get_pg_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS monitor_alerts (
            id              SERIAL PRIMARY KEY,
            time            TIMESTAMP,
            code            VARCHAR(20),
            strategy_name   VARCHAR(100),
            signal_type     VARCHAR(20),
            direction       VARCHAR(20),
            score           NUMERIC(10, 4),
            entry_price     NUMERIC(18, 4),
            stop_loss       NUMERIC(18, 4),
            take_profit     NUMERIC(18, 4),
            reason          TEXT,
            market_state    VARCHAR(20),
            market_return   NUMERIC(10, 4),
            market_volatility NUMERIC(10, 4),
            ma10            NUMERIC(18, 4),
            ma20            NUMERIC(18, 4),
            ma30            NUMERIC(18, 4),
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_monitor_alerts_time ON monitor_alerts(time DESC)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_monitor_alerts_code ON monitor_alerts(code)
    """)
    conn.commit()
    logger.info("Ensured monitor_alerts table exists")
    return conn


class AlertRecorder:
    """提醒记录器"""

    def __init__(self):
        """初始化提醒记录器"""
        self.conn = _get_pg_conn()

    def record_signal(self, signal: StrategySignal, market_state: MarketState) -> bool:
        """
        记录单个信号
        :param signal: 策略信号
        :param market_state: 市场状态
        :return: 是否成功
        """
        try:
            cur = self.conn.cursor()
            cur.execute("""
                INSERT INTO monitor_alerts
                (time, code, strategy_name, signal_type, direction, score,
                 entry_price, stop_loss, take_profit, reason, market_state,
                 market_return, market_volatility, ma10, ma20, ma30)
                VALUES (NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
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
            ))
            self.conn.commit()

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
            cur = self.conn.cursor()
            cur.execute("""
                SELECT * FROM monitor_alerts
                WHERE time >= NOW() - INTERVAL '%s hours'
                ORDER BY time DESC
                LIMIT %s
            """, [hours, limit])

            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]

            alerts = []
            for row in rows:
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
            cur = self.conn.cursor()
            cur.execute("""
                SELECT * FROM monitor_alerts
                WHERE code = %s
                ORDER BY time DESC
                LIMIT %s
            """, [code, limit])

            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]

            alerts = []
            for row in rows:
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
            cur = self.conn.cursor()
            cur.execute("""
                SELECT
                    COUNT(*) as total_alerts,
                    COUNT(DISTINCT code) as unique_codes,
                    AVG(score) as avg_score,
                    COUNT(CASE WHEN direction = 'buy' THEN 1 END) as buy_signals,
                    COUNT(CASE WHEN direction = 'sell' THEN 1 END) as sell_signals
                FROM monitor_alerts
                WHERE time >= NOW() - INTERVAL '%s hours'
            """, [hours])

            result = cur.fetchone()

            return {
                "total_alerts": result[0] or 0,
                "unique_codes": result[1] or 0,
                "avg_score": float(result[2]) if result[2] else 0.0,
                "buy_signals": result[3] or 0,
                "sell_signals": result[4] or 0,
            }

        except Exception as e:
            logger.error(f"Failed to get alert summary: {e}")
            return {}


# ============================================================
# 兼容层：保留 DuckDB 风格的函数
# ============================================================

def get_alert_db_path() -> Path:
    """兼容函数"""
    return Path("postgresql:monitor_alerts")


def get_alert_db_connection():
    """兼容函数"""
    return _get_pg_conn()