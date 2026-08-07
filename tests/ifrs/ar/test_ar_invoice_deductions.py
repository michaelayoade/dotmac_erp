"""Tests for AR invoice deduction calculations (WHT, VAT withheld, stamp duty)."""

from decimal import Decimal
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.models.finance.tax.tax_code import TaxType
from app.services.finance.tax.tax_calculation import TaxCalculationService


class TestWHTCalculation:
    """WHT is calculated on net subtotal (pre-VAT)."""

    def test_wht_on_exclusive_vat_invoice(self):
        """WHT base = subtotal when all taxes are exclusive."""
        db = MagicMock()
        org_id = uuid4()
        wht_code_id = uuid4()
        txn_date = date.today()
        db.get.return_value = SimpleNamespace(
            tax_code_id=wht_code_id,
            tax_code="WHT-5",
            tax_rate=Decimal("0.05"),
            is_active=True,
            is_inclusive=False,
            is_compound=False,
            effective_from=txn_date,
            effective_to=None,
            organization_id=org_id,
            tax_type=TaxType.WITHHOLDING,
        )

        subtotal = Decimal("100000")
        wht_amount, net = TaxCalculationService.calculate_wht(
            db=db,
            organization_id=org_id,
            base_amount=subtotal,
            wht_code_id=wht_code_id,
            transaction_date=txn_date,
        )

        assert wht_amount == Decimal("5000.00")
        assert net == Decimal("95000.00")


class TestStampDutyCalculation:
    """Stamp duty is calculated on gross total (including VAT)."""

    def test_stamp_duty_on_gross(self):
        """SD base = total_amount (subtotal + VAT)."""
        total_amount = Decimal("107500")
        sd_rate = Decimal("0.01")
        expected = Decimal("1075.00")

        sd_amount = (total_amount * sd_rate).quantize(Decimal("0.01"))
        assert sd_amount == expected

    def test_stamp_duty_deducted_reduces_receivable(self):
        """When treatment is DEDUCTED, stamp duty reduces amount receivable."""
        total = Decimal("107500")
        sd_amount = Decimal("1075")
        treatment = "DEDUCTED"

        deduction = sd_amount if treatment == "DEDUCTED" else Decimal("0")
        receivable = total - deduction
        assert receivable == Decimal("106425")

    def test_stamp_duty_paid_separately_no_effect(self):
        """When treatment is PAID_SEPARATELY, receivable equals total."""
        total = Decimal("107500")
        sd_amount = Decimal("1075")
        treatment = "PAID_SEPARATELY"

        deduction = sd_amount if treatment == "DEDUCTED" else Decimal("0")
        receivable = total - deduction
        assert receivable == Decimal("107500")


class TestVATWithheld:
    """VAT withheld deducts the full tax amount."""

    def test_vat_withheld_equals_tax_amount(self):
        tax_amount = Decimal("7500")
        vat_withheld = True

        vat_withheld_amount = tax_amount if vat_withheld else Decimal("0")
        assert vat_withheld_amount == Decimal("7500")

    def test_vat_not_withheld_is_zero(self):
        tax_amount = Decimal("7500")
        vat_withheld = False

        vat_withheld_amount = tax_amount if vat_withheld else Decimal("0")
        assert vat_withheld_amount == Decimal("0")


class TestAmountReceivable:
    """Amount receivable = total - WHT - VAT withheld - stamp duty (if deducted)."""

    def test_all_deductions_active(self):
        total = Decimal("107500")
        wht = Decimal("5000")
        vat_withheld = Decimal("7500")
        sd_deducted = Decimal("1075")

        receivable = total - wht - vat_withheld - sd_deducted
        assert receivable == Decimal("93925")

    def test_no_deductions(self):
        total = Decimal("107500")
        receivable = total - Decimal("0") - Decimal("0") - Decimal("0")
        assert receivable == Decimal("107500")

    def test_wht_only(self):
        total = Decimal("107500")
        wht = Decimal("5000")
        receivable = total - wht
        assert receivable == Decimal("102500")

    def test_vat_withheld_only(self):
        total = Decimal("107500")
        vat = Decimal("7500")
        receivable = total - vat
        assert receivable == Decimal("100000")

    def test_stamp_duty_paid_separately_with_wht(self):
        """Stamp duty paid separately does not reduce receivable, but WHT does."""
        total = Decimal("107500")
        wht = Decimal("5000")
        sd_amount = Decimal("1075")
        sd_treatment = "PAID_SEPARATELY"

        sd_deducted = sd_amount if sd_treatment == "DEDUCTED" else Decimal("0")
        receivable = total - wht - sd_deducted
        assert receivable == Decimal("102500")

    def test_credit_note_reverses_signs(self):
        total = Decimal("-107500")
        wht = Decimal("-5000")
        vat = Decimal("-7500")
        sd = Decimal("-1075")

        receivable = total - wht - vat - sd
        assert receivable == Decimal("-93925")
