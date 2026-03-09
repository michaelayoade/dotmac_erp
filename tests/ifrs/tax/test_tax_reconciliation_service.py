"""
Tests for TaxReconciliationService.

Tests IAS 12 tax rate reconciliation creation, review, and validation.
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.services.finance.tax.tax_reconciliation import (
    TaxReconciliationInput,
    TaxReconciliationService,
)


class MockTaxJurisdiction:
    """Mock TaxJurisdiction model."""

    def __init__(
        self,
        jurisdiction_id=None,
        organization_id=None,
        jurisdiction_code="US",
        jurisdiction_name="United States",
        current_tax_rate=Decimal("0.21"),
    ):
        self.jurisdiction_id = jurisdiction_id or uuid4()
        self.organization_id = organization_id or uuid4()
        self.jurisdiction_code = jurisdiction_code
        self.jurisdiction_name = jurisdiction_name
        self.current_tax_rate = current_tax_rate


class MockTaxReconciliation:
    """Mock TaxReconciliation model."""

    def __init__(
        self,
        reconciliation_id=None,
        organization_id=None,
        fiscal_period_id=None,
        jurisdiction_id=None,
        profit_before_tax=Decimal("1000000.00"),
        statutory_tax_rate=Decimal("0.21"),
        tax_at_statutory_rate=Decimal("210000.00"),
        permanent_differences=Decimal("0"),
        non_deductible_expenses=Decimal("0"),
        non_taxable_income=Decimal("0"),
        rate_differential_on_foreign_income=Decimal("0"),
        tax_credits_utilized=Decimal("0"),
        change_in_unrecognized_dta=Decimal("0"),
        effect_of_tax_rate_change=Decimal("0"),
        prior_year_adjustments=Decimal("0"),
        other_reconciling_items=Decimal("0"),
        other_items_description=None,
        total_tax_expense=Decimal("210000.00"),
        current_tax_expense=Decimal("180000.00"),
        deferred_tax_expense=Decimal("30000.00"),
        effective_tax_rate=Decimal("0.21"),
        rate_variance=Decimal("0"),
        notes=None,
        prepared_by_user_id=None,
        reviewed_by_user_id=None,
        reviewed_at=None,
        created_at=None,
    ):
        self.reconciliation_id = reconciliation_id or uuid4()
        self.organization_id = organization_id or uuid4()
        self.fiscal_period_id = fiscal_period_id or uuid4()
        self.jurisdiction_id = jurisdiction_id or uuid4()
        self.profit_before_tax = profit_before_tax
        self.statutory_tax_rate = statutory_tax_rate
        self.tax_at_statutory_rate = tax_at_statutory_rate
        self.permanent_differences = permanent_differences
        self.non_deductible_expenses = non_deductible_expenses
        self.non_taxable_income = non_taxable_income
        self.rate_differential_on_foreign_income = rate_differential_on_foreign_income
        self.tax_credits_utilized = tax_credits_utilized
        self.change_in_unrecognized_dta = change_in_unrecognized_dta
        self.effect_of_tax_rate_change = effect_of_tax_rate_change
        self.prior_year_adjustments = prior_year_adjustments
        self.other_reconciling_items = other_reconciling_items
        self.other_items_description = other_items_description
        self.total_tax_expense = total_tax_expense
        self.current_tax_expense = current_tax_expense
        self.deferred_tax_expense = deferred_tax_expense
        self.effective_tax_rate = effective_tax_rate
        self.rate_variance = rate_variance
        self.notes = notes
        self.prepared_by_user_id = prepared_by_user_id or uuid4()
        self.reviewed_by_user_id = reviewed_by_user_id
        self.reviewed_at = reviewed_at
        self.created_at = created_at or datetime.now(UTC)


@pytest.fixture
def org_id():
    return uuid4()


@pytest.fixture
def mock_db():
    db = MagicMock()
    return db


@pytest.fixture
def mock_jurisdiction(org_id):
    return MockTaxJurisdiction(organization_id=org_id)


class TestCalculateEffectiveTaxRate:
    """Tests for calculate_effective_tax_rate method."""

    def test_calculate_positive_profit(self):
        """Test effective rate with positive profit."""
        result = TaxReconciliationService.calculate_effective_tax_rate(
            total_tax_expense=Decimal("210000"),
            profit_before_tax=Decimal("1000000"),
        )

        assert result == Decimal("0.210000")

    def test_calculate_with_loss(self):
        """Test effective rate with loss (negative profit)."""
        result = TaxReconciliationService.calculate_effective_tax_rate(
            total_tax_expense=Decimal("-50000"),
            profit_before_tax=Decimal("-200000"),
        )

        # -50000 / -200000 = 0.25
        assert result == Decimal("0.250000")

    def test_calculate_zero_profit(self):
        """Test effective rate with zero profit."""
        result = TaxReconciliationService.calculate_effective_tax_rate(
            total_tax_expense=Decimal("10000"),
            profit_before_tax=Decimal("0"),
        )

        assert result == Decimal("0")

    def test_calculate_small_amounts(self):
        """Test effective rate with small amounts."""
        result = TaxReconciliationService.calculate_effective_tax_rate(
            total_tax_expense=Decimal("15.75"),
            profit_before_tax=Decimal("100.00"),
        )

        assert result == Decimal("0.157500")


class TestCreateReconciliation:
    """Tests for create_reconciliation method."""

    def test_create_reconciliation_success(self, mock_db, org_id, mock_jurisdiction):
        """Test successful reconciliation creation."""
        mock_db.get.return_value = mock_jurisdiction
        mock_db.scalars.return_value.first.return_value = None

        user_id = uuid4()
        fiscal_period_id = uuid4()

        input_data = TaxReconciliationInput(
            fiscal_period_id=fiscal_period_id,
            jurisdiction_id=mock_jurisdiction.jurisdiction_id,
            profit_before_tax=Decimal("1000000.00"),
            current_tax_expense=Decimal("180000.00"),
            deferred_tax_expense=Decimal("30000.00"),
        )

        TaxReconciliationService.create_reconciliation(
            mock_db, org_id, input_data, user_id
        )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once()

    def test_create_reconciliation_with_adjustments(
        self, mock_db, org_id, mock_jurisdiction
    ):
        """Test reconciliation with various adjustments."""
        mock_db.get.return_value = mock_jurisdiction
        mock_db.scalars.return_value.first.return_value = None

        user_id = uuid4()

        input_data = TaxReconciliationInput(
            fiscal_period_id=uuid4(),
            jurisdiction_id=mock_jurisdiction.jurisdiction_id,
            profit_before_tax=Decimal("1000000.00"),
            current_tax_expense=Decimal("180000.00"),
            deferred_tax_expense=Decimal("30000.00"),
            permanent_differences=Decimal("50000.00"),
            non_deductible_expenses=Decimal("10000.00"),
            non_taxable_income=Decimal("20000.00"),
            tax_credits_utilized=Decimal("5000.00"),
        )

        TaxReconciliationService.create_reconciliation(
            mock_db, org_id, input_data, user_id
        )

        mock_db.add.assert_called_once()

    def test_create_reconciliation_invalid_jurisdiction(self, mock_db, org_id):
        """Test that invalid jurisdiction raises error."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        input_data = TaxReconciliationInput(
            fiscal_period_id=uuid4(),
            jurisdiction_id=uuid4(),
            profit_before_tax=Decimal("1000000.00"),
            current_tax_expense=Decimal("180000.00"),
            deferred_tax_expense=Decimal("30000.00"),
        )

        with pytest.raises(HTTPException) as exc:
            TaxReconciliationService.create_reconciliation(
                mock_db, org_id, input_data, uuid4()
            )

        assert exc.value.status_code == 404
        assert "Jurisdiction not found" in exc.value.detail

    def test_create_reconciliation_wrong_organization(
        self, mock_db, org_id, mock_jurisdiction
    ):
        """Test that jurisdiction from different org raises error."""
        from fastapi import HTTPException

        mock_jurisdiction.organization_id = uuid4()  # Different org
        mock_db.get.return_value = mock_jurisdiction

        input_data = TaxReconciliationInput(
            fiscal_period_id=uuid4(),
            jurisdiction_id=mock_jurisdiction.jurisdiction_id,
            profit_before_tax=Decimal("1000000.00"),
            current_tax_expense=Decimal("180000.00"),
            deferred_tax_expense=Decimal("30000.00"),
        )

        with pytest.raises(HTTPException) as exc:
            TaxReconciliationService.create_reconciliation(
                mock_db, org_id, input_data, uuid4()
            )

        assert exc.value.status_code == 404

    def test_create_reconciliation_duplicate_fails(
        self, mock_db, org_id, mock_jurisdiction
    ):
        """Test that duplicate reconciliation raises error."""
        from fastapi import HTTPException

        mock_db.get.return_value = mock_jurisdiction

        existing = MockTaxReconciliation(organization_id=org_id)
        mock_db.scalars.return_value.first.return_value = existing

        input_data = TaxReconciliationInput(
            fiscal_period_id=uuid4(),
            jurisdiction_id=mock_jurisdiction.jurisdiction_id,
            profit_before_tax=Decimal("1000000.00"),
            current_tax_expense=Decimal("180000.00"),
            deferred_tax_expense=Decimal("30000.00"),
        )

        with pytest.raises(HTTPException) as exc:
            TaxReconciliationService.create_reconciliation(
                mock_db, org_id, input_data, uuid4()
            )

        assert exc.value.status_code == 400
        assert "already exists" in exc.value.detail


class TestReviewReconciliation:
    """Tests for review_reconciliation method."""

    def test_review_reconciliation_success(self, mock_db, org_id):
        """Test successful review."""
        preparer_id = uuid4()
        reviewer_id = uuid4()

        reconciliation = MockTaxReconciliation(
            organization_id=org_id,
            prepared_by_user_id=preparer_id,
        )
        mock_db.get.return_value = reconciliation

        TaxReconciliationService.review_reconciliation(
            mock_db, org_id, reconciliation.reconciliation_id, reviewer_id
        )

        assert reconciliation.reviewed_by_user_id == reviewer_id
        assert reconciliation.reviewed_at is not None
        mock_db.commit.assert_called_once()

    def test_review_reconciliation_not_found(self, mock_db, org_id):
        """Test review of non-existent reconciliation."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            TaxReconciliationService.review_reconciliation(
                mock_db, org_id, uuid4(), uuid4()
            )

        assert exc.value.status_code == 404

    def test_review_reconciliation_wrong_org(self, mock_db, org_id):
        """Test review of reconciliation from different org."""
        from fastapi import HTTPException

        reconciliation = MockTaxReconciliation(organization_id=uuid4())
        mock_db.get.return_value = reconciliation

        with pytest.raises(HTTPException) as exc:
            TaxReconciliationService.review_reconciliation(
                mock_db, org_id, reconciliation.reconciliation_id, uuid4()
            )

        assert exc.value.status_code == 404

    def test_review_reconciliation_sod_violation(self, mock_db, org_id):
        """Test that preparer cannot review their own reconciliation."""
        from fastapi import HTTPException

        user_id = uuid4()

        reconciliation = MockTaxReconciliation(
            organization_id=org_id,
            prepared_by_user_id=user_id,
        )
        mock_db.get.return_value = reconciliation

        with pytest.raises(HTTPException) as exc:
            TaxReconciliationService.review_reconciliation(
                mock_db, org_id, reconciliation.reconciliation_id, user_id
            )

        assert exc.value.status_code == 400
        assert "Segregation of duties" in exc.value.detail


class TestGetReconciliationLines:
    """Tests for get_reconciliation_lines method."""

    def test_basic_reconciliation_lines(self):
        """Test basic reconciliation with just statutory rate."""
        reconciliation = MockTaxReconciliation(
            profit_before_tax=Decimal("1000000.00"),
            statutory_tax_rate=Decimal("0.21"),
            tax_at_statutory_rate=Decimal("210000.00"),
            total_tax_expense=Decimal("210000.00"),
            effective_tax_rate=Decimal("0.21"),
        )

        lines = TaxReconciliationService.get_reconciliation_lines(reconciliation)

        # Should have 2 lines: statutory rate and total
        assert len(lines) == 2
        assert lines[0].description == "Tax at statutory rate"
        assert lines[0].amount == Decimal("210000.00")
        assert lines[-1].description == "Total tax expense"

    def test_reconciliation_with_permanent_differences(self):
        """Test reconciliation with permanent differences."""
        reconciliation = MockTaxReconciliation(
            profit_before_tax=Decimal("1000000.00"),
            statutory_tax_rate=Decimal("0.21"),
            tax_at_statutory_rate=Decimal("210000.00"),
            permanent_differences=Decimal("10000.00"),
            total_tax_expense=Decimal("220000.00"),
            effective_tax_rate=Decimal("0.22"),
        )

        lines = TaxReconciliationService.get_reconciliation_lines(reconciliation)

        # Should have 3 lines: statutory, permanent diff, total
        assert len(lines) == 3
        assert any(l.description == "Permanent differences" for l in lines)

    def test_reconciliation_with_all_adjustments(self):
        """Test reconciliation with all adjustment types."""
        reconciliation = MockTaxReconciliation(
            profit_before_tax=Decimal("1000000.00"),
            statutory_tax_rate=Decimal("0.21"),
            tax_at_statutory_rate=Decimal("210000.00"),
            permanent_differences=Decimal("5000.00"),
            non_deductible_expenses=Decimal("8000.00"),
            non_taxable_income=Decimal("3000.00"),
            rate_differential_on_foreign_income=Decimal("2000.00"),
            tax_credits_utilized=Decimal("4000.00"),
            change_in_unrecognized_dta=Decimal("1000.00"),
            effect_of_tax_rate_change=Decimal("500.00"),
            prior_year_adjustments=Decimal("-1500.00"),
            other_reconciling_items=Decimal("2000.00"),
            other_items_description="Misc adjustments",
            total_tax_expense=Decimal("220000.00"),
            effective_tax_rate=Decimal("0.22"),
        )

        lines = TaxReconciliationService.get_reconciliation_lines(reconciliation)

        # Should have 11 lines (statutory + 9 adjustments + total)
        assert len(lines) == 11

        descriptions = [l.description for l in lines]
        assert "Tax at statutory rate" in descriptions
        assert "Permanent differences" in descriptions
        assert "Non-deductible expenses" in descriptions
        assert "Non-taxable income" in descriptions
        assert "Rate differential on foreign income" in descriptions
        assert "Tax credits utilized" in descriptions
        assert "Change in unrecognized deferred tax assets" in descriptions
        assert "Effect of tax rate changes" in descriptions
        assert "Prior year adjustments" in descriptions
        assert "Misc adjustments" in descriptions
        assert "Total tax expense" in descriptions

    def test_reconciliation_lines_zero_profit(self):
        """Test lines with zero profit (no rate effect calculation)."""
        reconciliation = MockTaxReconciliation(
            profit_before_tax=Decimal("0"),
            statutory_tax_rate=Decimal("0.21"),
            tax_at_statutory_rate=Decimal("0"),
            permanent_differences=Decimal("1000.00"),
            total_tax_expense=Decimal("1000.00"),
            effective_tax_rate=Decimal("0"),
        )

        lines = TaxReconciliationService.get_reconciliation_lines(reconciliation)

        # Should not raise division by zero
        perm_diff_line = next(
            l for l in lines if l.description == "Permanent differences"
        )
        assert perm_diff_line.rate_effect == Decimal("0")


class TestValidateReconciliation:
    """Tests for validate_reconciliation method."""

    def test_valid_reconciliation(self):
        """Test validation of balanced reconciliation."""
        reconciliation = MockTaxReconciliation(
            tax_at_statutory_rate=Decimal("210000.00"),
            permanent_differences=Decimal("0"),
            non_deductible_expenses=Decimal("0"),
            non_taxable_income=Decimal("0"),
            rate_differential_on_foreign_income=Decimal("0"),
            tax_credits_utilized=Decimal("0"),
            change_in_unrecognized_dta=Decimal("0"),
            effect_of_tax_rate_change=Decimal("0"),
            prior_year_adjustments=Decimal("0"),
            other_reconciling_items=Decimal("0"),
            total_tax_expense=Decimal("210000.00"),
        )

        is_valid, error = TaxReconciliationService.validate_reconciliation(
            reconciliation
        )

        assert is_valid is True
        assert error is None

    def test_valid_reconciliation_with_adjustments(self):
        """Test validation with balancing adjustments."""
        reconciliation = MockTaxReconciliation(
            tax_at_statutory_rate=Decimal("210000.00"),
            permanent_differences=Decimal("5000.00"),
            non_deductible_expenses=Decimal("3000.00"),
            non_taxable_income=Decimal("2000.00"),  # Subtracted
            rate_differential_on_foreign_income=Decimal("0"),
            tax_credits_utilized=Decimal("1000.00"),  # Subtracted
            change_in_unrecognized_dta=Decimal("0"),
            effect_of_tax_rate_change=Decimal("0"),
            prior_year_adjustments=Decimal("0"),
            other_reconciling_items=Decimal("0"),
            # 210000 + 5000 + 3000 - 2000 - 1000 = 215000
            total_tax_expense=Decimal("215000.00"),
        )

        is_valid, error = TaxReconciliationService.validate_reconciliation(
            reconciliation
        )

        assert is_valid is True
        assert error is None

    def test_invalid_reconciliation_not_balanced(self):
        """Test validation of unbalanced reconciliation."""
        reconciliation = MockTaxReconciliation(
            tax_at_statutory_rate=Decimal("210000.00"),
            permanent_differences=Decimal("0"),
            non_deductible_expenses=Decimal("0"),
            non_taxable_income=Decimal("0"),
            rate_differential_on_foreign_income=Decimal("0"),
            tax_credits_utilized=Decimal("0"),
            change_in_unrecognized_dta=Decimal("0"),
            effect_of_tax_rate_change=Decimal("0"),
            prior_year_adjustments=Decimal("0"),
            other_reconciling_items=Decimal("0"),
            total_tax_expense=Decimal("220000.00"),  # Doesn't match
        )

        is_valid, error = TaxReconciliationService.validate_reconciliation(
            reconciliation
        )

        assert is_valid is False
        assert "does not balance" in error

    def test_valid_within_tolerance(self):
        """Test validation passes within tolerance."""
        reconciliation = MockTaxReconciliation(
            tax_at_statutory_rate=Decimal("210000.00"),
            permanent_differences=Decimal("0"),
            non_deductible_expenses=Decimal("0"),
            non_taxable_income=Decimal("0"),
            rate_differential_on_foreign_income=Decimal("0"),
            tax_credits_utilized=Decimal("0"),
            change_in_unrecognized_dta=Decimal("0"),
            effect_of_tax_rate_change=Decimal("0"),
            prior_year_adjustments=Decimal("0"),
            other_reconciling_items=Decimal("0"),
            total_tax_expense=Decimal("210000.005"),  # Within 0.01 tolerance
        )

        is_valid, error = TaxReconciliationService.validate_reconciliation(
            reconciliation
        )

        assert is_valid is True


class TestGetReconciliation:
    """Tests for get method."""

    def test_get_existing_reconciliation(self, mock_db):
        """Test getting existing reconciliation."""
        reconciliation = MockTaxReconciliation()
        mock_db.get.return_value = reconciliation

        result = TaxReconciliationService.get(
            mock_db, str(reconciliation.reconciliation_id)
        )

        assert result == reconciliation

    def test_get_nonexistent_raises_error(self, mock_db):
        """Test that getting nonexistent reconciliation raises error."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            TaxReconciliationService.get(mock_db, str(uuid4()))

        assert exc.value.status_code == 404


class TestGetByPeriodJurisdiction:
    """Tests for get_by_period_jurisdiction method."""

    def test_get_by_period_jurisdiction_found(self, mock_db, org_id):
        """Test finding reconciliation by period and jurisdiction."""
        reconciliation = MockTaxReconciliation(organization_id=org_id)

        mock_db.scalars.return_value.first.return_value = reconciliation

        result = TaxReconciliationService.get_by_period_jurisdiction(
            mock_db,
            str(org_id),
            str(reconciliation.fiscal_period_id),
            str(reconciliation.jurisdiction_id),
        )

        assert result == reconciliation

    def test_get_by_period_jurisdiction_not_found(self, mock_db, org_id):
        """Test when no reconciliation found."""
        mock_db.scalars.return_value.first.return_value = None

        result = TaxReconciliationService.get_by_period_jurisdiction(
            mock_db, str(org_id), str(uuid4()), str(uuid4())
        )

        assert result is None


class TestListReconciliations:
    """Tests for list method."""

    def test_list_all_reconciliations(self, mock_db, org_id):
        """Test listing all reconciliations."""
        reconciliations = [MockTaxReconciliation() for _ in range(3)]

        mock_db.scalars.return_value.all.return_value = reconciliations

        result = TaxReconciliationService.list(mock_db, organization_id=str(org_id))

        assert len(result) == 3

    def test_list_with_period_filter(self, mock_db, org_id):
        """Test listing with fiscal period filter."""
        mock_db.scalars.return_value.all.return_value = []

        result = TaxReconciliationService.list(
            mock_db,
            organization_id=str(org_id),
            fiscal_period_id=str(uuid4()),
        )

        assert result == []

    def test_list_reviewed_only(self, mock_db, org_id):
        """Test listing only reviewed reconciliations."""
        mock_db.scalars.return_value.all.return_value = []

        result = TaxReconciliationService.list(
            mock_db,
            organization_id=str(org_id),
            is_reviewed=True,
        )

        assert result == []

    def test_list_unreviewed_only(self, mock_db, org_id):
        """Test listing only unreviewed reconciliations."""
        mock_db.scalars.return_value.all.return_value = []

        result = TaxReconciliationService.list(
            mock_db,
            organization_id=str(org_id),
            is_reviewed=False,
        )

        assert result == []

    def test_list_with_pagination(self, mock_db, org_id):
        """Test listing with pagination."""
        mock_db.scalars.return_value.all.return_value = []

        result = TaxReconciliationService.list(
            mock_db,
            organization_id=str(org_id),
            limit=10,
            offset=20,
        )

        assert result == []
