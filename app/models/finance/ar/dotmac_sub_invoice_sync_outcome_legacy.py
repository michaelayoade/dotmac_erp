"""Frozen, read-only archive of pre-canonical Self-Care invoice sync outcomes.

TEST-ONLY. Production code never imports or queries this module — it exists
solely so tests can construct a legacy row at a colliding key and prove the
canonical write path (``app.services.dotmac_sub.invoice_sync_outcomes``,
which maps to ``DotmacSubInvoiceSyncOutcome`` in
``dotmac_sub_invoice_sync_outcome.py``) is unaffected by its presence.

These classes are mapped to the tables the
``20260918_invoice_sync_canonical_evidence`` migration renamed out of the
live path: ``ar.dotmac_sub_invoice_sync_outcome_legacy`` and
``ar.dotmac_sub_invoice_sync_issue_legacy``. Every row in them was written by
ERP's now-deleted, confirmed-buggy local fingerprint algorithm — never
forwarded from Self-Care's canonical digest — and is historically
unverifiable (ERP never archived the original full projection that produced
a stored ``projection_fingerprint``). They are preserved exactly as written,
forever, and are structurally excluded from ever being treated as canonical
evidence, parity proof, or cutover-success proof. ``app_user`` holds SELECT
only on the real tables (INSERT/UPDATE/DELETE/TRUNCATE are revoked) — these
ORM classes carry no such enforcement themselves; the privilege boundary is
proven against a real PostgreSQL database in
``tests/integration/test_dotmac_sub_invoice_sync_outcome_legacy_frozen.py``.

Column shape is the CURRENT (pre-``20260918``) shape of
``DotmacSubInvoiceSyncOutcome``/``DotmacSubInvoiceSyncIssue`` — no
``digest_version`` column, since legacy rows never had one.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class DotmacSubInvoiceSyncOutcomeLegacy(Base):
    """One frozen, historically-unverifiable legacy invoice sync outcome."""

    __tablename__ = "dotmac_sub_invoice_sync_outcome_legacy"
    __table_args__ = (
        # CHECK/FK constraint names are unique per RELATION, not per schema, so
        # the migration keeps their original (pre-rename) names — only the two
        # UniqueConstraints below collided with the fresh canonical table's
        # names and were renamed. Mirror that exactly.
        ForeignKeyConstraint(
            ["organization_id"],
            ["core_org.organization.organization_id"],
            name="fk_sub_invoice_outcome_org",
        ),
        UniqueConstraint(
            "organization_id",
            "source_invoice_id",
            "source_updated_at",
            name="uq_sub_invoice_outcome_legacy_org_revision",
        ),
        UniqueConstraint(
            "outcome_id",
            "organization_id",
            name="uq_sub_invoice_outcome_legacy_id_org",
        ),
        CheckConstraint(
            "disposition IN ('ready', 'blocked', 'not_applicable')",
            name="ck_sub_invoice_outcome_disposition",
        ),
        CheckConstraint(
            "contract_version = 'invoice-accounting-sync.v2'",
            name="ck_sub_invoice_outcome_contract",
        ),
        CheckConstraint(
            "source_kind IN ('native', 'splynx_legacy')",
            name="ck_sub_invoice_outcome_source_kind",
        ),
        CheckConstraint(
            "length(projection_fingerprint) = 64",
            name="ck_sub_invoice_outcome_fingerprint",
        ),
        CheckConstraint(
            "(disposition = 'blocked' AND issue_count > 0) OR "
            "(disposition <> 'blocked' AND issue_count = 0)",
            name="ck_sub_invoice_outcome_issue_count",
        ),
        CheckConstraint(
            "occurrence_count > 0",
            name="ck_sub_invoice_outcome_occurrences",
        ),
        {"schema": "ar"},
    )

    outcome_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    source_invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    source_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    contract_version: Mapped[str] = mapped_column(String(80), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    disposition: Mapped[str] = mapped_column(String(24), nullable=False)
    projection_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    issue_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    issues: Mapped[list[DotmacSubInvoiceSyncIssueLegacy]] = relationship(
        back_populates="outcome",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class DotmacSubInvoiceSyncIssueLegacy(Base):
    """Normalized, non-PII evidence for one frozen legacy blocked outcome."""

    __tablename__ = "dotmac_sub_invoice_sync_issue_legacy"
    __table_args__ = (
        ForeignKeyConstraint(
            ["outcome_id", "organization_id"],
            [
                "ar.dotmac_sub_invoice_sync_outcome_legacy.outcome_id",
                "ar.dotmac_sub_invoice_sync_outcome_legacy.organization_id",
            ],
            ondelete="CASCADE",
            name="fk_sub_invoice_issue_outcome_org",
        ),
        UniqueConstraint(
            "outcome_id",
            "issue_fingerprint",
            name="uq_sub_invoice_issue_legacy_outcome_fingerprint",
        ),
        CheckConstraint(
            "expected_amount IS NOT NULL OR actual_amount IS NOT NULL "
            "OR source_line_id IS NOT NULL OR issue_code <> ''",
            name="ck_sub_invoice_issue_has_evidence",
        ),
        {"schema": "ar"},
    )

    issue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    outcome_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    issue_code: Mapped[str] = mapped_column(String(80), nullable=False)
    source_line_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    expected_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    actual_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    issue_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    outcome: Mapped[DotmacSubInvoiceSyncOutcomeLegacy] = relationship(
        back_populates="issues"
    )
