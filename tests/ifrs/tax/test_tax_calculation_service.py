from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.services.finance.tax.tax_calculation import TaxCalculationService


def test_calculate_single_tax_treats_rate_one_as_100_percent():
    tax_code = SimpleNamespace(is_inclusive=False, tax_rate=Decimal("1.00"))

    net_base, tax_amount = TaxCalculationService.calculate_single_tax(
        Decimal("100.00"), tax_code
    )

    assert net_base == Decimal("100.00")
    assert tax_amount == Decimal("100.00")


def test_fixed_amount_tax_adds_flat_not_multiplied():
    """A fixed-amount tax (e.g. ₦50 levy) is added flat, not base × rate."""
    tax_code = SimpleNamespace(
        is_inclusive=False, is_fixed_amount=True, tax_rate=Decimal("50")
    )

    net_base, tax_amount = TaxCalculationService.calculate_single_tax(
        Decimal("1000.00"), tax_code
    )

    # Flat ₦50, NOT 1000 × 50 = 50,000
    assert tax_amount == Decimal("50.00")
    assert net_base == Decimal("1000.00")


def test_fixed_amount_inclusive_extracts_flat_from_price():
    """An inclusive fixed levy is carved out of the price."""
    tax_code = SimpleNamespace(
        is_inclusive=True, is_fixed_amount=True, tax_rate=Decimal("50")
    )

    net_base, tax_amount = TaxCalculationService.calculate_single_tax(
        Decimal("1000.00"), tax_code
    )

    assert tax_amount == Decimal("50.00")
    assert net_base == Decimal("950.00")


def test_fixed_amount_inclusive_never_exceeds_base():
    """An inclusive fixed levy larger than the line is capped at the line."""
    tax_code = SimpleNamespace(
        is_inclusive=True, is_fixed_amount=True, tax_rate=Decimal("50")
    )

    net_base, tax_amount = TaxCalculationService.calculate_single_tax(
        Decimal("30.00"), tax_code
    )

    assert tax_amount == Decimal("30.00")
    assert net_base == Decimal("0.00")


def test_calculate_wht_treats_rate_one_as_100_percent():
    db = MagicMock()
    org_id = uuid4()
    wht_code_id = uuid4()
    txn_date = date.today()
    db.get.return_value = SimpleNamespace(
        tax_code_id=wht_code_id,
        organization_id=org_id,
        is_active=True,
        effective_from=txn_date,
        effective_to=None,
        tax_rate=Decimal("1.00"),
    )

    wht_amount, net_received = TaxCalculationService.calculate_wht(
        db=db,
        organization_id=org_id,
        base_amount=Decimal("100.00"),
        wht_code_id=wht_code_id,
        transaction_date=txn_date,
    )

    assert wht_amount == Decimal("100.00")
    assert net_received == Decimal("0.00")
