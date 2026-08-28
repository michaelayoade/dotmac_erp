from __future__ import annotations

import pytest

from scripts.probe_people_employment_type_activation import ProbeExit, classify


@pytest.mark.parametrize(
    ("revision_recorded", "bootstrap_fence_present", "expected"),
    [
        (True, False, ProbeExit.ACTIVATED),
        (True, True, ProbeExit.ACTIVATED),
        (False, True, ProbeExit.DEFINITELY_PRE_ACTIVATION),
        (False, False, ProbeExit.AMBIGUOUS),
    ],
)
def test_probe_allows_rollback_only_from_a_positive_pre_activation_state(
    revision_recorded: bool,
    bootstrap_fence_present: bool,
    expected: ProbeExit,
) -> None:
    assert (
        classify(
            revision_recorded=revision_recorded,
            bootstrap_fence_present=bootstrap_fence_present,
        )
        is expected
    )
