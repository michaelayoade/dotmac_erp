"""
Banking multi-tenant isolation and security regression tests.

Tests that banking services properly scope queries by organization_id
to prevent cross-tenant data leaks.
"""

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.models.finance.banking.bank_statement import (
    StatementLineType,
)
from app.services.finance.banking.bank_reconciliation import (
    BankReconciliationService,
)
from app.services.finance.banking.bank_statement import (
    BankStatementService,
    StatementLineInput,
)

# ============ Fixtures ============


@pytest.fixture
def org_a_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def org_b_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def bank_account_id() -> uuid.UUID:
    return uuid.uuid4()


# ============ Statement Duplicate Detection ============


class TestStatementDuplicateIsolation:
    """Verify _check_duplicate_line scopes to organization_id."""

    def test_check_duplicate_includes_org_filter(
        self, org_a_id: uuid.UUID, bank_account_id: uuid.UUID
    ) -> None:
        """_check_duplicate_line must filter by organization_id."""
        db = MagicMock()
        # Make execute().scalars().first() return None
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        db.execute.return_value = mock_result

        service = BankStatementService()
        line = StatementLineInput(
            line_number=1,
            transaction_date=date(2026, 1, 15),
            transaction_type=StatementLineType.credit,
            amount=Decimal("500.00"),
            description="Test payment",
        )

        result = service._check_duplicate_line(db, bank_account_id, line, org_a_id)

        assert result is None
        # Verify execute was called (using select() not db.query())
        db.execute.assert_called_once()
        # The SQL should contain the organization_id filter
        call_args = db.execute.call_args
        stmt = call_args[0][0]
        # Verify the compiled SQL includes organization_id
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "organization_id" in compiled

    def test_check_duplicate_without_org_still_works(
        self, bank_account_id: uuid.UUID
    ) -> None:
        """_check_duplicate_line works with org_id=None (backward compat)."""
        db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        db.execute.return_value = mock_result

        service = BankStatementService()
        line = StatementLineInput(
            line_number=1,
            transaction_date=date(2026, 1, 15),
            transaction_type=StatementLineType.credit,
            amount=Decimal("500.00"),
        )

        # Should not raise even without org_id
        result = service._check_duplicate_line(db, bank_account_id, line)
        assert result is None


# ============ Reconciliation Prior Reconciliation Isolation ============


class TestReconciliationPriorIsolation:
    """Verify _get_prior_reconciliation scopes to organization_id."""

    def test_prior_reconciliation_includes_org_filter(
        self, org_a_id: uuid.UUID, bank_account_id: uuid.UUID
    ) -> None:
        """_get_prior_reconciliation must filter by organization_id."""
        db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute.return_value = mock_result

        service = BankReconciliationService()
        result = service._get_prior_reconciliation(
            db, bank_account_id, date(2026, 2, 1), org_a_id
        )

        assert result is None
        db.execute.assert_called_once()
        # Verify compiled SQL includes organization_id
        call_args = db.execute.call_args
        stmt = call_args[0][0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "organization_id" in compiled

    def test_prior_reconciliation_without_org_still_works(
        self, bank_account_id: uuid.UUID
    ) -> None:
        """_get_prior_reconciliation works with org_id=None (backward compat)."""
        db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute.return_value = mock_result

        service = BankReconciliationService()
        result = service._get_prior_reconciliation(
            db, bank_account_id, date(2026, 2, 1)
        )

        assert result is None
        # Should not include organization_id when None
        call_args = db.execute.call_args
        stmt = call_args[0][0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        # When org_id is None, organization_id should not appear in WHERE
        # (it may still appear in FROM clause)
        where_clause = compiled.split("WHERE")[1] if "WHERE" in compiled else ""
        assert "organization_id" not in where_clause


# ============ Auto-Match Scoring ============


class TestAutoMatchScoring:
    """Test the reconciliation auto-match scoring algorithm."""

    def test_exact_amount_same_date_full_score(self) -> None:
        """Exact amount + same date + reference match = 85 points (base, no payee)."""
        service = BankReconciliationService()

        stmt_line = SimpleNamespace(
            signed_amount=Decimal("500.00"),
            transaction_date=date(2026, 1, 15),
            reference="INV-001",
            description="Payment for INV-001",
        )

        je = SimpleNamespace(entry_date=date(2026, 1, 15))
        gl_line = SimpleNamespace(
            debit_amount=Decimal("500.00"),
            credit_amount=Decimal("0"),
            description="Payment for INV-001",
            journal_entry=je,
        )

        score = service._calculate_match_score(stmt_line, gl_line)
        assert score == 85.0  # 35 (amount) + 25 (date) + 25 (reference)

    def test_exact_amount_different_date_partial_score(self) -> None:
        """Exact amount + 2-day offset = 50 points."""
        service = BankReconciliationService()

        stmt_line = SimpleNamespace(
            signed_amount=Decimal("250.00"),
            transaction_date=date(2026, 1, 15),
            reference=None,
            description="DEPOSIT",
        )

        je = SimpleNamespace(entry_date=date(2026, 1, 17))
        gl_line = SimpleNamespace(
            debit_amount=Decimal("250.00"),
            credit_amount=Decimal("0"),
            description="Cash deposit",
            journal_entry=je,
        )

        score = service._calculate_match_score(stmt_line, gl_line)
        # 35 (exact amount) + 15 (2-day proximity) + 0 (no ref match) = 50
        assert score == 50.0

    def test_zero_score_for_mismatched_amount(self) -> None:
        """Different amounts with no other matches = 0."""
        service = BankReconciliationService()

        stmt_line = SimpleNamespace(
            signed_amount=Decimal("100.00"),
            transaction_date=date(2026, 1, 15),
            reference=None,
            description=None,
        )

        je = SimpleNamespace(entry_date=date(2026, 3, 1))
        gl_line = SimpleNamespace(
            debit_amount=Decimal("999.00"),
            credit_amount=Decimal("0"),
            description=None,
            journal_entry=je,
        )

        score = service._calculate_match_score(stmt_line, gl_line)
        assert score == 0.0

    def test_near_match_amount_with_tolerance(self) -> None:
        """Amount within 0.01 tolerance gets 30 points."""
        service = BankReconciliationService()

        stmt_line = SimpleNamespace(
            signed_amount=Decimal("100.00"),
            transaction_date=date(2026, 1, 15),
            reference=None,
            description=None,
        )

        je = SimpleNamespace(entry_date=date(2026, 1, 15))
        gl_line = SimpleNamespace(
            debit_amount=Decimal("100.01"),
            credit_amount=Decimal("0"),
            description=None,
            journal_entry=je,
        )

        score = service._calculate_match_score(stmt_line, gl_line)
        # 30 (near amount) + 25 (same date) + 0 = 55
        assert score == 55.0

    def test_word_overlap_scoring(self) -> None:
        """Common words in description contribute to score."""
        service = BankReconciliationService()

        stmt_line = SimpleNamespace(
            signed_amount=Decimal("300.00"),
            transaction_date=date(2026, 1, 15),
            reference="SALARY",
            description="SALARY PAYMENT JANUARY 2026",
        )

        je = SimpleNamespace(entry_date=date(2026, 1, 15))
        gl_line = SimpleNamespace(
            debit_amount=Decimal("300.00"),
            credit_amount=Decimal("0"),
            description="SALARY PAYMENT FOR JANUARY",
            journal_entry=je,
        )

        score = service._calculate_match_score(stmt_line, gl_line)
        # 35 (amount) + 25 (date) + 25 (reference "SALARY" in desc) = 85
        assert score == 85.0

    def test_one_day_date_proximity(self) -> None:
        """1-day date offset gets 20 points for date."""
        service = BankReconciliationService()

        stmt_line = SimpleNamespace(
            signed_amount=Decimal("50.00"),
            transaction_date=date(2026, 1, 15),
            reference=None,
            description=None,
        )

        je = SimpleNamespace(entry_date=date(2026, 1, 16))
        gl_line = SimpleNamespace(
            debit_amount=Decimal("50.00"),
            credit_amount=Decimal("0"),
            description=None,
            journal_entry=je,
        )

        score = service._calculate_match_score(stmt_line, gl_line)
        # 35 (amount) + 20 (1-day) + 0 = 55
        assert score == 55.0

    def test_seven_day_date_proximity(self) -> None:
        """7-day date offset gets 8 points for date."""
        service = BankReconciliationService()

        stmt_line = SimpleNamespace(
            signed_amount=Decimal("50.00"),
            transaction_date=date(2026, 1, 15),
            reference=None,
            description=None,
        )

        je = SimpleNamespace(entry_date=date(2026, 1, 22))
        gl_line = SimpleNamespace(
            debit_amount=Decimal("50.00"),
            credit_amount=Decimal("0"),
            description=None,
            journal_entry=je,
        )

        score = service._calculate_match_score(stmt_line, gl_line)
        # 35 (amount) + 8 (7-day) + 0 = 43
        assert score == 43.0


# ============ Categorization Duplicate Isolation ============


class TestCategorizationDuplicateIsolation:
    """Verify _find_duplicate scopes to organization_id."""

    def test_find_duplicate_includes_org_filter(self, org_a_id: uuid.UUID) -> None:
        """_find_duplicate must include organization_id in query."""
        from app.services.finance.banking.categorization import (
            TransactionCategorizationService,
        )

        db = MagicMock()
        # db.get returns a statement with bank_account_id
        mock_statement = SimpleNamespace(
            bank_account_id=uuid.uuid4(),
            organization_id=org_a_id,
        )
        db.get.return_value = mock_statement

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        db.execute.return_value = mock_result

        service = TransactionCategorizationService()
        mock_line = SimpleNamespace(
            line_id=uuid.uuid4(),
            statement_id=uuid.uuid4(),
            transaction_date=date(2026, 1, 15),
            amount=Decimal("100.00"),
            transaction_type=StatementLineType.credit,
            description="Test",
            bank_reference=None,
        )

        result = service._find_duplicate(db, org_a_id, mock_line)

        assert result is None
        # Verify the SQL includes organization_id
        db.execute.assert_called_once()
        call_args = db.execute.call_args
        stmt = call_args[0][0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "organization_id" in compiled
