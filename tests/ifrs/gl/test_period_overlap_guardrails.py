from datetime import date
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.finance.gl.fiscal_period import PeriodStatus
from app.services.finance.gl.fiscal_period import FiscalPeriodInput, FiscalPeriodService
from app.services.finance.gl.period_guard import PeriodGuardService
from tests.ifrs.gl.conftest import MockFiscalPeriod


def test_create_period_rejects_overlapping_normal_period(mock_db, org_id):
    input_data = FiscalPeriodInput(
        fiscal_year_id=uuid4(),
        period_number=2,
        period_name="Overlap Period",
        start_date=date(2024, 1, 15),
        end_date=date(2024, 2, 15),
    )

    overlap = MockFiscalPeriod(
        organization_id=org_id,
        period_name="January 2024",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
    )
    mock_db.scalars.return_value.first.side_effect = [None, overlap]

    with pytest.raises(HTTPException) as exc:
        FiscalPeriodService.create_period(mock_db, org_id, input_data)

    assert exc.value.status_code == 400
    assert "overlaps" in exc.value.detail.lower()


def test_can_post_prefers_open_period_over_soft_closed_overlap(org_id):
    db = MagicMock()

    open_month = MockFiscalPeriod(
        organization_id=org_id,
        status=PeriodStatus.OPEN,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
    )
    soft_closed_year = MockFiscalPeriod(
        organization_id=org_id,
        status=PeriodStatus.SOFT_CLOSED,
        period_name="test",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
    )

    db.scalars.return_value.all.return_value = [open_month, soft_closed_year]

    result = PeriodGuardService.can_post_to_date(db, org_id, date(2026, 1, 15))

    assert result.is_allowed is True
    assert result.fiscal_period_id == open_month.fiscal_period_id


def test_can_post_blocks_adjustment_period_without_allow_flag(org_id):
    db = MagicMock()

    adjustment_open = MockFiscalPeriod(
        organization_id=org_id,
        status=PeriodStatus.OPEN,
        is_adjustment_period=True,
        start_date=date(2026, 1, 31),
        end_date=date(2026, 1, 31),
    )
    db.scalars.return_value.all.return_value = [adjustment_open]

    result = PeriodGuardService.can_post_to_date(db, org_id, date(2026, 1, 31))

    assert result.is_allowed is False
    assert "adjustment periods require explicit allowance" in result.message.lower()
