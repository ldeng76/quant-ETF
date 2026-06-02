from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime

VALID_INTERVALS = {"1d", "5m", "15m", "30m", "60m"}


class UserModel(BaseModel):
    """用户模型"""
    id: int
    oauth_provider: str
    oauth_id: str
    username: str
    display_name: Optional[str] = ""
    email: Optional[str] = None
    avatar_url: Optional[str] = None
    role: str = "user"  # 'admin' | 'user'
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


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
    interval: int = Field(default=0, ge=0)  # 0=自动（由采集服务驱动）
    bar_interval: str = "1d"

    @field_validator("bar_interval")
    @classmethod
    def validate_bar_interval(cls, v):
        if v not in VALID_INTERVALS:
            raise ValueError(f"bar_interval must be one of {VALID_INTERVALS}")
        return v


class StrategyRunRequest(BaseModel):
    strategies: list[str] = Field(..., min_length=1)
    bar_interval: str = "1d"

    @field_validator("bar_interval")
    @classmethod
    def validate_bar_interval(cls, v):
        if v not in VALID_INTERVALS:
            raise ValueError(f"bar_interval must be one of {VALID_INTERVALS}")
        return v


class UserProfileUpdate(BaseModel):
    display_name: Optional[str] = None


class UserExtendRequest(BaseModel):
    days: int = Field(..., ge=1, le=365)


class WatchlistAdd(BaseModel):
    code: str = Field(..., min_length=6, max_length=6)
    name: str = ""
    notes: str = ""
