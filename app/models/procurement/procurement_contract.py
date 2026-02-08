"""
Procurement Contract Model - proc Schema.

Contract awarded after bid evaluation.
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.procurement.base import ProcurementBaseMixin
from app.models.procurement.enums import ContractStatus


class ProcurementContract(Base, ProcurementBaseMixin):
    """
    Procurement contract.

    Represents a formal contract awarded to a vendor after
    the bid evaluation process, with tracking for payments,
    performance bonds, and completion.
    """

    __tablename__ = "procurement_contract"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "contract_number",
            name="uq_proc_contract_org_number",
        ),
        Index("idx_proc_contract_status", "organization_id", "status"),
        Index("idx_proc_contract_supplier", "supplier_id"),
        Index("idx_proc_contract_dates", "organization_id", "start_date", "end_date"),
        {"schema": "proc"},
    )

    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    contract_number: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="ap.supplier",
    )
    rfq_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Source RFQ",
    )
    evaluation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Source bid evaluation",
    )
    purchase_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Generated PO (ap.purchase_order)",
    )
    contract_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    start_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    end_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    contract_value: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
    )
    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="NGN",
    )
    status: Mapped[ContractStatus] = mapped_column(
        default=ContractStatus.DRAFT,
    )
    bpp_clearance_number: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="BPP certificate of no objection number",
    )
    bpp_clearance_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    payment_terms: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    terms_and_conditions: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    performance_bond_required: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    performance_bond_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )
    retention_percentage: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )
    amount_paid: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=Decimal("0"),
    )
    completion_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Actual completion date",
    )
    completion_certificate_issued: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
