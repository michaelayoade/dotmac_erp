"""Separate remote quarantine evidence from its database write boundary."""

from pathlib import Path

BRANCH = "fix/erp-invoice-quarantine-io-budget"
TITLE = "fix(sync): release transactions before bounded quarantine evidence IO"
TESTS = ["tests/services/test_invoice_quarantine_io_boundaries.py"]
TESTS += [str(path) for path in Path("tests/services").glob("*invoice*.py") if "dotmac_sub" in path.name]


def apply(change, write, rewrite):
    shadow = "app/services/dotmac_sub/invoice_sync_shadow.py"
    def fetch_only(source):
        source = source.replace("def record_blocked_invoice_accounting_revision(\n    db: Session,", "def fetch_blocked_invoice_accounting_revision(")
        source = source.replace(") -> InvoiceSyncOutcomeReceipt:", ") -> RecordInvoiceSyncOutcome:")
        source = source.replace('"""Persist one exact blocked v2 revision through the durable outcome owner.', '"""Fetch and validate one exact blocked v2 revision without database access.')
        source = source.replace("    return record_invoice_sync_outcome(db, command)", "    return command")
        return source
    rewrite(shadow, "record_blocked_invoice_accounting_revision", fetch_only)
    change(shadow, "def observe_invoice_accounting_v2(\n", '''def record_blocked_invoice_accounting_revision(
    db: Session,
    client: DotmacSubClient,
    organization_id: UUID,
    *,
    invoice_id: UUID,
    expected_updated_at: datetime,
) -> InvoiceSyncOutcomeReceipt:
    """Compatibility owner for callers already controlling their IO boundary."""
    command = fetch_blocked_invoice_accounting_revision(
        client, organization_id,
        invoice_id=invoice_id, expected_updated_at=expected_updated_at,
    )
    return record_invoice_sync_outcome(db, command)


def observe_invoice_accounting_v2(
''')
    path = "app/services/dotmac_sub/sync/_invoices.py"
    change(path, "import logging\n", "import logging\nimport time\n\nfrom billiard.exceptions import SoftTimeLimitExceeded\n")
    change(path, "    DotmacSubError,\n", "    DotmacSubError,\n    DotmacSubRateLimitError,\n")
    change(path, '''from app.services.dotmac_sub.invoice_sync_shadow import (
    record_blocked_invoice_accounting_revision,
)''', '''from app.services.dotmac_sub.invoice_sync_shadow import (
    fetch_blocked_invoice_accounting_revision,
)
from app.services.dotmac_sub.invoice_sync_outcomes import record_invoice_sync_outcome''')
    change(path, "_QUARANTINE_EVIDENCE_LIMIT = 50", '''_QUARANTINE_EVIDENCE_LIMIT = 50
# Checked between evidence requests; an individual request keeps the existing
# integration client's timeout and Retry-After limits.
_QUARANTINE_REMOTE_BUDGET_SECONDS = 120.0''')
    change(path, "        quarantined = 0\n", '''        quarantined = 0
        quarantine_remote_seconds = 0.0
        quarantine_stop_reason: str | None = None
''')
    change(path, '''                    if isinstance(e, InvoiceSourceAccountingMismatchError):
                        quarantine_savepoint = None''', '''                    if isinstance(e, InvoiceSourceAccountingMismatchError):
                        if quarantine_remote_seconds >= _QUARANTINE_REMOTE_BUDGET_SECONDS:
                            quarantine_stop_reason = "invoice quarantine remote evidence time budget reached"
                            quarantine_limit_reached = True
                            progress.record_failure(row_updated_at, inv.id)
                            break
                        # Release prior accepted writes before remote IO. Do NOT
                        # advance the cursor here: a later unpositioned parse
                        # failure must still freeze the original run position.
                        # Committed rows/evidence can be safely replayed if the
                        # process stops before the final cursor transaction.
                        self.db.commit()
                        quarantine_savepoint = None''')
    change(path, '''                            quarantine_savepoint = self.db.begin_nested()
                            receipt = record_blocked_invoice_accounting_revision(
                                self.db,
                                self.client,
                                self.organization_id,
                                invoice_id=UUID(inv.id),
                                expected_updated_at=row_updated_at,
                            )
                            quarantine_savepoint.commit()
                        except Exception:  # noqa: BLE001''', '''                            evidence_started = time.monotonic()
                            try:
                                command = fetch_blocked_invoice_accounting_revision(
                                    self.client,
                                    self.organization_id,
                                    invoice_id=UUID(inv.id),
                                    expected_updated_at=row_updated_at,
                                )
                            finally:
                                quarantine_remote_seconds += time.monotonic() - evidence_started
                            self._reprime_tenant_context()
                            quarantine_savepoint = self.db.begin_nested()
                            receipt = record_invoice_sync_outcome(self.db, command)
                            quarantine_savepoint.commit()
                        except (DotmacSubAuthenticationError, SoftTimeLimitExceeded, OperationalError):
                            raise
                        except DotmacSubRateLimitError:
                            quarantine_stop_reason = "invoice quarantine deferred after upstream rate limit"
                            quarantine_limit_reached = True
                            progress.record_failure(row_updated_at, inv.id)
                            logger.warning(
                                "Stopping invoice quarantine work after upstream throttling; cursor remains safe",
                                extra={"event": "dotmac_sub_invoice_quarantine_throttled", "source_invoice_id": inv.id},
                            )
                            break
                        except Exception:  # noqa: BLE001''')
    change(path, '''                        # The durable blocked outcome and cursor update share
                        # the caller's transaction. A corrected source revision
                        # has a later updated_at and will be considered again.''', '''                        # Evidence is either in the final cursor transaction
                        # or already durable from a preceding IO checkpoint.
                        # The cursor never commits ahead of its evidence. A
                        # corrected source revision is considered again.''')
    change(path, '''            if quarantine_limit_reached:
                suffixes.append(
                    "invoice quarantine evidence work limit "
                    f"({_QUARANTINE_EVIDENCE_LIMIT}) reached"
                )''', '''            if quarantine_limit_reached:
                suffixes.append(
                    quarantine_stop_reason
                    or "invoice quarantine evidence work limit "
                    f"({_QUARANTINE_EVIDENCE_LIMIT}) reached"
                )''')
    write("tests/services/test_invoice_quarantine_io_boundaries.py", '''"""Remote evidence must not hold a write transaction or bypass safe cursors."""

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.services.dotmac_sub.sync import _invoices as invoices
from app.services.dotmac_sub.sync._base import (
    InvoiceSourceAccountingMismatchError,
    SyncWatermarkPosition,
)


def setup(monkeypatch, *, late_parse_failure=False):
    state = {"transaction": True, "events": []}
    service = invoices.InvoiceSyncMixin()
    service.organization_id = uuid4()
    service.db = MagicMock()
    row = SimpleNamespace(
        id=str(uuid4()), updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        invoice_number="SYNTHETIC-1", currency="NGN",
    )
    service.client = MagicMock()
    def rows(**kwargs):
        yield row
        if late_parse_failure:
            kwargs["on_parse_error"](SimpleNamespace(updated_at=None))
    service.client.get_invoices.side_effect = rows
    service._get_sync_watermark_position = lambda _: SyncWatermarkPosition(None, None)
    service._advance_sync_watermark_position = MagicMock()
    failure = InvoiceSourceAccountingMismatchError(
        "Synthetic source mismatch", dedupe_key=("synthetic",),
        line_subtotal=Decimal("100"), line_tax=Decimal("0"),
        header_subtotal=Decimal("100"), header_tax=Decimal("7.5"),
        header_total=Decimal("107.5"),
    )
    service._sync_single_invoice = MagicMock(side_effect=failure)
    def commit():
        state["transaction"] = False
        state["events"].append("commit")
    service.db.commit.side_effect = commit
    def begin():
        state["transaction"] = True
        state["events"].append("savepoint")
        return MagicMock()
    service.db.begin_nested.side_effect = begin
    def prime():
        state["transaction"] = True
        state["events"].append("prime")
    service._reprime_tenant_context = prime
    def fetch(*args, **kwargs):
        assert state["transaction"] is False
        service._advance_sync_watermark_position.assert_not_called()
        state["events"].append("fetch")
        return SimpleNamespace()
    fetch_mock = MagicMock(side_effect=fetch)
    def record(*args):
        assert state["transaction"] is True
        state["events"].append("record")
        return SimpleNamespace(outcome_id=uuid4())
    monkeypatch.setattr(invoices, "fetch_blocked_invoice_accounting_revision", fetch_mock)
    monkeypatch.setattr(invoices, "record_invoice_sync_outcome", record)
    return service, row, state, fetch_mock


def test_remote_lookup_occurs_between_commit_and_new_savepoint(monkeypatch):
    service, row, state, _ = setup(monkeypatch)
    result = service.sync_invoices()
    assert result.skipped == 1
    assert state["events"] == ["savepoint", "commit", "fetch", "prime", "savepoint", "record"]
    cursor = service._advance_sync_watermark_position.call_args.args[1]
    assert cursor.external_id == row.id


def test_later_unpositioned_failure_still_freezes_original_cursor(monkeypatch):
    service, _, state, _ = setup(monkeypatch, late_parse_failure=True)
    result = service.sync_invoices()
    assert result.skipped == 1
    assert "record" in state["events"]
    service._advance_sync_watermark_position.assert_not_called()


def test_exhausted_remote_budget_stops_before_another_lookup(monkeypatch):
    service, _, _, fetch = setup(monkeypatch)
    monkeypatch.setattr(invoices, "_QUARANTINE_REMOTE_BUDGET_SECONDS", 0)
    result = service.sync_invoices()
    fetch.assert_not_called()
    assert "time budget" in result.message
    cursor = service._advance_sync_watermark_position.call_args.args[1]
    assert cursor.watermark_at is None
    assert cursor.external_id is None


def test_upstream_throttle_defers_batch_without_skipping_source_revision(monkeypatch):
    service, _, _, fetch = setup(monkeypatch)
    fetch.side_effect = invoices.DotmacSubRateLimitError("synthetic throttle")
    result = service.sync_invoices()
    assert fetch.call_count == 1
    assert result.skipped == 0
    assert "rate limit" in result.message
    cursor = service._advance_sync_watermark_position.call_args.args[1]
    assert cursor.external_id is None


def test_outcome_conflict_keeps_revision_unconsumed(monkeypatch):
    from app.services.dotmac_sub.invoice_sync_outcomes import InvoiceSyncOutcomeError
    service, _, _, _ = setup(monkeypatch)
    monkeypatch.setattr(invoices, "record_invoice_sync_outcome", MagicMock(side_effect=InvoiceSyncOutcomeError("synthetic conflict")))
    result = service.sync_invoices()
    assert result.skipped == 0
    assert result.errors
    cursor = service._advance_sync_watermark_position.call_args.args[1]
    assert cursor.external_id is None


def test_database_checkpoint_error_aborts_instead_of_being_quarantined(monkeypatch):
    from sqlalchemy.exc import OperationalError
    service, _, _, fetch = setup(monkeypatch)
    service.db.commit.side_effect = OperationalError("synthetic", {}, RuntimeError("db"))
    with pytest.raises(OperationalError):
        service.sync_invoices()
    fetch.assert_not_called()
''')
    write("docs/runbooks/invoice-quarantine-io.md", '''# Invoice quarantine IO boundary

The invoice consumer commits already accepted row work before requesting remote
v2 quarantine evidence. The compound cursor deliberately stays unchanged at
these checkpoints: a later unpositioned parse failure still freezes the run's
original cursor. Interrupted work can therefore replay committed rows/outcomes
through existing idempotency checks, but cannot skip missing evidence.

Remote evidence is fetched and validated without an open ORM transaction. Tenant
scope is re-armed before a fresh savepoint persists the outcome. Final cursor
advancement never commits ahead of its supporting evidence. Intermediate
checkpoints may make evidence durable before the cursor, which is safe replay,
not an assertion that an entire sync phase is atomic.

The existing 50-quarantine limit remains. A cumulative 120-second remote evidence
budget is checked between requests, and an exhausted upstream rate limit ends
the batch rather than issuing another request for every remaining bad row.
An individual request still obeys the existing client's timeouts and Retry-After
policy. This does not guarantee an exact 120-second phase duration.

No source totals, fingerprints, blocked-revision conflicts or validation rules
are bypassed. Actual historical source discrepancies still need correction.
''')
