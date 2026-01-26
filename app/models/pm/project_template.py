"""
Project Template Model - PM Schema.

Defines reusable task templates that can be applied to new projects.
"""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.finance.core_org.project import ProjectType

if TYPE_CHECKING:
    from app.models.pm.project_template_task import ProjectTemplateTask


class ProjectTemplate(Base):
    """Reusable project template with ordered tasks."""

    __tablename__ = "project_template"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_project_template_name"),
        {"schema": "pm"},
    )

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    project_type: Mapped[ProjectType] = mapped_column(
        Enum(ProjectType, name="project_type", schema="pm"),
        nullable=False,
        default=ProjectType.INTERNAL,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    tasks: Mapped[List["ProjectTemplateTask"]] = relationship(
        "ProjectTemplateTask",
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="ProjectTemplateTask.order_index",
    )

    def __repr__(self) -> str:
        return f"<ProjectTemplate {self.name}>"
