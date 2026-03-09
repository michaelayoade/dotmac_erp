"""
Tests for ExpenseService.

Tests expense entry creation, workflow (submit/approve/reject), and GL posting.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.models.finance.exp.expense_entry import ExpenseStatus, PaymentMethod
from app.services.finance.exp.expense import ExpenseService


class MockExpenseEntry:
    """Mock ExpenseEntry model."""

    def __init__(
        self,
        expense_id=None,
        organization_id=None,
        expense_number="EXP-202501-0001",
        expense_date=None,
        expense_account_id=None,
        payment_account_id=None,
        amount=Decimal("100.00"),
        tax_amount=Decimal("0"),
        tax_code_id=None,
        currency_code="USD",
        description="Test Expense",
        payment_method=PaymentMethod.CASH,
        payee="Test Vendor",
        receipt_reference=None,
        notes=None,
        project_id=None,
        cost_center_id=None,
        business_unit_id=None,
        status=ExpenseStatus.DRAFT,
        journal_entry_id=None,
        created_by=None,
        submitted_by=None,
        submitted_at=None,
        approved_by=None,
        approved_at=None,
        posted_by=None,
        posted_at=None,
        updated_by=None,
        updated_at=None,
    ):
        self.expense_id = expense_id or uuid4()
        self.organization_id = organization_id or uuid4()
        self.expense_number = expense_number
        self.expense_date = expense_date or date.today()
        self.expense_account_id = expense_account_id or uuid4()
        self.payment_account_id = payment_account_id
        self.amount = amount
        self.tax_amount = tax_amount
        self.tax_code_id = tax_code_id
        self.currency_code = currency_code
        self.description = description
        self.payment_method = payment_method
        self.payee = payee
        self.receipt_reference = receipt_reference
        self.notes = notes
        self.project_id = project_id
        self.cost_center_id = cost_center_id
        self.business_unit_id = business_unit_id
        self.status = status
        self.journal_entry_id = journal_entry_id
        self.created_by = created_by
        self.submitted_by = submitted_by
        self.submitted_at = submitted_at
        self.approved_by = approved_by
        self.approved_at = approved_at
        self.posted_by = posted_by
        self.posted_at = posted_at
        self.updated_by = updated_by
        self.updated_at = updated_at


class MockJournalEntry:
    """Mock JournalEntry model."""

    def __init__(self, journal_entry_id=None, journal_number="JE-EXP-202501-0001"):
        self.journal_entry_id = journal_entry_id or uuid4()
        self.journal_number = journal_number


class MockTaxCode:
    """Mock TaxCode model."""

    def __init__(
        self, tax_code_id=None, input_tax_account_id=None, tax_paid_account_id=None
    ):
        self.tax_code_id = tax_code_id or uuid4()
        self.input_tax_account_id = input_tax_account_id or uuid4()
        self.tax_paid_account_id = tax_paid_account_id or self.input_tax_account_id


@pytest.fixture
def org_id():
    return uuid4()


@pytest.fixture
def user_id():
    return uuid4()


@pytest.fixture
def mock_db():
    db = MagicMock()
    return db


class TestGenerateExpenseNumber:
    """Tests for generate_expense_number method."""

    def test_generate_first_expense_number(self, mock_db, org_id):
        """Test generating first expense number of the month."""
        mock_db.scalars.return_value.first.return_value = None

        result = ExpenseService.generate_expense_number(mock_db, org_id)

        today = date.today()
        expected_prefix = f"EXP-{today.strftime('%Y%m')}-"
        assert result.startswith(expected_prefix)
        assert result.endswith("0001")

    def test_generate_sequential_expense_number(self, mock_db, org_id):
        """Test generating sequential expense number."""
        today = date.today()
        last_expense = MagicMock()
        last_expense.expense_number = f"EXP-{today.strftime('%Y%m')}-0005"

        mock_db.scalars.return_value.first.return_value = last_expense

        result = ExpenseService.generate_expense_number(mock_db, org_id)

        assert result.endswith("0006")

    def test_generate_expense_number_invalid_previous(self, mock_db, org_id):
        """Test generating expense number when previous has invalid format."""
        today = date.today()
        last_expense = MagicMock()
        last_expense.expense_number = f"EXP-{today.strftime('%Y%m')}-INVALID"

        mock_db.scalars.return_value.first.return_value = last_expense

        result = ExpenseService.generate_expense_number(mock_db, org_id)

        # Should start from 1 if parsing fails
        assert result.endswith("0001")


class TestCreateExpense:
    """Tests for create method."""

    def test_create_expense_success(self, mock_db, org_id, user_id):
        """Test successful expense creation."""
        mock_db.scalars.return_value.first.return_value = None

        expense_account_id = uuid4()

        ExpenseService.create(
            db=mock_db,
            organization_id=str(org_id),
            expense_date=date.today(),
            expense_account_id=str(expense_account_id),
            amount=Decimal("150.00"),
            description="Office Supplies",
            payment_method=PaymentMethod.CASH,
            created_by=str(user_id),
        )

        mock_db.add.assert_called_once()
        mock_db.flush.assert_called_once()

    def test_create_expense_with_tax(self, mock_db, org_id, user_id):
        """Test expense creation with tax."""
        mock_db.scalars.return_value.first.return_value = None

        expense_account_id = uuid4()
        tax_code_id = uuid4()

        ExpenseService.create(
            db=mock_db,
            organization_id=str(org_id),
            expense_date=date.today(),
            expense_account_id=str(expense_account_id),
            amount=Decimal("100.00"),
            tax_amount=Decimal("20.00"),
            tax_code_id=str(tax_code_id),
            description="Taxable Expense",
            payment_method=PaymentMethod.CORPORATE_CARD,
            created_by=str(user_id),
        )

        mock_db.add.assert_called_once()

    def test_create_expense_with_payment_account(self, mock_db, org_id, user_id):
        """Test expense creation with payment account."""
        mock_db.scalars.return_value.first.return_value = None

        expense_account_id = uuid4()
        payment_account_id = uuid4()

        ExpenseService.create(
            db=mock_db,
            organization_id=str(org_id),
            expense_date=date.today(),
            expense_account_id=str(expense_account_id),
            payment_account_id=str(payment_account_id),
            amount=Decimal("200.00"),
            description="Expense with Payment Account",
            payment_method=PaymentMethod.BANK_TRANSFER,
            created_by=str(user_id),
        )

        mock_db.add.assert_called_once()

    def test_create_expense_with_all_fields(self, mock_db, org_id, user_id):
        """Test expense creation with all optional fields."""
        mock_db.scalars.return_value.first.return_value = None

        ExpenseService.create(
            db=mock_db,
            organization_id=str(org_id),
            expense_date=date.today(),
            expense_account_id=str(uuid4()),
            payment_account_id=str(uuid4()),
            amount=Decimal("500.00"),
            tax_amount=Decimal("50.00"),
            tax_code_id=str(uuid4()),
            description="Complete Expense",
            payment_method=PaymentMethod.PETTY_CASH,
            created_by=str(user_id),
            currency_code="EUR",
            payee="Vendor ABC",
            receipt_reference="REC-001",
            notes="Test notes",
            project_id=str(uuid4()),
            cost_center_id=str(uuid4()),
            business_unit_id=str(uuid4()),
        )

        mock_db.add.assert_called_once()


class TestSubmitExpense:
    """Tests for submit method."""

    def test_submit_expense_success(self, mock_db, user_id):
        """Test successful expense submission."""
        expense = MockExpenseEntry(status=ExpenseStatus.DRAFT)
        mock_db.scalars.return_value.first.return_value = expense

        ExpenseService.submit(
            mock_db, str(expense.organization_id), str(expense.expense_id), str(user_id)
        )

        assert expense.status == ExpenseStatus.SUBMITTED
        assert expense.submitted_by is not None
        assert expense.submitted_at is not None
        mock_db.flush.assert_called_once()

    def test_submit_expense_not_found(self, mock_db, user_id):
        """Test submitting non-existent expense."""
        mock_db.scalars.return_value.first.return_value = None

        with pytest.raises(ValueError) as exc:
            ExpenseService.submit(mock_db, str(uuid4()), str(uuid4()), str(user_id))

        assert "Expense not found" in str(exc.value)

    def test_submit_expense_wrong_status(self, mock_db, user_id):
        """Test submitting expense in wrong status."""
        expense = MockExpenseEntry(status=ExpenseStatus.APPROVED)
        mock_db.scalars.return_value.first.return_value = expense

        with pytest.raises(ValueError) as exc:
            ExpenseService.submit(
                mock_db,
                str(expense.organization_id),
                str(expense.expense_id),
                str(user_id),
            )

        assert "Cannot submit expense" in str(exc.value)


class TestApproveExpense:
    """Tests for approve method."""

    def test_approve_expense_success(self, mock_db, user_id):
        """Test successful expense approval."""
        expense = MockExpenseEntry(status=ExpenseStatus.SUBMITTED)
        mock_db.scalars.return_value.first.return_value = expense

        ExpenseService.approve(
            mock_db, str(expense.organization_id), str(expense.expense_id), str(user_id)
        )

        assert expense.status == ExpenseStatus.APPROVED
        assert expense.approved_by is not None
        assert expense.approved_at is not None
        mock_db.flush.assert_called_once()

    def test_approve_expense_not_found(self, mock_db, user_id):
        """Test approving non-existent expense."""
        mock_db.scalars.return_value.first.return_value = None

        with pytest.raises(ValueError) as exc:
            ExpenseService.approve(mock_db, str(uuid4()), str(uuid4()), str(user_id))

        assert "Expense not found" in str(exc.value)

    def test_approve_expense_wrong_status(self, mock_db, user_id):
        """Test approving expense in wrong status."""
        expense = MockExpenseEntry(status=ExpenseStatus.DRAFT)
        mock_db.scalars.return_value.first.return_value = expense

        with pytest.raises(ValueError) as exc:
            ExpenseService.approve(
                mock_db,
                str(expense.organization_id),
                str(expense.expense_id),
                str(user_id),
            )

        assert "Cannot approve expense" in str(exc.value)


class TestRejectExpense:
    """Tests for reject method."""

    def test_reject_submitted_expense(self, mock_db, user_id):
        """Test rejecting submitted expense."""
        expense = MockExpenseEntry(status=ExpenseStatus.SUBMITTED)
        mock_db.scalars.return_value.first.return_value = expense

        ExpenseService.reject(
            mock_db, str(expense.organization_id), str(expense.expense_id), str(user_id)
        )

        assert expense.status == ExpenseStatus.REJECTED
        assert expense.updated_by is not None
        mock_db.flush.assert_called_once()

    def test_reject_approved_expense(self, mock_db, user_id):
        """Test rejecting approved expense."""
        expense = MockExpenseEntry(status=ExpenseStatus.APPROVED)
        mock_db.scalars.return_value.first.return_value = expense

        ExpenseService.reject(
            mock_db, str(expense.organization_id), str(expense.expense_id), str(user_id)
        )

        assert expense.status == ExpenseStatus.REJECTED

    def test_reject_expense_not_found(self, mock_db, user_id):
        """Test rejecting non-existent expense."""
        mock_db.scalars.return_value.first.return_value = None

        with pytest.raises(ValueError) as exc:
            ExpenseService.reject(mock_db, str(uuid4()), str(uuid4()), str(user_id))

        assert "Expense not found" in str(exc.value)

    def test_reject_expense_wrong_status(self, mock_db, user_id):
        """Test rejecting expense in wrong status."""
        expense = MockExpenseEntry(status=ExpenseStatus.DRAFT)
        mock_db.scalars.return_value.first.return_value = expense

        with pytest.raises(ValueError) as exc:
            ExpenseService.reject(
                mock_db,
                str(expense.organization_id),
                str(expense.expense_id),
                str(user_id),
            )

        assert "Cannot reject expense" in str(exc.value)


class TestPostExpense:
    """Tests for post method."""

    def test_post_expense_success(self, mock_db, user_id):
        """Test successful expense posting."""
        payment_account_id = uuid4()
        expense = MockExpenseEntry(
            status=ExpenseStatus.APPROVED,
            payment_account_id=payment_account_id,
            amount=Decimal("100.00"),
            tax_amount=Decimal("0"),
        )

        # Mock expense lookup via query().filter().first()
        mock_db.scalars.return_value.first.return_value = expense

        # Mock fiscal period lookup via db.get()
        fiscal_period_id = uuid4()
        mock_fiscal_period = MagicMock()
        mock_fiscal_period.organization_id = expense.organization_id
        mock_db.get.return_value = mock_fiscal_period

        journal = MagicMock()
        journal.journal_entry_id = uuid4()

        with (
            patch(
                "app.services.finance.gl.period_guard.PeriodGuardService.get_period_for_date",
                return_value=MagicMock(fiscal_period_id=fiscal_period_id),
            ),
            patch(
                "app.services.finance.gl.period_guard.PeriodGuardService.require_open_period",
                return_value=fiscal_period_id,
            ),
            patch(
                "app.services.finance.posting.base.BasePostingAdapter.create_and_approve_journal",
                return_value=(journal, None),
            ) as mock_create,
            patch(
                "app.services.finance.posting.base.BasePostingAdapter.post_to_ledger",
                return_value=MagicMock(
                    success=True, posting_batch_id=uuid4(), message="ok"
                ),
            ) as mock_post,
        ):
            ExpenseService.post(
                mock_db,
                str(expense.organization_id),
                str(expense.expense_id),
                str(user_id),
                str(fiscal_period_id),
            )

        mock_create.assert_called_once()
        mock_post.assert_called_once()

        assert expense.status == ExpenseStatus.POSTED
        assert expense.posted_by is not None
        assert expense.posted_at is not None
        assert expense.journal_entry_id == journal.journal_entry_id

    def test_post_expense_with_tax(self, mock_db, user_id):
        """Test posting expense with tax."""
        payment_account_id = uuid4()
        tax_code_id = uuid4()
        expense = MockExpenseEntry(
            status=ExpenseStatus.APPROVED,
            payment_account_id=payment_account_id,
            amount=Decimal("100.00"),
            tax_amount=Decimal("20.00"),
            tax_code_id=tax_code_id,
        )

        # Mock expense lookup via query().filter().first()
        mock_db.scalars.return_value.first.return_value = expense

        # Mock fiscal period and tax code lookups via db.get()
        tax_code = MockTaxCode(tax_code_id=tax_code_id)
        mock_fiscal_period = MagicMock()
        mock_fiscal_period.organization_id = expense.organization_id
        fiscal_period_id = uuid4()

        def mock_get(model, id):
            from app.models.finance.tax.tax_code import TaxCode

            if model is TaxCode:
                return tax_code
            # FiscalPeriod lookup
            return mock_fiscal_period

        mock_db.get.side_effect = mock_get

        journal = MagicMock()
        journal.journal_entry_id = uuid4()

        with (
            patch(
                "app.services.finance.gl.period_guard.PeriodGuardService.get_period_for_date",
                return_value=MagicMock(fiscal_period_id=fiscal_period_id),
            ),
            patch(
                "app.services.finance.gl.period_guard.PeriodGuardService.require_open_period",
                return_value=fiscal_period_id,
            ),
            patch(
                "app.services.finance.posting.base.BasePostingAdapter.create_and_approve_journal",
                return_value=(journal, None),
            ) as mock_create,
            patch(
                "app.services.finance.posting.base.BasePostingAdapter.post_to_ledger",
                return_value=MagicMock(
                    success=True, posting_batch_id=uuid4(), message="ok"
                ),
            ),
        ):
            ExpenseService.post(
                mock_db,
                str(expense.organization_id),
                str(expense.expense_id),
                str(user_id),
                str(fiscal_period_id),
            )

        journal_input = mock_create.call_args.kwargs["journal_input"]
        assert len(journal_input.lines) == 3

        assert expense.status == ExpenseStatus.POSTED
        assert expense.journal_entry_id == journal.journal_entry_id

    def test_post_expense_not_found(self, mock_db, user_id):
        """Test posting non-existent expense."""
        mock_db.scalars.return_value.first.return_value = None

        with pytest.raises(ValueError) as exc:
            ExpenseService.post(
                mock_db, str(uuid4()), str(uuid4()), str(user_id), str(uuid4())
            )

        assert "Expense not found" in str(exc.value)

    def test_post_expense_wrong_status(self, mock_db, user_id):
        """Test posting expense in wrong status."""
        expense = MockExpenseEntry(status=ExpenseStatus.DRAFT)
        mock_db.scalars.return_value.first.return_value = expense

        with pytest.raises(ValueError) as exc:
            ExpenseService.post(
                mock_db,
                str(expense.organization_id),
                str(expense.expense_id),
                str(user_id),
                str(uuid4()),
            )

        assert "Cannot post expense" in str(exc.value)

    def test_post_expense_no_payment_account(self, mock_db, user_id):
        """Test posting expense without payment account."""
        expense = MockExpenseEntry(
            status=ExpenseStatus.APPROVED,
            payment_account_id=None,
        )
        mock_db.scalars.return_value.first.return_value = expense

        with pytest.raises(ValueError) as exc:
            ExpenseService.post(
                mock_db,
                str(expense.organization_id),
                str(expense.expense_id),
                str(user_id),
                str(uuid4()),
            )

        assert "Payment account is required" in str(exc.value)


class TestVoidExpense:
    """Tests for void method."""

    def test_void_draft_expense(self, mock_db, user_id):
        """Test voiding draft expense."""
        expense = MockExpenseEntry(status=ExpenseStatus.DRAFT)
        mock_db.get.return_value = expense

        ExpenseService.void(mock_db, str(expense.expense_id), str(user_id))

        assert expense.status == ExpenseStatus.VOID
        assert expense.updated_by is not None
        mock_db.flush.assert_called_once()

    def test_void_submitted_expense(self, mock_db, user_id):
        """Test voiding submitted expense."""
        expense = MockExpenseEntry(status=ExpenseStatus.SUBMITTED)
        mock_db.get.return_value = expense

        ExpenseService.void(mock_db, str(expense.expense_id), str(user_id))

        assert expense.status == ExpenseStatus.VOID

    def test_void_approved_expense(self, mock_db, user_id):
        """Test voiding approved expense."""
        expense = MockExpenseEntry(status=ExpenseStatus.APPROVED)
        mock_db.get.return_value = expense

        ExpenseService.void(mock_db, str(expense.expense_id), str(user_id))

        assert expense.status == ExpenseStatus.VOID

    def test_void_expense_not_found(self, mock_db, user_id):
        """Test voiding non-existent expense."""
        mock_db.get.return_value = None

        with pytest.raises(ValueError) as exc:
            ExpenseService.void(mock_db, str(uuid4()), str(user_id))

        assert "Expense not found" in str(exc.value)

    def test_void_posted_expense_fails(self, mock_db, user_id):
        """Test that voiding posted expense fails."""
        expense = MockExpenseEntry(status=ExpenseStatus.POSTED)
        mock_db.get.return_value = expense

        with pytest.raises(ValueError) as exc:
            ExpenseService.void(mock_db, str(expense.expense_id), str(user_id))

        assert "Cannot void posted expense" in str(exc.value)


class TestExpenseWorkflow:
    """Integration tests for complete expense workflow."""

    def test_full_expense_workflow(self, mock_db, org_id, user_id):
        """Test complete expense workflow: create -> submit -> approve."""
        mock_db.scalars.return_value.first.return_value = None

        # Create
        expense = MockExpenseEntry(status=ExpenseStatus.DRAFT)
        mock_db.scalars.return_value.first.return_value = expense

        # Submit
        ExpenseService.submit(
            mock_db, str(expense.organization_id), str(expense.expense_id), str(user_id)
        )
        assert expense.status == ExpenseStatus.SUBMITTED

        # Approve
        approver_id = uuid4()
        ExpenseService.approve(
            mock_db,
            str(expense.organization_id),
            str(expense.expense_id),
            str(approver_id),
        )
        assert expense.status == ExpenseStatus.APPROVED

    def test_rejection_workflow(self, mock_db, user_id):
        """Test expense rejection workflow."""
        expense = MockExpenseEntry(status=ExpenseStatus.SUBMITTED)
        mock_db.scalars.return_value.first.return_value = expense

        # Reject
        ExpenseService.reject(
            mock_db, str(expense.organization_id), str(expense.expense_id), str(user_id)
        )
        assert expense.status == ExpenseStatus.REJECTED
