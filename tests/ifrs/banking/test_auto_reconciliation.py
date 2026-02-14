"""
Tests for AutoReconciliationService.

Verifies deterministic matching of bank statement lines to GL journal entries
via two strategies: PaymentIntent (Paystack-initiated) and Splynx
CustomerPayment (Splynx-originated).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.models.finance.banking.bank_statement import StatementLineType
from app.models.finance.payments.payment_intent import (
    PaymentDirection,
    PaymentIntentStatus,
)
from app.services.finance.banking.auto_reconciliation import (
    AMOUNT_TOLERANCE,
    AutoMatchResult,
    AutoReconciliationService,
)
from tests.ifrs.banking.conftest import (
    MockBankAccount,
    MockBankStatement,
    MockBankStatementLine,
)

# ── Helpers ──────────────────────────────────────────────────────────


class MockPaymentIntent:
    """Mock PaymentIntent model for testing."""

    def __init__(
        self,
        intent_id: uuid.UUID | None = None,
        organization_id: uuid.UUID | None = None,
        paystack_reference: str = "PSK-REF-001",
        amount: Decimal = Decimal("100.00"),
        bank_account_id: uuid.UUID | None = None,
        status: PaymentIntentStatus = PaymentIntentStatus.COMPLETED,
        source_type: str = "INVOICE",
        direction: PaymentDirection = PaymentDirection.INBOUND,
        paid_at: datetime | None = None,
    ):
        self.intent_id = intent_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.paystack_reference = paystack_reference
        self.amount = amount
        self.bank_account_id = bank_account_id
        self.status = status
        self.source_type = source_type
        self.direction = direction
        self.paid_at = paid_at or datetime(2026, 2, 14, 12, 0, tzinfo=UTC)


class MockCustomerPayment:
    """Mock CustomerPayment model for Splynx payment testing."""

    def __init__(
        self,
        payment_id: uuid.UUID | None = None,
        organization_id: uuid.UUID | None = None,
        splynx_id: str | None = "12345",
        reference: str | None = "PSK-SPLYNX-001",
        description: str | None = None,
        amount: Decimal = Decimal("100.00"),
        bank_account_id: uuid.UUID | None = None,
        correlation_id: str | None = None,
        journal_entry_id: uuid.UUID | None = None,
        status: str = "CLEARED",
        payment_date: object | None = None,
    ):
        from datetime import date

        self.payment_id = payment_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.splynx_id = splynx_id
        self.reference = reference
        self.description = description
        self.amount = amount
        self.bank_account_id = bank_account_id
        self.correlation_id = correlation_id or f"splynx-pmt-{splynx_id}"
        self.journal_entry_id = journal_entry_id or uuid.uuid4()
        self.status = status
        self.payment_date = payment_date or date(2026, 2, 15)


class MockJournalEntry:
    """Mock JournalEntry with lines for auto-reconciliation tests."""

    def __init__(
        self,
        journal_entry_id: uuid.UUID | None = None,
        organization_id: uuid.UUID | None = None,
        correlation_id: str | None = None,
        status: str = "POSTED",
        lines: list | None = None,
    ):
        self.journal_entry_id = journal_entry_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.correlation_id = correlation_id
        self.status = status
        self.lines = lines or []


class MockJournalEntryLine:
    """Mock JournalEntryLine for auto-reconciliation tests."""

    def __init__(
        self,
        line_id: uuid.UUID | None = None,
        journal_entry_id: uuid.UUID | None = None,
        account_id: uuid.UUID | None = None,
        debit_amount: Decimal = Decimal("0"),
        credit_amount: Decimal = Decimal("0"),
    ):
        self.line_id = line_id or uuid.uuid4()
        self.journal_entry_id = journal_entry_id or uuid.uuid4()
        self.account_id = account_id or uuid.uuid4()
        self.debit_amount = debit_amount
        self.credit_amount = credit_amount


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def org_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def gl_account_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def bank_account(org_id: uuid.UUID, gl_account_id: uuid.UUID) -> MockBankAccount:
    return MockBankAccount(
        organization_id=org_id,
        gl_account_id=gl_account_id,
    )


@pytest.fixture
def statement(org_id: uuid.UUID, bank_account: MockBankAccount) -> MockBankStatement:
    return MockBankStatement(
        organization_id=org_id,
        bank_account_id=bank_account.bank_account_id,
        total_lines=3,
        matched_lines=0,
        unmatched_lines=3,
    )


@pytest.fixture
def service() -> AutoReconciliationService:
    return AutoReconciliationService()


@pytest.fixture
def mock_db() -> MagicMock:
    session = MagicMock()
    session.flush = MagicMock()
    session.commit = MagicMock()
    return session


# ── Helper to wire up db.get() ───────────────────────────────────────


def setup_db_get(
    mock_db: MagicMock,
    statement: MockBankStatement,
    bank_account: MockBankAccount,
) -> None:
    """Configure mock_db.get() to return the right object by model class."""

    def get_side_effect(model_cls: type, pk: uuid.UUID) -> object | None:
        cls_name = model_cls.__name__
        if cls_name == "BankStatement" and pk == statement.statement_id:
            return statement
        if cls_name == "BankAccount" and pk == bank_account.bank_account_id:
            return bank_account
        return None

    mock_db.get.side_effect = get_side_effect


def setup_db_scalars(
    mock_db: MagicMock,
    unmatched_lines: list,
    intents: list,
    splynx_payments: list | None = None,
    extra_gl_account_ids: list | None = None,
) -> None:
    """Configure mock_db.scalars() for sequential calls.

    Call order:
    1. Fallback bank GL account IDs (new — for extra_gl_account_ids)
    2. Unmatched statement lines
    3. Splynx CustomerPayments (always loaded, shared by passes 2 & 3)
    4. PaymentIntents for pass 1

    Provide *splynx_payments* to control what the Splynx pass receives.
    Defaults to empty list (no Splynx payments).
    """
    items_list = [
        extra_gl_account_ids or [],  # 1. fallback GL accounts
        unmatched_lines,  # 2. unmatched lines
        splynx_payments or [],  # 3. Splynx payments
        intents,  # 4. PaymentIntents
    ]

    scalars_results = []
    for items in items_list:
        mock_result = MagicMock()
        mock_result.all.return_value = items
        scalars_results.append(mock_result)

    mock_db.scalars.side_effect = scalars_results


def setup_db_execute_journal(
    mock_db: MagicMock,
    journals: object | list,
) -> None:
    """Configure mock_db.execute() chain for _find_journal_line().

    The service calls: ``db.execute(stmt).unique().scalar_one_or_none()``
    because joinedload on a collection requires ``.unique()`` deduplication.

    Args:
        mock_db: The mock session.
        journals: A single journal (or None) for return_value,
                  or a list of journals for side_effect (multiple calls).
    """
    if isinstance(journals, list):
        results = []
        for j in journals:
            mock_unique = MagicMock()
            mock_unique.scalar_one_or_none.return_value = j
            mock_exec = MagicMock()
            mock_exec.unique.return_value = mock_unique
            results.append(mock_exec)
        mock_db.execute.side_effect = results
    else:
        mock_unique = MagicMock()
        mock_unique.scalar_one_or_none.return_value = journals
        mock_exec = MagicMock()
        mock_exec.unique.return_value = mock_unique
        mock_db.execute.return_value = mock_exec


# ── Tests: auto_match_statement() ────────────────────────────────────


class TestAutoMatchStatement:
    """Tests for auto_match_statement()."""

    def test_statement_not_found(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
    ) -> None:
        """Returns error when statement doesn't exist."""
        mock_db.get.return_value = None

        result = service.auto_match_statement(mock_db, org_id, uuid.uuid4())

        assert result.matched == 0
        assert len(result.errors) == 1
        assert "not found" in result.errors[0]

    def test_statement_wrong_org(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
    ) -> None:
        """Returns error when statement belongs to different org."""
        stmt = MockBankStatement(organization_id=uuid.uuid4())
        mock_db.get.return_value = stmt
        different_org = uuid.uuid4()

        result = service.auto_match_statement(mock_db, different_org, stmt.statement_id)

        assert result.matched == 0
        assert len(result.errors) == 1

    def test_no_unmatched_lines(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Returns zero when all lines are already matched."""
        setup_db_get(mock_db, statement, bank_account)
        setup_db_scalars(mock_db, unmatched_lines=[], intents=[])

        result = service.auto_match_statement(mock_db, org_id, statement.statement_id)

        assert result.matched == 0
        assert result.skipped == 0

    def test_no_intents_no_splynx(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Skips all lines when no PaymentIntents and no Splynx payments."""
        line = MockBankStatementLine(
            statement_id=statement.statement_id,
            reference="SOME-REF",
            amount=Decimal("100.00"),
        )
        setup_db_get(mock_db, statement, bank_account)
        # Pass 1: no intents → pass 2: no Splynx payments
        setup_db_scalars(mock_db, [line], intents=[], splynx_payments=[])

        result = service.auto_match_statement(mock_db, org_id, statement.statement_id)

        assert result.matched == 0
        assert result.skipped == 1

    def test_bank_account_no_gl_account(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
    ) -> None:
        """Returns error when bank account has no GL account configured."""
        stmt = MockBankStatement(organization_id=org_id)
        ba = MockBankAccount(
            bank_account_id=stmt.bank_account_id,
            organization_id=org_id,
            gl_account_id=None,  # type: ignore[arg-type]
        )
        ba.gl_account_id = None  # Force None

        def get_side_effect(model_cls: type, pk: uuid.UUID) -> object | None:
            cls_name = model_cls.__name__
            if cls_name == "BankStatement":
                return stmt
            if cls_name == "BankAccount":
                return ba
            return None

        mock_db.get.side_effect = get_side_effect

        result = service.auto_match_statement(mock_db, org_id, stmt.statement_id)

        assert result.matched == 0
        assert len(result.errors) == 1
        assert "GL account" in result.errors[0]


# ── Tests: PaymentIntent matching (pass 1) ───────────────────────────


class TestPaymentIntentMatching:
    """Tests for pass 1 — PaymentIntent matching."""

    def test_happy_path_reference_in_reference_field(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Matches when paystack_reference appears in line.reference."""
        intent = MockPaymentIntent(
            organization_id=org_id,
            paystack_reference="PSK-ABC-123",
            amount=Decimal("500.00"),
            bank_account_id=bank_account.bank_account_id,
        )
        line = MockBankStatementLine(
            statement_id=statement.statement_id,
            reference="PSK-ABC-123",
            amount=Decimal("500.00"),
        )
        jl = MockJournalEntryLine(
            account_id=gl_account_id,
            credit_amount=Decimal("500.00"),
        )
        je = MockJournalEntry(
            organization_id=org_id,
            correlation_id=str(intent.intent_id),
            lines=[jl],
        )

        setup_db_get(mock_db, statement, bank_account)
        setup_db_scalars(mock_db, unmatched_lines=[line], intents=[intent])
        setup_db_execute_journal(mock_db, je)

        with patch(
            "app.services.finance.banking.bank_reconciliation.BankReconciliationService"
        ) as mock_recon_cls:
            mock_recon = mock_recon_cls.return_value
            mock_recon.match_statement_line.return_value = line

            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        assert result.matched == 1
        assert result.skipped == 0
        mock_recon.match_statement_line.assert_called_once_with(
            db=mock_db,
            organization_id=org_id,
            statement_line_id=line.line_id,
            journal_line_id=jl.line_id,
            matched_by=None,
            force_match=True,
        )

    def test_happy_path_reference_in_description(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Matches when paystack_reference is embedded in line.description."""
        intent = MockPaymentIntent(
            organization_id=org_id,
            paystack_reference="PSK-XYZ-789",
            amount=Decimal("250.00"),
            bank_account_id=bank_account.bank_account_id,
        )
        line = MockBankStatementLine(
            statement_id=statement.statement_id,
            reference=None,
            description="Transfer from Paystack PSK-XYZ-789 settlement",
            amount=Decimal("250.00"),
        )
        jl = MockJournalEntryLine(account_id=gl_account_id)
        je = MockJournalEntry(
            organization_id=org_id,
            correlation_id=str(intent.intent_id),
            lines=[jl],
        )

        setup_db_get(mock_db, statement, bank_account)
        setup_db_scalars(mock_db, unmatched_lines=[line], intents=[intent])
        setup_db_execute_journal(mock_db, je)

        with patch(
            "app.services.finance.banking.bank_reconciliation.BankReconciliationService"
        ) as mock_recon_cls:
            mock_recon = mock_recon_cls.return_value
            mock_recon.match_statement_line.return_value = line

            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        assert result.matched == 1

    def test_happy_path_reference_in_bank_reference(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Matches when paystack_reference appears in line.bank_reference."""
        intent = MockPaymentIntent(
            organization_id=org_id,
            paystack_reference="PSK-BANK-456",
            amount=Decimal("300.00"),
            bank_account_id=bank_account.bank_account_id,
        )
        line = MockBankStatementLine(
            statement_id=statement.statement_id,
            reference=None,
            description=None,
            bank_reference="PSK-BANK-456",
            amount=Decimal("300.00"),
        )
        jl = MockJournalEntryLine(account_id=gl_account_id)
        je = MockJournalEntry(
            organization_id=org_id,
            correlation_id=str(intent.intent_id),
            lines=[jl],
        )

        setup_db_get(mock_db, statement, bank_account)
        setup_db_scalars(mock_db, unmatched_lines=[line], intents=[intent])
        setup_db_execute_journal(mock_db, je)

        with patch(
            "app.services.finance.banking.bank_reconciliation.BankReconciliationService"
        ) as mock_recon_cls:
            mock_recon = mock_recon_cls.return_value
            mock_recon.match_statement_line.return_value = line

            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        assert result.matched == 1

    def test_amount_mismatch_skips(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Skips line when reference matches but amount differs."""
        intent = MockPaymentIntent(
            organization_id=org_id,
            paystack_reference="PSK-MISMATCH",
            amount=Decimal("500.00"),
            bank_account_id=bank_account.bank_account_id,
        )
        line = MockBankStatementLine(
            statement_id=statement.statement_id,
            reference="PSK-MISMATCH",
            amount=Decimal("999.99"),  # Different amount
        )

        setup_db_get(mock_db, statement, bank_account)
        # Line stays unmatched after pass 1 → pass 2 fires (no Splynx payments)
        setup_db_scalars(mock_db, [line], intents=[intent], splynx_payments=[])

        result = service.auto_match_statement(mock_db, org_id, statement.statement_id)

        assert result.matched == 0
        assert result.skipped == 1

    def test_amount_within_tolerance_matches(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Matches when amounts differ by <= 0.01 (rounding tolerance)."""
        intent = MockPaymentIntent(
            organization_id=org_id,
            paystack_reference="PSK-CLOSE",
            amount=Decimal("100.00"),
            bank_account_id=bank_account.bank_account_id,
        )
        line = MockBankStatementLine(
            statement_id=statement.statement_id,
            reference="PSK-CLOSE",
            amount=Decimal("100.01"),  # Within 0.01 tolerance
        )
        jl = MockJournalEntryLine(account_id=gl_account_id)
        je = MockJournalEntry(
            organization_id=org_id,
            correlation_id=str(intent.intent_id),
            lines=[jl],
        )

        setup_db_get(mock_db, statement, bank_account)
        setup_db_scalars(mock_db, unmatched_lines=[line], intents=[intent])
        setup_db_execute_journal(mock_db, je)

        with patch(
            "app.services.finance.banking.bank_reconciliation.BankReconciliationService"
        ) as mock_recon_cls:
            mock_recon = mock_recon_cls.return_value
            mock_recon.match_statement_line.return_value = line

            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        assert result.matched == 1

    def test_no_journal_entry_skips(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Skips when reference and amount match but no GL journal found."""
        intent = MockPaymentIntent(
            organization_id=org_id,
            paystack_reference="PSK-NOJE",
            amount=Decimal("200.00"),
            bank_account_id=bank_account.bank_account_id,
        )
        line = MockBankStatementLine(
            statement_id=statement.statement_id,
            reference="PSK-NOJE",
            amount=Decimal("200.00"),
        )

        setup_db_get(mock_db, statement, bank_account)
        # Line unmatched after pass 1 → pass 2 fires
        setup_db_scalars(mock_db, [line], intents=[intent], splynx_payments=[])
        setup_db_execute_journal(mock_db, None)  # No journal entry

        result = service.auto_match_statement(mock_db, org_id, statement.statement_id)

        assert result.matched == 0
        assert result.skipped == 1

    def test_journal_entry_wrong_gl_account_skips(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Skips when journal exists but no line hits the bank GL account."""
        intent = MockPaymentIntent(
            organization_id=org_id,
            paystack_reference="PSK-WRONGGL",
            amount=Decimal("150.00"),
            bank_account_id=bank_account.bank_account_id,
        )
        line = MockBankStatementLine(
            statement_id=statement.statement_id,
            reference="PSK-WRONGGL",
            amount=Decimal("150.00"),
        )
        different_account_id = uuid.uuid4()
        jl = MockJournalEntryLine(account_id=different_account_id)
        je = MockJournalEntry(
            organization_id=org_id,
            correlation_id=str(intent.intent_id),
            lines=[jl],
        )

        setup_db_get(mock_db, statement, bank_account)
        # Line unmatched after pass 1 → pass 2 fires
        setup_db_scalars(mock_db, [line], intents=[intent], splynx_payments=[])
        setup_db_execute_journal(mock_db, je)

        result = service.auto_match_statement(mock_db, org_id, statement.statement_id)

        assert result.matched == 0
        assert result.skipped == 1

    def test_multiple_lines_mixed_results(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Handles mix of matchable and non-matchable lines correctly."""
        intent1 = MockPaymentIntent(
            organization_id=org_id,
            paystack_reference="PSK-MATCH-1",
            amount=Decimal("100.00"),
            bank_account_id=bank_account.bank_account_id,
        )
        intent2 = MockPaymentIntent(
            organization_id=org_id,
            paystack_reference="PSK-MATCH-2",
            amount=Decimal("200.00"),
            bank_account_id=bank_account.bank_account_id,
        )

        line1 = MockBankStatementLine(
            statement_id=statement.statement_id,
            line_number=1,
            reference="PSK-MATCH-1",
            amount=Decimal("100.00"),
        )
        line2 = MockBankStatementLine(
            statement_id=statement.statement_id,
            line_number=2,
            reference="UNKNOWN-REF",
            amount=Decimal("50.00"),
        )
        line3 = MockBankStatementLine(
            statement_id=statement.statement_id,
            line_number=3,
            reference="PSK-MATCH-2",
            amount=Decimal("200.00"),
        )

        jl1 = MockJournalEntryLine(account_id=gl_account_id)
        je1 = MockJournalEntry(
            organization_id=org_id,
            correlation_id=str(intent1.intent_id),
            lines=[jl1],
        )
        jl2 = MockJournalEntryLine(account_id=gl_account_id)
        je2 = MockJournalEntry(
            organization_id=org_id,
            correlation_id=str(intent2.intent_id),
            lines=[jl2],
        )

        setup_db_get(mock_db, statement, bank_account)
        # line2 stays unmatched → pass 2 fires (no Splynx payments)
        setup_db_scalars(
            mock_db,
            unmatched_lines=[line1, line2, line3],
            intents=[intent1, intent2],
            splynx_payments=[],
        )
        setup_db_execute_journal(mock_db, [je1, je2])

        with patch(
            "app.services.finance.banking.bank_reconciliation.BankReconciliationService"
        ) as mock_recon_cls:
            mock_recon = mock_recon_cls.return_value
            mock_recon.match_statement_line.return_value = MagicMock()

            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        assert result.matched == 2
        assert result.skipped == 1
        assert mock_recon.match_statement_line.call_count == 2

    def test_match_error_records_and_continues(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Records error per line and continues processing remaining lines."""
        intent = MockPaymentIntent(
            organization_id=org_id,
            paystack_reference="PSK-ERR",
            amount=Decimal("100.00"),
            bank_account_id=bank_account.bank_account_id,
        )
        line = MockBankStatementLine(
            statement_id=statement.statement_id,
            line_number=5,
            reference="PSK-ERR",
            amount=Decimal("100.00"),
        )
        jl = MockJournalEntryLine(account_id=gl_account_id)
        je = MockJournalEntry(
            organization_id=org_id,
            correlation_id=str(intent.intent_id),
            lines=[jl],
        )

        setup_db_get(mock_db, statement, bank_account)
        # Error line remains unmatched → pass 2 fires
        setup_db_scalars(mock_db, [line], intents=[intent], splynx_payments=[])
        setup_db_execute_journal(mock_db, je)

        with patch(
            "app.services.finance.banking.bank_reconciliation.BankReconciliationService"
        ) as mock_recon_cls:
            mock_recon = mock_recon_cls.return_value
            mock_recon.match_statement_line.side_effect = RuntimeError("DB error")

            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        assert result.matched == 0
        # Errors count separately from skipped
        assert result.skipped == 0
        assert len(result.errors) == 1
        assert "Line 5" in result.errors[0]

    def test_paystack_opex_expense_fallback_matches_by_date_amount(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Paystack OPEX debit lines can match expense intents by date+amount."""
        from datetime import date

        bank_account.account_name = "Paystack OPEX"
        bank_account.bank_name = "Paystack"
        intent = MockPaymentIntent(
            organization_id=org_id,
            paystack_reference="PSK-OPEX-001",
            amount=Decimal("45000.00"),
            bank_account_id=bank_account.bank_account_id,
            source_type="EXPENSE_CLAIM",
            direction=PaymentDirection.OUTBOUND,
            paid_at=datetime(2026, 2, 14, 9, 30, tzinfo=UTC),
        )
        line = MockBankStatementLine(
            statement_id=statement.statement_id,
            transaction_type=StatementLineType.debit,
            amount=Decimal("45000.00"),
            transaction_date=date(2026, 2, 14),
            reference=None,
            description="Transfer to employee",
            bank_reference=None,
        )
        jl = MockJournalEntryLine(account_id=gl_account_id)
        je = MockJournalEntry(
            organization_id=org_id,
            correlation_id=str(intent.intent_id),
            lines=[jl],
        )

        setup_db_get(mock_db, statement, bank_account)
        setup_db_scalars(mock_db, [line], intents=[intent], splynx_payments=[])
        setup_db_execute_journal(mock_db, je)

        with patch(
            "app.services.finance.banking.bank_reconciliation.BankReconciliationService"
        ) as mock_recon_cls:
            mock_recon = mock_recon_cls.return_value
            mock_recon.match_statement_line.return_value = line

            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        assert result.matched == 1
        assert result.skipped == 0

    def test_expense_fallback_not_applied_for_non_paystack_opex_bank(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Date+amount expense fallback should be limited to Paystack OPEX."""
        from datetime import date

        bank_account.account_name = "Main Operating"
        bank_account.bank_name = "Zenith Bank"
        intent = MockPaymentIntent(
            organization_id=org_id,
            paystack_reference="PSK-OPEX-002",
            amount=Decimal("12000.00"),
            bank_account_id=bank_account.bank_account_id,
            source_type="EXPENSE_CLAIM",
            direction=PaymentDirection.OUTBOUND,
            paid_at=datetime(2026, 2, 14, 10, 0, tzinfo=UTC),
        )
        line = MockBankStatementLine(
            statement_id=statement.statement_id,
            transaction_type=StatementLineType.debit,
            amount=Decimal("12000.00"),
            transaction_date=date(2026, 2, 14),
            reference=None,
            description="Transfer to employee",
            bank_reference=None,
        )

        setup_db_get(mock_db, statement, bank_account)
        setup_db_scalars(mock_db, [line], intents=[intent], splynx_payments=[])

        with patch(
            "app.services.finance.banking.bank_reconciliation.BankReconciliationService"
        ) as mock_recon_cls:
            mock_recon = mock_recon_cls.return_value
            mock_recon.match_statement_line.return_value = line

            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        assert result.matched == 0
        assert result.skipped == 1


# ── Tests: Splynx CustomerPayment matching (pass 2) ──────────────────


class TestSplynxPaymentMatching:
    """Tests for pass 2 — Splynx CustomerPayment matching."""

    def test_happy_path_splynx_reference_matches(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Matches when Splynx payment reference appears on bank statement line."""
        pmt = MockCustomerPayment(
            organization_id=org_id,
            splynx_id="99001",
            reference="PSK-SPLYNX-REF-42",
            amount=Decimal("750.00"),
            bank_account_id=bank_account.bank_account_id,
            correlation_id="splynx-pmt-99001",
        )
        line = MockBankStatementLine(
            statement_id=statement.statement_id,
            reference="PSK-SPLYNX-REF-42",
            amount=Decimal("750.00"),
        )
        jl = MockJournalEntryLine(account_id=gl_account_id)
        je = MockJournalEntry(
            organization_id=org_id,
            correlation_id="splynx-pmt-99001",
            lines=[jl],
        )

        setup_db_get(mock_db, statement, bank_account)
        # Pass 1: no intents → pass 2: Splynx payment matches
        setup_db_scalars(mock_db, [line], intents=[], splynx_payments=[pmt])
        setup_db_execute_journal(mock_db, je)

        with patch(
            "app.services.finance.banking.bank_reconciliation.BankReconciliationService"
        ) as mock_recon_cls:
            mock_recon = mock_recon_cls.return_value
            mock_recon.match_statement_line.return_value = line

            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        assert result.matched == 1
        assert result.skipped == 0
        mock_recon.match_statement_line.assert_called_once_with(
            db=mock_db,
            organization_id=org_id,
            statement_line_id=line.line_id,
            journal_line_id=jl.line_id,
            matched_by=None,
            force_match=True,
        )

    def test_splynx_ref_in_description(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Matches when Splynx reference is embedded in line.description."""
        pmt = MockCustomerPayment(
            organization_id=org_id,
            splynx_id="99002",
            reference="TRF-55443322",
            amount=Decimal("500.00"),
            bank_account_id=bank_account.bank_account_id,
        )
        line = MockBankStatementLine(
            statement_id=statement.statement_id,
            reference=None,
            description="Paystack settlement TRF-55443322 for ISP services",
            amount=Decimal("500.00"),
        )
        jl = MockJournalEntryLine(account_id=gl_account_id)
        je = MockJournalEntry(
            organization_id=org_id,
            correlation_id=pmt.correlation_id,
            lines=[jl],
        )

        setup_db_get(mock_db, statement, bank_account)
        setup_db_scalars(mock_db, [line], intents=[], splynx_payments=[pmt])
        setup_db_execute_journal(mock_db, je)

        with patch(
            "app.services.finance.banking.bank_reconciliation.BankReconciliationService"
        ):
            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        assert result.matched == 1

    def test_splynx_amount_mismatch_skips(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Skips when Splynx reference matches but amount differs."""
        pmt = MockCustomerPayment(
            organization_id=org_id,
            reference="SPLYNX-AMT-MISS",
            amount=Decimal("100.00"),
            bank_account_id=bank_account.bank_account_id,
        )
        line = MockBankStatementLine(
            statement_id=statement.statement_id,
            reference="SPLYNX-AMT-MISS",
            amount=Decimal("999.99"),
        )

        setup_db_get(mock_db, statement, bank_account)
        setup_db_scalars(mock_db, [line], intents=[], splynx_payments=[pmt])

        result = service.auto_match_statement(mock_db, org_id, statement.statement_id)

        assert result.matched == 0
        assert result.skipped == 1

    def test_splynx_no_reference_skips(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Skips when Splynx payment has no reference (ref_to_payment is empty)."""
        pmt = MockCustomerPayment(
            organization_id=org_id,
            reference=None,  # No reference
            amount=Decimal("100.00"),
            bank_account_id=bank_account.bank_account_id,
        )
        line = MockBankStatementLine(
            statement_id=statement.statement_id,
            reference="SOME-BANK-REF",
            amount=Decimal("100.00"),
        )

        setup_db_get(mock_db, statement, bank_account)
        setup_db_scalars(mock_db, [line], intents=[], splynx_payments=[pmt])

        result = service.auto_match_statement(mock_db, org_id, statement.statement_id)

        assert result.matched == 0
        assert result.skipped == 1

    def test_splynx_no_gl_journal_skips(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Skips when Splynx payment has no posted GL journal."""
        pmt = MockCustomerPayment(
            organization_id=org_id,
            reference="SPLYNX-NO-GL",
            amount=Decimal("200.00"),
            bank_account_id=bank_account.bank_account_id,
        )
        line = MockBankStatementLine(
            statement_id=statement.statement_id,
            reference="SPLYNX-NO-GL",
            amount=Decimal("200.00"),
        )

        setup_db_get(mock_db, statement, bank_account)
        setup_db_scalars(mock_db, [line], intents=[], splynx_payments=[pmt])
        setup_db_execute_journal(mock_db, None)

        result = service.auto_match_statement(mock_db, org_id, statement.statement_id)

        assert result.matched == 0
        assert result.skipped == 1

    def test_splynx_error_records_and_continues(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Records error in Splynx pass without crashing."""
        pmt = MockCustomerPayment(
            organization_id=org_id,
            reference="SPLYNX-ERR",
            amount=Decimal("100.00"),
            bank_account_id=bank_account.bank_account_id,
        )
        line = MockBankStatementLine(
            statement_id=statement.statement_id,
            line_number=7,
            reference="SPLYNX-ERR",
            amount=Decimal("100.00"),
        )
        jl = MockJournalEntryLine(account_id=gl_account_id)
        je = MockJournalEntry(
            organization_id=org_id,
            correlation_id=pmt.correlation_id,
            lines=[jl],
        )

        setup_db_get(mock_db, statement, bank_account)
        setup_db_scalars(mock_db, [line], intents=[], splynx_payments=[pmt])
        setup_db_execute_journal(mock_db, je)

        with patch(
            "app.services.finance.banking.bank_reconciliation.BankReconciliationService"
        ) as mock_recon_cls:
            mock_recon = mock_recon_cls.return_value
            mock_recon.match_statement_line.side_effect = RuntimeError("fail")

            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        assert result.matched == 0
        assert len(result.errors) == 1
        assert "Line 7" in result.errors[0]


# ── Tests: Mixed pass 1 + pass 2 ─────────────────────────────────────


class TestMixedMatching:
    """Tests for both passes running together."""

    def test_intent_match_then_splynx_match(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Line 1 matched by PaymentIntent, line 2 matched by Splynx."""
        intent = MockPaymentIntent(
            organization_id=org_id,
            paystack_reference="PSK-INTENT-1",
            amount=Decimal("100.00"),
            bank_account_id=bank_account.bank_account_id,
        )
        splynx_pmt = MockCustomerPayment(
            organization_id=org_id,
            splynx_id="88001",
            reference="SPLYNX-TRF-88",
            amount=Decimal("200.00"),
            bank_account_id=bank_account.bank_account_id,
        )

        line1 = MockBankStatementLine(
            statement_id=statement.statement_id,
            line_number=1,
            reference="PSK-INTENT-1",
            amount=Decimal("100.00"),
        )
        line2 = MockBankStatementLine(
            statement_id=statement.statement_id,
            line_number=2,
            reference="SPLYNX-TRF-88",
            amount=Decimal("200.00"),
        )

        # GL journals for both passes
        jl1 = MockJournalEntryLine(account_id=gl_account_id)
        je1 = MockJournalEntry(
            organization_id=org_id,
            correlation_id=str(intent.intent_id),
            lines=[jl1],
        )
        jl2 = MockJournalEntryLine(account_id=gl_account_id)
        je2 = MockJournalEntry(
            organization_id=org_id,
            correlation_id=splynx_pmt.correlation_id,
            lines=[jl2],
        )

        setup_db_get(mock_db, statement, bank_account)
        setup_db_scalars(
            mock_db,
            unmatched_lines=[line1, line2],
            intents=[intent],
            splynx_payments=[splynx_pmt],
        )
        # je1 for pass 1 (intent), je2 for pass 2 (splynx)
        setup_db_execute_journal(mock_db, [je1, je2])

        with patch(
            "app.services.finance.banking.bank_reconciliation.BankReconciliationService"
        ) as mock_recon_cls:
            mock_recon = mock_recon_cls.return_value

            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        assert result.matched == 2
        assert result.skipped == 0
        assert mock_recon.match_statement_line.call_count == 2

    def test_intent_matched_line_excluded_from_splynx_pass(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Line matched in pass 1 is not retried in pass 2."""
        # Both passes could match the same line, but pass 1 wins
        intent = MockPaymentIntent(
            organization_id=org_id,
            paystack_reference="SHARED-REF",
            amount=Decimal("100.00"),
            bank_account_id=bank_account.bank_account_id,
        )
        _splynx_pmt = MockCustomerPayment(  # noqa: F841
            organization_id=org_id,
            reference="SHARED-REF",  # Same ref as intent
            amount=Decimal("100.00"),
            bank_account_id=bank_account.bank_account_id,
        )
        line = MockBankStatementLine(
            statement_id=statement.statement_id,
            reference="SHARED-REF",
            amount=Decimal("100.00"),
        )
        jl = MockJournalEntryLine(account_id=gl_account_id)
        je = MockJournalEntry(
            organization_id=org_id,
            correlation_id=str(intent.intent_id),
            lines=[jl],
        )

        setup_db_get(mock_db, statement, bank_account)
        # Pass 1 matches the line → pass 2 gets no lines to try
        # So splynx_payments won't actually be queried
        setup_db_scalars(mock_db, [line], intents=[intent])
        setup_db_execute_journal(mock_db, je)

        with patch(
            "app.services.finance.banking.bank_reconciliation.BankReconciliationService"
        ) as mock_recon_cls:
            mock_recon = mock_recon_cls.return_value

            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        assert result.matched == 1
        assert result.skipped == 0
        # Only one call — pass 2 never ran
        assert mock_recon.match_statement_line.call_count == 1


# ── Tests: Paystack ref extraction from description ──────────────────


class TestExtractPaystackRef:
    """Tests for _extract_paystack_ref() static method."""

    def test_extracts_reference_no_prefix(self) -> None:
        """Extracts hex ref directly in description."""
        ref = AutoReconciliationService._extract_paystack_ref(
            "Splynx payment via Paystack. 698717ea5321b"
        )
        assert ref == "698717ea5321b"

    def test_extracts_reference_with_prefix(self) -> None:
        """Extracts hex ref after 'Reference No.:' prefix."""
        ref = AutoReconciliationService._extract_paystack_ref(
            "Splynx payment via Paystack. Reference No.: 69871fd7d9178"
        )
        assert ref == "69871fd7d9178"

    def test_returns_none_for_no_ref(self) -> None:
        """Returns None when no hex ref present."""
        ref = AutoReconciliationService._extract_paystack_ref(
            "Splynx payment via Paystack. Pay by Paystack"
        )
        assert ref is None

    def test_returns_none_for_empty(self) -> None:
        assert AutoReconciliationService._extract_paystack_ref(None) is None
        assert AutoReconciliationService._extract_paystack_ref("") is None

    def test_case_insensitive(self) -> None:
        """Returns lowercase regardless of input case."""
        ref = AutoReconciliationService._extract_paystack_ref("Payment: 69871FD7D9178")
        assert ref == "69871fd7d9178"


class TestPaystackRefMatching:
    """Tests for pass 2 matching via Paystack ref in description."""

    def test_match_by_paystack_ref_in_description(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Matches when Paystack ref from payment description matches line reference."""
        pmt = MockCustomerPayment(
            organization_id=org_id,
            splynx_id="93585",
            reference="2026-11-00763",  # Splynx receipt number (won't match)
            description="Splynx payment via Paystack. Reference No.: 69871fd7d9178",
            amount=Decimal("18812.50"),
            bank_account_id=bank_account.bank_account_id,
            correlation_id="splynx-pmt-93585",
        )
        line = MockBankStatementLine(
            statement_id=statement.statement_id,
            reference="69871fd7d9178",  # Paystack ref on statement
            description="Payment: 69871fd7d9178 via bank_transfer",
            bank_reference="69871fd7d9178",
            amount=Decimal("18812.50"),
        )
        jl = MockJournalEntryLine(account_id=gl_account_id)
        je = MockJournalEntry(
            organization_id=org_id,
            correlation_id="splynx-pmt-93585",
            lines=[jl],
        )

        setup_db_get(mock_db, statement, bank_account)
        setup_db_scalars(mock_db, [line], intents=[], splynx_payments=[pmt])
        setup_db_execute_journal(mock_db, je)

        with patch(
            "app.services.finance.banking.bank_reconciliation.BankReconciliationService"
        ) as mock_recon_cls:
            mock_recon = mock_recon_cls.return_value
            mock_recon.match_statement_line.return_value = line

            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        assert result.matched == 1
        assert result.skipped == 0

    def test_paystack_ref_priority_over_splynx_receipt(
        self,
        service: AutoReconciliationService,
    ) -> None:
        """Paystack ref from description takes priority over Splynx receipt number."""
        pmt = MockCustomerPayment(
            splynx_id="99999",
            reference="2026-01-00001",
            description="Splynx payment via Paystack. 698717ea5321b",
        )
        ref = AutoReconciliationService._extract_paystack_ref(pmt.description)
        assert ref == "698717ea5321b"

        # The ref_to_payment lookup uses paystack_ref as primary key
        ref_to_payment: dict[str, MockCustomerPayment] = {}
        paystack_ref = AutoReconciliationService._extract_paystack_ref(pmt.description)
        if paystack_ref:
            ref_to_payment[paystack_ref] = pmt
        # Splynx receipt added as fallback
        if pmt.reference and pmt.reference not in ref_to_payment:
            ref_to_payment[pmt.reference] = pmt

        assert "698717ea5321b" in ref_to_payment
        assert "2026-01-00001" in ref_to_payment


# ── Tests: Date + amount matching (pass 3) ───────────────────────────


class TestDateAmountMatching:
    """Tests for pass 3 — date + amount unique matching."""

    def test_unique_date_amount_matches(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Matches when exactly one payment and one line share date+amount."""
        from datetime import date

        pmt = MockCustomerPayment(
            organization_id=org_id,
            splynx_id="50001",
            reference=None,
            description="Splynx payment via Paystack. Pay by Paystack",
            amount=Decimal("18812.50"),
            bank_account_id=bank_account.bank_account_id,
            correlation_id="splynx-pmt-50001",
            payment_date=date(2026, 2, 14),
        )
        line = MockBankStatementLine(
            statement_id=statement.statement_id,
            reference="699054e1c8b79",
            amount=Decimal("18812.50"),
            transaction_date=date(2026, 2, 14),
        )
        jl = MockJournalEntryLine(account_id=gl_account_id)
        je = MockJournalEntry(
            organization_id=org_id,
            correlation_id="splynx-pmt-50001",
            lines=[jl],
        )

        setup_db_get(mock_db, statement, bank_account)
        setup_db_scalars(mock_db, [line], intents=[], splynx_payments=[pmt])
        setup_db_execute_journal(mock_db, je)

        with patch(
            "app.services.finance.banking.bank_reconciliation.BankReconciliationService"
        ) as mock_recon_cls:
            mock_recon = mock_recon_cls.return_value
            mock_recon.match_statement_line.return_value = line

            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        assert result.matched == 1

    def test_greedy_pairing_two_payments_one_line(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Matches 1 when 2 payments and 1 line share the same date+amount."""
        from datetime import date

        pmt1 = MockCustomerPayment(
            organization_id=org_id,
            splynx_id="60001",
            reference=None,
            description="Splynx payment via Paystack. Pay by Paystack",
            amount=Decimal("18812.50"),
            bank_account_id=bank_account.bank_account_id,
            payment_date=date(2026, 2, 14),
        )
        pmt2 = MockCustomerPayment(
            organization_id=org_id,
            splynx_id="60002",
            reference=None,
            description="Splynx payment via Paystack. Pay by Paystack",
            amount=Decimal("18812.50"),
            bank_account_id=bank_account.bank_account_id,
            payment_date=date(2026, 2, 14),
        )
        line = MockBankStatementLine(
            statement_id=statement.statement_id,
            reference="699054e1c8b79",
            amount=Decimal("18812.50"),
            transaction_date=date(2026, 2, 14),
        )
        jl = MockJournalEntryLine(account_id=gl_account_id)
        je = MockJournalEntry(
            organization_id=org_id,
            correlation_id=pmt1.correlation_id,
            lines=[jl],
        )

        setup_db_get(mock_db, statement, bank_account)
        setup_db_scalars(mock_db, [line], intents=[], splynx_payments=[pmt1, pmt2])
        setup_db_execute_journal(mock_db, je)

        with patch(
            "app.services.finance.banking.bank_reconciliation.BankReconciliationService"
        ) as mock_recon_cls:
            mock_recon = mock_recon_cls.return_value
            mock_recon.match_statement_line.return_value = line

            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        assert result.matched == 1

    def test_greedy_pairing_one_payment_two_lines(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Matches 1 when 1 payment and 2 lines share the same date+amount."""
        from datetime import date

        pmt = MockCustomerPayment(
            organization_id=org_id,
            splynx_id="70001",
            reference=None,
            description="Splynx payment via Paystack. Pay by Paystack",
            amount=Decimal("18812.50"),
            bank_account_id=bank_account.bank_account_id,
            payment_date=date(2026, 2, 14),
        )
        line1 = MockBankStatementLine(
            statement_id=statement.statement_id,
            reference="699054e1c8b79",
            amount=Decimal("18812.50"),
            transaction_date=date(2026, 2, 14),
        )
        line2 = MockBankStatementLine(
            statement_id=statement.statement_id,
            reference="6990516f0f5b0",
            amount=Decimal("18812.50"),
            transaction_date=date(2026, 2, 14),
        )
        jl = MockJournalEntryLine(account_id=gl_account_id)
        je = MockJournalEntry(
            organization_id=org_id,
            correlation_id=pmt.correlation_id,
            lines=[jl],
        )

        setup_db_get(mock_db, statement, bank_account)
        setup_db_scalars(mock_db, [line1, line2], intents=[], splynx_payments=[pmt])
        setup_db_execute_journal(mock_db, je)

        with patch(
            "app.services.finance.banking.bank_reconciliation.BankReconciliationService"
        ) as mock_recon_cls:
            mock_recon = mock_recon_cls.return_value
            mock_recon.match_statement_line.return_value = line1

            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        assert result.matched == 1
        assert result.skipped == 1  # line2 unmatched


# ── Tests: Shared helpers ─────────────────────────────────────────────


class TestFindRefInLine:
    """Tests for _find_ref_in_line()."""

    def test_no_text_fields(self) -> None:
        """Returns None when all text fields are empty."""
        line = MockBankStatementLine(
            reference=None, description=None, bank_reference=None
        )
        refs = {"PSK-123": MockPaymentIntent(paystack_reference="PSK-123")}

        result = AutoReconciliationService._find_ref_in_line(
            line,
            refs,  # type: ignore[arg-type]
        )

        assert result is None

    def test_substring_match(self) -> None:
        """Matches when reference is substring of description."""
        line = MockBankStatementLine(
            reference=None,
            description="Payment via Paystack ref PSK-SUBSTR-99 done",
        )
        intent = MockPaymentIntent(paystack_reference="PSK-SUBSTR-99")
        refs = {"PSK-SUBSTR-99": intent}

        result = AutoReconciliationService._find_ref_in_line(
            line,
            refs,  # type: ignore[arg-type]
        )

        assert result is intent

    def test_works_with_customer_payment(self) -> None:
        """Works with CustomerPayment keyed by reference."""
        line = MockBankStatementLine(
            reference=None,
            bank_reference="Settlement TRF-12345 processed",
        )
        pmt = MockCustomerPayment(reference="TRF-12345")
        refs = {"TRF-12345": pmt}

        result = AutoReconciliationService._find_ref_in_line(
            line,
            refs,  # type: ignore[arg-type]
        )

        assert result is pmt


class TestAmountsMatch:
    """Tests for _amounts_match()."""

    def test_exact_match(self) -> None:
        assert (
            AutoReconciliationService._amounts_match(
                Decimal("100.00"), Decimal("100.00")
            )
            is True
        )

    def test_within_tolerance(self) -> None:
        assert (
            AutoReconciliationService._amounts_match(
                Decimal("100.01"), Decimal("100.00")
            )
            is True
        )

    def test_beyond_tolerance(self) -> None:
        assert (
            AutoReconciliationService._amounts_match(
                Decimal("100.02"), Decimal("100.00")
            )
            is False
        )

    def test_tolerance_boundary(self) -> None:
        """Exactly at tolerance boundary (0.01) should match."""
        assert (
            AutoReconciliationService._amounts_match(
                Decimal("100.00"), Decimal("100.00") + AMOUNT_TOLERANCE
            )
            is True
        )


class TestAutoMatchResult:
    """Tests for AutoMatchResult dataclass."""

    def test_default_values(self) -> None:
        result = AutoMatchResult()
        assert result.matched == 0
        assert result.skipped == 0
        assert result.errors == []

    def test_errors_are_independent(self) -> None:
        """Verify errors list isn't shared between instances."""
        r1 = AutoMatchResult()
        r2 = AutoMatchResult()
        r1.errors.append("test")
        assert r2.errors == []


# ── Mock for Account (Pass 4) ──────────────────────────────────────


class MockAccount:
    """Mock Account model for bank fee tests."""

    def __init__(
        self,
        account_id: uuid.UUID | None = None,
        organization_id: uuid.UUID | None = None,
        account_code: str = "6080",
        account_name: str = "Finance Cost",
    ):
        self.account_id = account_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.account_code = account_code
        self.account_name = account_name


class MockApprovedJournal:
    """Mock JournalEntry returned by create_and_approve_journal."""

    def __init__(
        self,
        journal_entry_id: uuid.UUID | None = None,
        journal_number: str = "JE-0001",
    ):
        self.journal_entry_id = journal_entry_id or uuid.uuid4()
        self.journal_number = journal_number


class MockPostingResult:
    """Mock PostingResult for bank fee tests."""

    def __init__(
        self,
        success: bool = True,
        journal_entry_id: uuid.UUID | None = None,
        message: str = "",
    ):
        self.success = success
        self.journal_entry_id = journal_entry_id or uuid.uuid4()
        self.message = message


# ── Tests: Bank fee matching (pass 4) ──────────────────────────────


class TestBankFeeMatching:
    """Tests for pass 4 — bank fee auto-journal creation and matching."""

    def test_happy_path_fee_line_matched(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Creates GL journal for fee line and matches it."""
        from datetime import date

        fee_line = MockBankStatementLine(
            statement_id=statement.statement_id,
            line_number=1,
            reference="FEE-9544438",
            description="Paystack Fee: Settlement 9544438",
            amount=Decimal("-5000.00"),
            transaction_date=date(2026, 2, 10),
        )
        finance_cost_account = MockAccount(organization_id=org_id)
        jl = MockJournalEntryLine(account_id=gl_account_id)
        je = MockJournalEntry(
            organization_id=org_id,
            correlation_id=f"bank-fee-{fee_line.line_id}",
            lines=[jl],
        )

        setup_db_get(mock_db, statement, bank_account)
        # Passes 1-3 have no data (no intents, no splynx payments)
        setup_db_scalars(mock_db, [fee_line], intents=[], splynx_payments=[])
        # db.scalar() for Account lookup
        mock_db.scalar.return_value = finance_cost_account
        # db.execute() for _find_journal_line after posting
        setup_db_execute_journal(mock_db, je)

        mock_journal = MockApprovedJournal()
        mock_posting = MockPostingResult(success=True)

        with (
            patch(
                "app.services.finance.posting.base.BasePostingAdapter"
            ) as mock_adapter_cls,
            patch(
                "app.services.finance.banking.bank_reconciliation."
                "BankReconciliationService"
            ),
        ):
            mock_adapter_cls.create_and_approve_journal.return_value = (
                mock_journal,
                None,
            )
            mock_adapter_cls.post_to_ledger.return_value = mock_posting
            mock_adapter_cls.make_idempotency_key.return_value = "test-key"

            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        assert result.matched == 1
        assert result.skipped == 0
        assert result.errors == []

        # Verify journal was created with correct parameters
        call_args = mock_adapter_cls.create_and_approve_journal.call_args
        journal_input = call_args[0][2]  # positional arg: journal_input
        assert journal_input.source_module == "BANKING"
        assert journal_input.source_document_type == "BANK_FEE"
        assert journal_input.correlation_id == f"bank-fee-{fee_line.line_id}"
        assert len(journal_input.lines) == 2
        # Debit Finance Cost (absolute amount)
        assert journal_input.lines[0].account_id == finance_cost_account.account_id
        assert journal_input.lines[0].debit_amount == Decimal("5000.00")
        # Credit bank GL
        assert journal_input.lines[1].account_id == gl_account_id
        assert journal_input.lines[1].credit_amount == Decimal("5000.00")

    def test_no_fee_lines_no_action(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """No journal creation when no fee lines present."""
        line = MockBankStatementLine(
            statement_id=statement.statement_id,
            reference="PAYMENT-123",
            description="Customer payment via Paystack",
            amount=Decimal("10000.00"),
        )

        setup_db_get(mock_db, statement, bank_account)
        setup_db_scalars(mock_db, [line], intents=[], splynx_payments=[])
        # Account lookup still happens
        mock_db.scalar.return_value = MockAccount(organization_id=org_id)

        with patch(
            "app.services.finance.posting.base.BasePostingAdapter"
        ) as mock_adapter_cls:
            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        assert result.matched == 0
        assert result.skipped == 1
        mock_adapter_cls.create_and_approve_journal.assert_not_called()

    def test_finance_cost_account_not_found(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Skips fee pass gracefully when Finance Cost account doesn't exist."""
        fee_line = MockBankStatementLine(
            statement_id=statement.statement_id,
            description="Paystack Fee: Settlement 9544438",
            amount=Decimal("-5000.00"),
        )

        setup_db_get(mock_db, statement, bank_account)
        setup_db_scalars(mock_db, [fee_line], intents=[], splynx_payments=[])
        # Account not found
        mock_db.scalar.return_value = None

        with patch(
            "app.services.finance.posting.base.BasePostingAdapter"
        ) as mock_adapter_cls:
            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        assert result.matched == 0
        assert result.skipped == 1
        mock_adapter_cls.create_and_approve_journal.assert_not_called()

    def test_journal_creation_failure_records_error(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Records error when GLPostingAdapter fails to create journal."""
        fee_line = MockBankStatementLine(
            statement_id=statement.statement_id,
            line_number=3,
            description="Paystack Fee: Settlement 9544438",
            amount=Decimal("-5000.00"),
        )

        setup_db_get(mock_db, statement, bank_account)
        setup_db_scalars(mock_db, [fee_line], intents=[], splynx_payments=[])
        mock_db.scalar.return_value = MockAccount(organization_id=org_id)

        create_error = MockPostingResult(
            success=False,
            message="Fee journal creation failed: Fiscal period not found",
        )

        with patch(
            "app.services.finance.posting.base.BasePostingAdapter"
        ) as mock_adapter_cls:
            mock_adapter_cls.create_and_approve_journal.return_value = (
                None,
                create_error,
            )

            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        assert result.matched == 0
        assert len(result.errors) == 1
        assert "Fee journal creation failed" in result.errors[0]
        assert "Line 3" in result.errors[0]

    def test_exception_during_fee_match_continues(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Records error per fee line and continues processing remaining."""
        from datetime import date

        fee1 = MockBankStatementLine(
            statement_id=statement.statement_id,
            line_number=1,
            description="Paystack Fee: Settlement 001",
            amount=Decimal("-100.00"),
            transaction_date=date(2026, 2, 10),
        )
        fee2 = MockBankStatementLine(
            statement_id=statement.statement_id,
            line_number=2,
            description="Paystack Fee: Settlement 002",
            amount=Decimal("-200.00"),
            transaction_date=date(2026, 2, 11),
        )

        jl = MockJournalEntryLine(account_id=gl_account_id)
        je = MockJournalEntry(
            organization_id=org_id,
            correlation_id=f"bank-fee-{fee2.line_id}",
            lines=[jl],
        )

        setup_db_get(mock_db, statement, bank_account)
        setup_db_scalars(mock_db, [fee1, fee2], intents=[], splynx_payments=[])
        mock_db.scalar.return_value = MockAccount(organization_id=org_id)

        # First fee: posting succeeds, but _find_journal_line raises
        # Second fee: posting succeeds and matches
        mock_journal = MockApprovedJournal()
        posting_ok = MockPostingResult(success=True)

        with (
            patch(
                "app.services.finance.posting.base.BasePostingAdapter"
            ) as mock_adapter_cls,
            patch(
                "app.services.finance.banking.bank_reconciliation."
                "BankReconciliationService"
            ),
        ):
            mock_adapter_cls.create_and_approve_journal.return_value = (
                mock_journal,
                None,
            )
            mock_adapter_cls.post_to_ledger.return_value = posting_ok
            mock_adapter_cls.make_idempotency_key.return_value = "test-key"
            # First call: exception during execute; second call: success
            mock_exec_fail = MagicMock()
            mock_exec_fail.unique.side_effect = RuntimeError("DB glitch")
            mock_exec_ok = MagicMock()
            mock_unique_ok = MagicMock()
            mock_unique_ok.scalar_one_or_none.return_value = je
            mock_exec_ok.unique.return_value = mock_unique_ok
            mock_db.execute.side_effect = [mock_exec_fail, mock_exec_ok]

            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        # fee1 errored, fee2 matched
        assert result.matched == 1
        assert len(result.errors) == 1
        assert "Line 1" in result.errors[0]

    def test_multiple_fee_lines_all_matched(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Successfully matches multiple fee lines in one pass."""
        from datetime import date

        fee1 = MockBankStatementLine(
            statement_id=statement.statement_id,
            line_number=1,
            description="Paystack Fee: Settlement 001",
            amount=Decimal("-100.00"),
            transaction_date=date(2026, 2, 10),
        )
        fee2 = MockBankStatementLine(
            statement_id=statement.statement_id,
            line_number=2,
            description="Paystack Fee: Settlement 002",
            amount=Decimal("-200.00"),
            transaction_date=date(2026, 2, 11),
        )

        jl1 = MockJournalEntryLine(account_id=gl_account_id)
        je1 = MockJournalEntry(
            organization_id=org_id,
            correlation_id=f"bank-fee-{fee1.line_id}",
            lines=[jl1],
        )
        jl2 = MockJournalEntryLine(account_id=gl_account_id)
        je2 = MockJournalEntry(
            organization_id=org_id,
            correlation_id=f"bank-fee-{fee2.line_id}",
            lines=[jl2],
        )

        setup_db_get(mock_db, statement, bank_account)
        setup_db_scalars(mock_db, [fee1, fee2], intents=[], splynx_payments=[])
        mock_db.scalar.return_value = MockAccount(organization_id=org_id)
        setup_db_execute_journal(mock_db, [je1, je2])

        mock_journal = MockApprovedJournal()
        posting_ok = MockPostingResult(success=True)

        with (
            patch(
                "app.services.finance.posting.base.BasePostingAdapter"
            ) as mock_adapter_cls,
            patch(
                "app.services.finance.banking.bank_reconciliation."
                "BankReconciliationService"
            ),
        ):
            mock_adapter_cls.create_and_approve_journal.return_value = (
                mock_journal,
                None,
            )
            mock_adapter_cls.post_to_ledger.return_value = posting_ok
            mock_adapter_cls.make_idempotency_key.return_value = "test-key"

            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        assert result.matched == 2
        assert result.skipped == 0
        assert result.errors == []
        assert mock_adapter_cls.create_and_approve_journal.call_count == 2


# ── Tests: Settlement matching (pass 5) ────────────────────────────


class TestSettlementMatching:
    """Tests for _match_settlements() (pass 5).

    Verifies cross-bank settlement matching: Paystack settlement debits
    matched against deposits on receiving bank accounts within a date window.
    """

    @staticmethod
    def _make_settlement_line(
        statement_id: uuid.UUID,
        *,
        line_number: int = 1,
        amount: Decimal = Decimal("500000.00"),
        txn_date: object | None = None,
        reference: str = "STL-9999999",
    ) -> MockBankStatementLine:
        from datetime import date

        return MockBankStatementLine(
            statement_id=statement_id,
            line_number=line_number,
            description=f"Settlement to bank: {txn_date or date(2026, 2, 10)}",
            amount=amount,
            reference=reference,
            bank_reference="9999999",
            transaction_date=txn_date or date(2026, 2, 10),
            transaction_type=StatementLineType.debit,
        )

    @staticmethod
    def _make_deposit_line(
        statement_id: uuid.UUID,
        *,
        line_number: int = 1,
        amount: Decimal = Decimal("510000.00"),
        txn_date: object | None = None,
        description: str = "TNF-Paystack/PSST10abc123",
    ) -> MockBankStatementLine:
        from datetime import date

        return MockBankStatementLine(
            statement_id=statement_id,
            line_number=line_number,
            description=description,
            amount=amount,
            transaction_date=txn_date or date(2026, 2, 11),
            transaction_type=StatementLineType.credit,
        )

    def _setup_settlement_mocks(
        self,
        mock_db: MagicMock,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
        settlement_lines: list,
        deposit_lines: list,
        dest_bank: MockBankAccount,
        dest_statement: MockBankStatement | None = None,
    ) -> None:
        """Set up mock_db for settlement pass with all preceding passes empty."""

        def get_side_effect(model_cls: type, pk: uuid.UUID) -> object | None:
            cls_name = model_cls.__name__
            if cls_name == "BankStatement":
                if pk == statement.statement_id:
                    return statement
                # Deposit's parent statement
                if dest_statement and pk == dest_statement.statement_id:
                    return dest_statement
                # Check deposit lines for their statement_id
                for dep in deposit_lines:
                    if pk == dep.statement_id:
                        ds = MockBankStatement(
                            statement_id=dep.statement_id,
                            organization_id=statement.organization_id,
                            bank_account_id=dest_bank.bank_account_id,
                        )
                        return ds
            if cls_name == "BankAccount" and pk == bank_account.bank_account_id:
                return bank_account
            return None

        mock_db.get.side_effect = get_side_effect

        # scalars() call order in auto_match_statement():
        # 1. fallback GL account IDs
        # 2. unmatched lines
        # 3. splynx payments (empty)
        # 4. intents (empty)
        # Then in _match_settlements():
        # 5. other_bank_ids
        # 6. deposit_lines (from other bank statements)
        # 7. target_accounts (BankAccount objects)
        scalars_calls = [
            [],  # 1. fallback GL accounts
            settlement_lines,  # 2. unmatched lines
            [],  # 3. splynx payments
            [],  # 4. intents
            [dest_bank.bank_account_id],  # 5. other bank account IDs
            deposit_lines,  # 6. deposit lines from other banks
            [dest_bank],  # 7. target BankAccount objects
        ]

        scalars_results = []
        for items in scalars_calls:
            mock_result = MagicMock()
            mock_result.all.return_value = items
            scalars_results.append(mock_result)

        mock_db.scalars.side_effect = scalars_results

    def test_happy_path_settlement_matched(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Settlement debit matched to deposit credit on destination bank."""
        from datetime import date

        dest_gl_id = uuid.uuid4()
        dest_bank = MockBankAccount(
            organization_id=org_id,
            gl_account_id=dest_gl_id,
            account_name="UBA 96 (Main)",
        )
        dest_stmt = MockBankStatement(
            organization_id=org_id,
            bank_account_id=dest_bank.bank_account_id,
        )

        stl = self._make_settlement_line(
            statement.statement_id,
            amount=Decimal("500000.00"),
            txn_date=date(2026, 2, 10),
        )
        dep = self._make_deposit_line(
            dest_stmt.statement_id,
            amount=Decimal("510000.00"),
            txn_date=date(2026, 2, 11),
        )

        # Journal lines for the transfer
        credit_jl = MockJournalEntryLine(account_id=gl_account_id)
        debit_jl = MockJournalEntryLine(account_id=dest_gl_id)
        journal = MockJournalEntry(
            organization_id=org_id,
            correlation_id=f"settlement-{stl.line_id}",
            lines=[credit_jl, debit_jl],
        )

        self._setup_settlement_mocks(
            mock_db, statement, bank_account, [stl], [dep], dest_bank, dest_stmt
        )
        # Pre-check returns None (no existing journal), then 2 lookups after posting
        setup_db_execute_journal(mock_db, [None, journal, journal])

        mock_approved = MockApprovedJournal()
        posting_ok = MockPostingResult(success=True)

        with (
            patch(
                "app.services.finance.posting.base.BasePostingAdapter"
            ) as mock_adapter,
            patch(
                "app.services.finance.banking.bank_reconciliation."
                "BankReconciliationService"
            ),
        ):
            mock_adapter.create_and_approve_journal.return_value = (
                mock_approved,
                None,
            )
            mock_adapter.post_to_ledger.return_value = posting_ok
            mock_adapter.make_idempotency_key.return_value = "test-key"

            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        assert result.matched == 1
        assert result.errors == []
        assert mock_adapter.create_and_approve_journal.call_count == 1

    def test_no_settlement_lines_no_action(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Non-settlement lines are skipped entirely."""
        from datetime import date

        # Regular payment line, not a settlement
        regular = MockBankStatementLine(
            statement_id=statement.statement_id,
            line_number=1,
            description="Payment from customer ABC",
            amount=Decimal("1000.00"),
            transaction_date=date(2026, 2, 10),
        )

        setup_db_get(mock_db, statement, bank_account)
        setup_db_scalars(mock_db, [regular], intents=[], splynx_payments=[])
        # No execute calls needed since no pass matches

        result = service.auto_match_statement(mock_db, org_id, statement.statement_id)

        # Line not matched by any pass (including settlement)
        assert result.matched == 0

    def test_no_deposits_on_other_banks(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Settlement lines skipped when no deposits found on other banks."""
        from datetime import date

        stl = self._make_settlement_line(
            statement.statement_id, txn_date=date(2026, 2, 10)
        )

        dest_bank = MockBankAccount(organization_id=org_id, account_name="UBA")

        self._setup_settlement_mocks(
            mock_db, statement, bank_account, [stl], [], dest_bank
        )

        result = service.auto_match_statement(mock_db, org_id, statement.statement_id)

        assert result.matched == 0

    def test_deposit_outside_date_window_skipped(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Deposits more than 10 days after settlement are not matched."""
        from datetime import date

        dest_bank = MockBankAccount(organization_id=org_id, account_name="UBA")
        dest_stmt = MockBankStatement(
            organization_id=org_id,
            bank_account_id=dest_bank.bank_account_id,
        )

        stl = self._make_settlement_line(
            statement.statement_id,
            amount=Decimal("500000.00"),
            txn_date=date(2026, 2, 10),
        )
        # Deposit 11 days later — outside 0-10 window
        dep = self._make_deposit_line(
            dest_stmt.statement_id,
            amount=Decimal("510000.00"),
            txn_date=date(2026, 2, 21),
        )

        self._setup_settlement_mocks(
            mock_db, statement, bank_account, [stl], [dep], dest_bank, dest_stmt
        )

        result = service.auto_match_statement(mock_db, org_id, statement.statement_id)

        assert result.matched == 0

    def test_duplicate_settlement_lines_both_matched(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Duplicate settlement lines (same date/ref/amount) all matched."""
        from datetime import date

        dest_gl_id = uuid.uuid4()
        dest_bank = MockBankAccount(
            organization_id=org_id,
            gl_account_id=dest_gl_id,
            account_name="UBA",
        )
        dest_stmt = MockBankStatement(
            organization_id=org_id,
            bank_account_id=dest_bank.bank_account_id,
        )

        # Two identical settlement lines (import artifact)
        stl1 = self._make_settlement_line(
            statement.statement_id,
            line_number=1,
            amount=Decimal("500000.00"),
            txn_date=date(2026, 2, 10),
            reference="STL-1234567",
        )
        stl2 = self._make_settlement_line(
            statement.statement_id,
            line_number=2,
            amount=Decimal("500000.00"),
            txn_date=date(2026, 2, 10),
            reference="STL-1234567",
        )

        dep = self._make_deposit_line(
            dest_stmt.statement_id,
            amount=Decimal("510000.00"),
            txn_date=date(2026, 2, 11),
        )

        credit_jl = MockJournalEntryLine(account_id=gl_account_id)
        debit_jl = MockJournalEntryLine(account_id=dest_gl_id)
        journal = MockJournalEntry(
            organization_id=org_id,
            correlation_id=f"settlement-{stl1.line_id}",
            lines=[credit_jl, debit_jl],
        )

        self._setup_settlement_mocks(
            mock_db,
            statement,
            bank_account,
            [stl1, stl2],
            [dep],
            dest_bank,
            dest_stmt,
        )
        # Pre-check None, then credit_jl + debit_jl after posting
        setup_db_execute_journal(mock_db, [None, journal, journal])

        mock_approved = MockApprovedJournal()
        posting_ok = MockPostingResult(success=True)

        with (
            patch(
                "app.services.finance.posting.base.BasePostingAdapter"
            ) as mock_adapter,
            patch(
                "app.services.finance.banking.bank_reconciliation."
                "BankReconciliationService"
            ),
        ):
            mock_adapter.create_and_approve_journal.return_value = (
                mock_approved,
                None,
            )
            mock_adapter.post_to_ledger.return_value = posting_ok
            mock_adapter.make_idempotency_key.return_value = "test-key"

            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        # Both duplicate lines matched (only 1 journal created)
        assert result.matched == 2
        assert result.errors == []
        assert mock_adapter.create_and_approve_journal.call_count == 1

    def test_journal_creation_failure_records_error(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Journal creation failure is recorded as an error, not a crash."""
        from datetime import date

        dest_bank = MockBankAccount(organization_id=org_id, account_name="UBA")
        dest_stmt = MockBankStatement(
            organization_id=org_id,
            bank_account_id=dest_bank.bank_account_id,
        )

        stl = self._make_settlement_line(
            statement.statement_id, txn_date=date(2026, 2, 10)
        )
        dep = self._make_deposit_line(
            dest_stmt.statement_id, txn_date=date(2026, 2, 11)
        )

        self._setup_settlement_mocks(
            mock_db, statement, bank_account, [stl], [dep], dest_bank, dest_stmt
        )
        # Pre-check returns None (no existing journal)
        setup_db_execute_journal(mock_db, None)

        create_error = MockPostingResult(
            success=False, message="Settlement journal creation failed: test"
        )

        with (
            patch(
                "app.services.finance.posting.base.BasePostingAdapter"
            ) as mock_adapter,
            patch(
                "app.services.finance.banking.bank_reconciliation."
                "BankReconciliationService"
            ),
        ):
            mock_adapter.create_and_approve_journal.return_value = (
                None,
                create_error,
            )

            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        assert result.matched == 0
        assert len(result.errors) == 1
        assert "Settlement journal creation failed" in result.errors[0]

    def test_closest_amount_wins(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """When multiple deposits exist, the closest by amount is chosen."""
        from datetime import date

        dest_gl_id = uuid.uuid4()
        dest_bank = MockBankAccount(
            organization_id=org_id,
            gl_account_id=dest_gl_id,
            account_name="UBA",
        )
        dest_stmt = MockBankStatement(
            organization_id=org_id,
            bank_account_id=dest_bank.bank_account_id,
        )

        stl = self._make_settlement_line(
            statement.statement_id,
            amount=Decimal("500000.00"),
            txn_date=date(2026, 2, 10),
        )

        # Two deposits on same day — different amounts
        dep_far = self._make_deposit_line(
            dest_stmt.statement_id,
            line_number=1,
            amount=Decimal("999000.00"),  # far from 500k
            txn_date=date(2026, 2, 11),
        )
        dep_close = self._make_deposit_line(
            dest_stmt.statement_id,
            line_number=2,
            amount=Decimal("510000.00"),  # close to 500k
            txn_date=date(2026, 2, 11),
        )

        credit_jl = MockJournalEntryLine(account_id=gl_account_id)
        debit_jl = MockJournalEntryLine(account_id=dest_gl_id)
        journal = MockJournalEntry(
            organization_id=org_id,
            correlation_id=f"settlement-{stl.line_id}",
            lines=[credit_jl, debit_jl],
        )

        self._setup_settlement_mocks(
            mock_db,
            statement,
            bank_account,
            [stl],
            [dep_far, dep_close],
            dest_bank,
            dest_stmt,
        )
        # Pre-check None, then credit_jl + debit_jl after posting
        setup_db_execute_journal(mock_db, [None, journal, journal])

        mock_approved = MockApprovedJournal()
        posting_ok = MockPostingResult(success=True)

        with (
            patch(
                "app.services.finance.posting.base.BasePostingAdapter"
            ) as mock_adapter,
            patch(
                "app.services.finance.banking.bank_reconciliation."
                "BankReconciliationService"
            ) as mock_recon_cls,
        ):
            mock_adapter.create_and_approve_journal.return_value = (
                mock_approved,
                None,
            )
            mock_adapter.post_to_ledger.return_value = posting_ok
            mock_adapter.make_idempotency_key.return_value = "test-key"

            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        assert result.matched == 1
        assert result.errors == []
        # Verify the deposit match call used the closer amount line
        recon = mock_recon_cls.return_value
        deposit_match_calls = [
            c
            for c in recon.match_statement_line.call_args_list
            if c.kwargs.get("statement_line_id") == dep_close.line_id
        ]
        assert len(deposit_match_calls) == 1

    def test_rerun_reuses_existing_journal(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """On re-run, existing journal is reused — no duplicate creation."""
        from datetime import date

        dest_gl_id = uuid.uuid4()
        dest_bank = MockBankAccount(
            organization_id=org_id,
            gl_account_id=dest_gl_id,
            account_name="UBA 96 (Main)",
        )
        dest_stmt = MockBankStatement(
            organization_id=org_id,
            bank_account_id=dest_bank.bank_account_id,
        )

        stl = self._make_settlement_line(
            statement.statement_id,
            amount=Decimal("500000.00"),
            txn_date=date(2026, 2, 10),
        )
        dep = self._make_deposit_line(
            dest_stmt.statement_id,
            amount=Decimal("510000.00"),
            txn_date=date(2026, 2, 11),
        )

        # Existing journal from previous partial run
        credit_jl = MockJournalEntryLine(account_id=gl_account_id)
        debit_jl = MockJournalEntryLine(account_id=dest_gl_id)
        existing_journal = MockJournalEntry(
            organization_id=org_id,
            correlation_id=f"settlement-{stl.line_id}",
            lines=[credit_jl, debit_jl],
        )

        self._setup_settlement_mocks(
            mock_db, statement, bank_account, [stl], [dep], dest_bank, dest_stmt
        )
        # Pre-check finds existing journal, then debit lookup also succeeds
        setup_db_execute_journal(mock_db, [existing_journal, existing_journal])

        with (
            patch(
                "app.services.finance.posting.base.BasePostingAdapter"
            ) as mock_adapter,
            patch(
                "app.services.finance.banking.bank_reconciliation."
                "BankReconciliationService"
            ),
        ):
            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        assert result.matched == 1
        assert result.errors == []
        # No journal creation — reused existing
        mock_adapter.create_and_approve_journal.assert_not_called()
        mock_adapter.post_to_ledger.assert_not_called()

    def test_deposit_already_matched_graceful_skip(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        gl_account_id: uuid.UUID,
        statement: MockBankStatement,
        bank_account: MockBankAccount,
    ) -> None:
        """Deposit already matched doesn't crash — settlement still counts."""
        from datetime import date

        dest_gl_id = uuid.uuid4()
        dest_bank = MockBankAccount(
            organization_id=org_id,
            gl_account_id=dest_gl_id,
            account_name="UBA 96 (Main)",
        )
        dest_stmt = MockBankStatement(
            organization_id=org_id,
            bank_account_id=dest_bank.bank_account_id,
        )

        stl = self._make_settlement_line(
            statement.statement_id,
            amount=Decimal("500000.00"),
            txn_date=date(2026, 2, 10),
        )
        dep = self._make_deposit_line(
            dest_stmt.statement_id,
            amount=Decimal("510000.00"),
            txn_date=date(2026, 2, 11),
        )

        credit_jl = MockJournalEntryLine(account_id=gl_account_id)
        debit_jl = MockJournalEntryLine(account_id=dest_gl_id)
        journal = MockJournalEntry(
            organization_id=org_id,
            correlation_id=f"settlement-{stl.line_id}",
            lines=[credit_jl, debit_jl],
        )

        self._setup_settlement_mocks(
            mock_db, statement, bank_account, [stl], [dep], dest_bank, dest_stmt
        )
        # Pre-check None, then credit + debit lookups
        setup_db_execute_journal(mock_db, [None, journal, journal])

        mock_approved = MockApprovedJournal()
        posting_ok = MockPostingResult(success=True)

        with (
            patch(
                "app.services.finance.posting.base.BasePostingAdapter"
            ) as mock_adapter,
            patch(
                "app.services.finance.banking.bank_reconciliation."
                "BankReconciliationService"
            ) as mock_recon_cls,
        ):
            mock_adapter.create_and_approve_journal.return_value = (
                mock_approved,
                None,
            )
            mock_adapter.post_to_ledger.return_value = posting_ok
            mock_adapter.make_idempotency_key.return_value = "test-key"

            # Settlement match succeeds, but deposit match raises
            # (simulating an already-matched deposit from a prior statement)
            mock_recon = mock_recon_cls.return_value
            call_count = [0]

            def match_side_effect(**kwargs: object) -> MagicMock:
                call_count[0] += 1
                if kwargs.get("statement_line_id") == dep.line_id:
                    raise RuntimeError("Statement line is already matched")
                return MagicMock()

            mock_recon.match_statement_line.side_effect = match_side_effect

            result = service.auto_match_statement(
                mock_db, org_id, statement.statement_id
            )

        # Settlement line matched, deposit error handled gracefully
        assert result.matched == 1
        assert result.errors == []  # Deposit skip is debug-logged, not an error


class TestContraSuggestionPass:
    """Dry-run contra suggestion tests (no posting/matching side effects)."""

    def test_suggest_contra_transfers_adds_suggestion(
        self,
        service: AutoReconciliationService,
        mock_db: MagicMock,
        org_id: uuid.UUID,
        bank_account: MockBankAccount,
    ) -> None:
        from datetime import date

        src_line = MockBankStatementLine(
            transaction_type=StatementLineType.debit,
            amount=Decimal("120000.00"),
            transaction_date=date(2026, 2, 12),
            description="Transfer to Zenith 461",
            reference="TRF-12345",
            line_number=10,
        )

        dest_statement = MockBankStatement(
            organization_id=org_id,
            bank_account_id=uuid.uuid4(),
        )
        dest_line = MockBankStatementLine(
            statement_id=dest_statement.statement_id,
            transaction_type=StatementLineType.credit,
            amount=Decimal("120000.00"),
            transaction_date=date(2026, 2, 12),
            description="Incoming transfer from UBA",
            reference="TRF-12345",
            line_number=5,
            is_matched=False,
        )

        bank_ids_result = MagicMock()
        bank_ids_result.all.return_value = [dest_statement.bank_account_id]
        dest_lines_result = MagicMock()
        dest_lines_result.all.return_value = [dest_line]
        mock_db.scalars.side_effect = [bank_ids_result, dest_lines_result]

        def _get_side_effect(model_cls: type, pk: uuid.UUID) -> object | None:
            if (
                model_cls.__name__ == "BankStatement"
                and pk == dest_statement.statement_id
            ):
                return dest_statement
            return None

        mock_db.get.side_effect = _get_side_effect

        result = AutoMatchResult()
        service._suggest_contra_transfers(
            mock_db,
            org_id,
            bank_account,
            [src_line],
            set(),
            result,
        )

        assert len(result.contra_suggestions) == 1
        suggestion = result.contra_suggestions[0]
        assert suggestion["source_line_id"] == str(src_line.line_id)
        assert suggestion["destination_line_id"] == str(dest_line.line_id)
        assert suggestion["score"] >= 90
