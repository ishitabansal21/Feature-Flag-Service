from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class FlagCreate(BaseModel):
    name: str
    description: str = ""
    enabled: bool = False
    rollout_percentage: int = Field(default=0, ge=0, le=100)


class FlagUpdate(BaseModel):
    enabled: Optional[bool] = None
    rollout_percentage: Optional[int] = Field(default=None, ge=0, le=100)
    description: Optional[str] = None


class FlagResponse(BaseModel):
    id: int
    name: str
    description: str
    enabled: bool
    rollout_percentage: int
    created_at: datetime
    updated_at: datetime
