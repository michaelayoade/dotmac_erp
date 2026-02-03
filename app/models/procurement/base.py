"""
Procurement Module Base Models and Mixins.

Provides base classes for all Procurement models.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class ProcurementBaseMixin:
    """
    Base mixin for all Procurement models.

    Provides:
    - Organization ID for multi-tenancy
    - Created/updated timestamps

    Note: Primary key should be defined in each model with the naming
    convention {entity}_id (e.g., plan_id, requisition_id).
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
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )
