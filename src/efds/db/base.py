"""SQLAlchemy declarative base and common timestamp behavior."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base for all EFDS ORM models."""


class TimestampMixin:
    """Application-managed timezone-aware creation and update timestamps."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )


@event.listens_for(Base, "before_update", propagate=True)
def _set_updated_at(_mapper: object, _connection: object, target: object) -> None:
    """Keep ``updated_at`` current without introducing database triggers in V1."""

    if hasattr(target, "updated_at"):
        setattr(target, "updated_at", datetime.now(timezone.utc))

