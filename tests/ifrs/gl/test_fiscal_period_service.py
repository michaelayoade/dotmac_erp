"""
Tests for FiscalPeriodService.
"""

from datetime import date
from uuid import uuid4

import pytest

from app.models.finance.gl.fiscal_period import PeriodStatus
from app.services.finance.gl.fiscal_period import (
    FiscalPeriodInput,
    FiscalPeriodService,
)
from tests.ifrs.gl.conftest import (
    MockFiscalPeriod,
)


@pytest.fixture
def service():
    """Create FiscalPeriodService instance."""
    return FiscalPeriodService()


@pytest.fixture
def sample_period_input():
    """Create sample period input."""
    return FiscalPeriodInput(
        fiscal_year_id=uuid4(),
        period_number=1,
        period_name="January 2024",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
    )


class TestCreatePeriod:
    """Tests for create_period method."""

    def test_create_period_success(self, service, mock_db, org_id, sample_period_input):
        """Test successful period creation."""
        mock_db.scalars.return_value.first.return_value = None

        service.create_period(mock_db, org_id, sample_period_input)

        mock_db.add.assert_called_once()
        mock_db.flush.assert_called_once()
        mock_db.refresh.assert_called_once()

    def test_create_period_duplicate_fails(
        self, service, mock_db, org_id, sample_period_input
    ):
        """Test that duplicate period number fails."""
        from fastapi import HTTPException

        existing = MockFiscalPeriod(
            fiscal_year_id=sample_period_input.fiscal_year_id,
            period_number=sample_period_input.period_number,
        )
        mock_db.scalars.return_value.first.return_value = existing

        with pytest.raises(HTTPException) as exc:
            service.create_period(mock_db, org_id, sample_period_input)

        assert exc.value.status_code == 400
        assert "already exists" in exc.value.detail

    def test_create_adjustment_period(self, service, mock_db, org_id):
        """Test creating an adjustment period."""
        input_data = FiscalPeriodInput(
            fiscal_year_id=uuid4(),
            period_number=13,
            period_name="Adjustment Period",
            start_date=date(2024, 12, 31),
            end_date=date(2024, 12, 31),
            is_adjustment_period=True,
        )
        mock_db.scalars.return_value.first.return_value = None

        service.create_period(mock_db, org_id, input_data)

        mock_db.add.assert_called_once()


class TestOpenPeriod:
    """Tests for open_period method."""

    def test_open_period_from_future(self, service, mock_db, org_id, user_id):
        """Test opening a future period."""
        period = MockFiscalPeriod(
            organization_id=org_id,
            status=PeriodStatus.FUTURE,
        )
        mock_db.get.return_value = period

        result = service.open_period(mock_db, org_id, period.fiscal_period_id, user_id)

        mock_db.flush.assert_called_once()
        assert result.status == PeriodStatus.OPEN

    def test_open_period_from_soft_closed(self, service, mock_db, org_id, user_id):
        """Test opening a soft-closed period."""
        period = MockFiscalPeriod(
            organization_id=org_id,
            status=PeriodStatus.SOFT_CLOSED,
        )
        mock_db.get.return_value = period

        result = service.open_period(mock_db, org_id, period.fiscal_period_id, user_id)

        assert result.status == PeriodStatus.OPEN

    def test_open_period_not_found(self, service, mock_db, org_id, user_id):
        """Test opening non-existent period."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.open_period(mock_db, org_id, uuid4(), user_id)

        assert exc.value.status_code == 404

    def test_open_period_wrong_org(self, service, mock_db, org_id, user_id):
        """Test opening period from wrong organization."""
        from fastapi import HTTPException

        period = MockFiscalPeriod(organization_id=uuid4())  # Different org
        mock_db.get.return_value = period

        with pytest.raises(HTTPException) as exc:
            service.open_period(mock_db, org_id, period.fiscal_period_id, user_id)

        assert exc.value.status_code == 404

    def test_open_hard_closed_period_fails(self, service, mock_db, org_id, user_id):
        """Test opening a hard-closed period fails."""
        from fastapi import HTTPException

        period = MockFiscalPeriod(
            organization_id=org_id,
            status=PeriodStatus.HARD_CLOSED,
        )
        mock_db.get.return_value = period

        with pytest.raises(HTTPException) as exc:
            service.open_period(mock_db, org_id, period.fiscal_period_id, user_id)

        assert exc.value.status_code == 400


class TestSoftClosePeriod:
    """Tests for soft_close_period method."""

    def test_soft_close_open_period(self, service, mock_db, org_id, user_id):
        """Test soft closing an open period."""
        period = MockFiscalPeriod(
            organization_id=org_id,
            status=PeriodStatus.OPEN,
        )
        mock_db.get.return_value = period

        result = service.soft_close_period(
            mock_db, org_id, period.fiscal_period_id, user_id
        )

        mock_db.flush.assert_called_once()
        assert result.status == PeriodStatus.SOFT_CLOSED

    def test_soft_close_reopened_period(self, service, mock_db, org_id, user_id):
        """Test soft closing a reopened period."""
        period = MockFiscalPeriod(
            organization_id=org_id,
            status=PeriodStatus.REOPENED,
        )
        mock_db.get.return_value = period

        result = service.soft_close_period(
            mock_db, org_id, period.fiscal_period_id, user_id
        )

        assert result.status == PeriodStatus.SOFT_CLOSED

    def test_soft_close_not_found(self, service, mock_db, org_id, user_id):
        """Test soft closing non-existent period."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.soft_close_period(mock_db, org_id, uuid4(), user_id)

        assert exc.value.status_code == 404

    def test_soft_close_future_period_fails(self, service, mock_db, org_id, user_id):
        """Test soft closing a future period fails."""
        from fastapi import HTTPException

        period = MockFiscalPeriod(
            organization_id=org_id,
            status=PeriodStatus.FUTURE,
        )
        mock_db.get.return_value = period

        with pytest.raises(HTTPException) as exc:
            service.soft_close_period(mock_db, org_id, period.fiscal_period_id, user_id)

        assert exc.value.status_code == 400


class TestBankReconciliationGate:
    """Tests for the bank-reconciliation closing gate (Bug #13 follow-up).

    Why this exists: a period was once soft-closed without any bank
    reconciliations because the close service had zero pre-close
    validation. The gate refuses close if any active bank account lacks
    a covering, finalised (pending_review / approved) reconciliation.
    """

    def _make_account(self, org_id, name="Test Bank"):
        from types import SimpleNamespace
        from uuid import uuid4

        from app.models.finance.banking.bank_account import BankAccountStatus

        return SimpleNamespace(
            bank_account_id=uuid4(),
            organization_id=org_id,
            account_name=name,
            status=BankAccountStatus.active,
        )

    def _make_rec(self, account, period, status):
        from types import SimpleNamespace

        return SimpleNamespace(
            bank_account_id=account.bank_account_id,
            organization_id=account.organization_id,
            period_start=period.start_date,
            period_end=period.end_date,
            status=status,
        )

    def _wire_scalars(self, mock_db, accounts, rec_map):
        """Make ``db.scalars(stmt).all()`` return the right rows.

        ``rec_map`` is ``{bank_account_id: [reconciliation, ...]}``. The
        first scalars() call (in the gate) returns accounts; subsequent
        calls return recs by account_id. We sniff which is which by
        inspecting the SELECT's first FROM table — but it's easier and
        more reliable to use side_effect with positional ordering.
        """
        from unittest.mock import MagicMock

        from app.models.finance.banking.bank_account import BankAccount

        def _scalars_side_effect(stmt):
            sm = MagicMock()
            # ORM SELECTs expose their first entity on .column_descriptions
            cols = stmt.column_descriptions
            entity = cols[0]["entity"] if cols else None
            if entity is BankAccount:
                sm.all.return_value = accounts
            else:
                # BankReconciliation lookup — find the bank_account_id in
                # the compiled where clause.
                params = stmt.compile().params
                acc_id = params.get("bank_account_id_1") or params.get("param_1")
                # Fallback: just look at the in-flight where clauses.
                if not acc_id:
                    for clause in stmt.whereclause.clauses:
                        if "bank_account_id" in str(clause):
                            acc_id = clause.right.value
                            break
                sm.all.return_value = rec_map.get(acc_id, [])
            return sm

        mock_db.scalars.side_effect = _scalars_side_effect

    def test_soft_close_blocked_when_no_rec_exists(
        self, service, mock_db, org_id, user_id
    ):
        """Period close fails with 400 if an active account has no rec."""
        from fastapi import HTTPException

        period = MockFiscalPeriod(
            organization_id=org_id,
            status=PeriodStatus.OPEN,
            start_date=date(2025, 2, 1),
            end_date=date(2025, 2, 28),
            period_name="February 2025",
        )
        mock_db.get.return_value = period

        acct = self._make_account(org_id, name="Unreconciled Bank")
        self._wire_scalars(mock_db, [acct], rec_map={})

        with pytest.raises(HTTPException) as exc:
            service.soft_close_period(mock_db, org_id, period.fiscal_period_id, user_id)

        assert exc.value.status_code == 400
        assert "February 2025" in exc.value.detail
        assert "Unreconciled Bank" in exc.value.detail
        assert "no reconciliation" in exc.value.detail

    def test_soft_close_blocked_when_only_draft_rec(
        self, service, mock_db, org_id, user_id
    ):
        """Draft / rejected recs do not satisfy the gate."""
        from fastapi import HTTPException

        from app.models.finance.banking.bank_reconciliation import (
            ReconciliationStatus,
        )

        period = MockFiscalPeriod(
            organization_id=org_id,
            status=PeriodStatus.OPEN,
            start_date=date(2025, 2, 1),
            end_date=date(2025, 2, 28),
        )
        mock_db.get.return_value = period

        acct = self._make_account(org_id, name="Draft Only")
        draft_rec = self._make_rec(acct, period, ReconciliationStatus.draft)
        rejected_rec = self._make_rec(acct, period, ReconciliationStatus.rejected)
        self._wire_scalars(
            mock_db,
            [acct],
            rec_map={acct.bank_account_id: [draft_rec, rejected_rec]},
        )

        with pytest.raises(HTTPException) as exc:
            service.soft_close_period(mock_db, org_id, period.fiscal_period_id, user_id)

        assert exc.value.status_code == 400
        assert "Draft Only" in exc.value.detail
        assert "draft" in exc.value.detail
        assert "rejected" in exc.value.detail

    def test_soft_close_passes_when_pending_review_rec_present(
        self, service, mock_db, org_id, user_id
    ):
        """A pending_review rec satisfies the gate (workflow default)."""
        from app.models.finance.banking.bank_reconciliation import (
            ReconciliationStatus,
        )

        period = MockFiscalPeriod(
            organization_id=org_id,
            status=PeriodStatus.OPEN,
            start_date=date(2025, 2, 1),
            end_date=date(2025, 2, 28),
        )
        mock_db.get.return_value = period

        acct = self._make_account(org_id)
        rec = self._make_rec(acct, period, ReconciliationStatus.pending_review)
        self._wire_scalars(mock_db, [acct], rec_map={acct.bank_account_id: [rec]})

        result = service.soft_close_period(
            mock_db, org_id, period.fiscal_period_id, user_id
        )

        assert result.status == PeriodStatus.SOFT_CLOSED

    def test_soft_close_passes_with_no_active_bank_accounts(
        self, service, mock_db, org_id, user_id
    ):
        """If the org has no active bank accounts, the gate doesn't fire.

        New orgs / orgs that closed all their banks must still be able to
        close their periods.
        """
        period = MockFiscalPeriod(organization_id=org_id, status=PeriodStatus.OPEN)
        mock_db.get.return_value = period
        self._wire_scalars(mock_db, [], rec_map={})

        result = service.soft_close_period(
            mock_db, org_id, period.fiscal_period_id, user_id
        )

        assert result.status == PeriodStatus.SOFT_CLOSED


class TestHardClosePeriod:
    """Tests for hard_close_period method."""

    def test_hard_close_soft_closed_period(self, service, mock_db, org_id, user_id):
        """Test hard closing a soft-closed period."""
        period = MockFiscalPeriod(
            organization_id=org_id,
            status=PeriodStatus.SOFT_CLOSED,
        )
        mock_db.get.return_value = period

        result = service.hard_close_period(
            mock_db, org_id, period.fiscal_period_id, user_id
        )

        mock_db.flush.assert_called_once()
        assert result.status == PeriodStatus.HARD_CLOSED

    def test_hard_close_open_period_fails(self, service, mock_db, org_id, user_id):
        """Test hard closing an open period fails."""
        from fastapi import HTTPException

        period = MockFiscalPeriod(
            organization_id=org_id,
            status=PeriodStatus.OPEN,
        )
        mock_db.get.return_value = period

        with pytest.raises(HTTPException) as exc:
            service.hard_close_period(mock_db, org_id, period.fiscal_period_id, user_id)

        assert exc.value.status_code == 400
        assert "soft closed" in exc.value.detail


class TestReopenPeriod:
    """Tests for reopen_period method."""

    def test_reopen_soft_closed_period(self, service, mock_db, org_id, user_id):
        """Test reopening a soft-closed period."""
        period = MockFiscalPeriod(
            organization_id=org_id,
            status=PeriodStatus.SOFT_CLOSED,
            reopen_count=0,
        )
        mock_db.get.return_value = period

        session_id = uuid4()
        result = service.reopen_period(
            mock_db, org_id, period.fiscal_period_id, user_id, session_id
        )

        mock_db.flush.assert_called_once()
        assert result.status == PeriodStatus.REOPENED
        assert result.reopen_count == 1
        assert result.last_reopen_session_id == session_id

    def test_reopen_hard_closed_period_fails(self, service, mock_db, org_id, user_id):
        """Test reopening a hard-closed period fails."""
        from fastapi import HTTPException

        period = MockFiscalPeriod(
            organization_id=org_id,
            status=PeriodStatus.HARD_CLOSED,
        )
        mock_db.get.return_value = period

        with pytest.raises(HTTPException) as exc:
            service.reopen_period(
                mock_db, org_id, period.fiscal_period_id, user_id, uuid4()
            )

        assert exc.value.status_code == 400
        assert "hard-closed" in exc.value.detail


class TestGetPeriod:
    """Tests for get method."""

    def test_get_existing_period(self, service, mock_db, org_id):
        """Test getting existing period."""
        period = MockFiscalPeriod(organization_id=org_id)
        mock_db.get.return_value = period

        result = service.get(mock_db, str(period.fiscal_period_id))

        assert result == period

    def test_get_nonexistent_period(self, service, mock_db):
        """Test getting non-existent period."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.get(mock_db, str(uuid4()))

        assert exc.value.status_code == 404


class TestListPeriods:
    """Tests for list method."""

    def test_list_all_periods(self, service, mock_db, org_id):
        """Test listing all periods."""
        periods = [MockFiscalPeriod(organization_id=org_id) for _ in range(12)]
        mock_db.scalars.return_value.all.return_value = periods

        result = service.list(mock_db, organization_id=str(org_id))

        assert len(result) == 12

    def test_list_by_fiscal_year(self, service, mock_db, org_id):
        """Test listing periods by fiscal year."""
        year_id = uuid4()
        periods = [MockFiscalPeriod(organization_id=org_id, fiscal_year_id=year_id)]
        mock_db.scalars.return_value.all.return_value = periods

        result = service.list(
            mock_db, organization_id=str(org_id), fiscal_year_id=str(year_id)
        )

        assert len(result) == 1

    def test_list_by_status(self, service, mock_db, org_id):
        """Test listing periods by status."""
        periods = [MockFiscalPeriod(organization_id=org_id, status=PeriodStatus.OPEN)]
        mock_db.scalars.return_value.all.return_value = periods

        result = service.list(
            mock_db, organization_id=str(org_id), status=PeriodStatus.OPEN
        )

        assert len(result) == 1
