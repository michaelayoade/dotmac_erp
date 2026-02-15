"""Tests for recurring bill and journal generation."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.services.finance.automation.recurring import (
    RecurringService,
)


@pytest.fixture
def service() -> RecurringService:
    return RecurringService()


@pytest.fixture
def org_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def user_id() -> uuid.UUID:
    return uuid.uuid4()


def _make_template(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    entity_type: str,
    template_data: dict,
    days_before_due: int = 30,
) -> MagicMock:
    """Create a mock RecurringTemplate."""
    template = MagicMock()
    template.template_id = uuid.uuid4()
    template.organization_id = org_id
    template.entity_type = MagicMock()
    template.entity_type.value = entity_type
    template.template_data = template_data
    template.days_before_due = days_before_due
    template.created_by = user_id
    template.frequency = MagicMock()
    template.frequency.value = "MONTHLY"
    template.status = MagicMock()
    template.status.value = "ACTIVE"
    template.next_run_date = date.today()
    template.end_date = None
    template.occurrences_limit = None
    template.occurrences_count = 0
    template.schedule_config = None
    return template


def _patch_numbering(return_value: str):
    """Patch SyncNumberingService to return a fixed number.

    Since SyncNumberingService is imported at module level in recurring.py,
    we patch it at the module where it's used.
    """
    patcher = patch("app.services.finance.automation.recurring.SyncNumberingService")
    mock_cls = patcher.start()
    mock_instance = mock_cls.return_value
    mock_instance.generate_next_number.return_value = return_value
    return patcher, mock_cls


# ── generate_bill tests ──


class TestGenerateBill:
    """Tests for RecurringService.generate_bill()."""

    def test_generate_bill_happy_path(
        self,
        service: RecurringService,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        """Should create a supplier invoice with lines and taxes."""
        supplier_id = uuid.uuid4()
        account_id = uuid.uuid4()
        ap_control_id = uuid.uuid4()

        template = _make_template(
            org_id,
            user_id,
            "BILL",
            {
                "supplier_id": str(supplier_id),
                "ap_control_account_id": str(ap_control_id),
                "currency_code": "NGN",
                "lines": [
                    {
                        "description": "Monthly service fee",
                        "quantity": "1",
                        "unit_price": "50000",
                        "account_id": str(account_id),
                    },
                ],
            },
        )

        db = MagicMock()
        db.flush = MagicMock()

        with patch(
            "app.services.finance.automation.recurring.SyncNumberingService"
        ) as mock_cls:
            mock_cls.return_value.generate_next_number.return_value = "BILL-0001"

            result = service.generate_bill(db, template)

        assert result.success is True
        assert result.entity_type == "BILL"
        assert result.entity_number == "BILL-0001"
        assert db.add.called
        assert db.flush.called

    def test_generate_bill_missing_supplier_id(
        self,
        service: RecurringService,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        """Should fail gracefully when supplier_id is missing."""
        template = _make_template(
            org_id,
            user_id,
            "BILL",
            {
                "ap_control_account_id": str(uuid.uuid4()),
                "currency_code": "NGN",
                "lines": [],
            },
        )

        db = MagicMock()

        with patch(
            "app.services.finance.automation.recurring.SyncNumberingService"
        ) as mock_cls:
            mock_cls.return_value.generate_next_number.return_value = "BILL-0002"

            result = service.generate_bill(db, template)

        assert result.success is False
        assert result.error_message is not None
        assert "supplier_id" in result.error_message.lower()

    def test_generate_bill_with_tax(
        self,
        service: RecurringService,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        """Should calculate taxes via TaxCalculationService."""
        supplier_id = uuid.uuid4()
        account_id = uuid.uuid4()
        ap_control_id = uuid.uuid4()
        tax_code_id = uuid.uuid4()

        template = _make_template(
            org_id,
            user_id,
            "BILL",
            {
                "supplier_id": str(supplier_id),
                "ap_control_account_id": str(ap_control_id),
                "currency_code": "NGN",
                "lines": [
                    {
                        "description": "Consulting",
                        "quantity": "2",
                        "unit_price": "100000",
                        "account_id": str(account_id),
                        "tax_code_ids": [str(tax_code_id)],
                    },
                ],
            },
        )

        db = MagicMock()

        # Mock tax calculation
        mock_tax_result = MagicMock()
        mock_tax_result.total_tax = Decimal("30000")  # 15% of 200000
        mock_line_result = MagicMock()
        mock_line_result.total_tax = Decimal("30000")
        mock_line_result.taxes = [
            MagicMock(
                tax_code_id=tax_code_id,
                base_amount=Decimal("200000"),
                tax_rate=Decimal("15"),
                tax_amount=Decimal("30000"),
                is_inclusive=False,
                sequence=1,
                is_recoverable=True,
                recoverable_amount=Decimal("30000"),
            )
        ]
        mock_tax_result.lines = [mock_line_result]

        with (
            patch(
                "app.services.finance.automation.recurring.SyncNumberingService"
            ) as mock_cls,
            patch(
                "app.services.finance.tax.tax_calculation.TaxCalculationService"
                ".calculate_invoice_taxes",
                return_value=mock_tax_result,
            ),
        ):
            mock_cls.return_value.generate_next_number.return_value = "BILL-0003"

            result = service.generate_bill(db, template)

        assert result.success is True
        assert result.entity_type == "BILL"


# ── generate_journal tests ──


class TestGenerateJournal:
    """Tests for RecurringService.generate_journal()."""

    def test_generate_journal_happy_path(
        self,
        service: RecurringService,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        """Should create a journal entry with balanced lines."""
        debit_account = uuid.uuid4()
        credit_account = uuid.uuid4()
        period_id = uuid.uuid4()

        template = _make_template(
            org_id,
            user_id,
            "JOURNAL",
            {
                "description": "Monthly depreciation",
                "currency_code": "NGN",
                "lines": [
                    {
                        "account_id": str(debit_account),
                        "debit_amount": "50000",
                        "credit_amount": "0",
                        "description": "Depreciation expense",
                    },
                    {
                        "account_id": str(credit_account),
                        "debit_amount": "0",
                        "credit_amount": "50000",
                        "description": "Accumulated depreciation",
                    },
                ],
            },
        )

        from app.models.finance.gl.fiscal_period import PeriodStatus

        db = MagicMock()
        mock_period = MagicMock()
        mock_period.fiscal_period_id = period_id
        mock_period.status = PeriodStatus.OPEN

        with (
            patch(
                "app.services.finance.automation.recurring.SyncNumberingService"
            ) as mock_cls,
            patch(
                "app.services.finance.gl.period_guard.PeriodGuardService"
                ".get_period_for_date",
                return_value=mock_period,
            ),
        ):
            mock_cls.return_value.generate_next_number.return_value = "JE-0001"

            result = service.generate_journal(db, template)

        assert result.success is True
        assert result.entity_type == "JOURNAL"
        assert result.entity_number == "JE-0001"
        assert db.add.called

    def test_generate_journal_unbalanced(
        self,
        service: RecurringService,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        """Should fail when debits != credits."""
        template = _make_template(
            org_id,
            user_id,
            "JOURNAL",
            {
                "description": "Bad entry",
                "currency_code": "NGN",
                "lines": [
                    {
                        "account_id": str(uuid.uuid4()),
                        "debit_amount": "50000",
                        "credit_amount": "0",
                    },
                    {
                        "account_id": str(uuid.uuid4()),
                        "debit_amount": "0",
                        "credit_amount": "30000",
                    },
                ],
            },
        )

        from app.models.finance.gl.fiscal_period import PeriodStatus

        db = MagicMock()
        mock_period = MagicMock()
        mock_period.fiscal_period_id = uuid.uuid4()
        mock_period.status = PeriodStatus.OPEN

        with patch(
            "app.services.finance.gl.period_guard.PeriodGuardService"
            ".get_period_for_date",
            return_value=mock_period,
        ):
            result = service.generate_journal(db, template)

        assert result.success is False
        assert "do not equal" in (result.error_message or "").lower()

    def test_generate_journal_no_open_period(
        self,
        service: RecurringService,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        """Should fail when no open fiscal period exists."""
        template = _make_template(
            org_id,
            user_id,
            "JOURNAL",
            {
                "description": "Depreciation",
                "currency_code": "NGN",
                "lines": [
                    {
                        "account_id": str(uuid.uuid4()),
                        "debit_amount": "100",
                        "credit_amount": "0",
                    },
                    {
                        "account_id": str(uuid.uuid4()),
                        "debit_amount": "0",
                        "credit_amount": "100",
                    },
                ],
            },
        )

        db = MagicMock()

        with patch(
            "app.services.finance.gl.period_guard.PeriodGuardService"
            ".get_period_for_date",
            return_value=None,
        ):
            result = service.generate_journal(db, template)

        assert result.success is False
        assert "fiscal period" in (result.error_message or "").lower()

    def test_generate_journal_closed_period(
        self,
        service: RecurringService,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        """Should fail when the fiscal period is CLOSED."""
        from app.models.finance.gl.fiscal_period import PeriodStatus

        template = _make_template(
            org_id,
            user_id,
            "JOURNAL",
            {
                "description": "Depreciation",
                "currency_code": "NGN",
                "lines": [
                    {
                        "account_id": str(uuid.uuid4()),
                        "debit_amount": "100",
                        "credit_amount": "0",
                    },
                    {
                        "account_id": str(uuid.uuid4()),
                        "debit_amount": "0",
                        "credit_amount": "100",
                    },
                ],
            },
        )

        db = MagicMock()
        mock_period = MagicMock()
        mock_period.fiscal_period_id = uuid.uuid4()
        mock_period.status = PeriodStatus.HARD_CLOSED
        mock_period.period_name = "Jan 2026"

        with patch(
            "app.services.finance.gl.period_guard.PeriodGuardService"
            ".get_period_for_date",
            return_value=mock_period,
        ):
            result = service.generate_journal(db, template)

        assert result.success is False
        assert "closed period" in (result.error_message or "").lower()

    def test_generate_journal_no_lines(
        self,
        service: RecurringService,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        """Should fail when template has no journal lines."""
        template = _make_template(
            org_id,
            user_id,
            "JOURNAL",
            {
                "description": "Empty",
                "currency_code": "NGN",
                "lines": [],
            },
        )

        from app.models.finance.gl.fiscal_period import PeriodStatus

        db = MagicMock()
        mock_period = MagicMock()
        mock_period.fiscal_period_id = uuid.uuid4()
        mock_period.status = PeriodStatus.OPEN

        with patch(
            "app.services.finance.gl.period_guard.PeriodGuardService"
            ".get_period_for_date",
            return_value=mock_period,
        ):
            result = service.generate_journal(db, template)

        assert result.success is False
        assert "no journal lines" in (result.error_message or "").lower()


# ── Celery task registration ──


class TestCeleryTask:
    """Tests for process_recurring_templates Celery task."""

    def test_task_is_registered(self) -> None:
        """The task should be importable and in __all__."""
        from app.tasks import __all__ as task_exports

        assert "process_recurring_templates" in task_exports

    def test_task_is_importable(self) -> None:
        """The task function should be importable."""
        from app.tasks.automation import process_recurring_templates

        assert callable(process_recurring_templates)
