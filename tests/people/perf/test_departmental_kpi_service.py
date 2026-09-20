"""Unit coverage for configurable departmental KPI scoring contracts."""

from decimal import Decimal
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.models.people.perf import DepartmentPerformanceTemplate
from app.services.people.perf.departmental_kpi_service import (
    DepartmentalKPIError,
    DepartmentalKPIService,
)


def test_higher_is_better_score_is_capped() -> None:
    assert DepartmentalKPIService.calculate_score(
        actual_value=Decimal("120"),
        target_value=Decimal("100"),
        direction="HIGHER_IS_BETTER",
    ) == Decimal("100.00")
    assert DepartmentalKPIService.calculate_score(
        actual_value=Decimal("75"),
        target_value=Decimal("100"),
        direction="HIGHER_IS_BETTER",
    ) == Decimal("75.00")


def test_lower_is_better_score_rewards_values_at_or_below_target() -> None:
    assert DepartmentalKPIService.calculate_score(
        actual_value=Decimal("12"),
        target_value=Decimal("15"),
        direction="LOWER_IS_BETTER",
    ) == Decimal("100.00")
    assert DepartmentalKPIService.calculate_score(
        actual_value=Decimal("30"),
        target_value=Decimal("15"),
        direction="LOWER_IS_BETTER",
    ) == Decimal("50.00")


def test_weighted_score_is_normalized_and_missing_data_is_not_zero() -> None:
    assert DepartmentalKPIService.weighted_average(
        [
            (Decimal("100"), Decimal("25")),
            (Decimal("50"), Decimal("75")),
        ]
    ) == Decimal("62.50")
    assert DepartmentalKPIService.weighted_average([]) is None


def test_effective_period_overlap_supports_versioned_assignments() -> None:
    assert DepartmentalKPIService._effective_periods_overlap(
        date(2026, 1, 1),
        date(2026, 6, 30),
        date(2026, 6, 30),
        date(2026, 12, 31),
    )
    assert not DepartmentalKPIService._effective_periods_overlap(
        date(2026, 1, 1),
        date(2026, 6, 29),
        date(2026, 6, 30),
        date(2026, 12, 31),
    )


def test_target_band_score_supports_both_sides() -> None:
    kwargs = {
        "target_value": Decimal("50"),
        "direction": "TARGET_BAND",
        "band_min_value": Decimal("45"),
        "band_max_value": Decimal("55"),
    }
    assert DepartmentalKPIService.calculate_score(
        actual_value=Decimal("50"), **kwargs
    ) == Decimal("100.00")
    assert DepartmentalKPIService.calculate_score(
        actual_value=Decimal("36"), **kwargs
    ) == Decimal("80.00")
    assert DepartmentalKPIService.calculate_score(
        actual_value=Decimal("68.75"), **kwargs
    ) == Decimal("80.00")


def test_target_band_requires_bounds() -> None:
    with pytest.raises(DepartmentalKPIError, match="minimum and maximum"):
        DepartmentalKPIService.calculate_score(
            actual_value=Decimal("50"),
            target_value=Decimal("50"),
            direction="TARGET_BAND",
        )


def test_configured_thresholds_drive_status() -> None:
    assert (
        DepartmentalKPIService.performance_status(
            Decimal("91"),
            green_threshold=Decimal("90"),
            amber_threshold=Decimal("70"),
        )
        == "GREEN"
    )
    assert (
        DepartmentalKPIService.performance_status(
            Decimal("75"),
            green_threshold=Decimal("90"),
            amber_threshold=Decimal("70"),
        )
        == "AMBER"
    )
    assert (
        DepartmentalKPIService.performance_status(
            Decimal("69"),
            green_threshold=Decimal("90"),
            amber_threshold=Decimal("70"),
        )
        == "RED"
    )
    assert (
        DepartmentalKPIService.performance_status(
            None,
            green_threshold=Decimal("90"),
            amber_threshold=Decimal("70"),
        )
        == "NO_DATA"
    )


def test_threshold_configuration_rejects_out_of_range_values() -> None:
    service = DepartmentalKPIService(db=None)  # type: ignore[arg-type]
    values = {
        "direction": "HIGHER_IS_BETTER",
        "measurement_type": "PERCENTAGE",
        "frequency": "MONTHLY",
        "calculation_method": "RATIO",
        "weightage": Decimal("20"),
        "green_threshold": Decimal("101"),
        "amber_threshold": Decimal("80"),
        "band_min_value": None,
        "band_max_value": None,
        "effective_start": None,
        "effective_end": None,
    }
    with pytest.raises(DepartmentalKPIError, match="between 0 and 100"):
        service._validate_config_values(values)


def test_department_configuration_contains_required_contract_fields() -> None:
    columns = DepartmentPerformanceTemplate.__table__.columns
    for name in (
        "kpi_code",
        "category",
        "measurement_type",
        "direction",
        "frequency",
        "calculation_method",
        "green_threshold",
        "amber_threshold",
        "assignment_scope",
        "designation_id",
        "position_id",
        "employee_id",
        "config_version",
    ):
        assert name in columns


def test_hardcoded_department_kpi_library_is_not_active() -> None:
    source = Path("app/services/people/perf/perf_service.py").read_text(
        encoding="utf-8"
    )
    assert "DEPARTMENT_TEMPLATE_LIBRARY" not in source
    assert "_department_template_key" not in source


@pytest.mark.parametrize(
    "source_key",
    ["support.resolution_rate", "support.avg_resolution_days"],
)
def test_automatic_rates_without_source_records_are_not_reported_as_zero(
    source_key: str,
) -> None:
    db = MagicMock()
    db.scalar.return_value = 0
    db.scalars.return_value.all.return_value = []

    value = DepartmentalKPIService(db)._automatic_value(
        uuid4(),
        employee_id=uuid4(),
        source_key=source_key,
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 30),
    )

    assert value is None


def test_migration_provisions_audit_rls_and_dedicated_permissions() -> None:
    migration = Path(
        "alembic/versions/20260920_configurable_departmental_kpis.py"
    ).read_text(encoding="utf-8")
    assert '"kpi_measurement_history"' in migration
    assert '"kpi_configuration_audit"' in migration
    assert "FORCE ROW LEVEL SECURITY" in migration
    for permission in (
        "performance:kpi:dashboard:view",
        "performance:kpi:dashboard:view_all_departments",
        "performance:kpi:manage",
        "performance:kpi:measure",
        "performance:kpi:approve",
        "performance:kpi:export",
    ):
        assert permission in migration
