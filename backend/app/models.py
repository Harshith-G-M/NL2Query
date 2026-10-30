from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, deferred, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    datasets: Mapped[list["Dataset"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Dataset(Base):
    """Metadata for an uploaded file. The original bytes are kept (deferred) so the DuckDB
    table can be rebuilt if the container's disk is ephemeral."""

    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("user_id", "table_name", name="uq_user_table"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    table_name: Mapped[str] = mapped_column(String(128))
    filename: Mapped[str] = mapped_column(String(255))
    file_ext: Mapped[str] = mapped_column(String(10))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    profile: Mapped[list] = mapped_column(JSON, default=list)  # per-column profile
    file_blob: Mapped[bytes | None] = deferred(mapped_column(LargeBinary, nullable=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="datasets")


class QueryLog(Base):
    """One row per question: the result plus every operational metric we monitor."""

    __tablename__ = "query_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("query_logs.id"), nullable=True)
    question: Mapped[str] = mapped_column(Text)
    route: Mapped[str] = mapped_column(String(20), default="data")
    sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), index=True)  # ok | error
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    result_preview: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    chart: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    provider: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(80), default="")
    feedback: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1 / -1
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
