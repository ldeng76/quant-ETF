from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class AccountCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    broker: str = ""
    cash: float = 0.0


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    broker: Optional[str] = None
    cash: Optional[float] = None


class HoldingCreate(BaseModel):
    account_id: int
    code: str = Field(..., min_length=6, max_length=6)
    name: str = ""
    quantity: int = Field(..., ge=0)
    cost_price: float = Field(..., ge=0)
    strategy: str = ""
    notes: str = ""


class HoldingUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    quantity: Optional[int] = None
    cost_price: Optional[float] = None
    strategy: Optional[str] = None
    notes: Optional[str] = None


class AlertRuleCreate(BaseModel):
    name: str
    rule_type: str  # top3_entry / momentum_shock / position_deviation
    config: str = "{}"  # JSON string


class AlertUpdate(BaseModel):
    status: str  # active / acknowledged / resolved


class ScheduleCreate(BaseModel):
    strategy: str
    interval: int = Field(..., ge=60)  # 最少60秒


class StrategyRunRequest(BaseModel):
    strategies: list[str] = Field(..., min_length=1)
