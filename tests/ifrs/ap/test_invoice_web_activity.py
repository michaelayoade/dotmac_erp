from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.models.finance.audit.audit_log import AuditAction
from app.services.finance.ap.web.base import recent_activity_view


def test_recent_activity_view_formats_entries(monkeypatch):
    db = MagicMock()
    user_id = uuid4()

    log_row = SimpleNamespace(
        audit_id=uuid4(),
        occurred_at=datetime(2026, 1, 1, 9, 30, tzinfo=UTC),
        action=AuditAction.UPDATE,
        user_id=user_id,
        changed_fields=["status", "due_date", "total_amount", "currency_code"],
        reason="Corrected due date",
        ip_address="127.0.0.1",
        correlation_id="req-123",
    )
    db.scalars.return_value.all.return_value = [log_row]

    class _FakeAuditService:
        @staticmethod
        def get_user_names_batch(user_ids):
            return {user_id: "Ada Lovelace"}

    monkeypatch.setattr(
        "app.services.recent_activity.get_audit_service",
        lambda _db: _FakeAuditService(),
    )

    entries = recent_activity_view(
        db=db,
        organization_id=uuid4(),
        table_schema="ap",
        table_name="supplier_invoice",
        record_id=str(uuid4()),
        limit=10,
    )

    assert len(entries) == 1
    entry = entries[0]
    assert entry["action"] == "UPDATE"
    assert entry["action_label"] == "Updated"
    assert entry["actor_name"] == "Ada Lovelace"
    assert entry["changed_fields_label"] == "status, due_date, total_amount (+1 more)"
    assert entry["reason"] == "Corrected due date"
    assert entry["ip_address"] == "127.0.0.1"
    assert entry["correlation_id"] == "req-123"
