"""
ReportDefinitionService - Report template management.

Manages report definitions/templates for financial and operational reporting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.finance.rpt.report_definition import ReportDefinition, ReportType
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin


@dataclass
class ReportDefinitionInput:
    """Input for creating a report definition."""

    report_code: str
    report_name: str
    report_type: ReportType
    data_source_type: str
    description: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    default_format: str = "PDF"
    supported_formats: Optional[list] = None
    report_structure: Optional[dict] = None
    column_definitions: Optional[dict] = None
    row_definitions: Optional[dict] = None
    filter_definitions: Optional[dict] = None
    data_source_config: Optional[dict] = None
    template_file_path: Optional[str] = None
    required_permissions: Optional[list] = None
    is_system_report: bool = False


@dataclass
class ReportColumn:
    """Column definition for reports."""

    column_code: str
    column_name: str
    data_type: str
    width: Optional[int] = None
    alignment: str = "LEFT"
    format_string: Optional[str] = None


@dataclass
class ReportFilter:
    """Filter definition for reports."""

    filter_code: str
    filter_name: str
    data_type: str
    is_required: bool = False
    default_value: Optional[str] = None
    options: Optional[list] = None


class ReportDefinitionService(ListResponseMixin):
    """
    Service for report definition management.

    Handles:
    - Report template CRUD
    - Column and row definitions
    - Filter configurations
    - Report categorization
    """

    @staticmethod
    def create_definition(
        db: Session,
        organization_id: UUID,
        input: ReportDefinitionInput,
        created_by_user_id: UUID,
    ) -> ReportDefinition:
        """
        Create a new report definition.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Definition input data
            created_by_user_id: User creating the definition

        Returns:
            Created ReportDefinition
        """
        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(created_by_user_id)

        # Check for duplicate report code
        existing = (
            db.query(ReportDefinition)
            .filter(
                ReportDefinition.organization_id == org_id,
                ReportDefinition.report_code == input.report_code,
            )
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Report code {input.report_code} already exists",
            )

        definition = ReportDefinition(
            organization_id=org_id,
            report_code=input.report_code,
            report_name=input.report_name,
            description=input.description,
            report_type=input.report_type,
            category=input.category,
            subcategory=input.subcategory,
            default_format=input.default_format,
            supported_formats=input.supported_formats or ["PDF", "XLSX", "CSV"],
            report_structure=input.report_structure,
            column_definitions=input.column_definitions,
            row_definitions=input.row_definitions,
            filter_definitions=input.filter_definitions,
            data_source_type=input.data_source_type,
            data_source_config=input.data_source_config,
            template_file_path=input.template_file_path,
            required_permissions=input.required_permissions,
            is_system_report=input.is_system_report,
            created_by_user_id=user_id,
        )

        db.add(definition)
        db.commit()
        db.refresh(definition)

        return definition

    @staticmethod
    def update_definition(
        db: Session,
        organization_id: UUID,
        report_def_id: UUID,
        report_name: Optional[str] = None,
        description: Optional[str] = None,
        category: Optional[str] = None,
        subcategory: Optional[str] = None,
        default_format: Optional[str] = None,
        supported_formats: Optional[list] = None,
    ) -> ReportDefinition:
        """
        Update a report definition.

        Args:
            db: Database session
            organization_id: Organization scope
            report_def_id: Definition to update
            report_name: New name
            description: New description
            category: New category
            subcategory: New subcategory
            default_format: New default format
            supported_formats: New supported formats

        Returns:
            Updated ReportDefinition
        """
        org_id = coerce_uuid(organization_id)
        def_id = coerce_uuid(report_def_id)

        definition = db.get(ReportDefinition, def_id)
        if not definition or definition.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Report definition not found")

        if definition.is_system_report:
            raise HTTPException(
                status_code=400,
                detail="Cannot modify system report definitions",
            )

        if report_name is not None:
            definition.report_name = report_name
        if description is not None:
            definition.description = description
        if category is not None:
            definition.category = category
        if subcategory is not None:
            definition.subcategory = subcategory
        if default_format is not None:
            definition.default_format = default_format
        if supported_formats is not None:
            definition.supported_formats = supported_formats

        db.commit()
        db.refresh(definition)

        return definition

    @staticmethod
    def update_structure(
        db: Session,
        organization_id: UUID,
        report_def_id: UUID,
        report_structure: Optional[dict] = None,
        column_definitions: Optional[dict] = None,
        row_definitions: Optional[dict] = None,
        filter_definitions: Optional[dict] = None,
    ) -> ReportDefinition:
        """
        Update report structure definitions.

        Args:
            db: Database session
            organization_id: Organization scope
            report_def_id: Definition to update
            report_structure: Overall structure
            column_definitions: Column configs
            row_definitions: Row configs
            filter_definitions: Filter configs

        Returns:
            Updated ReportDefinition
        """
        org_id = coerce_uuid(organization_id)
        def_id = coerce_uuid(report_def_id)

        definition = db.get(ReportDefinition, def_id)
        if not definition or definition.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Report definition not found")

        if report_structure is not None:
            definition.report_structure = report_structure
        if column_definitions is not None:
            definition.column_definitions = column_definitions
        if row_definitions is not None:
            definition.row_definitions = row_definitions
        if filter_definitions is not None:
            definition.filter_definitions = filter_definitions

        # Increment template version
        definition.template_version += 1

        db.commit()
        db.refresh(definition)

        return definition

    @staticmethod
    def update_data_source(
        db: Session,
        organization_id: UUID,
        report_def_id: UUID,
        data_source_type: str,
        data_source_config: Optional[dict] = None,
    ) -> ReportDefinition:
        """
        Update report data source configuration.

        Args:
            db: Database session
            organization_id: Organization scope
            report_def_id: Definition to update
            data_source_type: Type of data source
            data_source_config: Data source configuration

        Returns:
            Updated ReportDefinition
        """
        org_id = coerce_uuid(organization_id)
        def_id = coerce_uuid(report_def_id)

        definition = db.get(ReportDefinition, def_id)
        if not definition or definition.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Report definition not found")

        definition.data_source_type = data_source_type
        definition.data_source_config = data_source_config

        db.commit()
        db.refresh(definition)

        return definition

    @staticmethod
    def deactivate(
        db: Session,
        organization_id: UUID,
        report_def_id: UUID,
    ) -> ReportDefinition:
        """
        Deactivate a report definition.

        Args:
            db: Database session
            organization_id: Organization scope
            report_def_id: Definition to deactivate

        Returns:
            Updated ReportDefinition
        """
        org_id = coerce_uuid(organization_id)
        def_id = coerce_uuid(report_def_id)

        definition = db.get(ReportDefinition, def_id)
        if not definition or definition.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Report definition not found")

        if definition.is_system_report:
            raise HTTPException(
                status_code=400,
                detail="Cannot deactivate system report definitions",
            )

        definition.is_active = False
        db.commit()
        db.refresh(definition)

        return definition

    @staticmethod
    def clone_definition(
        db: Session,
        organization_id: UUID,
        source_def_id: UUID,
        new_report_code: str,
        new_report_name: str,
        created_by_user_id: UUID,
    ) -> ReportDefinition:
        """
        Clone a report definition.

        Args:
            db: Database session
            organization_id: Organization scope
            source_def_id: Source definition to clone
            new_report_code: New report code
            new_report_name: New report name
            created_by_user_id: User creating the clone

        Returns:
            Cloned ReportDefinition
        """
        org_id = coerce_uuid(organization_id)
        src_id = coerce_uuid(source_def_id)
        user_id = coerce_uuid(created_by_user_id)

        source = db.get(ReportDefinition, src_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source definition not found")

        # Check for duplicate
        existing = (
            db.query(ReportDefinition)
            .filter(
                ReportDefinition.organization_id == org_id,
                ReportDefinition.report_code == new_report_code,
            )
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Report code {new_report_code} already exists",
            )

        clone = ReportDefinition(
            organization_id=org_id,
            report_code=new_report_code,
            report_name=new_report_name,
            description=source.description,
            report_type=source.report_type,
            category=source.category,
            subcategory=source.subcategory,
            default_format=source.default_format,
            supported_formats=source.supported_formats,
            report_structure=source.report_structure,
            column_definitions=source.column_definitions,
            row_definitions=source.row_definitions,
            filter_definitions=source.filter_definitions,
            data_source_type=source.data_source_type,
            data_source_config=source.data_source_config,
            template_file_path=source.template_file_path,
            required_permissions=source.required_permissions,
            is_system_report=False,
            created_by_user_id=user_id,
        )

        db.add(clone)
        db.commit()
        db.refresh(clone)

        return clone

    @staticmethod
    def get_by_code(
        db: Session,
        organization_id: str,
        report_code: str,
    ) -> Optional[ReportDefinition]:
        """Get report definition by code."""
        return (
            db.query(ReportDefinition)
            .filter(
                ReportDefinition.organization_id == coerce_uuid(organization_id),
                ReportDefinition.report_code == report_code,
            )
            .first()
        )

    @staticmethod
    def get_by_type(
        db: Session,
        organization_id: str,
        report_type: ReportType,
    ) -> List[ReportDefinition]:
        """Get report definitions by type."""
        return (
            db.query(ReportDefinition)
            .filter(
                ReportDefinition.organization_id == coerce_uuid(organization_id),
                ReportDefinition.report_type == report_type,
                ReportDefinition.is_active == True,
            )
            .order_by(ReportDefinition.report_name)
            .all()
        )

    @staticmethod
    def get(
        db: Session,
        report_def_id: str,
    ) -> ReportDefinition:
        """Get a report definition by ID."""
        definition = db.get(ReportDefinition, coerce_uuid(report_def_id))
        if not definition:
            raise HTTPException(status_code=404, detail="Report definition not found")
        return definition

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        report_type: Optional[ReportType] = None,
        category: Optional[str] = None,
        is_active: Optional[bool] = None,
        is_system_report: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ReportDefinition]:
        """List report definitions with optional filters."""
        query = db.query(ReportDefinition)

        if organization_id:
            query = query.filter(
                ReportDefinition.organization_id == coerce_uuid(organization_id)
            )

        if report_type:
            query = query.filter(ReportDefinition.report_type == report_type)

        if category:
            query = query.filter(ReportDefinition.category == category)

        if is_active is not None:
            query = query.filter(ReportDefinition.is_active == is_active)

        if is_system_report is not None:
            query = query.filter(ReportDefinition.is_system_report == is_system_report)

        query = query.order_by(ReportDefinition.category, ReportDefinition.report_name)
        return query.limit(limit).offset(offset).all()


# Module-level singleton instance
report_definition_service = ReportDefinitionService()
