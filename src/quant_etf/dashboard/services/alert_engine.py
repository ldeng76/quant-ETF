"""
告警条件检测引擎
"""
import json
from datetime import datetime
from typing import Optional, Any
from dataclasses import dataclass
from loguru import logger

from ..db import execute, query


@dataclass
class AlertRule:
    name: str
    check_fn: callable
    severity: str  # info / warning / danger


class AlertEngine:
    """告警引擎"""

    def __init__(self):
        self.rules: list[AlertRule] = [
            AlertRule("评分进入前三", self._check_top3_entry, "warning"),
            AlertRule("动量得分突变", self._check_momentum_shock, "danger"),
            AlertRule("持仓偏离目标", self._check_position_deviation, "info"),
        ]

    def _check_top3_entry(self, latest_result, prev_result) -> Optional[dict]:
        """检查是否有标的首次进入前3"""
        if not latest_result or not prev_result:
            return None
        try:
            curr_top3 = set(item["code"] for item in latest_result[:3] if item.get("code"))
            prev_top3 = set(item["code"] for item in prev_result[:3] if item.get("code"))
            new_entries = curr_top3 - prev_top3
            if new_entries:
                entries_str = ", ".join(sorted(new_entries))
                return {
                    "title": "新标的进入前三",
                    "message": f"{entries_str} 首次进入评分前3",
                    "data": {"new_entries": list(new_entries)},
                }
        except Exception as e:
            logger.warning(f"Alert check top3_entry failed: {e}")
        return None

    def _check_momentum_shock(self, latest_result, prev_result) -> Optional[dict]:
        """检查标的得分是否发生剧烈变化"""
        if not latest_result or not prev_result:
            return None
        try:
            prev_map = {}
            for item in prev_result:
                code = item.get("code")
                score = item.get("score") or item.get("weight") or 0
                if code:
                    prev_map[code] = float(score)

            for item in latest_result:
                code = item.get("code")
                score = float(item.get("score") or item.get("weight") or 0)
                if code and code in prev_map:
                    change = abs(score - prev_map[code])
                    if change > 0.15:
                        return {
                            "title": f"{code} 动量突变",
                            "message": f"得分变化 {change:.2%}",
                            "data": {"code": code, "change": change},
                        }
        except Exception as e:
            logger.warning(f"Alert check momentum_shock failed: {e}")
        return None

    def _check_position_deviation(self, latest_result, prev_result) -> Optional[dict]:
        """检查持仓偏离目标（预留）
        需要结合 portfolio 数据，MVP阶段简化为占位
        """
        return None

    def check(self, latest_result, prev_result, portfolio_data=None) -> list[dict]:
        """执行所有规则检查"""
        alerts = []
        for rule in self.rules:
            try:
                result = rule.check_fn(latest_result, prev_result)
                if result:
                    alerts.append({
                        "alert_type": rule.name,
                        "severity": rule.severity,
                        **result,
                    })
            except Exception as e:
                logger.warning(f"Alert rule '{rule.name}' check failed: {e}")
        return alerts

    def save_alerts(self, alerts: list[dict]) -> list[int]:
        """保存告警到数据库"""
        ids = []
        for alert in alerts:
            alert_id = execute(
                """INSERT INTO alerts_dashboard
                   (rule_id, alert_type, severity, title, message, data)
                   VALUES (NULL, ?, ?, ?, ?, ?)""",
                [
                    alert.get("alert_type", ""),
                    alert.get("severity", "info"),
                    alert.get("title", ""),
                    alert.get("message", ""),
                    json.dumps(alert.get("data", {}), ensure_ascii=False),
                ]
            )
            ids.append(alert_id)
        return ids


# 全局告警引擎实例
alert_engine = AlertEngine()
