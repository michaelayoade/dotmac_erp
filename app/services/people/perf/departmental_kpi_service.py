"""Configurable departmental KPI management, scoring, and reporting.

The existing ``KPI`` row remains the period-and-employee performance record.
``DepartmentPerformanceTemplate`` is the administrator-owned definition and
assignment. This service is the single calculation boundary used by dashboard,
manual entry, and automatic data-source refreshes.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.people.hr import Department, Designation, Employee, EmployeeStatus
from app.models.people.hr.position import Position
from app.models.people.hr.position_assignment import PositionAssignment
from app.models.people.perf import (
    DepartmentPerformanceTemplate,
    KPI,
    KPIConfigurationAudit,
    KPIMeasurementHistory,
    KPIStatus,
)
from app.models.support.ticket import Ticket, TicketStatus


class DepartmentalKPIError(ValueError):
    """Raised when a departmental KPI operation violates configuration rules."""


MEASUREMENT_TYPES = (
    "PERCENTAGE",
    "NUMBER",
    "CURRENCY",
    "DURATION",
    "COUNT",
    "RATIO",
    "BOOLEAN",
)
KPI_DIRECTIONS = ("HIGHER_IS_BETTER", "LOWER_IS_BETTER", "TARGET_BAND")
KPI_FREQUENCIES = ("WEEKLY", "MONTHLY", "QUARTERLY", "YEARLY")
ASSIGNMENT_SCOPES = ("DEPARTMENT", "DESIGNATION", "POSITION", "EMPLOYEE")
CALCULATION_METHODS = ("RATIO", "BAND", "BOOLEAN")
AUTOMATIC_DATA_SOURCES = (
    "support.tickets_resolved",
    "support.open_backlog",
    "support.resolution_rate",
    "support.avg_resolution_days",
)


class DepartmentalKPIService:
    """Tenant-scoped service for reusable departmental KPI configuration."""

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def calculate_score(
        *,
        actual_value: Decimal | None,
        target_value: Decimal,
        direction: str,
        band_min_value: Decimal | None = None,
        band_max_value: Decimal | None = None,
    ) -> Decimal | None:
        """Calculate a capped 0-100 score for every supported KPI direction."""
        if actual_value is None:
            return None

        direction = direction.upper()
        if direction == "HIGHER_IS_BETTER":
            if target_value == 0:
                raw_score = Decimal("100") if actual_value >= 0 else Decimal("0")
            else:
                raw_score = actual_value / target_value * Decimal("100")
        elif direction == "LOWER_IS_BETTER":
            if target_value == 0:
                raw_score = Decimal("100") if actual_value <= 0 else Decimal("0")
            elif actual_value <= 0:
                raw_score = Decimal("100")
            else:
                raw_score = target_value / actual_value * Decimal("100")
        elif direction == "TARGET_BAND":
            if band_min_value is None or band_max_value is None:
                raise DepartmentalKPIError(
                    "Target-band KPIs require both minimum and maximum values"
                )
            if band_min_value > band_max_value:
                raise DepartmentalKPIError(
                    "Target-band minimum cannot exceed the maximum"
                )
            if band_min_value <= actual_value <= band_max_value:
                raw_score = Decimal("100")
            elif actual_value < band_min_value:
                raw_score = (
                    Decimal("0")
                    if band_min_value == 0
                    else actual_value / band_min_value * Decimal("100")
                )
            else:
                raw_score = (
                    Decimal("0")
                    if actual_value == 0
                    else band_max_value / actual_value * Decimal("100")
                )
        else:
            raise DepartmentalKPIError(f"Unsupported KPI direction: {direction}")

        return max(Decimal("0"), min(Decimal("100"), raw_score)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    @staticmethod
    def performance_status(
        score: Decimal | None,
        *,
        green_threshold: Decimal,
        amber_threshold: Decimal,
    ) -> str:
        """Resolve a display status from configured score thresholds."""
        if score is None:
            return "NO_DATA"
        if green_threshold < amber_threshold:
            raise DepartmentalKPIError(
                "Green threshold must be greater than or equal to amber threshold"
            )
        if score >= green_threshold:
            return "GREEN"
        if score >= amber_threshold:
            return "AMBER"
        return "RED"

    @staticmethod
    def config_snapshot(config: DepartmentPerformanceTemplate) -> dict[str, Any]:
        """Return the immutable fields needed to reproduce a score."""
        return {
            "template_id": str(config.template_id),
            "version": config.config_version,
            "kpi_code": config.kpi_code,
            "kpi_name": config.kpi_name,
            "target_value": str(config.target_value),
            "weightage": str(config.weightage),
            "measurement_type": config.measurement_type,
            "unit_of_measure": config.unit_of_measure,
            "direction": config.direction,
            "calculation_method": config.calculation_method,
            "green_threshold": str(config.green_threshold),
            "amber_threshold": str(config.amber_threshold),
            "band_min_value": (
                str(config.band_min_value)
                if config.band_min_value is not None
                else None
            ),
            "band_max_value": (
                str(config.band_max_value)
                if config.band_max_value is not None
                else None
            ),
            "metric_source_key": config.metric_source_key,
        }

    @staticmethod
    def _serialize_config(config: DepartmentPerformanceTemplate) -> dict[str, Any]:
        snapshot = DepartmentalKPIService.config_snapshot(config)
        snapshot.update(
            {
                "department_id": str(config.department_id),
                "kra_name": config.kra_name,
                "category": config.category,
                "frequency": config.frequency,
                "assignment_scope": config.assignment_scope,
                "designation_id": (
                    str(config.designation_id) if config.designation_id else None
                ),
                "position_id": str(config.position_id) if config.position_id else None,
                "employee_id": str(config.employee_id) if config.employee_id else None,
                "effective_start": (
                    config.effective_start.isoformat()
                    if config.effective_start
                    else None
                ),
                "effective_end": (
                    config.effective_end.isoformat() if config.effective_end else None
                ),
                "is_active": config.is_active,
            }
        )
        return snapshot

    def _audit_config(
        self,
        *,
        org_id: UUID,
        config: DepartmentPerformanceTemplate,
        action: str,
        actor_id: UUID | None,
        previous_values: dict[str, Any] | None,
    ) -> None:
        self.db.add(
            KPIConfigurationAudit(
                organization_id=org_id,
                template_id=config.template_id,
                action=action,
                previous_values=previous_values,
                new_values=self._serialize_config(config),
                changed_by_id=actor_id,
            )
        )

    def list_departments(self, org_id: UUID) -> list[Department]:
        return list(
            self.db.scalars(
                select(Department)
                .where(
                    Department.organization_id == org_id,
                    Department.is_active.is_(True),
                )
                .order_by(Department.department_name)
            ).all()
        )

    def list_configurations(
        self,
        org_id: UUID,
        *,
        department_id: UUID | None = None,
        include_inactive: bool = False,
    ) -> list[DepartmentPerformanceTemplate]:
        query = (
            select(DepartmentPerformanceTemplate)
            .options(
                joinedload(DepartmentPerformanceTemplate.department),
                joinedload(DepartmentPerformanceTemplate.employee),
                joinedload(DepartmentPerformanceTemplate.designation),
                joinedload(DepartmentPerformanceTemplate.position),
            )
            .where(DepartmentPerformanceTemplate.organization_id == org_id)
        )
        if department_id:
            query = query.where(
                DepartmentPerformanceTemplate.department_id == department_id
            )
        if not include_inactive:
            query = query.where(DepartmentPerformanceTemplate.is_active.is_(True))
        return list(
            self.db.scalars(
                query.order_by(
                    DepartmentPerformanceTemplate.department_id,
                    DepartmentPerformanceTemplate.kpi_name,
                )
            )
            .unique()
            .all()
        )

    def configuration_health(
        self,
        org_id: UUID,
        configurations: list[DepartmentPerformanceTemplate],
        *,
        period_start: date | None = None,
        period_end: date | None = None,
        weight_summaries: dict[UUID, dict[str, Any]] | None = None,
    ) -> dict[UUID, dict[str, Any]]:
        """Build visible readiness issues for KPI definitions.

        Health is a projection only. It never changes a definition or creates
        an assignment, and all assignment checks remain tenant-scoped.
        """
        if not configurations:
            return {}
        today = date.today()
        start = period_start or date(today.year, 1, 1)
        end = period_end or date(today.year, 12, 31)
        ids = [config.template_id for config in configurations]
        coverage = {
            template_id: (employee_count, record_count)
            for template_id, employee_count, record_count in self.db.execute(
                select(
                    KPI.department_template_id,
                    func.count(func.distinct(KPI.employee_id)),
                    func.count(KPI.kpi_id),
                )
                .where(
                    KPI.organization_id == org_id,
                    KPI.department_template_id.in_(ids),
                    KPI.period_start <= end,
                    KPI.period_end >= start,
                )
                .group_by(KPI.department_template_id)
            ).all()
        }
        health: dict[UUID, dict[str, Any]] = {}
        for config in configurations:
            employee_count, record_count = coverage.get(config.template_id, (0, 0))
            issues: list[str] = []
            if not config.is_active:
                issues.append("Inactive")
            if config.effective_end and config.effective_end < today:
                issues.append("Expired")
            if (
                config.metric_source_key
                and config.metric_source_key not in AUTOMATIC_DATA_SOURCES
            ):
                issues.append("Unsupported source")
            if config.target_value is None or (
                config.target_value <= 0 and config.direction != "LOWER_IS_BETTER"
            ):
                issues.append("Missing target")
            if config.direction == "TARGET_BAND" and (
                config.band_min_value is None or config.band_max_value is None
            ):
                issues.append("Missing thresholds")
            if employee_count == 0:
                issues.append("No employee coverage")
            if record_count == 0:
                issues.append("Not instantiated")
            summary = (weight_summaries or {}).get(config.department_id)
            if summary and not summary.get("is_complete"):
                issues.append("Department weights incomplete")
            health[config.template_id] = {
                "issues": issues,
                "employee_count": employee_count,
                "record_count": record_count,
                "ready": not issues,
            }
        return health

    def configuration_health_summary(
        self,
        org_id: UUID,
        *,
        department_ids: set[UUID] | None = None,
        period_start: date | None = None,
        period_end: date | None = None,
    ) -> dict[str, Any]:
        """Summarize definition readiness for the Overview health panel."""
        configurations = self.list_configurations(org_id, include_inactive=True)
        if department_ids is not None:
            configurations = [
                config
                for config in configurations
                if config.department_id in department_ids
            ]
        weight_summaries = {
            department_id: self.weight_summary(org_id, department_id)
            for department_id in {config.department_id for config in configurations}
        }
        health = self.configuration_health(
            org_id,
            configurations,
            period_start=period_start,
            period_end=period_end,
            weight_summaries=weight_summaries,
        )
        issue_counts: dict[str, int] = defaultdict(int)
        for item in health.values():
            for issue in item["issues"]:
                issue_counts[issue] += 1
        return {
            "definitions": len(configurations),
            "ready": sum(1 for item in health.values() if item["ready"]),
            "not_ready": sum(1 for item in health.values() if not item["ready"]),
            "issue_counts": dict(sorted(issue_counts.items())),
            "incomplete_weights": issue_counts.get("Department weights incomplete", 0),
            "unsupported_sources": issue_counts.get("Unsupported source", 0),
            "not_instantiated": issue_counts.get("Not instantiated", 0),
        }

    def get_configuration(
        self, org_id: UUID, template_id: UUID
    ) -> DepartmentPerformanceTemplate:
        config = self.db.scalar(
            select(DepartmentPerformanceTemplate)
            .options(
                joinedload(DepartmentPerformanceTemplate.department),
                joinedload(DepartmentPerformanceTemplate.employee),
                joinedload(DepartmentPerformanceTemplate.designation),
                joinedload(DepartmentPerformanceTemplate.position),
            )
            .where(
                DepartmentPerformanceTemplate.organization_id == org_id,
                DepartmentPerformanceTemplate.template_id == template_id,
            )
        )
        if config is None:
            raise DepartmentalKPIError("KPI configuration was not found")
        return config

    def _validate_scope(
        self,
        org_id: UUID,
        *,
        department_id: UUID,
        assignment_scope: str,
        designation_id: UUID | None,
        position_id: UUID | None,
        employee_id: UUID | None,
    ) -> None:
        department = self.db.scalar(
            select(Department.department_id).where(
                Department.organization_id == org_id,
                Department.department_id == department_id,
            )
        )
        if department is None:
            raise DepartmentalKPIError("Department was not found")

        scope = assignment_scope.upper()
        if scope not in ASSIGNMENT_SCOPES:
            raise DepartmentalKPIError("Invalid KPI assignment scope")
        targets = {
            "DESIGNATION": designation_id,
            "POSITION": position_id,
            "EMPLOYEE": employee_id,
        }
        selected_targets = [
            name for name, value in targets.items() if value is not None
        ]
        if scope != "DEPARTMENT" and selected_targets != [scope]:
            raise DepartmentalKPIError(
                f"{scope.title()} assignment requires its matching target"
            )
        if scope == "DEPARTMENT" and any(
            value is not None for value in (designation_id, position_id, employee_id)
        ):
            raise DepartmentalKPIError(
                "Department assignment cannot include a narrower target"
            )

        if designation_id:
            found = self.db.scalar(
                select(Designation.designation_id).where(
                    Designation.organization_id == org_id,
                    Designation.designation_id == designation_id,
                )
            )
            if found is None:
                raise DepartmentalKPIError("Designation was not found")
        if position_id:
            found = self.db.scalar(
                select(Position.position_id).where(
                    Position.organization_id == org_id,
                    Position.position_id == position_id,
                    Position.department_id == department_id,
                )
            )
            if found is None:
                raise DepartmentalKPIError(
                    "Position does not belong to the selected department"
                )
        if employee_id:
            found = self.db.scalar(
                select(Employee.employee_id).where(
                    Employee.organization_id == org_id,
                    Employee.employee_id == employee_id,
                    Employee.department_id == department_id,
                )
            )
            if found is None:
                raise DepartmentalKPIError(
                    "Employee does not belong to the selected department"
                )

    def _validate_config_values(self, values: dict[str, Any]) -> None:
        direction = str(values["direction"]).upper()
        if direction not in KPI_DIRECTIONS:
            raise DepartmentalKPIError("Invalid KPI direction")
        if str(values["measurement_type"]).upper() not in MEASUREMENT_TYPES:
            raise DepartmentalKPIError("Invalid KPI measurement type")
        if str(values["frequency"]).upper() not in KPI_FREQUENCIES:
            raise DepartmentalKPIError("Invalid KPI frequency")
        if str(values["calculation_method"]).upper() not in CALCULATION_METHODS:
            raise DepartmentalKPIError("Invalid KPI calculation method")
        weightage = Decimal(values["weightage"])
        if weightage < 0 or weightage > 100:
            raise DepartmentalKPIError("KPI weight must be between 0 and 100")
        green = Decimal(values["green_threshold"])
        amber = Decimal(values["amber_threshold"])
        if green < 0 or green > 100 or amber < 0 or amber > 100:
            raise DepartmentalKPIError("KPI thresholds must be between 0 and 100")
        if green < amber:
            raise DepartmentalKPIError(
                "Green threshold must be greater than or equal to amber threshold"
            )
        if direction == "TARGET_BAND" and (
            values.get("band_min_value") is None or values.get("band_max_value") is None
        ):
            raise DepartmentalKPIError(
                "Target-band KPIs require minimum and maximum values"
            )
        if (
            values.get("band_min_value") is not None
            and values.get("band_max_value") is not None
            and Decimal(values["band_min_value"]) > Decimal(values["band_max_value"])
        ):
            raise DepartmentalKPIError("Target-band minimum cannot exceed the maximum")
        if values.get("effective_start") and values.get("effective_end"):
            if values["effective_end"] < values["effective_start"]:
                raise DepartmentalKPIError(
                    "Effective end must be on or after effective start"
                )

    def _validate_department_weight(
        self,
        org_id: UUID,
        *,
        department_id: UUID,
        proposed_weight: Decimal,
        assignment_scope: str,
        designation_id: UUID | None,
        position_id: UUID | None,
        employee_id: UUID | None,
        effective_start: date | None,
        effective_end: date | None,
        exclude_template_id: UUID | None = None,
    ) -> None:
        query = select(DepartmentPerformanceTemplate).where(
            DepartmentPerformanceTemplate.organization_id == org_id,
            DepartmentPerformanceTemplate.department_id == department_id,
            DepartmentPerformanceTemplate.is_active.is_(True),
        )
        if exclude_template_id:
            query = query.where(
                DepartmentPerformanceTemplate.template_id != exclude_template_id
            )
        existing_configs = list(self.db.scalars(query).all())
        as_of = effective_start or date.today()
        proposed_employee_ids = self._employee_ids_for_scope(
            org_id,
            department_id=department_id,
            assignment_scope=assignment_scope,
            designation_id=designation_id,
            position_id=position_id,
            employee_id=employee_id,
            as_of=as_of,
        )
        totals = {
            candidate_id: proposed_weight for candidate_id in proposed_employee_ids
        }
        for existing in existing_configs:
            if not self._effective_periods_overlap(
                effective_start,
                effective_end,
                existing.effective_start,
                existing.effective_end,
            ):
                continue
            matching_ids = self._employee_ids_for_scope(
                org_id,
                department_id=existing.department_id,
                assignment_scope=existing.assignment_scope,
                designation_id=existing.designation_id,
                position_id=existing.position_id,
                employee_id=existing.employee_id,
                as_of=as_of,
            )
            for candidate_id in proposed_employee_ids.intersection(matching_ids):
                totals[candidate_id] += existing.weightage
        if any(total > Decimal("100.00") for total in totals.values()):
            raise DepartmentalKPIError(
                "Applicable KPI weights for an employee cannot exceed 100%"
            )

        # A future role may have no employee yet. Validate the equivalent
        # assignment bucket so an empty team cannot accumulate invalid weight.
        if not proposed_employee_ids:
            same_bucket_total = sum(
                (
                    existing.weightage
                    for existing in existing_configs
                    if existing.assignment_scope == assignment_scope
                    and existing.designation_id == designation_id
                    and existing.position_id == position_id
                    and existing.employee_id == employee_id
                    and self._effective_periods_overlap(
                        effective_start,
                        effective_end,
                        existing.effective_start,
                        existing.effective_end,
                    )
                ),
                Decimal("0"),
            )
            if same_bucket_total + proposed_weight > Decimal("100.00"):
                raise DepartmentalKPIError(
                    "KPI weights for this assignment cannot exceed 100%"
                )

    @staticmethod
    def _effective_periods_overlap(
        first_start: date | None,
        first_end: date | None,
        second_start: date | None,
        second_end: date | None,
    ) -> bool:
        if first_end is not None and second_start is not None:
            if first_end < second_start:
                return False
        if second_end is not None and first_start is not None:
            if second_end < first_start:
                return False
        return True

    def _employee_ids_for_scope(
        self,
        org_id: UUID,
        *,
        department_id: UUID,
        assignment_scope: str,
        designation_id: UUID | None,
        position_id: UUID | None,
        employee_id: UUID | None,
        as_of: date,
    ) -> set[UUID]:
        query = select(Employee.employee_id).where(
            Employee.organization_id == org_id,
            Employee.department_id == department_id,
            Employee.status == EmployeeStatus.ACTIVE,
        )
        if assignment_scope == "EMPLOYEE":
            query = query.where(Employee.employee_id == employee_id)
        elif assignment_scope == "DESIGNATION":
            query = query.where(Employee.designation_id == designation_id)
        elif assignment_scope == "POSITION":
            query = query.join(
                PositionAssignment,
                PositionAssignment.employee_id == Employee.employee_id,
            ).where(
                PositionAssignment.organization_id == org_id,
                PositionAssignment.position_id == position_id,
                PositionAssignment.start_date <= as_of,
                or_(
                    PositionAssignment.end_date.is_(None),
                    PositionAssignment.end_date >= as_of,
                ),
            )
        return set(self.db.scalars(query).all())

    def create_configuration(
        self,
        org_id: UUID,
        *,
        actor_id: UUID | None,
        values: dict[str, Any],
    ) -> DepartmentPerformanceTemplate:
        normalized = dict(values)
        for key in (
            "measurement_type",
            "direction",
            "frequency",
            "calculation_method",
            "assignment_scope",
        ):
            normalized[key] = str(normalized[key]).upper()
        normalized["kpi_code"] = str(normalized["kpi_code"]).strip().upper()
        normalized["lower_is_better"] = normalized["direction"] == "LOWER_IS_BETTER"
        self._validate_config_values(normalized)
        self._validate_scope(
            org_id,
            department_id=normalized["department_id"],
            assignment_scope=normalized["assignment_scope"],
            designation_id=normalized.get("designation_id"),
            position_id=normalized.get("position_id"),
            employee_id=normalized.get("employee_id"),
        )
        if normalized.get("is_active", True):
            self._validate_department_weight(
                org_id,
                department_id=normalized["department_id"],
                proposed_weight=Decimal(normalized["weightage"]),
                assignment_scope=normalized["assignment_scope"],
                designation_id=normalized.get("designation_id"),
                position_id=normalized.get("position_id"),
                employee_id=normalized.get("employee_id"),
                effective_start=normalized.get("effective_start"),
                effective_end=normalized.get("effective_end"),
            )
        config = DepartmentPerformanceTemplate(
            organization_id=org_id,
            created_by_id=actor_id,
            updated_by_id=actor_id,
            **normalized,
        )
        self.db.add(config)
        self.db.flush()
        self._audit_config(
            org_id=org_id,
            config=config,
            action="CREATED",
            actor_id=actor_id,
            previous_values=None,
        )
        self.db.flush()
        return config

    def update_configuration(
        self,
        org_id: UUID,
        template_id: UUID,
        *,
        actor_id: UUID | None,
        values: dict[str, Any],
    ) -> DepartmentPerformanceTemplate:
        config = self.get_configuration(org_id, template_id)
        previous = self._serialize_config(config)
        merged = {
            key: value
            for key, value in previous.items()
            if key
            not in {
                "template_id",
                "version",
                "unit_of_measure",
                "metric_source_key",
                "category",
                "designation_id",
                "position_id",
                "employee_id",
                "effective_start",
                "effective_end",
            }
        }
        merged.update(values)
        for key in (
            "measurement_type",
            "direction",
            "frequency",
            "calculation_method",
            "assignment_scope",
        ):
            if key in merged:
                merged[key] = str(merged[key]).upper()
        if "kpi_code" in merged:
            merged["kpi_code"] = str(merged["kpi_code"]).strip().upper()
        merged["lower_is_better"] = merged["direction"] == "LOWER_IS_BETTER"
        self._validate_config_values(merged)
        self._validate_scope(
            org_id,
            department_id=values.get("department_id", config.department_id),
            assignment_scope=values.get("assignment_scope", config.assignment_scope),
            designation_id=values.get("designation_id", config.designation_id),
            position_id=values.get("position_id", config.position_id),
            employee_id=values.get("employee_id", config.employee_id),
        )
        if values.get("is_active", config.is_active):
            self._validate_department_weight(
                org_id,
                department_id=values.get("department_id", config.department_id),
                proposed_weight=Decimal(values.get("weightage", config.weightage)),
                assignment_scope=values.get(
                    "assignment_scope", config.assignment_scope
                ),
                designation_id=values.get("designation_id", config.designation_id),
                position_id=values.get("position_id", config.position_id),
                employee_id=values.get("employee_id", config.employee_id),
                effective_start=values.get("effective_start", config.effective_start),
                effective_end=values.get("effective_end", config.effective_end),
                exclude_template_id=config.template_id,
            )
        normalized_values = dict(values)
        for key in (
            "measurement_type",
            "direction",
            "frequency",
            "calculation_method",
            "assignment_scope",
            "kpi_code",
            "lower_is_better",
        ):
            if key in merged:
                normalized_values[key] = merged[key]
        for key, value in normalized_values.items():
            if hasattr(config, key):
                setattr(config, key, value)
        config.config_version += 1
        config.updated_by_id = actor_id
        self.db.flush()
        self._audit_config(
            org_id=org_id,
            config=config,
            action="UPDATED" if config.is_active else "DEACTIVATED",
            actor_id=actor_id,
            previous_values=previous,
        )
        self.db.flush()
        return config

    def weight_summary(self, org_id: UUID, department_id: UUID) -> dict[str, Any]:
        configurations = self.list_configurations(org_id, department_id=department_id)
        employee_ids = self._employee_ids_for_scope(
            org_id,
            department_id=department_id,
            assignment_scope="DEPARTMENT",
            designation_id=None,
            position_id=None,
            employee_id=None,
            as_of=date.today(),
        )
        totals = {candidate_id: Decimal("0") for candidate_id in employee_ids}
        today = date.today()
        for configuration in configurations:
            if (
                configuration.effective_start and configuration.effective_start > today
            ) or (configuration.effective_end and configuration.effective_end < today):
                continue
            matching_ids = self._employee_ids_for_scope(
                org_id,
                department_id=department_id,
                assignment_scope=configuration.assignment_scope,
                designation_id=configuration.designation_id,
                position_id=configuration.position_id,
                employee_id=configuration.employee_id,
                as_of=today,
            )
            for candidate_id in employee_ids.intersection(matching_ids):
                totals[candidate_id] += configuration.weightage
        assigned_totals = list(totals.values())
        average = self._average(assigned_totals) or Decimal("0.00")
        return {
            "total": average,
            "minimum": min(assigned_totals) if assigned_totals else Decimal("0.00"),
            "maximum": max(assigned_totals) if assigned_totals else Decimal("0.00"),
            "employee_count": len(assigned_totals),
            "is_complete": bool(assigned_totals)
            and all(total == Decimal("100.00") for total in assigned_totals),
        }

    def _employees_for_config(
        self,
        org_id: UUID,
        config: DepartmentPerformanceTemplate,
        *,
        as_of: date,
    ) -> list[Employee]:
        query = (
            select(Employee)
            .options(joinedload(Employee.person))
            .where(
                Employee.organization_id == org_id,
                Employee.department_id == config.department_id,
                Employee.status == EmployeeStatus.ACTIVE,
            )
        )
        if config.assignment_scope == "EMPLOYEE":
            query = query.where(Employee.employee_id == config.employee_id)
        elif config.assignment_scope == "DESIGNATION":
            query = query.where(Employee.designation_id == config.designation_id)
        elif config.assignment_scope == "POSITION":
            query = query.join(
                PositionAssignment,
                PositionAssignment.employee_id == Employee.employee_id,
            ).where(
                PositionAssignment.organization_id == org_id,
                PositionAssignment.position_id == config.position_id,
                PositionAssignment.start_date <= as_of,
                or_(
                    PositionAssignment.end_date.is_(None),
                    PositionAssignment.end_date >= as_of,
                ),
            )
        return list(
            self.db.scalars(query.order_by(Employee.employee_code)).unique().all()
        )

    def ensure_period_records(
        self,
        org_id: UUID,
        *,
        period_start: date,
        period_end: date,
        department_id: UUID | None = None,
        actor_id: UUID | None = None,
    ) -> dict[str, int]:
        """Instantiate configured KPIs for applicable employees for one period."""
        if period_end < period_start:
            raise DepartmentalKPIError("Period end must be on or after period start")
        query = select(DepartmentPerformanceTemplate).where(
            DepartmentPerformanceTemplate.organization_id == org_id,
            DepartmentPerformanceTemplate.is_active.is_(True),
            or_(
                DepartmentPerformanceTemplate.effective_start.is_(None),
                DepartmentPerformanceTemplate.effective_start <= period_end,
            ),
            or_(
                DepartmentPerformanceTemplate.effective_end.is_(None),
                DepartmentPerformanceTemplate.effective_end >= period_start,
            ),
        )
        if department_id:
            query = query.where(
                DepartmentPerformanceTemplate.department_id == department_id
            )
        configs = list(self.db.scalars(query).all())
        created = 0
        skipped = 0
        for config in configs:
            for employee in self._employees_for_config(
                org_id, config, as_of=period_end
            ):
                existing = self.db.scalar(
                    select(KPI.kpi_id).where(
                        KPI.organization_id == org_id,
                        KPI.department_template_id == config.template_id,
                        KPI.employee_id == employee.employee_id,
                        KPI.period_start == period_start,
                        KPI.period_end == period_end,
                    )
                )
                if existing:
                    skipped += 1
                    continue
                self.db.add(
                    KPI(
                        organization_id=org_id,
                        employee_id=employee.employee_id,
                        department_template_id=config.template_id,
                        kpi_name=config.kpi_name,
                        description=config.description,
                        period_start=period_start,
                        period_end=period_end,
                        target_value=config.target_value,
                        unit_of_measure=config.unit_of_measure,
                        weightage=config.weightage,
                        lower_is_better=config.direction == "LOWER_IS_BETTER",
                        threshold_value=config.amber_threshold,
                        config_snapshot=self.config_snapshot(config),
                        status=KPIStatus.ACTIVE,
                        created_by_id=actor_id,
                        updated_by_id=actor_id,
                    )
                )
                created += 1
        self.db.flush()
        return {"configurations": len(configs), "created": created, "skipped": skipped}

    def _score_for_kpi(
        self, kpi: KPI, config: DepartmentPerformanceTemplate
    ) -> Decimal | None:
        snapshot = kpi.config_snapshot or self.config_snapshot(config)
        return self.calculate_score(
            actual_value=kpi.actual_value,
            target_value=Decimal(str(snapshot["target_value"])),
            direction=str(snapshot["direction"]),
            band_min_value=(
                Decimal(str(snapshot["band_min_value"]))
                if snapshot.get("band_min_value") is not None
                else None
            ),
            band_max_value=(
                Decimal(str(snapshot["band_max_value"]))
                if snapshot.get("band_max_value") is not None
                else None
            ),
        )

    @staticmethod
    def _thresholds_for_kpi(
        kpi: KPI, config: DepartmentPerformanceTemplate
    ) -> tuple[Decimal, Decimal]:
        snapshot = kpi.config_snapshot or DepartmentalKPIService.config_snapshot(config)
        return (
            Decimal(str(snapshot["green_threshold"])),
            Decimal(str(snapshot["amber_threshold"])),
        )

    def record_measurement(
        self,
        org_id: UUID,
        *,
        kpi_id: UUID,
        actual_value: Decimal,
        actor_id: UUID | None,
        evidence: str | None = None,
        notes: str | None = None,
        measurement_mode: str = "MANUAL",
        approve: bool = False,
        is_recalculation: bool = False,
    ) -> KPI:
        kpi = self.db.scalar(
            select(KPI)
            .options(joinedload(KPI.department_template))
            .where(KPI.organization_id == org_id, KPI.kpi_id == kpi_id)
        )
        if kpi is None:
            raise DepartmentalKPIError("KPI record was not found")
        config = kpi.department_template
        if config is None:
            raise DepartmentalKPIError(
                "Legacy KPI has no departmental configuration; use the legacy progress form"
            )
        if measurement_mode.upper() == "MANUAL" and config.metric_source_key:
            raise DepartmentalKPIError(
                "This KPI is automatic; refresh its configured data source instead"
            )
        if config.measurement_type == "BOOLEAN" and actual_value not in {
            Decimal("0"),
            Decimal("1"),
        }:
            raise DepartmentalKPIError("Boolean KPI measurements must be 0 or 1")
        if (
            config.measurement_type
            in {
                "PERCENTAGE",
                "CURRENCY",
                "DURATION",
                "COUNT",
                "RATIO",
            }
            and actual_value < 0
        ):
            raise DepartmentalKPIError("This KPI measurement cannot be negative")
        snapshot = kpi.config_snapshot or self.config_snapshot(config)
        score = self.calculate_score(
            actual_value=actual_value,
            target_value=Decimal(str(snapshot["target_value"])),
            direction=str(snapshot["direction"]),
            band_min_value=(
                Decimal(str(snapshot["band_min_value"]))
                if snapshot.get("band_min_value") is not None
                else None
            ),
            band_max_value=(
                Decimal(str(snapshot["band_max_value"]))
                if snapshot.get("band_max_value") is not None
                else None
            ),
        )
        status = self.performance_status(
            score,
            green_threshold=Decimal(str(snapshot["green_threshold"])),
            amber_threshold=Decimal(str(snapshot["amber_threshold"])),
        )

        # Preserve the approved value until a submitted revision is approved.
        old_actual = kpi.actual_value
        if approve:
            kpi.actual_value = actual_value
            kpi.achievement_percentage = score
            kpi.status = {
                "GREEN": KPIStatus.ACHIEVED,
                "AMBER": KPIStatus.ON_TRACK,
                "RED": KPIStatus.AT_RISK,
            }[status]
            kpi.evidence = evidence or kpi.evidence
            kpi.notes = notes or kpi.notes
            kpi.updated_by_id = actor_id

        # Only the newest submitted revision can remain actionable. This avoids
        # an approver publishing stale data after a corrected value is submitted.
        pending_revisions = self.db.scalars(
            select(KPIMeasurementHistory).where(
                KPIMeasurementHistory.organization_id == org_id,
                KPIMeasurementHistory.kpi_id == kpi.kpi_id,
                KPIMeasurementHistory.approval_status == "SUBMITTED",
            )
        ).all()
        for pending in pending_revisions:
            pending.approval_status = "SUPERSEDED"
            pending.updated_by_id = actor_id

        next_revision = (
            int(
                self.db.scalar(
                    select(
                        func.coalesce(func.max(KPIMeasurementHistory.revision), 0)
                    ).where(
                        KPIMeasurementHistory.organization_id == org_id,
                        KPIMeasurementHistory.kpi_id == kpi.kpi_id,
                    )
                )
                or 0
            )
            + 1
        )
        now = datetime.now(timezone.utc)
        self.db.add(
            KPIMeasurementHistory(
                organization_id=org_id,
                kpi_id=kpi.kpi_id,
                department_template_id=config.template_id,
                employee_id=kpi.employee_id,
                period_start=kpi.period_start,
                period_end=kpi.period_end,
                revision=next_revision,
                previous_actual_value=old_actual,
                actual_value=actual_value,
                score=score,
                weighted_score=(
                    score * kpi.weightage / Decimal("100")
                    if score is not None
                    else None
                ),
                performance_status=status,
                measurement_mode=measurement_mode.upper(),
                source_key=config.metric_source_key,
                evidence=evidence,
                notes=notes,
                approval_status="APPROVED" if approve else "SUBMITTED",
                submitted_by_id=actor_id,
                approved_by_id=actor_id if approve else None,
                approved_at=now if approve else None,
                config_snapshot=snapshot,
                is_recalculation=is_recalculation,
                created_by_id=actor_id,
                updated_by_id=actor_id,
            )
        )
        self.db.flush()
        return kpi

    def approve_measurement(
        self,
        org_id: UUID,
        *,
        history_id: UUID,
        actor_id: UUID | None,
    ) -> KPIMeasurementHistory:
        """Approve a submitted measurement and publish it to the KPI result."""
        history = self.db.scalar(
            select(KPIMeasurementHistory)
            .options(
                joinedload(KPIMeasurementHistory.kpi).joinedload(
                    KPI.department_template
                )
            )
            .where(
                KPIMeasurementHistory.organization_id == org_id,
                KPIMeasurementHistory.history_id == history_id,
            )
        )
        if history is None:
            raise DepartmentalKPIError("Measurement revision was not found")
        if history.approval_status == "APPROVED":
            return history
        if history.approval_status != "SUBMITTED":
            raise DepartmentalKPIError("Only submitted measurements can be approved")
        newest_revision = self.db.scalar(
            select(func.max(KPIMeasurementHistory.revision)).where(
                KPIMeasurementHistory.organization_id == org_id,
                KPIMeasurementHistory.kpi_id == history.kpi_id,
            )
        )
        if newest_revision != history.revision:
            raise DepartmentalKPIError(
                "A newer measurement revision exists; approve the latest submission"
            )
        kpi = history.kpi
        config = kpi.department_template
        if config is None:
            raise DepartmentalKPIError("KPI configuration was not found")
        history.approval_status = "APPROVED"
        history.approved_by_id = actor_id
        history.approved_at = datetime.now(timezone.utc)
        history.updated_by_id = actor_id
        kpi.actual_value = history.actual_value
        kpi.achievement_percentage = history.score
        kpi.status = {
            "GREEN": KPIStatus.ACHIEVED,
            "AMBER": KPIStatus.ON_TRACK,
            "RED": KPIStatus.AT_RISK,
        }.get(history.performance_status or "", KPIStatus.ACTIVE)
        kpi.evidence = history.evidence or kpi.evidence
        kpi.notes = history.notes or kpi.notes
        kpi.updated_by_id = actor_id
        self.db.flush()
        return history

    def _automatic_value(
        self,
        org_id: UUID,
        *,
        employee_id: UUID,
        source_key: str,
        period_start: date,
        period_end: date,
    ) -> Decimal | None:
        resolved = (TicketStatus.RESOLVED, TicketStatus.CLOSED)
        if source_key == "support.tickets_resolved":
            value = self.db.scalar(
                select(func.count(Ticket.ticket_id)).where(
                    Ticket.organization_id == org_id,
                    Ticket.assigned_to_id == employee_id,
                    Ticket.status.in_(resolved),
                    Ticket.opening_date.isnot(None),
                    Ticket.resolution_date.isnot(None),
                    Ticket.resolution_date >= period_start,
                    Ticket.resolution_date <= period_end,
                )
            )
            return Decimal(value or 0)
        if source_key == "support.open_backlog":
            value = self.db.scalar(
                select(func.count(Ticket.ticket_id)).where(
                    Ticket.organization_id == org_id,
                    Ticket.assigned_to_id == employee_id,
                    Ticket.status.in_(
                        (TicketStatus.OPEN, TicketStatus.REPLIED, TicketStatus.ON_HOLD)
                    ),
                    Ticket.opening_date <= period_end,
                )
            )
            return Decimal(value or 0)
        if source_key == "support.resolution_rate":
            total = self.db.scalar(
                select(func.count(Ticket.ticket_id)).where(
                    Ticket.organization_id == org_id,
                    Ticket.assigned_to_id == employee_id,
                    Ticket.opening_date >= period_start,
                    Ticket.opening_date <= period_end,
                )
            )
            if not total:
                return None
            resolved_count = self.db.scalar(
                select(func.count(Ticket.ticket_id)).where(
                    Ticket.organization_id == org_id,
                    Ticket.assigned_to_id == employee_id,
                    Ticket.opening_date >= period_start,
                    Ticket.opening_date <= period_end,
                    Ticket.status.in_(resolved),
                )
            )
            return (Decimal(resolved_count or 0) / Decimal(total) * 100).quantize(
                Decimal("0.01")
            )
        if source_key == "support.avg_resolution_days":
            tickets = self.db.scalars(
                select(Ticket).where(
                    Ticket.organization_id == org_id,
                    Ticket.assigned_to_id == employee_id,
                    Ticket.status.in_(resolved),
                    Ticket.resolution_date >= period_start,
                    Ticket.resolution_date <= period_end,
                )
            ).all()
            durations = [
                (ticket.resolution_date - ticket.opening_date).days
                for ticket in tickets
                if ticket.resolution_date
                and ticket.opening_date
                and ticket.resolution_date >= ticket.opening_date
            ]
            if not durations:
                return None
            return (Decimal(sum(durations)) / Decimal(len(durations))).quantize(
                Decimal("0.01")
            )
        raise DepartmentalKPIError(
            f"No automatic KPI adapter is registered for {source_key}"
        )

    def refresh_automatic_measurements(
        self,
        org_id: UUID,
        *,
        template_id: UUID,
        period_start: date,
        period_end: date,
        actor_id: UUID | None,
    ) -> dict[str, int]:
        config = self.get_configuration(org_id, template_id)
        if not config.metric_source_key:
            raise DepartmentalKPIError("This KPI is configured for manual measurement")
        if config.metric_source_key not in AUTOMATIC_DATA_SOURCES:
            raise DepartmentalKPIError(
                f"Data source {config.metric_source_key} is not available"
            )
        self.ensure_period_records(
            org_id,
            period_start=period_start,
            period_end=period_end,
            department_id=config.department_id,
            actor_id=actor_id,
        )
        records = list(
            self.db.scalars(
                select(KPI).where(
                    KPI.organization_id == org_id,
                    KPI.department_template_id == template_id,
                    KPI.period_start == period_start,
                    KPI.period_end == period_end,
                )
            ).all()
        )
        updated = 0
        unavailable = 0
        for kpi in records:
            value = self._automatic_value(
                org_id,
                employee_id=kpi.employee_id,
                source_key=config.metric_source_key,
                period_start=period_start,
                period_end=period_end,
            )
            if value is None:
                unavailable += 1
                continue
            self.record_measurement(
                org_id,
                kpi_id=kpi.kpi_id,
                actual_value=value,
                actor_id=actor_id,
                evidence=f"Calculated from {config.metric_source_key}",
                measurement_mode="AUTOMATIC",
                approve=True,
                is_recalculation=kpi.actual_value is not None,
            )
            updated += 1
        return {"updated": updated, "unavailable": unavailable}

    @staticmethod
    def _average(values: list[Decimal]) -> Decimal | None:
        if not values:
            return None
        return (sum(values, Decimal("0")) / Decimal(len(values))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    @staticmethod
    def weighted_average(
        values_and_weights: list[tuple[Decimal, Decimal]],
    ) -> Decimal | None:
        """Normalize configured weights while excluding values with no data."""
        total_weight = sum((weight for _, weight in values_and_weights), Decimal("0"))
        if not values_and_weights or total_weight == 0:
            return None
        return (
            sum(
                (value * weight for value, weight in values_and_weights),
                Decimal("0"),
            )
            / total_weight
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def dashboard(
        self,
        org_id: UUID,
        *,
        department_id: UUID,
        period_start: date,
        period_end: date,
        employee_id: UUID | None = None,
        category: str | None = None,
        status_filter: str | None = None,
    ) -> dict[str, Any]:
        department = self.db.scalar(
            select(Department).where(
                Department.organization_id == org_id,
                Department.department_id == department_id,
            )
        )
        if department is None:
            raise DepartmentalKPIError("Department was not found")
        configs = self.list_configurations(
            org_id, department_id=department_id, include_inactive=True
        )
        configs = [
            config
            for config in configs
            if (not category or config.category == category)
            and (not config.effective_start or config.effective_start <= period_end)
            and (not config.effective_end or config.effective_end >= period_start)
        ]
        config_ids = [config.template_id for config in configs]
        kpis: list[KPI] = []
        if config_ids:
            query = (
                select(KPI)
                .options(joinedload(KPI.employee).joinedload(Employee.person))
                .where(
                    KPI.organization_id == org_id,
                    KPI.department_template_id.in_(config_ids),
                    KPI.period_start <= period_end,
                    KPI.period_end >= period_start,
                    KPI.status.notin_((KPIStatus.CANCELLED, KPIStatus.DEFERRED)),
                )
            )
            if employee_id:
                query = query.where(KPI.employee_id == employee_id)
            kpis = list(self.db.scalars(query).unique().all())
        by_template: dict[UUID, list[KPI]] = defaultdict(list)
        for kpi in kpis:
            if kpi.department_template_id:
                by_template[kpi.department_template_id].append(kpi)
        # Deactivation prevents new periods but must never hide historical
        # records that were valid for the selected period.
        configs = [
            config
            for config in configs
            if config.is_active or by_template.get(config.template_id)
        ]

        previous_by_template: dict[UUID, list[KPI]] = defaultdict(list)
        previous_records: list[KPI] = []
        if config_ids:
            previous_query = select(KPI).where(
                KPI.organization_id == org_id,
                KPI.department_template_id.in_(config_ids),
                KPI.period_end < period_start,
                KPI.status.notin_((KPIStatus.CANCELLED, KPIStatus.DEFERRED)),
            )
            if employee_id:
                previous_query = previous_query.where(KPI.employee_id == employee_id)
            previous_records = list(self.db.scalars(previous_query).all())
            latest_start: dict[UUID, date] = {}
            for record in previous_records:
                if record.department_template_id is None:
                    continue
                current_latest = latest_start.get(record.department_template_id)
                if current_latest is None or record.period_start > current_latest:
                    latest_start[record.department_template_id] = record.period_start
            for record in previous_records:
                template_id = record.department_template_id
                if template_id and record.period_start == latest_start.get(template_id):
                    previous_by_template[template_id].append(record)

        rows: list[dict[str, Any]] = []
        for config in configs:
            records = by_template.get(config.template_id, [])
            scores = [
                score
                for score in (self._score_for_kpi(record, config) for record in records)
                if score is not None
            ]
            actuals = [
                record.actual_value
                for record in records
                if record.actual_value is not None
            ]
            score = self._average(scores)
            previous_scores = [
                previous_score
                for previous_score in (
                    self._score_for_kpi(record, config)
                    for record in previous_by_template.get(config.template_id, [])
                )
                if previous_score is not None
            ]
            previous_score = self._average(previous_scores)
            trend_delta = (
                (score - previous_score).quantize(Decimal("0.01"))
                if score is not None and previous_score is not None
                else None
            )
            status = self.performance_status(
                score,
                green_threshold=(
                    self._average(
                        [
                            self._thresholds_for_kpi(record, config)[0]
                            for record in records
                        ]
                    )
                    or config.green_threshold
                ),
                amber_threshold=(
                    self._average(
                        [
                            self._thresholds_for_kpi(record, config)[1]
                            for record in records
                        ]
                    )
                    or config.amber_threshold
                ),
            )
            if status_filter and status != status_filter:
                continue
            rows.append(
                {
                    "config": config,
                    "target_value": (
                        Decimal(
                            str(
                                (records[0].config_snapshot or {}).get(
                                    "target_value", records[0].target_value
                                )
                            )
                        )
                        if records
                        else config.target_value
                    ),
                    "unit_of_measure": (
                        (records[0].config_snapshot or {}).get("unit_of_measure")
                        or records[0].unit_of_measure
                        if records
                        else config.unit_of_measure
                    ),
                    "weightage": (
                        self._average([record.weightage for record in records])
                        or config.weightage
                    ),
                    "green_threshold": (
                        self._average(
                            [
                                self._thresholds_for_kpi(record, config)[0]
                                for record in records
                            ]
                        )
                        or config.green_threshold
                    ),
                    "amber_threshold": (
                        self._average(
                            [
                                self._thresholds_for_kpi(record, config)[1]
                                for record in records
                            ]
                        )
                        or config.amber_threshold
                    ),
                    "actual": self._average(actuals),
                    "score": score,
                    "status": status,
                    "measurement_count": len(actuals),
                    "employee_count": len(records),
                    "trend_delta": trend_delta,
                    "weighted_score": (
                        score
                        * (
                            self._average([record.weightage for record in records])
                            or config.weightage
                        )
                        / Decimal("100")
                        if score is not None
                        else None
                    ),
                }
            )

        scored_rows = [row for row in rows if row["score"] is not None]
        overall_score = self.weighted_average(
            [(row["score"], row["weightage"]) for row in scored_rows]
        )
        scored_weight = sum((row["weightage"] for row in scored_rows), Decimal("0"))
        overall_green = (
            sum(
                (row["green_threshold"] * row["weightage"] for row in scored_rows),
                Decimal("0"),
            )
            / scored_weight
            if scored_weight
            else Decimal("100")
        )
        overall_amber = (
            sum(
                (row["amber_threshold"] * row["weightage"] for row in scored_rows),
                Decimal("0"),
            )
            / scored_weight
            if scored_weight
            else Decimal("100")
        )

        trend_groups: dict[tuple[date, date, UUID], list[KPI]] = defaultdict(list)
        trend_config_ids = {config.template_id for config in configs}
        for record in [*previous_records, *kpis]:
            if record.department_template_id in trend_config_ids:
                trend_groups[
                    (
                        record.period_start,
                        record.period_end,
                        record.department_template_id,
                    )
                ].append(record)
        trend_periods: dict[tuple[date, date], list[tuple[Decimal, Decimal]]] = (
            defaultdict(list)
        )
        trend_config_by_id = {config.template_id: config for config in configs}
        for (start, end, template_id), period_records in trend_groups.items():
            trend_config = trend_config_by_id.get(template_id)
            if trend_config is None:
                continue
            period_scores = [
                item_score
                for item_score in (
                    self._score_for_kpi(record, trend_config)
                    for record in period_records
                )
                if item_score is not None
            ]
            period_score = self._average(period_scores)
            period_weight = self._average(
                [record.weightage for record in period_records]
            )
            if period_score is not None and period_weight is not None:
                trend_periods[(start, end)].append((period_score, period_weight))
        department_trend = []
        for (start, end), scores_and_weights in sorted(trend_periods.items())[-12:]:
            trend_weight = sum(
                (weight for _, weight in scores_and_weights), Decimal("0")
            )
            if trend_weight == 0:
                continue
            trend_score = (
                sum(
                    (score * weight for score, weight in scores_and_weights),
                    Decimal("0"),
                )
                / trend_weight
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            department_trend.append(
                {
                    "period_start": start,
                    "period_end": end,
                    "label": start.strftime("%b %Y"),
                    "score": trend_score,
                    "score_float": float(trend_score),
                }
            )

        employees = list(
            self.db.scalars(
                select(Employee)
                .options(joinedload(Employee.person))
                .where(
                    Employee.organization_id == org_id,
                    Employee.department_id == department_id,
                    Employee.status == EmployeeStatus.ACTIVE,
                )
                .order_by(Employee.employee_code)
            )
            .unique()
            .all()
        )
        staff_rows: list[dict[str, Any]] = []
        records_by_employee: dict[
            UUID, list[tuple[KPI, DepartmentPerformanceTemplate]]
        ] = defaultdict(list)
        config_by_id = {config.template_id: config for config in configs}
        for kpi in kpis:
            if kpi.department_template_id is None:
                continue
            matched_config = config_by_id.get(kpi.department_template_id)
            if matched_config:
                records_by_employee[kpi.employee_id].append((kpi, matched_config))
        for employee in employees:
            employee_rows = records_by_employee.get(employee.employee_id, [])
            employee_scores = [
                (score, kpi.weightage, kpi, config)
                for kpi, config in employee_rows
                if (score := self._score_for_kpi(kpi, config)) is not None
            ]
            total_weight = sum(
                (weight for _, weight, _, _ in employee_scores), Decimal("0")
            )
            staff_score = (
                None
                if not employee_scores or total_weight == 0
                else (
                    sum(
                        (score * weight for score, weight, _, _ in employee_scores),
                        Decimal("0"),
                    )
                    / total_weight
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            )
            staff_green = (
                sum(
                    (
                        self._thresholds_for_kpi(kpi, config)[0] * kpi.weightage
                        for _, _, kpi, config in employee_scores
                    ),
                    Decimal("0"),
                )
                / total_weight
                if total_weight
                else Decimal("100")
            )
            staff_amber = (
                sum(
                    (
                        self._thresholds_for_kpi(kpi, config)[1] * kpi.weightage
                        for _, _, kpi, config in employee_scores
                    ),
                    Decimal("0"),
                )
                / total_weight
                if total_weight
                else Decimal("100")
            )
            staff_rows.append(
                {
                    "employee": employee,
                    "score": staff_score,
                    "status": self.performance_status(
                        staff_score,
                        green_threshold=staff_green,
                        amber_threshold=staff_amber,
                    ),
                    "measured_kpis": len(employee_scores),
                    "assigned_kpis": len(employee_rows),
                }
            )

        return {
            "department": department,
            "rows": rows,
            "overall_score": overall_score,
            "overall_target": overall_green.quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            ),
            "overall_status": self.performance_status(
                overall_score,
                green_threshold=overall_green,
                amber_threshold=overall_amber,
            ),
            "trend": department_trend,
            "staff_rows": staff_rows,
            "employees": employees,
            "weight_summary": self.weight_summary(org_id, department_id),
            "categories": sorted(
                {config.category for config in configs if config.category}
            ),
        }

    def configuration_detail(
        self,
        org_id: UUID,
        *,
        template_id: UUID,
    ) -> dict[str, Any]:
        config = self.get_configuration(org_id, template_id)
        records = list(
            self.db.scalars(
                select(KPI)
                .options(joinedload(KPI.employee).joinedload(Employee.person))
                .where(
                    KPI.organization_id == org_id,
                    KPI.department_template_id == template_id,
                )
                .order_by(KPI.period_start.desc(), KPI.employee_id)
            )
            .unique()
            .all()
        )
        history = list(
            self.db.scalars(
                select(KPIMeasurementHistory)
                .options(
                    joinedload(KPIMeasurementHistory.employee).joinedload(
                        Employee.person
                    )
                )
                .where(
                    KPIMeasurementHistory.organization_id == org_id,
                    KPIMeasurementHistory.department_template_id == template_id,
                )
                .order_by(KPIMeasurementHistory.recorded_at.desc())
                .limit(100)
            )
            .unique()
            .all()
        )
        configuration_audit = list(
            self.db.scalars(
                select(KPIConfigurationAudit)
                .where(
                    KPIConfigurationAudit.organization_id == org_id,
                    KPIConfigurationAudit.template_id == template_id,
                )
                .order_by(KPIConfigurationAudit.changed_at.desc())
                .limit(100)
            ).all()
        )
        period_scores: dict[tuple[date, date], list[Decimal]] = defaultdict(list)
        for record in records:
            score = self._score_for_kpi(record, config)
            if score is not None:
                period_scores[(record.period_start, record.period_end)].append(score)
        trend = [
            {
                "period_start": start,
                "period_end": end,
                "score": self._average(scores),
                "label": start.strftime("%b %Y"),
                "score_float": float(self._average(scores) or 0),
            }
            for (start, end), scores in sorted(period_scores.items())
        ]
        return {
            "config": config,
            "records": records,
            "history": history,
            "configuration_audit": configuration_audit,
            "trend": trend,
        }

    def measurement_queue(
        self,
        org_id: UUID,
        *,
        period_start: date,
        period_end: date,
        department_ids: set[UUID] | None = None,
        state: str = "all",
        employee_search: str = "",
    ) -> list[dict[str, Any]]:
        """Return the operational measurement work queue.

        This is a read-only projection over employee KPI assignments and their
        latest measurement revision. It deliberately delegates all writes to
        ``record_measurement`` and ``approve_measurement``.
        """
        query = (
            select(KPI)
            .options(
                joinedload(KPI.employee).joinedload(Employee.person),
                joinedload(KPI.department_template).joinedload(
                    DepartmentPerformanceTemplate.department
                ),
            )
            .where(
                KPI.organization_id == org_id,
                KPI.period_start <= period_end,
                KPI.period_end >= period_start,
                KPI.department_template_id.is_not(None),
            )
            .order_by(KPI.period_end, KPI.kpi_name, KPI.employee_id)
        )
        if department_ids:
            query = query.join(
                DepartmentPerformanceTemplate,
                KPI.department_template_id == DepartmentPerformanceTemplate.template_id,
            ).where(DepartmentPerformanceTemplate.department_id.in_(department_ids))
        if employee_search.strip():
            query = query.join(Employee, KPI.employee_id == Employee.employee_id)
            query = query.where(
                func.lower(Employee.employee_code).contains(
                    employee_search.strip().lower()
                )
            )
        records = list(self.db.scalars(query).unique().all())
        if not records:
            return []

        histories = list(
            self.db.scalars(
                select(KPIMeasurementHistory)
                .where(
                    KPIMeasurementHistory.organization_id == org_id,
                    KPIMeasurementHistory.kpi_id.in_(
                        [record.kpi_id for record in records]
                    ),
                )
                .order_by(
                    KPIMeasurementHistory.kpi_id,
                    KPIMeasurementHistory.revision.desc(),
                    KPIMeasurementHistory.recorded_at.desc(),
                )
            ).all()
        )
        latest: dict[UUID, KPIMeasurementHistory] = {}
        for measurement in histories:
            latest.setdefault(measurement.kpi_id, measurement)

        allowed_states = {
            "missing",
            "draft",
            "awaiting_approval",
            "returned",
            "automatic",
            "recorded",
        }
        if state not in {"all", *allowed_states}:
            raise DepartmentalKPIError("Choose a valid measurement queue state")

        rows: list[dict[str, Any]] = []
        for record in records:
            history: KPIMeasurementHistory | None = latest.get(record.kpi_id)
            if history and history.approval_status in {"REJECTED", "RETURNED"}:
                queue_state = "returned"
            elif history and history.approval_status == "SUBMITTED":
                queue_state = "awaiting_approval"
            elif history and history.measurement_mode == "AUTOMATIC":
                queue_state = "automatic"
            elif record.actual_value is None:
                queue_state = "missing"
            elif record.status == KPIStatus.DRAFT:
                queue_state = "draft"
            else:
                queue_state = "recorded"
            if state != "all" and state != queue_state:
                continue
            rows.append(
                {
                    "kpi": record,
                    "config": record.department_template,
                    "employee": record.employee,
                    "history": history,
                    "state": queue_state,
                    "source_key": (
                        history.source_key
                        if history and history.source_key
                        else (
                            record.department_template.metric_source_key
                            if record.department_template
                            else None
                        )
                    ),
                    "last_refreshed_at": (
                        history.recorded_at
                        if history and history.measurement_mode == "AUTOMATIC"
                        else None
                    ),
                    "source_state": (
                        "manual"
                        if not (
                            record.department_template
                            and record.department_template.metric_source_key
                        )
                        else (
                            "available"
                            if history and history.measurement_mode == "AUTOMATIC"
                            else "unavailable"
                        )
                    ),
                }
            )
        return rows

    def employee_dashboard(
        self,
        org_id: UUID,
        *,
        employee_id: UUID,
        period_start: date,
        period_end: date,
    ) -> dict[str, Any]:
        employee = self.db.scalar(
            select(Employee)
            .options(joinedload(Employee.person), joinedload(Employee.department))
            .where(
                Employee.organization_id == org_id,
                Employee.employee_id == employee_id,
            )
        )
        if employee is None:
            raise DepartmentalKPIError("Employee was not found")
        records = list(
            self.db.scalars(
                select(KPI)
                .options(joinedload(KPI.department_template))
                .where(
                    KPI.organization_id == org_id,
                    KPI.employee_id == employee_id,
                    KPI.period_start <= period_end,
                    KPI.period_end >= period_start,
                    KPI.department_template_id.isnot(None),
                )
                .order_by(KPI.kpi_name)
            )
            .unique()
            .all()
        )
        rows: list[dict[str, Any]] = []
        for record in records:
            config = record.department_template
            if config is None:
                continue
            score = self._score_for_kpi(record, config)
            snapshot = record.config_snapshot or self.config_snapshot(config)
            rows.append(
                {
                    "record": record,
                    "config": config,
                    "score": score,
                    "status": self.performance_status(
                        score,
                        green_threshold=Decimal(str(snapshot["green_threshold"])),
                        amber_threshold=Decimal(str(snapshot["amber_threshold"])),
                    ),
                }
            )
        scored_rows = [row for row in rows if row["score"] is not None]
        total_weight = sum(
            (row["record"].weightage for row in scored_rows), Decimal("0")
        )
        overall = (
            None
            if not scored_rows or total_weight == 0
            else (
                sum(
                    (row["score"] * row["record"].weightage for row in scored_rows),
                    Decimal("0"),
                )
                / total_weight
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        )
        overall_green = (
            sum(
                (
                    self._thresholds_for_kpi(row["record"], row["config"])[0]
                    * row["record"].weightage
                    for row in scored_rows
                ),
                Decimal("0"),
            )
            / total_weight
            if total_weight
            else Decimal("100")
        )
        overall_amber = (
            sum(
                (
                    self._thresholds_for_kpi(row["record"], row["config"])[1]
                    * row["record"].weightage
                    for row in scored_rows
                ),
                Decimal("0"),
            )
            / total_weight
            if total_weight
            else Decimal("100")
        )
        return {
            "employee": employee,
            "rows": rows,
            "overall_score": overall,
            "overall_status": self.performance_status(
                overall,
                green_threshold=overall_green,
                amber_threshold=overall_amber,
            ),
        }
