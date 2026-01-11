"""
Tests for TaxCodeService and TaxJurisdictionService.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.models.ifrs.tax.tax_code import TaxType
from tests.ifrs.tax.conftest import (
    MockTaxCode,
    MockTaxJurisdiction,
)


class TestTaxCodeService:
    """Tests for TaxCodeService."""

    def test_create_tax_code_success(self, mock_db, org_id, mock_jurisdiction):
        """Test successful tax code creation."""
        from app.services.ifrs.tax.tax_master import TaxCodeService, TaxCodeInput

        # No existing tax code
        mock_db.query.return_value.filter.return_value.first.return_value = None

        input_data = TaxCodeInput(
            tax_code="VAT20",
            tax_name="VAT 20%",
            tax_type=TaxType.VAT,
            jurisdiction_id=mock_jurisdiction.jurisdiction_id,
            tax_rate=Decimal("0.20"),
            effective_from=date(2024, 1, 1),
        )

        result = TaxCodeService.create_tax_code(mock_db, org_id, input_data)

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once()

    def test_create_tax_code_duplicate(self, mock_db, org_id, mock_jurisdiction):
        """Test tax code creation with duplicate code fails."""
        from app.services.ifrs.tax.tax_master import TaxCodeService, TaxCodeInput
        from fastapi import HTTPException

        existing = MockTaxCode(organization_id=org_id)
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        input_data = TaxCodeInput(
            tax_code="VAT20",
            tax_name="VAT 20%",
            tax_type=TaxType.VAT,
            jurisdiction_id=mock_jurisdiction.jurisdiction_id,
            tax_rate=Decimal("0.20"),
            effective_from=date(2024, 1, 1),
        )

        with pytest.raises(HTTPException) as exc_info:
            TaxCodeService.create_tax_code(mock_db, org_id, input_data)

        assert exc_info.value.status_code == 400
        assert "already exists" in exc_info.value.detail

    def test_calculate_tax_success(self, mock_db, org_id, mock_tax_code):
        """Test successful tax calculation."""
        from app.services.ifrs.tax.tax_master import TaxCodeService

        mock_tax_code.tax_rate = Decimal("0.20")
        mock_tax_code.is_recoverable = True
        mock_tax_code.recovery_rate = Decimal("1.0")
        mock_tax_code.is_inclusive = False

        mock_db.get.return_value = mock_tax_code

        result = TaxCodeService.calculate_tax(
            mock_db,
            org_id,
            mock_tax_code.tax_code_id,
            base_amount=Decimal("1000.00"),
            transaction_date=date(2024, 6, 15),
        )

        assert result.base_amount == Decimal("1000.00")
        assert result.tax_amount == Decimal("200.00")
        assert result.total_amount == Decimal("1200.00")

    def test_calculate_tax_code_not_found(self, mock_db, org_id):
        """Test tax calculation fails when tax code not found."""
        from app.services.ifrs.tax.tax_master import TaxCodeService
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            TaxCodeService.calculate_tax(
                mock_db,
                org_id,
                uuid.uuid4(),
                base_amount=Decimal("1000.00"),
                transaction_date=date.today(),
            )

        assert exc_info.value.status_code == 404

    def test_calculate_tax_code_inactive(self, mock_db, org_id, mock_tax_code):
        """Test tax calculation fails when tax code is inactive."""
        from app.services.ifrs.tax.tax_master import TaxCodeService
        from fastapi import HTTPException

        mock_tax_code.is_active = False
        mock_db.get.return_value = mock_tax_code

        with pytest.raises(HTTPException) as exc_info:
            TaxCodeService.calculate_tax(
                mock_db,
                org_id,
                mock_tax_code.tax_code_id,
                base_amount=Decimal("1000.00"),
                transaction_date=date.today(),
            )

        assert exc_info.value.status_code == 400
        assert "not active" in exc_info.value.detail

    def test_calculate_tax_before_effective_date(self, mock_db, org_id, mock_tax_code):
        """Test tax calculation fails for date before effective date."""
        from app.services.ifrs.tax.tax_master import TaxCodeService
        from fastapi import HTTPException

        mock_tax_code.effective_from = date(2024, 1, 1)
        mock_db.get.return_value = mock_tax_code

        with pytest.raises(HTTPException) as exc_info:
            TaxCodeService.calculate_tax(
                mock_db,
                org_id,
                mock_tax_code.tax_code_id,
                base_amount=Decimal("1000.00"),
                transaction_date=date(2023, 12, 31),  # Before effective date
            )

        assert exc_info.value.status_code == 400
        assert "before" in exc_info.value.detail

    def test_calculate_tax_inclusive(self, mock_db, org_id, mock_tax_code):
        """Test tax calculation for inclusive tax."""
        from app.services.ifrs.tax.tax_master import TaxCodeService

        mock_tax_code.tax_rate = Decimal("0.20")
        mock_tax_code.is_inclusive = True
        mock_tax_code.is_recoverable = True
        mock_tax_code.recovery_rate = Decimal("1.0")

        mock_db.get.return_value = mock_tax_code

        result = TaxCodeService.calculate_tax(
            mock_db,
            org_id,
            mock_tax_code.tax_code_id,
            base_amount=Decimal("1200.00"),  # Total inclusive of tax
            transaction_date=date(2024, 6, 15),
        )

        # For inclusive tax, the base is calculated from total
        assert result.total_amount == Decimal("1200.00")

    def test_calculate_tax_partial_recovery(self, mock_db, org_id, mock_tax_code):
        """Test tax calculation with partial recovery."""
        from app.services.ifrs.tax.tax_master import TaxCodeService

        mock_tax_code.tax_rate = Decimal("0.20")
        mock_tax_code.is_recoverable = True
        mock_tax_code.recovery_rate = Decimal("0.50")  # 50% recoverable
        mock_tax_code.is_inclusive = False

        mock_db.get.return_value = mock_tax_code

        result = TaxCodeService.calculate_tax(
            mock_db,
            org_id,
            mock_tax_code.tax_code_id,
            base_amount=Decimal("1000.00"),
            transaction_date=date(2024, 6, 15),
        )

        # 200 tax, 50% recoverable = 100 recoverable, 100 non-recoverable
        assert result.recoverable_amount == Decimal("100.00")
        assert result.non_recoverable_amount == Decimal("100.00")

    def test_get_tax_code_success(self, mock_db, mock_tax_code):
        """Test getting a tax code by ID."""
        from app.services.ifrs.tax.tax_master import TaxCodeService

        mock_db.get.return_value = mock_tax_code

        result = TaxCodeService.get(mock_db, str(mock_tax_code.tax_code_id))

        assert result is not None
        assert result.tax_code_id == mock_tax_code.tax_code_id

    def test_get_tax_code_not_found(self, mock_db):
        """Test getting non-existent tax code raises HTTPException."""
        from app.services.ifrs.tax.tax_master import TaxCodeService
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            TaxCodeService.get(mock_db, str(uuid.uuid4()))

        assert exc_info.value.status_code == 404

    def test_list_tax_codes(self, mock_db, org_id):
        """Test listing tax codes."""
        from app.services.ifrs.tax.tax_master import TaxCodeService

        mock_codes = [MockTaxCode(organization_id=org_id) for _ in range(5)]
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = mock_codes

        result = TaxCodeService.list(mock_db, str(org_id))

        assert len(result) == 5

    def test_list_tax_codes_with_type_filter(self, mock_db, org_id):
        """Test listing tax codes with type filter."""
        from app.services.ifrs.tax.tax_master import TaxCodeService

        mock_codes = [MockTaxCode(organization_id=org_id, tax_type=TaxType.VAT)]
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = mock_codes

        result = TaxCodeService.list(mock_db, str(org_id), tax_type=TaxType.VAT)

        assert len(result) == 1


class TestTaxJurisdictionService:
    """Tests for TaxJurisdictionService."""

    def test_create_jurisdiction_success(self, mock_db, org_id):
        """Test successful jurisdiction creation."""
        from app.services.ifrs.tax.tax_master import TaxJurisdictionService, TaxJurisdictionInput

        mock_db.query.return_value.filter.return_value.first.return_value = None

        input_data = TaxJurisdictionInput(
            jurisdiction_code="US-CA",
            jurisdiction_name="California",
            country_code="US",
            jurisdiction_level="STATE",
            current_tax_rate=Decimal("8.25"),
            tax_rate_effective_from=date(2024, 1, 1),
            currency_code="USD",
            current_tax_payable_account_id=uuid.uuid4(),
            current_tax_expense_account_id=uuid.uuid4(),
            deferred_tax_asset_account_id=uuid.uuid4(),
            deferred_tax_liability_account_id=uuid.uuid4(),
            deferred_tax_expense_account_id=uuid.uuid4(),
        )

        result = TaxJurisdictionService.create_jurisdiction(mock_db, org_id, input_data)

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_create_jurisdiction_duplicate(self, mock_db, org_id):
        """Test jurisdiction creation with duplicate code fails."""
        from app.services.ifrs.tax.tax_master import TaxJurisdictionService, TaxJurisdictionInput
        from fastapi import HTTPException

        existing = MockTaxJurisdiction(organization_id=org_id)
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        input_data = TaxJurisdictionInput(
            jurisdiction_code="US-CA",
            jurisdiction_name="California",
            country_code="US",
            jurisdiction_level="STATE",
            current_tax_rate=Decimal("8.25"),
            tax_rate_effective_from=date(2024, 1, 1),
            currency_code="USD",
            current_tax_payable_account_id=uuid.uuid4(),
            current_tax_expense_account_id=uuid.uuid4(),
            deferred_tax_asset_account_id=uuid.uuid4(),
            deferred_tax_liability_account_id=uuid.uuid4(),
            deferred_tax_expense_account_id=uuid.uuid4(),
        )

        with pytest.raises(HTTPException) as exc_info:
            TaxJurisdictionService.create_jurisdiction(mock_db, org_id, input_data)

        assert exc_info.value.status_code == 400

    def test_get_jurisdiction_success(self, mock_db, mock_jurisdiction):
        """Test getting a jurisdiction by ID."""
        from app.services.ifrs.tax.tax_master import TaxJurisdictionService

        mock_db.get.return_value = mock_jurisdiction

        result = TaxJurisdictionService.get(mock_db, str(mock_jurisdiction.jurisdiction_id))

        assert result is not None
        assert result.jurisdiction_id == mock_jurisdiction.jurisdiction_id

    def test_get_jurisdiction_not_found(self, mock_db):
        """Test getting non-existent jurisdiction raises HTTPException."""
        from app.services.ifrs.tax.tax_master import TaxJurisdictionService
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            TaxJurisdictionService.get(mock_db, str(uuid.uuid4()))

        assert exc_info.value.status_code == 404

    def test_list_jurisdictions(self, mock_db, org_id):
        """Test listing jurisdictions."""
        from app.services.ifrs.tax.tax_master import TaxJurisdictionService

        mock_jurisdictions = [MockTaxJurisdiction(organization_id=org_id) for _ in range(3)]
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = mock_jurisdictions

        result = TaxJurisdictionService.list(mock_db, str(org_id))

        assert len(result) == 3
