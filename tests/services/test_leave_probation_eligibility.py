from datetime import date
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.services.people.leave.leave_service import (
    LeaveEligibilityError,
    LeaveService,
)


def _service_for_eligibility(
    *, restricted: bool, probation_days: int = 90, allocation=None
):
    db = MagicMock()
    employee = MagicMock(date_of_joining=date(2026, 1, 1))
    organization = MagicMock(hr_probation_days=probation_days)
    db.scalar.side_effect = [employee, allocation]
    db.get.return_value = organization
    service = LeaveService(db)
    leave_type = MagicMock(
        restricted_during_probation=restricted,
        leave_type_id=uuid4(),
    )
    return service, leave_type


def test_unrestricted_leave_is_allowed_during_probation():
    service, leave_type = _service_for_eligibility(restricted=False)

    service._validate_probation_eligibility(
        uuid4(), uuid4(), leave_type, date(2026, 1, 15), date(2026, 1, 16)
    )

    service.db.scalar.assert_not_called()


def test_restricted_leave_is_blocked_before_configured_probation_ends():
    service, leave_type = _service_for_eligibility(restricted=True)

    with pytest.raises(LeaveEligibilityError, match="restricted during probation"):
        service._validate_probation_eligibility(
            uuid4(), uuid4(), leave_type, date(2026, 1, 15), date(2026, 1, 16)
        )


def test_hr_allocation_overrides_probation_restriction():
    allocation = MagicMock()
    service, leave_type = _service_for_eligibility(
        restricted=True, allocation=allocation
    )

    service._validate_probation_eligibility(
        uuid4(), uuid4(), leave_type, date(2026, 1, 15), date(2026, 1, 16)
    )


def test_restricted_leave_is_allowed_after_probation_ends():
    service, leave_type = _service_for_eligibility(restricted=True, probation_days=30)

    service._validate_probation_eligibility(
        uuid4(), uuid4(), leave_type, date(2026, 2, 1), date(2026, 2, 2)
    )

    service.db.scalar.assert_called_once()
