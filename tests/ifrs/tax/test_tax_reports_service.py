"""
Tests for TaxReportService reporting helpers.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock


class TestStampDutyReport:
    """Tests for stamp duty reporting."""

    def test_get_stamp_duty_report_aggregates_ar_and_ap(self):
        """Stamp duty summary should aggregate AR and AP invoice headers."""
        from app.services.finance.tax.tax_reports import tax_report_service

        org_id = uuid.uuid4()
        mock_db = MagicMock()
        mock_db.execute.side_effect = [
            [
                (
                    "SD-1%",
                    "Stamp Duty 1%",
                    Decimal("0.01"),
                    Decimal("1250.00"),
                    2,
                )
            ],
            [
                (
                    "SD-1%",
                    "Stamp Duty 1%",
                    Decimal("0.01"),
                    Decimal("500.00"),
                    1,
                )
            ],
        ]

        report = tax_report_service.get_stamp_duty_report(
            mock_db,
            org_id,
            date(2026, 2, 1),
            date(2026, 2, 28),
            include_transactions=False,
        )

        assert report.stamp_duty_on_sales == Decimal("1250.00")
        assert report.sales_count == 2
        assert report.stamp_duty_on_purchases == Decimal("500.00")
        assert report.purchase_count == 1
        assert report.total_stamp_duty == Decimal("1750.00")
        assert report.transactions == []
        assert len(report.by_code) == 2
        assert report.by_code[0]["source_module"] == "AP"
        assert report.by_code[1]["source_module"] == "AR"

    def test_get_stamp_duty_report_includes_transaction_details(self):
        """Detailed stamp duty report should surface transaction-level context."""
        from app.services.finance.tax.tax_reports import tax_report_service

        org_id = uuid.uuid4()
        mock_db = MagicMock()
        mock_db.execute.side_effect = [
            [
                (
                    "SD-1%",
                    "Stamp Duty 1%",
                    Decimal("0.01"),
                    Decimal("1000.00"),
                    1,
                )
            ],
            [],
            [
                (
                    date(2026, 2, 19),
                    "INV-001",
                    "Acme Customer",
                    "CUST-TIN",
                    "SD-1%",
                    "Stamp Duty 1%",
                    Decimal("0.01"),
                    Decimal("1000.00"),
                    "DEDUCTED",
                )
            ],
            [],
        ]

        report = tax_report_service.get_stamp_duty_report(
            mock_db,
            org_id,
            date(2026, 2, 1),
            date(2026, 2, 28),
            include_transactions=True,
        )

        assert len(report.transactions) == 1
        txn = report.transactions[0]
        assert txn["reference"] == "INV-001"
        assert txn["source_module"] == "AR"
        assert txn["treatment"] == "DEDUCTED"
        assert txn["stamp_duty_amount"] == Decimal("1000.00")


class TestWHTReport:
    """WHT reports preserve customer/supplier direction and write-offs."""

    def test_accrual_report_classifies_payment_source_types(self):
        from app.services.finance.tax.tax_reports import tax_report_service

        mock_db = MagicMock()
        mock_db.execute.return_value = [
            (
                "WHT-5",
                "WHT 5%",
                Decimal("0.05"),
                "CUSTOMER_PAYMENT",
                Decimal("100"),
                Decimal("5"),
                1,
            ),
            (
                "WHT-5",
                "WHT 5%",
                Decimal("0.05"),
                "CUSTOMER_PAYMENT_WHT_WRITE_OFF",
                Decimal("0"),
                Decimal("-2"),
                1,
            ),
            (
                "WHT-5",
                "WHT 5%",
                Decimal("0.05"),
                "SUPPLIER_PAYMENT",
                Decimal("60"),
                Decimal("3"),
                1,
            ),
        ]

        report = tax_report_service.get_wht_report(
            mock_db,
            uuid.uuid4(),
            date(2026, 7, 1),
            date(2026, 7, 31),
        )

        assert report.wht_deducted_by_customers == Decimal("3")
        assert report.wht_withheld_from_suppliers == Decimal("3")
        assert report.net_wht_payable == Decimal("0")
        assert {row["source_module"] for row in report.by_rate} == {"AR", "AP"}

    def test_cash_report_applies_current_period_write_off_adjustment(self):
        from app.services.finance.tax.tax_reports import tax_report_service

        mock_db = MagicMock()
        # AR receipts, AR write-off adjustment, AP payments.
        mock_db.scalar.side_effect = [Decimal("10"), Decimal("-4"), Decimal("3")]

        report = tax_report_service.get_wht_report(
            mock_db,
            uuid.uuid4(),
            date(2026, 7, 1),
            date(2026, 7, 31),
            basis="cash",
        )

        assert report.wht_deducted_by_customers == Decimal("6")
        assert report.wht_withheld_from_suppliers == Decimal("3")
        assert report.net_wht_payable == Decimal("-3")
