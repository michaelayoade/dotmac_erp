"""
Project Importers.

CSV importers for core project data.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.ar.customer import Customer
from app.models.finance.core_org.business_unit import BusinessUnit
from app.models.finance.core_org.cost_center import CostCenter
from app.models.finance.core_org.project import (
    Project,
    ProjectPriority,
    ProjectStatus,
    ProjectType,
)
from app.models.finance.core_org.reporting_segment import ReportingSegment
from app.services.finance.import_export.base import (
    BaseImporter,
    FieldMapping,
    ImportConfig,
)

logger = logging.getLogger(__name__)


def _first_value(row: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _parse_uuid(value: Any) -> UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value).strip())
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid UUID: {value}") from exc


class ProjectImporter(BaseImporter[Project]):
    """Importer for projects."""

    entity_name = "Project"
    model_class = Project

    def __init__(self, db: Session, config: ImportConfig):
        super().__init__(db, config)

    def get_field_mappings(self) -> list[FieldMapping]:
        return [
            FieldMapping("Project Code", "project_code", required=False),
            FieldMapping("Code", "project_code_alt", required=False),
            FieldMapping("Project ID", "project_code_alt2", required=False),
            FieldMapping("Project Name", "project_name", required=False),
            FieldMapping("Name", "project_name_alt", required=False),
            FieldMapping("Description", "description", required=False),
            FieldMapping(
                "Project Status",
                "status",
                required=False,
                transformer=lambda v: self.parse_enum(
                    v, ProjectStatus, ProjectStatus.ACTIVE
                ),
            ),
            FieldMapping(
                "Project Priority",
                "project_priority",
                required=False,
                transformer=lambda v: self.parse_enum(
                    v, ProjectPriority, ProjectPriority.MEDIUM
                ),
            ),
            FieldMapping(
                "Project Type",
                "project_type",
                required=False,
                transformer=lambda v: self.parse_enum(
                    v, ProjectType, ProjectType.INTERNAL
                ),
            ),
            FieldMapping(
                "Start Date", "start_date", required=False, transformer=self.parse_date
            ),
            FieldMapping(
                "End Date", "end_date", required=False, transformer=self.parse_date
            ),
            FieldMapping(
                "Budget Amount",
                "budget_amount",
                required=False,
                transformer=self.parse_decimal,
            ),
            FieldMapping("Budget Currency", "budget_currency_code", required=False),
            FieldMapping(
                "Is Capitalizable",
                "is_capitalizable",
                required=False,
                transformer=self.parse_boolean,
            ),
            FieldMapping("Cost Center Code", "cost_center_code", required=False),
            FieldMapping("Business Unit Code", "business_unit_code", required=False),
            FieldMapping("Segment Code", "segment_code", required=False),
            FieldMapping("Customer Code", "customer_code", required=False),
            FieldMapping("Customer Name", "customer_name", required=False),
            FieldMapping(
                "Project Manager User ID",
                "project_manager_user_id",
                required=False,
                transformer=_parse_uuid,
            ),
        ]

    def get_unique_key(self, row: dict[str, Any]) -> str:
        return _first_value(row, "Project Code", "Code", "Project ID") or "unknown"

    def check_duplicate(self, row: dict[str, Any]) -> Project | None:
        code = _first_value(row, "Project Code", "Code", "Project ID")
        if not code:
            return None
        return self.db.scalar(
            select(Project).where(
                Project.organization_id == self.config.organization_id,
                Project.project_code == code,
            )
        )

    def _resolve_cost_center_id(self, code: str | None) -> UUID | None:
        if not code:
            return None
        cache_key = f"cost_center:{code}"
        if cache_key in self._id_cache:
            return self._id_cache[cache_key]
        cc = self.db.scalar(
            select(CostCenter).where(
                CostCenter.organization_id == self.config.organization_id,
                CostCenter.cost_center_code == code,
            )
        )
        if cc:
            self._id_cache[cache_key] = cc.cost_center_id
            return cc.cost_center_id
        return None

    def _resolve_business_unit_id(self, code: str | None) -> UUID | None:
        if not code:
            return None
        cache_key = f"business_unit:{code}"
        if cache_key in self._id_cache:
            return self._id_cache[cache_key]
        bu = self.db.scalar(
            select(BusinessUnit).where(
                BusinessUnit.organization_id == self.config.organization_id,
                BusinessUnit.unit_code == code,
            )
        )
        if bu:
            self._id_cache[cache_key] = bu.business_unit_id
            return bu.business_unit_id
        return None

    def _resolve_segment_id(self, code: str | None) -> UUID | None:
        if not code:
            return None
        cache_key = f"segment:{code}"
        if cache_key in self._id_cache:
            return self._id_cache[cache_key]
        segment = self.db.scalar(
            select(ReportingSegment).where(
                ReportingSegment.organization_id == self.config.organization_id,
                ReportingSegment.segment_code == code,
            )
        )
        if segment:
            self._id_cache[cache_key] = segment.segment_id
            return segment.segment_id
        return None

    def _resolve_customer_id(self, code: str | None, name: str | None) -> UUID | None:
        if not code and not name:
            return None
        cache_key = f"customer:{code or name}"
        if cache_key in self._id_cache:
            return self._id_cache[cache_key]

        stmt = select(Customer).where(
            Customer.organization_id == self.config.organization_id
        )
        if code:
            stmt = stmt.where(Customer.customer_code == code)
        elif name:
            stmt = stmt.where(
                (Customer.legal_name.ilike(f"%{name}%"))
                | (Customer.trading_name.ilike(f"%{name}%"))
            )
        customer = self.db.scalar(stmt)
        if customer:
            self._id_cache[cache_key] = customer.customer_id
            return customer.customer_id
        return None

    def create_entity(self, row: dict[str, Any]) -> Project:
        project_code = _first_value(
            row, "project_code", "project_code_alt", "project_code_alt2"
        )
        project_name = _first_value(row, "project_name", "project_name_alt")

        if not project_code:
            raise ValueError("Project Code is required")
        if not project_name:
            raise ValueError("Project Name is required")

        cost_center_id = self._resolve_cost_center_id(row.get("cost_center_code"))
        if row.get("cost_center_code") and not cost_center_id:
            raise ValueError(f"Cost Center not found: {row.get('cost_center_code')}")

        business_unit_id = self._resolve_business_unit_id(row.get("business_unit_code"))
        if row.get("business_unit_code") and not business_unit_id:
            raise ValueError(
                f"Business Unit not found: {row.get('business_unit_code')}"
            )

        segment_id = self._resolve_segment_id(row.get("segment_code"))
        if row.get("segment_code") and not segment_id:
            raise ValueError(f"Segment not found: {row.get('segment_code')}")

        customer_id = self._resolve_customer_id(
            row.get("customer_code"), row.get("customer_name")
        )
        if (row.get("customer_code") or row.get("customer_name")) and not customer_id:
            raise ValueError("Customer not found for project")

        return Project(
            project_id=uuid4(),
            organization_id=self.config.organization_id,
            project_code=project_code[:20],
            project_name=project_name[:200],
            description=row.get("description"),
            business_unit_id=business_unit_id,
            segment_id=segment_id,
            project_manager_user_id=row.get("project_manager_user_id"),
            start_date=row.get("start_date"),
            end_date=row.get("end_date"),
            budget_amount=row.get("budget_amount"),
            budget_currency_code=row.get("budget_currency_code"),
            status=row.get("status") or ProjectStatus.ACTIVE,
            is_capitalizable=row.get("is_capitalizable") or False,
            cost_center_id=cost_center_id,
            customer_id=customer_id,
            project_priority=row.get("project_priority") or ProjectPriority.MEDIUM,
            project_type=row.get("project_type") or ProjectType.INTERNAL,
        )
