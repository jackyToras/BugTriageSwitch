import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class ReportType(str, enum.Enum):
    bug = "bug"
    question = "question"
    request = "request"


class ReportStatus(str, enum.Enum):
    queued = "queued"
    triaging = "triaging"
    reproducing = "reproducing"
    needs_info = "needs_info"
    resolved = "resolved"
    failed = "failed"


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    environment: Mapped[str] = mapped_column(String(240), default="Not provided")
    reporter: Mapped[str] = mapped_column(String(80), default="You")
    category: Mapped[ReportType | None] = mapped_column(Enum(ReportType), nullable=True)
    priority: Mapped[str] = mapped_column(String(20), default="pending")
    status: Mapped[ReportStatus] = mapped_column(Enum(ReportStatus), default=ReportStatus.queued)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    reproduction: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    events: Mapped[list["ReportEvent"]] = relationship(back_populates="report", cascade="all, delete-orphan", order_by="ReportEvent.created_at")


class ReportEvent(Base):
    __tablename__ = "report_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), nullable=False)
    actor: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    report: Mapped[Report] = relationship(back_populates="events")


class SwitchMessageLink(Base):
    __tablename__ = "switch_message_links"
    message_id: Mapped[str] = mapped_column(String(240), primary_key=True)
    room_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, index=True)
