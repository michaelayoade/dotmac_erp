"""
Tests for banking Celery tasks.

Verifies the periodic auto-match task processes statements
correctly, including per-statement commit/rollback isolation.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# ── Helpers ──────────────────────────────────────────────────────────


def _mock_statement(
    org_id: uuid.UUID | None = None,
    unmatched_lines: int = 5,
) -> SimpleNamespace:
    return SimpleNamespace(
        statement_id=uuid.uuid4(),
        organization_id=org_id or uuid.uuid4(),
        unmatched_lines=unmatched_lines,
    )


def _mock_match_result(
    matched: int = 0,
    skipped: int = 0,
    errors: list[str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        matched=matched,
        skipped=skipped,
        errors=errors or [],
    )


# ── Tests ────────────────────────────────────────────────────────────


class TestAutoMatchUnreconciledStatements:
    """Tests for the auto_match_unreconciled_statements Celery task."""

    @patch("app.tasks.banking.SessionLocal")
    def test_no_unmatched_statements(self, mock_session_cls: MagicMock) -> None:
        """Returns zero counts when no statements have unmatched lines."""
        from app.tasks.banking import auto_match_unreconciled_statements

        mock_db = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_db.scalars.return_value = mock_scalars

        result = auto_match_unreconciled_statements()

        assert result["statements_processed"] == 0
        assert result["total_matched"] == 0
        assert result["errors"] == []

    @patch("app.tasks.banking.SessionLocal")
    def test_processes_multiple_statements(self, mock_session_cls: MagicMock) -> None:
        """Processes each statement and accumulates match counts."""
        from app.tasks.banking import auto_match_unreconciled_statements

        mock_db = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        org_id = uuid.uuid4()
        stmt1 = _mock_statement(org_id=org_id)
        stmt2 = _mock_statement(org_id=org_id)

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [stmt1, stmt2]
        mock_db.scalars.return_value = mock_scalars

        with patch(
            "app.services.finance.banking.auto_reconciliation.AutoReconciliationService"
        ) as mock_svc_cls:
            mock_svc = mock_svc_cls.return_value
            mock_svc.auto_match_statement.side_effect = [
                _mock_match_result(matched=3, skipped=2),
                _mock_match_result(matched=1, skipped=4),
            ]

            result = auto_match_unreconciled_statements()

        assert result["statements_processed"] == 2
        assert result["total_matched"] == 4
        assert result["errors"] == []
        # Each statement should trigger a commit
        assert mock_db.commit.call_count == 2

    @patch("app.tasks.banking.SessionLocal")
    def test_per_statement_commit_isolation(self, mock_session_cls: MagicMock) -> None:
        """First statement succeeds and commits; second fails and rolls back."""
        from app.tasks.banking import auto_match_unreconciled_statements

        mock_db = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        stmt1 = _mock_statement()
        stmt2 = _mock_statement()

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [stmt1, stmt2]
        mock_db.scalars.return_value = mock_scalars

        with patch(
            "app.services.finance.banking.auto_reconciliation.AutoReconciliationService"
        ) as mock_svc_cls:
            mock_svc = mock_svc_cls.return_value
            mock_svc.auto_match_statement.side_effect = [
                _mock_match_result(matched=2),
                RuntimeError("DB exploded"),
            ]

            result = auto_match_unreconciled_statements()

        # First statement committed successfully
        assert result["statements_processed"] == 1
        assert result["total_matched"] == 2
        # Second statement recorded as error
        assert len(result["errors"]) == 1
        assert "DB exploded" in result["errors"][0]
        # Commit for stmt1, rollback for stmt2
        assert mock_db.commit.call_count == 1
        assert mock_db.rollback.call_count == 1

    @patch("app.tasks.banking.SessionLocal")
    def test_match_errors_appended_to_results(
        self, mock_session_cls: MagicMock
    ) -> None:
        """Per-line errors from auto_match are propagated to task results."""
        from app.tasks.banking import auto_match_unreconciled_statements

        mock_db = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        stmt = _mock_statement()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [stmt]
        mock_db.scalars.return_value = mock_scalars

        with patch(
            "app.services.finance.banking.auto_reconciliation.AutoReconciliationService"
        ) as mock_svc_cls:
            mock_svc = mock_svc_cls.return_value
            mock_svc.auto_match_statement.return_value = _mock_match_result(
                matched=1,
                errors=["Line 3: amount mismatch"],
            )

            result = auto_match_unreconciled_statements()

        assert result["total_matched"] == 1
        assert len(result["errors"]) == 1
        assert "Line 3" in result["errors"][0]
