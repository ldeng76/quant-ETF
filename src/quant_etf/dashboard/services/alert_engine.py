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
                score = self._parse_score(item.get("score") or item.get("weight"))
                if code and score is not None:
                    prev_map[code] = score

            for item in latest_result:
                code = item.get("code")
                score = self._parse_score(item.get("score") or item.get("weight"))
                if code and score is not None and code in prev_map:
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

    def _parse_score(self, value) -> Optional[float]:
        """解析得分，支持 '54.00%' 或 '54.00' 或 54.0 格式"""
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        # 处理字符串格式
        s = str(value).strip().rstrip("%")
        try:
            return float(s) / 100 if "%" in str(value) else float(s)
        except (ValueError, TypeError):
            return None

    def _check_position_deviation(self, latest_result, prev_result) -> Optional[dict]:
        """检查持仓偏离目标（预留）"""
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

    def save_alerts(self, alerts: list[dict], user_id: Optional[int] = None) -> list[int]:
        """
        保存告警到数据库
        user_id: 可选，关联到特定用户；为 None 时表示系统告警（admin 可见）
        """
        ids = []
        for alert in alerts:
            alert_id = execute(
                """INSERT INTO alerts_dashboard
                   (user_id, rule_id, alert_type, severity, title, message, data)
                   VALUES (%s, NULL, %s, %s, %s, %s, %s)""",
                [
                    user_id,
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