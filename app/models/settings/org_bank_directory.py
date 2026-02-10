"""
Organization Bank Directory - Settings Schema.

Per-organization allowed banks for expense claim reimbursements.
"""

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.mixins import TimestampMixin


class OrgBankDirectory(Base, TimestampMixin):
    """
    Organization-specific allowed bank list.

    Stores bank name and sort code per organization for claim reimbursements.
    """

    __tablename__ = "org_bank_directory"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "bank_name",
            name="uq_org_bank_directory_org_bank_name",
        ),
        UniqueConstraint(
            "organization_id",
            "bank_sort_code",
            name="uq_org_bank_directory_org_bank_sort_code",
        ),
        Index("ix_org_bank_directory_org", "organization_id"),
        {"schema": "settings"},
    )

    org_bank_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id", ondelete="CASCADE"),
        nullable=False,
    )
    bank_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    bank_sort_code: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="Bank sort code / bank code",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
