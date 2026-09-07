from __future__ import annotations

from unittest.mock import MagicMock

from app.services.dotmac_sub.sync._credit_notes import CreditNoteSyncMixin
from app.services.dotmac_sub.sync._types import SyncConfirmation, SyncResult


def test_committed_credit_note_confirmation_has_traceable_ids(monkeypatch) -> None:
    import app.services.dotmac_sub.sync._credit_notes as credit_notes_module

    logger = MagicMock()
    monkeypatch.setattr(credit_notes_module, "logger", logger)
    result = SyncResult(
        success=True,
        entity_type="credit_notes",
        confirmations=[
            SyncConfirmation(
                source_id="source-cn-1",
                local_id="erp-cn-1",
                journal_entry_id="journal-1",
                action="created",
            )
        ],
    )

    CreditNoteSyncMixin._log_committed_credit_note_confirmations(result)

    _, kwargs = logger.info.call_args
    assert kwargs["extra"] == {
        "event": "dotmac_sub_credit_note_projection_committed",
        "source_credit_note_id": "source-cn-1",
        "erp_credit_note_id": "erp-cn-1",
        "journal_entry_id": "journal-1",
        "external_sync_mapping_recorded": True,
        "action": "created",
    }
    assert result.confirmations == []


def test_confirmation_is_not_exposed_in_task_result_payload() -> None:
    result = SyncResult(
        success=True,
        entity_type="credit_notes",
        confirmations=[
            SyncConfirmation(
                source_id="source-cn-1",
                local_id="erp-cn-1",
                journal_entry_id=None,
                action="unchanged",
            )
        ],
    )

    assert "confirmations" not in result.to_dict()
