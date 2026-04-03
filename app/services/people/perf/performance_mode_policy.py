"""Performance mode compatibility policy helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.models.finance.core_org import Organization
from app.models.finance.core_org import PerformanceMode
from app.services.people.perf.performance_policy import PerformancePolicyProfile
from app.services.people.perf.performance_policy import get_policy_profile

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def resolve_performance_mode(organization) -> PerformanceMode:
    """Resolve effective mode with legacy fallback for transition compatibility."""
    raw_mode = getattr(organization, "performance_mode", None) if organization else None
    legacy_value = getattr(organization, "pms_ohcsf_enabled", False)
    legacy_pms_enabled = legacy_value is True
    if isinstance(raw_mode, PerformanceMode):
        return raw_mode
    if isinstance(raw_mode, str):
        normalized = raw_mode.strip().upper()
        if normalized:
            try:
                return PerformanceMode(normalized)
            except ValueError:
                pass
    if legacy_pms_enabled:
        return PerformanceMode.GOVERNMENT_PMS
    return PerformanceMode.PRIVATE


def get_policy_profile_for_mode(
    mode: PerformanceMode | str | None,
) -> PerformancePolicyProfile:
    """Resolve the policy profile that should drive UI/behavior for a mode."""
    if isinstance(mode, PerformanceMode):
        normalized = mode
    elif isinstance(mode, str):
        try:
            normalized = PerformanceMode(mode.strip().upper())
        except ValueError:
            normalized = PerformanceMode.PRIVATE
    else:
        normalized = PerformanceMode.PRIVATE

    if normalized == PerformanceMode.PRIVATE:
        return get_policy_profile("PRIVATE")
    return get_policy_profile("GOVERNMENT_PMS")


def is_pms_enabled_for_org(organization) -> bool:
    """Return whether PMS should be treated as enabled for an organization."""
    return resolve_performance_mode(organization) in {
        PerformanceMode.GOVERNMENT_PMS,
        PerformanceMode.HYBRID,
    }


def _load_org_for_mode(db: Session, org_id: UUID) -> Organization | None:
    """Return real Organization model instance when available, else None."""
    organization = db.get(Organization, org_id)
    return organization if isinstance(organization, Organization) else None


def enforce_pms_write_mode(db: Session, org_id: UUID) -> None:
    """Block PMS-specific writes when org mode does not allow PMS operations."""
    organization = _load_org_for_mode(db, org_id)
    if organization is None:
        return
    mode = resolve_performance_mode(organization)
    if mode in {PerformanceMode.GOVERNMENT_PMS, PerformanceMode.HYBRID}:
        return
    raise ValueError(
        "MODE_POLICY_BLOCKED:pms_write_requires_government_or_hybrid "
        f"(current_mode={mode.value})"
    )


def enforce_private_write_mode(db: Session, org_id: UUID) -> None:
    """Block private-performance writes when org mode does not allow them."""
    organization = _load_org_for_mode(db, org_id)
    if organization is None:
        return
    mode = resolve_performance_mode(organization)
    if mode in {PerformanceMode.PRIVATE, PerformanceMode.HYBRID}:
        return
    raise ValueError(
        "MODE_POLICY_BLOCKED:private_write_requires_private_or_hybrid "
        f"(current_mode={mode.value})"
    )
