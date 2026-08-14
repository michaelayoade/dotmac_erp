"""A stored webhook payload must never be re-dispatched to a payment handler.

Signature verification happens once, on the inbound request, against the raw
request body. Before 2026-08-14 `retry_failed_webhook` reset a FAILED webhook to
RECEIVED and re-dispatched `_handle_charge_success` and its siblings from
`webhook.payload` — a row in our own database — with no re-verification anywhere
in the path. Anyone able to influence a stored payload and reach that method
could execute charge, transfer and reversal handlers on data Paystack never sent.

This is a canary, not a unit test of a raise. It is written to run on BOTH the
unsafe parent and the fixed commit: the service is handed a mock FAILED webhook
and a mock intent, so on the unsafe implementation it drives all the way into a
payment handler and fails with a message naming what it reached, and on the
fixed implementation it refuses before touching the database at all.

The guard is looked up with `getattr` rather than imported directly for exactly
that reason — an import would make the parent fail at collection time, which
proves nothing about behaviour.
"""

from __future__ import annotations

from unittest.mock import DEFAULT, MagicMock, patch
from uuid import uuid4

import pytest

from app.models.finance.payments.payment_webhook import WebhookStatus
from app.services.finance.payments import webhook_service as ws

# Every event the retry path used to dispatch from a stored payload, and the
# handler it reached. Parameterized so a newly added handler that forgets the
# guard fails on its own row rather than hiding inside someone else's.
EVENT_HANDLERS = [
    ("charge.success", "_handle_charge_success"),
    ("charge.failed", "_handle_charge_failed"),
    ("transfer.success", "_handle_transfer_success"),
    ("transfer.failed", "_handle_transfer_failed"),
    ("transfer.reversed", "_handle_transfer_reversed"),
]

PATCHED_ATTRS = {handler: DEFAULT for _, handler in EVENT_HANDLERS}
PATCHED_ATTRS["_commit_and_refresh"] = DEFAULT


def _service_primed_for_replay(event_type: str) -> tuple[ws.WebhookService, MagicMock]:
    """A service whose database would happily serve a replayable webhook.

    Everything the old code path needed is present and valid: a FAILED webhook
    with a payload, and a matching PaymentIntent. If the method reads any of it,
    the mocks record that it did.
    """
    service = ws.WebhookService.__new__(ws.WebhookService)

    webhook = MagicMock()
    webhook.status = WebhookStatus.FAILED
    webhook.event_type = event_type
    webhook.payload = {"data": {"amount": 10_000_00, "reference": "replayed"}}
    webhook.retry_count = 0

    db = MagicMock()
    db.get.return_value = webhook
    db.scalar.return_value = MagicMock()  # a matching PaymentIntent
    service.db = db

    return service, db


@pytest.mark.parametrize(("event_type", "handler"), EVENT_HANDLERS)
def test_retry_refuses_before_reaching_a_payment_handler(
    event_type: str, handler: str
) -> None:
    """Retry must refuse before any DB read, and reach no money-moving handler."""
    service, db = _service_primed_for_replay(event_type)
    guard = getattr(ws, "WebhookRetryDisabledError", None)

    with patch.multiple(ws.WebhookService, **PATCHED_ATTRS) as mocks:
        if guard is None:
            # Unsafe implementation: no guard exists. Drive the path and report
            # exactly what it reached, so the failure is evidence rather than
            # an assertion about a missing symbol.
            service.retry_failed_webhook(uuid4())
            pytest.fail(
                "UNSAFE: retry_failed_webhook has no guard. "
                f"For {event_type!r} it reached {handler}="
                f"{mocks[handler].called}, after reading the stored webhook "
                f"(db.get called={db.get.called}) and its intent "
                f"(db.scalar called={db.scalar.called}). A stored payload "
                "carries no re-verified signature."
            )

        with pytest.raises(guard):
            service.retry_failed_webhook(uuid4())

        assert not mocks[handler].called, (
            f"{handler} was reached through the retry path for {event_type!r}. "
            "A stored payload carries no re-verified signature, so reaching a "
            "payment handler from it moves money on unverified data."
        )
        assert not db.get.called and not db.scalar.called, (
            "retry_failed_webhook touched the database before refusing. It must "
            "refuse first, so no stored payload can influence its behaviour at "
            f"all (db.get={db.get.called}, db.scalar={db.scalar.called})."
        )
