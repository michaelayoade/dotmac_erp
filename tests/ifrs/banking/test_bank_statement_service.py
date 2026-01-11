"""
Tests for BankStatementService.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.ifrs.banking.bank_statement import (
    BankStatementService,
    StatementLineInput,
)
from tests.ifrs.banking.conftest import (
    MockBankAccount,
    MockBankStatement,
    MockBankStatementLine,
    MockBankStatementStatus,
    MockStatementLineType,
)


@pytest.fixture
def service():
    """Create service instance."""
    return BankStatementService()


@pytest.fixture
def org_id():
    """Create test organization ID."""
    return uuid4()


@pytest.fixture
def user_id():
    """Create test user ID."""
    return uuid4()


@pytest.fixture
def sample_statement_lines():
    """Create sample statement line inputs."""
    from app.models.ifrs.banking.bank_statement import StatementLineType

    return [
        StatementLineInput(
            line_number=1,
            transaction_date=date.today(),
            transaction_type=StatementLineType.credit,
            amount=Decimal("500.00"),
            description="Deposit",
            reference="DEP001",
        ),
        StatementLineInput(
            line_number=2,
            transaction_date=date.today(),
            transaction_type=StatementLineType.debit,
            amount=Decimal("100.00"),
            description="Payment",
            reference="PAY001",
        ),
    ]


class TestImportStatement:
    """Tests for import_statement method."""

    def test_import_statement_success(
        self, service, mock_db, org_id, user_id, sample_statement_lines
    ):
        """Test successful statement import."""
        bank_account = MockBankAccount(organization_id=org_id)
        mock_db.get.return_value = bank_account

        # No duplicate statement
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        result = service.import_statement(
            mock_db,
            org_id,
            bank_account.bank_account_id,
            statement_number="STMT-001",
            statement_date=date.today(),
            period_start=date.today().replace(day=1),
            period_end=date.today(),
            opening_balance=Decimal("1000.00"),
            closing_balance=Decimal("1400.00"),
            lines=sample_statement_lines,
            imported_by=user_id,
        )

        assert result.lines_imported == 2
        assert result.lines_skipped == 0
        mock_db.add.assert_called()

    def test_import_statement_nonexistent_account_fails(
        self, service, mock_db, org_id, sample_statement_lines
    ):
        """Test importing statement for non-existent account fails."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.import_statement(
                mock_db,
                org_id,
                uuid4(),
                statement_number="STMT-001",
                statement_date=date.today(),
                period_start=date.today().replace(day=1),
                period_end=date.today(),
                opening_balance=Decimal("1000.00"),
                closing_balance=Decimal("1400.00"),
                lines=sample_statement_lines,
            )

        assert exc.value.status_code == 404

    def test_import_statement_wrong_org_fails(
        self, service, mock_db, org_id, sample_statement_lines
    ):
        """Test importing statement for wrong organization fails."""
        from fastapi import HTTPException

        bank_account = MockBankAccount(organization_id=uuid4())  # Different org
        mock_db.get.return_value = bank_account

        with pytest.raises(HTTPException) as exc:
            service.import_statement(
                mock_db,
                org_id,
                bank_account.bank_account_id,
                statement_number="STMT-001",
                statement_date=date.today(),
                period_start=date.today().replace(day=1),
                period_end=date.today(),
                opening_balance=Decimal("1000.00"),
                closing_balance=Decimal("1400.00"),
                lines=sample_statement_lines,
            )

        assert exc.value.status_code == 403

    def test_import_duplicate_statement_fails(
        self, service, mock_db, org_id, sample_statement_lines
    ):
        """Test importing duplicate statement fails."""
        from fastapi import HTTPException

        bank_account = MockBankAccount(organization_id=org_id)
        mock_db.get.return_value = bank_account

        # Existing statement found
        existing = MockBankStatement(statement_number="STMT-001")
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        mock_db.execute.return_value = mock_result

        with pytest.raises(HTTPException) as exc:
            service.import_statement(
                mock_db,
                org_id,
                bank_account.bank_account_id,
                statement_number="STMT-001",
                statement_date=date.today(),
                period_start=date.today().replace(day=1),
                period_end=date.today(),
                opening_balance=Decimal("1000.00"),
                closing_balance=Decimal("1400.00"),
                lines=sample_statement_lines,
            )

        assert exc.value.status_code == 400
        assert "already exists" in exc.value.detail


class TestGetStatement:
    """Tests for get method."""

    def test_get_existing_statement(self, service, mock_db):
        """Test getting existing statement."""
        statement = MockBankStatement()
        mock_db.get.return_value = statement

        result = service.get(mock_db, statement.statement_id)

        assert result == statement

    def test_get_nonexistent_statement(self, service, mock_db):
        """Test getting non-existent statement returns None."""
        mock_db.get.return_value = None

        result = service.get(mock_db, uuid4())

        assert result is None


class TestGetWithLines:
    """Tests for get_with_lines method."""

    def test_get_with_lines(self, service, mock_db):
        """Test getting statement with lines."""
        statement = MockBankStatement()
        statement.lines = [MockBankStatementLine() for _ in range(3)]
        mock_db.get.return_value = statement

        result = service.get_with_lines(mock_db, statement.statement_id)

        assert result == statement
        assert len(result.lines) == 3


class TestListStatements:
    """Tests for list method."""

    def test_list_all_statements(self, service, mock_db, org_id):
        """Test listing all statements."""
        statements = [MockBankStatement(organization_id=org_id) for _ in range(3)]

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = statements
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        result = service.list(mock_db, org_id)

        assert result == statements

    def test_list_with_bank_account_filter(self, service, mock_db, org_id):
        """Test listing statements with bank account filter."""
        bank_account_id = uuid4()
        statements = [
            MockBankStatement(
                organization_id=org_id,
                bank_account_id=bank_account_id,
            )
        ]

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = statements
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        result = service.list(mock_db, org_id, bank_account_id=bank_account_id)

        assert result == statements

    def test_list_with_date_range(self, service, mock_db, org_id):
        """Test listing statements with date range."""
        statements = [MockBankStatement(organization_id=org_id)]

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = statements
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        result = service.list(
            mock_db,
            org_id,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )

        assert result == statements


class TestGetUnmatchedLines:
    """Tests for get_unmatched_lines method."""

    def test_get_unmatched_lines(self, service, mock_db):
        """Test getting unmatched lines."""
        statement = MockBankStatement()
        unmatched = [
            MockBankStatementLine(statement_id=statement.statement_id, is_matched=False)
            for _ in range(3)
        ]

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = unmatched
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        result = service.get_unmatched_lines(mock_db, statement.statement_id)

        assert result == unmatched
        assert len(result) == 3


class TestMarkLineMatched:
    """Tests for mark_line_matched method."""

    def test_mark_line_matched_success(self, service, mock_db, user_id):
        """Test marking line as matched."""
        statement = MockBankStatement(unmatched_lines=5, matched_lines=0)
        line = MockBankStatementLine(is_matched=False)
        line.statement = statement
        mock_db.get.return_value = line

        result = service.mark_line_matched(
            mock_db, line.line_id, uuid4(), user_id
        )

        assert result.is_matched is True
        assert result.matched_by == user_id
        mock_db.flush.assert_called_once()

    def test_mark_line_nonexistent_fails(self, service, mock_db):
        """Test marking non-existent line fails."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.mark_line_matched(mock_db, uuid4(), uuid4())

        assert exc.value.status_code == 404


class TestUnmatchLine:
    """Tests for unmatch_line method."""

    def test_unmatch_line_success(self, service, mock_db):
        """Test unmatching a line."""
        statement = MockBankStatement(matched_lines=3, unmatched_lines=2, status="processing")
        line = MockBankStatementLine(is_matched=True)
        line.statement = statement
        mock_db.get.return_value = line

        result = service.unmatch_line(mock_db, line.line_id)

        assert result.is_matched is False
        assert result.matched_journal_line_id is None
        mock_db.flush.assert_called_once()

    def test_unmatch_already_unmatched(self, service, mock_db):
        """Test unmatching an already unmatched line returns unchanged."""
        line = MockBankStatementLine(is_matched=False)
        mock_db.get.return_value = line

        result = service.unmatch_line(mock_db, line.line_id)

        assert result.is_matched is False

    def test_unmatch_nonexistent_fails(self, service, mock_db):
        """Test unmatching non-existent line fails."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.unmatch_line(mock_db, uuid4())

        assert exc.value.status_code == 404


class TestUpdateStatus:
    """Tests for update_status method."""

    def test_update_status_success(self, service, mock_db):
        """Test updating statement status."""
        from app.models.ifrs.banking.bank_statement import BankStatementStatus

        statement = MockBankStatement(status="imported")
        mock_db.get.return_value = statement

        result = service.update_status(
            mock_db, statement.statement_id, BankStatementStatus.processing
        )

        mock_db.flush.assert_called_once()

    def test_update_status_nonexistent_fails(self, service, mock_db):
        """Test updating status of non-existent statement fails."""
        from fastapi import HTTPException
        from app.models.ifrs.banking.bank_statement import BankStatementStatus

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.update_status(mock_db, uuid4(), BankStatementStatus.closed)

        assert exc.value.status_code == 404


class TestDeleteStatement:
    """Tests for delete method."""

    def test_delete_statement_success(self, service, mock_db):
        """Test deleting a statement."""
        statement = MockBankStatement(status="imported")
        mock_db.get.return_value = statement

        result = service.delete(mock_db, statement.statement_id)

        assert result is True
        mock_db.delete.assert_called_once_with(statement)

    def test_delete_nonexistent_statement(self, service, mock_db):
        """Test deleting non-existent statement returns False."""
        mock_db.get.return_value = None

        result = service.delete(mock_db, uuid4())

        assert result is False

    def test_delete_reconciled_statement_fails(self, service, mock_db):
        """Test deleting reconciled statement fails."""
        from fastapi import HTTPException
        from app.models.ifrs.banking.bank_statement import BankStatementStatus

        statement = MockBankStatement(status=BankStatementStatus.reconciled)
        mock_db.get.return_value = statement

        with pytest.raises(HTTPException) as exc:
            service.delete(mock_db, statement.statement_id)

        assert exc.value.status_code == 400


class TestGetStatementSummary:
    """Tests for get_statement_summary method."""

    def test_get_statement_summary(self, service, mock_db):
        """Test getting statement summary."""
        mock_row = MagicMock()
        mock_row.total_statements = 5
        mock_row.total_lines = 100
        mock_row.matched_lines = 80
        mock_row.unmatched_lines = 20
        mock_db.execute.return_value.one.return_value = mock_row

        result = service.get_statement_summary(mock_db, uuid4())

        assert result["total_statements"] == 5
        assert result["total_lines"] == 100
        assert result["matched_lines"] == 80
        assert result["unmatched_lines"] == 20
        assert result["match_rate"] == 80.0

    def test_get_statement_summary_empty(self, service, mock_db):
        """Test getting summary with no statements."""
        mock_row = MagicMock()
        mock_row.total_statements = None
        mock_row.total_lines = None
        mock_row.matched_lines = None
        mock_row.unmatched_lines = None
        mock_db.execute.return_value.one.return_value = mock_row

        result = service.get_statement_summary(mock_db, uuid4())

        assert result["total_statements"] == 0
        assert result["match_rate"] == 0
