"""Add claim/lease and terminal-evidence columns to platform.event_outbox.

E3 (kernel-adoption plan): harden the finance outbox to applied-result
semantics with a claim/deliver/settle relay protocol.

Schema decision — columns, not new enum values:

- PENDING -> CLAIMED visibility is carried by the lease columns
  (``claim_token`` / ``claimed_at`` / ``lease_expires_at``): a PENDING or
  FAILED row with an unexpired lease is claimed; expired leases are
  reclaimable. This deliberately avoids adding a CLAIMED value to the
  ``event_status`` PostgreSQL enum — this repo family has a recurring
  defect class around ``ALTER TYPE ... ADD VALUE`` (autocommit blocks,
  re-emitted CREATE TYPE on sa.Enum columns), and the lease columns are
  the smaller correct schema.
- ``terminal_reason`` is a plain VARCHAR (values documented on
  ``TerminalReason`` in the model), not an enum, so dead-letter causes
  (unsupported_event, max_retries_exceeded, invalid_payload, ...) stay
  additive without any type migration.
- ``error_class`` records the exception class of the last delivery
  failure for triage/alerting.

All changes are additive and guarded, so the incremental upgrade path
from the current head is a no-op-safe ALTER; no enum type is touched.
"""

from __future__ import annotations

from sqlalchemy import inspect

from alembic import op

revision = "20260802_add_outbox_claim_lease_columns"
down_revision = "20260723_driver_fleet_rbac"
branch_labels = None
depends_on = None

_NEW_COLUMNS: dict[str, str] = {
    "error_class": "VARCHAR(200)",
    "terminal_reason": "VARCHAR(100)",
    "claimed_by": "VARCHAR(200)",
    "claim_token": "UUID",
    "claimed_at": "TIMESTAMPTZ",
    "lease_expires_at": "TIMESTAMPTZ",
}


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    columns = {c["name"] for c in insp.get_columns("event_outbox", schema="platform")}
    for name, ddl_type in _NEW_COLUMNS.items():
        if name not in columns:
            op.execute(
                f"ALTER TABLE platform.event_outbox ADD COLUMN {name} {ddl_type}"
            )

    indexes = {ix["name"] for ix in insp.get_indexes("event_outbox", schema="platform")}
    if "idx_outbox_claim" not in indexes:
        op.execute(
            "CREATE INDEX idx_outbox_claim"
            " ON platform.event_outbox (status, lease_expires_at)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    indexes = {ix["name"] for ix in insp.get_indexes("event_outbox", schema="platform")}
    if "idx_outbox_claim" in indexes:
        op.execute("DROP INDEX platform.idx_outbox_claim")

    columns = {c["name"] for c in insp.get_columns("event_outbox", schema="platform")}
    for name in _NEW_COLUMNS:
        if name in columns:
            op.execute(f"ALTER TABLE platform.event_outbox DROP COLUMN {name}")
