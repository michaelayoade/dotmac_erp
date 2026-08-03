"""C5 regression: credit notes honour the sync cutoff and use their real date.

Credit notes were stamped ``date.today()`` (wrong fiscal period) and skipped the
``DOTMAC_SUB_SYNC_MIN_DATE`` guard that invoices/payments apply — so a pre-cutoff
credit note imported without its invoice. Now the sync parses the credit note's
own ``issued_at`` and skips anything before the cutoff.

The cutoff test doubles as proof the real date is used: a pre-cutoff ``issued_at``
only triggers the skip if the code reads ``issued_at`` rather than today().
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.models.finance.ar.external_sync import EntityType
from app.services.dotmac_sub.client import CreditNoteRecord
from app.services.dotmac_sub.sync._constants import _PRE_CUTOFF_SENTINEL
from app.services.dotmac_sub.sync._credit_notes import CreditNoteSyncMixin
from app.services.dotmac_sub.sync._types import SyncResult


class _Harness(CreditNoteSyncMixin):
    def __init__(self):
        self.organization_id = uuid.uuid4()
        self.recorded: list[tuple] = []

    def _compute_hash(self, _data):
        return "hash"

    def _has_changed(self, _et, _eid, _hash):
        return True

    def _get_customer_for_account(self, _account):
        return uuid.uuid4()

    def _record_sync(self, entity_type, external_id, local_id=None, data_hash=None):
        self.recorded.append((entity_type, external_id, local_id))

    def _get_synced_entity(self, _et, _eid):
        return None


def _credit_note(issued_at: datetime | None) -> CreditNoteRecord:
    return CreditNoteRecord(
        id="cn-1",
        account_id="acc-1",
        invoice_id="inv-1",
        credit_number="CN-1",
        status="issued",
        currency="NGN",
        subtotal=Decimal("10"),
        tax_total=Decimal("0"),
        total=Decimal("10"),
        issued_at=issued_at,
    )


def test_pre_cutoff_credit_note_is_skipped_by_its_real_date():
    harness = _Harness()
    result = SyncResult(success=True, entity_type="credit_notes")

    # 2025-06-01 is before DOTMAC_SUB_SYNC_MIN_DATE (2026-01-01).
    harness._sync_single_credit_note(
        _credit_note(datetime(2025, 6, 1, tzinfo=timezone.utc)),
        None,
        result,
        skip_unchanged=True,
    )

    assert result.skipped == 1
    assert (EntityType.CREDIT_NOTE, "cn-1", _PRE_CUTOFF_SENTINEL) in harness.recorded
