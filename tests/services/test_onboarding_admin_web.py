from types import SimpleNamespace

import pytest

from app.services.people.hr.web.onboarding_admin_web import _progress_context


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], 0),
        (["PENDING", "PENDING"], 0),
        (["COMPLETED", "PENDING"], 50),
        (["COMPLETED", "SKIPPED"], 100),
    ],
)
def test_progress_context_matches_template_contract(
    statuses: list[str], expected: int
) -> None:
    onboarding = SimpleNamespace(
        activities=[
            SimpleNamespace(activity_status=status, status=None) for status in statuses
        ]
    )

    assert _progress_context(onboarding) == {"percentage": expected}
