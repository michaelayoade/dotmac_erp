"""
Reconciliation Match Rule Models.

Configurable rules for matching bank statement lines to source documents
(payments, invoices, fees). Separate from TransactionRule which handles
GL account categorization.
"""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as SAUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SourceDocType(str, enum.Enum):
    """What type of source document a rule matches against."""

    CUSTOMER_PAYMENT = "CUSTOMER_PAYMENT"
    SUPPLIER_PAYMENT = "SUPPLIER_PAYMENT"
    INVOICE = "INVOICE"
    PAYMENT_INTENT = "PAYMENT_INTENT"
    BANK_FEE = "BANK_FEE"
    INTER_BANK = "INTER_BANK"
    EXPENSE = "EXPENSE"


class MatchOperator(str, enum.Enum):
    """Operators for condition matching."""

    EQUALS = "EQUALS"
    CONTAINS = "CONTAINS"
    STARTS_WITH = "STARTS_WITH"
    REGEX = "REGEX"
    BETWEEN = "BETWEEN"
    GREATER_THAN = "GREATER_THAN"
    LESS_THAN = "LESS_THAN"


class ReconciliationMatchRule(Base):
    """Configurable rule for matching bank statement lines to source documents."""

    __tablename__ = "reconciliation_match_rule"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_recon_match_rule_name"),
        Index("ix_recon_match_rule_org", "organization_id"),
        {"schema": "banking"},
    )

    rule_id: Mapped[UUID] = mapped_column(
        SAUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[UUID] = mapped_column(
        SAUUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    source_doc_type: Mapped[str] = mapped_column(String(50), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Match conditions: [{"field": "DESCRIPTION", "operator": "REGEX", "value": "..."}]
    conditions: Mapped[list | None] = mapped_column(JSONB, nullable=True, default=list)

    # Transaction direction filter
    match_debit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    match_credit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Override global tolerances for this rule
    amount_tolerance_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    date_window_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Action on match
    action_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="MATCH"
    )
    min_confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=90)

    # For BANK_FEE / CREATE_JOURNAL rules
    writeoff_account_id: Mapped[UUID | None] = mapped_column(
        SAUUID(as_uuid=True),
        ForeignKey("gl.account.account_id"),
        nullable=True,
    )
    journal_label_template: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )

    # Analytics
    match_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_matched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<ReconciliationMatchRule {self.name!r} priority={self.priority}>"


class ReconciliationMatchLog(Base):
    """Audit trail: which rule matched which statement line, and why."""

    __tablename__ = "reconciliation_match_log"
    __table_args__ = (
        Index("ix_recon_match_log_org_date", "organization_id", "matched_at"),
        Index("ix_recon_match_log_rule", "rule_id"),
        Index("ix_recon_match_log_line", "statement_line_id"),
        {"schema": "banking"},
    )

    log_id: Mapped[UUID] = mapped_column(
        SAUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[UUID] = mapped_column(SAUUID(as_uuid=True), nullable=False)
    rule_id: Mapped[UUID | None] = mapped_column(
        SAUUID(as_uuid=True),
        ForeignKey("banking.reconciliation_match_rule.rule_id"),
        nullable=True,
    )

    statement_line_id: Mapped[UUID] = mapped_column(
        SAUUID(as_uuid=True),
        ForeignKey("banking.bank_statement_lines.line_id", ondelete="CASCADE"),
        nullable=False,
    )
    source_doc_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_doc_id: Mapped[UUID | None] = mapped_column(
        SAUUID(as_uuid=True), nullable=True
    )
    journal_line_id: Mapped[UUID | None] = mapped_column(
        SAUUID(as_uuid=True), nullable=True
    )

    confidence_score: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    action_taken: Mapped[str] = mapped_column(String(30), nullable=False)
    matched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # User confirmation (for SUGGESTED matches)
    confirmed_by_id: Mapped[UUID | None] = mapped_column(
        SAUUID(as_uuid=True), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<ReconciliationMatchLog line={self.statement_line_id} "
            f"action={self.action_taken}>"
        )
