from types import SimpleNamespace

from app.models.finance.core_org import PerformanceMode
from app.services.people.perf.performance_mode_policy import (
    get_policy_profile_for_mode,
    is_pms_enabled_for_org,
    resolve_performance_mode,
)


def test_resolve_mode_prefers_explicit_enum_value() -> None:
    org = SimpleNamespace(
        performance_mode=PerformanceMode.HYBRID,
        pms_ohcsf_enabled=False,
    )
    assert resolve_performance_mode(org) == PerformanceMode.HYBRID


def test_resolve_mode_falls_back_to_legacy_flag_when_mode_missing() -> None:
    org = SimpleNamespace(
        performance_mode=None,
        pms_ohcsf_enabled=True,
    )
    assert resolve_performance_mode(org) == PerformanceMode.GOVERNMENT_PMS


def test_is_pms_enabled_for_org_from_resolved_mode() -> None:
    private_org = SimpleNamespace(performance_mode=PerformanceMode.PRIVATE)
    gov_org = SimpleNamespace(performance_mode=PerformanceMode.GOVERNMENT_PMS)

    assert is_pms_enabled_for_org(private_org) is False
    assert is_pms_enabled_for_org(gov_org) is True


def test_resolve_mode_keeps_explicit_private_mode_when_legacy_pms_true() -> None:
    org = SimpleNamespace(
        performance_mode=PerformanceMode.PRIVATE,
        pms_ohcsf_enabled=True,
    )
    assert resolve_performance_mode(org) == PerformanceMode.PRIVATE


def test_policy_profile_resolution_by_mode() -> None:
    assert (
        get_policy_profile_for_mode(PerformanceMode.PRIVATE).name
        == "PRIVATE"
    )
    assert (
        get_policy_profile_for_mode(PerformanceMode.HYBRID).name
        == "GOVERNMENT_PMS"
    )
