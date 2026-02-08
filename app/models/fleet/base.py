"""
Fleet Module Base Models and Mixins.

Provides base classes for all Fleet models.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class FleetBaseMixin:
    """
    Base mixin for all Fleet models.

    Provides:
    - Organization ID for multi-tenancy
    - Created/updated timestamps

    Note: Primary key should be defined in each model with the naming
    convention {entity}_id (e.g., vehicle_id, maintenance_id).
    """

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )
