"""
Regression test for opening-balance import period-lock bypass.

Pre-fix: ``OpeningBalanceImporter.import_file`` resolved the fiscal period
by date range only and never checked whether the period was closed. A
re-import targeting an already hard-closed period would land silently.

This test stubs out CSV parsing and the FiscalPeriod lookup, patches
``PeriodGuardService.can_post_to_date`` to refuse the post, and asserts
the importer surfaces the rejection in its OpeningBalanceResult.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest


def _make_importer():
    from app.services.finance.import_export.base import ImportConfig
    from app.services.finance.import_export.opening_balance import (
        OpeningBalanceImporter,
    )

    db = MagicMock()
    # _load_accounts() runs in __init__; make it return an empty result
    accounts_result = MagicMock()
    accounts_result.scalars.return_value = []
    db.execute.return_value = accounts_result

    cfg = ImportConfig(organization_id=uuid4(), user_id=uuid4())
    importer = OpeningBalanceImporter(db, cfg)
    return importer, db, cfg


def _balanced_preview(entry_date: date):
    from app.services.finance.import_export.opening_balance import (
        OpeningBalancePreview,
    )

    return OpeningBalancePreview(
        total_rows=1,
        total_debit=Decimal("100"),
        total_credit=Decimal("100"),
        is_balanced=True,
        difference=Decimal("0"),
        lines=[],
        matched_count=1,
        unmatched_count=0,
        unmatched_accounts=[],
        validation_errors=[],
        entry_date=entry_date,
        detected_format="generic",
    )


@pytest.fixture
def mock_period():
    """Mock fiscal period that exists for the entry_date."""
    period = MagicMock()
    period.fiscal_period_id = uuid4()
    period.start_date = date(2024, 1, 1)
    period.end_date = date(2024, 1, 31)
    return period


def test_import_file_rejects_entry_into_closed_period(mock_period):
    """
    import_file must consult PeriodGuardService.can_post_to_date and refuse
    the import when the period is closed.
    """
    importer, db, _ = _make_importer()
    entry_date = date(2024, 1, 15)

    importer.preview_file = MagicMock(return_value=_balanced_preview(entry_date))

    # FiscalPeriod query — the only db.execute(...).scalar_one_or_none() call
    period_query = MagicMock()
    period_query.scalar_one_or_none.return_value = mock_period
    db.execute.return_value = period_query

    from app.services.finance.gl.period_guard import PeriodGuardResult

    closed_result = PeriodGuardResult(
        is_allowed=False,
        fiscal_period_id=mock_period.fiscal_period_id,
        period_status="HARD_CLOSED",
        message="Period 'Jan 2024' is permanently closed",
    )

    with patch(
        "app.services.finance.gl.period_guard.PeriodGuardService.can_post_to_date",
        return_value=closed_result,
    ) as guard:
        result = importer.import_file(
            file_path="/dev/null",
            entry_date=entry_date,
        )

    assert guard.called, (
        "PeriodGuardService.can_post_to_date was not called — bypass remains"
    )
    assert result.success is False, "Import succeeded against a closed period"
    assert result.journal_entry_id is None
    assert any("closed" in err.lower() for err in result.errors), (
        f"Expected 'closed' in errors, got: {result.errors}"
    )
    assert not db.add.called, "Journal was persisted despite period rejection"
