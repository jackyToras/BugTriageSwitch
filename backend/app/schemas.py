from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class ReportCreate(BaseModel):
    title: str = Field(min_length=5, max_length=180)
    description: str = Field(min_length=10, max_length=6000)
    environment: str = Field(default="Not provided", max_length=240)
    reporter: str = Field(default="You", max_length=80)


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    actor: str
    kind: str
    message: str
    created_at: datetime


class ReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    description: str
    environment: str
    reporter: str
    category: str | None
    priority: str
    status: str
    summary: str | None
    reproduction: str | None
    result: str | None
    created_at: datetime
    updated_at: datetime
    events: list[EventRead]


class SwitchEventIn(BaseModel):
    event: dict
    adapter_agent_id: str | None = None
