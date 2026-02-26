"""
Analysis Cube models for ad-hoc reporting.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AnalysisCube(Base):
    """Metadata for a materialized reporting cube."""

    __tablename__ = "analysis_cube"
    __table_args__ = {"schema": "rpt"}

    cube_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=True,
    )

    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_view: Mapped[str] = mapped_column(String(120), nullable=False)

    dimensions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    measures: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    default_rows: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    default_columns: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    default_measures: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)

    drill_down_url_template: Mapped[str | None] = mapped_column(
        String(300), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    refresh_interval_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=60,
        server_default=text("60"),
    )
    last_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class SavedAnalysis(Base):
    """User-saved cube query configuration."""

    __tablename__ = "saved_analysis"
    __table_args__ = {"schema": "rpt"}

    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
    )
    cube_code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    row_dimensions: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    column_dimensions: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    measures: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    filters: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)

    is_shared: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
