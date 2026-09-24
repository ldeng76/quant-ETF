"""做T策略参数：全部可调参数集中于此，params.json 快照由 to_json 生成。"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class TTradeParams:
    """「做T狂人」体系参数。默认值见设计方案 §2.8。"""

    # 级别与指标
    primary_level: str = "5m"  # 文章对 ETF 建议 15m，回测矩阵对比组
    resonance_level: str = "15m"  # 纵向共振高级别；主 15m 时用 60m
    ma_len: int = 96
    rsi_len: int = 6
    rsi_buy_th: float = 20.0
    rsi_sell_th: float = 80.0
    slope_n: int = 8
    slope_th: float = 0.001

    # 增强开关（默认全开；消融实验逐项关闭）
    confirm_fractal: bool = True
    confirm_volume: bool = True
    resonance: bool = True
    index_filter: bool = True
    amp_filter: bool = True
    amp_min: float = 0.015
    amp_dead: float = 0.015
    time_window: bool = True
    buy_window: tuple[str, str] = ("09:35", "11:00")
    sell_window: tuple[str, str] = ("13:30", "14:30")
    dev_th: float = 0.015

    # 仓位纪律
    units_per_trade: int = 1
    resonance_units: int = 2
    max_concurrent: int = 1
    max_trades_per_day: int = 3
    allow_offside_t: bool = False

    # 当日闭环
    strict_eod: bool = True
    eod_force_time: str = "14:55"

    # 摩擦成本
    commission_rate: float = 1e-4  # 万1，ETF 免印花税
    min_commission: float = 0.0
    slippage: float = 5e-4  # 单边

    # 账户拆分
    base_units: int = 10
    mobile_units: int = 10
    lot_size: int = 100

    def to_json(self) -> str:
        data = asdict(self)
        data["buy_window"] = list(data["buy_window"])
        data["sell_window"] = list(data["sell_window"])
        return json.dumps(data, ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "TTradeParams":
        data = json.loads(text)
        data["buy_window"] = tuple(data["buy_window"])
        data["sell_window"] = tuple(data["sell_window"])
        return cls(**data)

    def save(self, path: Path) -> None:
        path.write_text(self.to_json(), encoding="utf-8")
