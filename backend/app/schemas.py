from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RegisterIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=255)
    password: str = Field(min_length=8, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: int
    email: str
    is_admin: bool


class DatasetOut(BaseModel):
    id: int
    name: str
    table_name: str
    filename: str
    row_count: int
    columns: list[dict]
    created_at: datetime


class QueryIn(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    parent_query_id: int | None = None  # set for follow-up questions


class QueryOut(BaseModel):
    id: int
    question: str
    route: str
    status: Literal["ok", "error"]
    answer: str | None
    sql: str | None
    columns: list[str] = []
    rows: list[list] = []
    truncated: bool = False
    chart: dict | None = None
    insights: list[dict] = []
    attempts: int
    attempt_log: list[dict] = []
    error: str | None = None
    latency_ms: int
    llm_calls: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    provider: str
    model: str


class HistoryItem(BaseModel):
    id: int
    question: str
    sql: str | None
    answer: str | None
    status: str
    route: str
    latency_ms: int
    feedback: int | None
    created_at: datetime
    columns: list[str] = []
    rows: list[list] = []
    chart: dict | None = None


class FeedbackIn(BaseModel):
    value: Literal[1, -1]
