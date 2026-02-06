"""
RPT API Router.

Financial Reporting API endpoints per IAS 1.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id, require_tenant_auth
from app.services.auth_dependencies import require_tenant_permission
from app.db import SessionLocal
from app.models.finance.rpt.disclosure_checklist import DisclosureStatus
from app.models.finance.rpt.report_definition import ReportType, ReportDefinition
from app.models.finance.rpt.financial_statement_line import StatementType
from app.models.finance.rpt.report_instance import ReportStatus
from app.models.finance.rpt.report_schedule import ScheduleFrequency
from app.schemas.finance.common import ListResponse
from app.services.finance.rpt import (
    report_definition_service,
    financial_statement_service,
    report_instance_service,
    disclosure_checklist_service,
    report_scheduler_service,
    ReportDefinitionInput,
    StatementLineInput,
    ReportGenerationRequest,
    DisclosureItemInput,
    DisclosureCompletionInput,
    ScheduleInput,
)


router = APIRouter(
    prefix="/rpt",
    tags=["reporting"],
    dependencies=[Depends(require_tenant_auth)],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _resolve_statement_type(
    db: Session, organization_id: UUID, report_id: UUID
) -> StatementType:
    definition = db.get(ReportDefinition, report_id)
    if not definition or definition.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Report definition not found")
    try:
        return StatementType(definition.report_type.value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Report type {definition.report_type} does not support statement lines",
        ) from exc


# =============================================================================
# Schemas
# =============================================================================


class ReportDefinitionCreate(BaseModel):
    """Create report definition request."""

    report_code: str = Field(max_length=30)
    report_name: str = Field(max_length=200)
    report_type: str = Field(max_length=30)  # BALANCE_SHEET, INCOME_STATEMENT, etc
    data_source_type: str = Field(max_length=50)
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


class ReportColumnCreate(BaseModel):
    """Report column definition."""

    column_code: str = Field(max_length=30)
    column_name: str = Field(max_length=100)
    column_type: str = Field(max_length=20)  # DATA, CALCULATED, PERIOD
    sequence: int
    formula: Optional[str] = None
    data_source: Optional[str] = None


class ReportFilterCreate(BaseModel):
    """Report filter definition."""

    filter_code: str = Field(max_length=30)
    filter_name: str = Field(max_length=100)
    filter_type: str = Field(max_length=20)
    default_value: Optional[str] = None
    is_required: bool = False


class ReportDefinitionRead(BaseModel):
    """Report definition response."""

    model_config = ConfigDict(from_attributes=True)

    report_def_id: UUID
    organization_id: UUID
    report_code: str
    report_name: str
    report_type: str
    description: Optional[str]
    category: Optional[str]
    subcategory: Optional[str]
    default_format: str
    supported_formats: Optional[list]
    data_source_type: str
    is_system_report: bool
    is_active: bool
    created_by_user_id: UUID
    created_at: datetime


class StatementLineCreate(BaseModel):
    """Create financial statement line request."""

    report_id: UUID
    line_code: str = Field(max_length=30)
    line_name: str = Field(max_length=200)
    line_type: str = Field(max_length=20)  # HEADER, DETAIL, TOTAL, SUBTOTAL
    sequence: int
    parent_line_id: Optional[UUID] = None
    account_id: Optional[UUID] = None
    formula: Optional[str] = None
    is_bold: bool = False
    indent_level: int = 0


class StatementLineRead(BaseModel):
    """Financial statement line response."""

    model_config = ConfigDict(from_attributes=True)

    line_id: UUID
    report_id: UUID
    line_code: str
    line_name: str
    line_type: str
    sequence: int
    parent_line_id: Optional[UUID]
    account_id: Optional[UUID]
    formula: Optional[str]
    is_bold: bool
    indent_level: int


class ReportInstanceCreate(BaseModel):
    """Create report instance request."""

    report_def_id: Optional[UUID] = None
    report_code: Optional[str] = None
    output_format: str = "PDF"
    fiscal_period_id: Optional[UUID] = None
    parameters: Optional[dict] = None


class ReportInstanceRead(BaseModel):
    """Report instance response."""

    model_config = ConfigDict(from_attributes=True)

    instance_id: UUID
    organization_id: UUID
    report_def_id: UUID
    fiscal_period_id: Optional[UUID]
    parameters_used: Optional[dict]
    output_format: str
    output_file_path: Optional[str]
    output_size_bytes: Optional[int]
    status: str
    queued_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    generated_at: Optional[datetime]
    generated_by_user_id: UUID


class ReportDataRead(BaseModel):
    """Report data response."""

    instance_id: UUID
    report_def_id: UUID
    report_name: str
    payload: dict


class DisclosureItemCreate(BaseModel):
    """Create disclosure checklist item request."""

    fiscal_period_id: UUID
    disclosure_code: str = Field(max_length=50)
    disclosure_name: str = Field(max_length=300)
    ifrs_standard: str = Field(max_length=50)
    sequence_number: int
    paragraph_reference: Optional[str] = Field(default=None, max_length=50)
    description: Optional[str] = None
    parent_checklist_id: Optional[UUID] = None
    indent_level: int = 0
    is_mandatory: bool = True
    applicability_criteria: Optional[str] = None


class DisclosureItemRead(BaseModel):
    """Disclosure checklist item response."""

    model_config = ConfigDict(from_attributes=True)

    checklist_id: UUID
    organization_id: UUID
    fiscal_period_id: UUID
    disclosure_code: str
    disclosure_name: str
    description: Optional[str] = None
    ifrs_standard: str
    paragraph_reference: Optional[str] = None
    parent_checklist_id: Optional[UUID] = None
    sequence_number: int
    indent_level: int
    is_mandatory: bool
    applicability_criteria: Optional[str] = None
    status: DisclosureStatus
    disclosure_location: Optional[str] = None
    notes: Optional[str] = None
    completed_by_user_id: Optional[UUID] = None
    completed_at: Optional[datetime] = None
    reviewed_by_user_id: Optional[UUID] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class DisclosureCompletionCreate(BaseModel):
    """Record disclosure completion request."""

    disclosure_item_id: UUID
    report_instance_id: UUID
    is_complete: bool = False
    notes: Optional[str] = None
    evidence_reference: Optional[str] = None


class DisclosureCompletionRead(BaseModel):
    """Disclosure completion response."""

    model_config = ConfigDict(from_attributes=True)

    completion_id: UUID
    disclosure_item_id: UUID
    report_instance_id: UUID
    is_complete: bool
    completed_by_user_id: Optional[UUID]
    completed_at: Optional[datetime]
    reviewed_by_user_id: Optional[UUID]
    reviewed_at: Optional[datetime]
    notes: Optional[str]


class DisclosureSummaryRead(BaseModel):
    """Disclosure checklist summary."""

    report_instance_id: UUID
    total_items: int
    completed_items: int
    pending_items: int
    completion_percentage: Decimal
    mandatory_incomplete: int


class ReportScheduleCreate(BaseModel):
    """Create report schedule request."""

    schedule_name: str = Field(max_length=200)
    report_def_id: UUID
    frequency: ScheduleFrequency
    output_format: str = Field(default="PDF", max_length=20)
    description: Optional[str] = None
    cron_expression: Optional[str] = None
    day_of_month: Optional[int] = None
    day_of_week: Optional[int] = None
    time_of_day: Optional[str] = Field(default=None, max_length=10)
    timezone: str = Field(default="UTC", max_length=50)
    report_parameters: Optional[dict] = None
    email_recipients: Optional[list] = None
    storage_path: Optional[str] = None
    retention_days: Optional[int] = None


class ReportScheduleRead(BaseModel):
    """Report schedule response."""

    model_config = ConfigDict(from_attributes=True)

    schedule_id: UUID
    organization_id: UUID
    schedule_name: str
    report_def_id: UUID
    description: Optional[str] = None
    frequency: ScheduleFrequency
    cron_expression: Optional[str] = None
    day_of_week: Optional[int] = None
    day_of_month: Optional[int] = None
    time_of_day: Optional[str] = None
    timezone: str
    report_parameters: Optional[dict] = None
    output_format: str
    email_recipients: Optional[list] = None
    storage_path: Optional[str] = None
    retention_days: Optional[int] = None
    is_active: bool
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    created_by_user_id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None


class ScheduleExecutionRead(BaseModel):
    """Schedule execution record."""

    model_config = ConfigDict(from_attributes=True)

    execution_id: UUID
    schedule_id: UUID
    execution_date: datetime
    status: str
    report_instance_id: Optional[UUID]
    error_message: Optional[str]


# =============================================================================
# Report Definitions
# =============================================================================


@router.post(
    "/definitions",
    response_model=ReportDefinitionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_report_definition(
    payload: ReportDefinitionCreate,
    organization_id: UUID = Depends(require_organization_id),
    created_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("rpt:definitions:create")),
    db: Session = Depends(get_db),
):
    """Create a new report definition."""
    input_data = ReportDefinitionInput(
        report_code=payload.report_code,
        report_name=payload.report_name,
        report_type=ReportType(payload.report_type),
        data_source_type=payload.data_source_type,
        description=payload.description,
        category=payload.category,
        subcategory=payload.subcategory,
        default_format=payload.default_format,
        supported_formats=payload.supported_formats,
        report_structure=payload.report_structure,
        column_definitions=payload.column_definitions,
        row_definitions=payload.row_definitions,
        filter_definitions=payload.filter_definitions,
        data_source_config=payload.data_source_config,
        template_file_path=payload.template_file_path,
        required_permissions=payload.required_permissions,
        is_system_report=payload.is_system_report,
    )
    return report_definition_service.create_definition(
        db=db,
        organization_id=organization_id,
        input=input_data,
        created_by_user_id=created_by_user_id,
    )


@router.get("/definitions/{report_id}", response_model=ReportDefinitionRead)
def get_report_definition(
    report_id: UUID,
    auth: dict = Depends(require_tenant_permission("rpt:definitions:read")),
    db: Session = Depends(get_db),
):
    """Get a report definition by ID."""
    return report_definition_service.get(db, str(report_id))


@router.get("/definitions", response_model=ListResponse[ReportDefinitionRead])
def list_report_definitions(
    organization_id: UUID = Depends(require_organization_id),
    report_type: Optional[str] = None,
    is_active: Optional[bool] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("rpt:definitions:read")),
    db: Session = Depends(get_db),
):
    """List report definitions with filters."""
    definitions = report_definition_service.list(
        db=db,
        organization_id=str(organization_id),
        report_type=ReportType(report_type) if report_type else None,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=definitions,
        count=len(definitions),
        limit=limit,
        offset=offset,
    )


@router.post("/definitions/{report_id}/clone", response_model=ReportDefinitionRead)
def clone_report_definition(
    report_id: UUID,
    new_report_code: str = Query(...),
    new_report_name: str = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    cloned_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("rpt:definitions:clone")),
    db: Session = Depends(get_db),
):
    """Clone an existing report definition."""
    return report_definition_service.clone_definition(
        db=db,
        organization_id=organization_id,
        source_def_id=report_id,
        new_report_code=new_report_code,
        new_report_name=new_report_name,
        created_by_user_id=cloned_by_user_id,
    )


# =============================================================================
# Statement Lines
# =============================================================================


@router.post(
    "/lines", response_model=StatementLineRead, status_code=status.HTTP_201_CREATED
)
def create_statement_line(
    payload: StatementLineCreate,
    organization_id: UUID = Depends(require_organization_id),
    created_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("rpt:lines:create")),
    db: Session = Depends(get_db),
):
    """Create a financial statement line."""
    statement_type = _resolve_statement_type(db, organization_id, payload.report_id)
    line_type = payload.line_type.upper()
    input_data = StatementLineInput(
        statement_type=statement_type,
        line_code=payload.line_code,
        line_name=payload.line_name,
        sequence_number=payload.sequence,
        parent_line_id=payload.parent_line_id,
        calculation_formula=payload.formula,
        indent_level=payload.indent_level,
        is_header=line_type == "HEADER",
        is_total=line_type == "TOTAL",
        is_subtotal=line_type == "SUBTOTAL",
    )
    return financial_statement_service.create_line(
        db=db,
        organization_id=organization_id,
        input=input_data,
    )


@router.get(
    "/definitions/{report_id}/lines", response_model=ListResponse[StatementLineRead]
)
def list_statement_lines(
    report_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("rpt:lines:read")),
    db: Session = Depends(get_db),
):
    """List statement lines for a report."""
    statement_type = _resolve_statement_type(db, organization_id, report_id)
    lines = financial_statement_service.get_statement_structure(
        db=db,
        organization_id=str(organization_id),
        statement_type=statement_type,
    )
    lines = lines[offset : offset + limit]
    return ListResponse(
        items=lines,
        count=len(lines),
        limit=limit,
        offset=offset,
    )


@router.post("/lines/reorder", response_model=dict)
def reorder_statement_lines(
    report_id: UUID = Query(...),
    line_order: list[UUID] = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    updated_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("rpt:lines:reorder")),
    db: Session = Depends(get_db),
):
    """Reorder statement lines."""
    statement_type = _resolve_statement_type(db, organization_id, report_id)
    line_sequences = [(line_id, index + 1) for index, line_id in enumerate(line_order)]
    result = financial_statement_service.reorder_lines(
        db=db,
        organization_id=organization_id,
        statement_type=statement_type,
        line_sequences=line_sequences,
    )
    return {"lines_updated": len(result)}


# =============================================================================
# Report Instances
# =============================================================================


@router.post(
    "/instances", response_model=ReportInstanceRead, status_code=status.HTTP_201_CREATED
)
def create_report_instance(
    payload: ReportInstanceCreate,
    organization_id: UUID = Depends(require_organization_id),
    created_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("rpt:instances:create")),
    db: Session = Depends(get_db),
):
    """Create a report instance (schedule for generation)."""
    request = ReportGenerationRequest(
        report_def_id=payload.report_def_id,
        report_code=payload.report_code,
        output_format=payload.output_format,
        fiscal_period_id=payload.fiscal_period_id,
        parameters=payload.parameters,
    )
    return report_instance_service.create_instance(
        db=db,
        organization_id=organization_id,
        request=request,
        created_by_user_id=created_by_user_id,
    )


@router.get("/instances/{instance_id}", response_model=ReportInstanceRead)
def get_report_instance(
    instance_id: UUID,
    auth: dict = Depends(require_tenant_permission("rpt:instances:read")),
    db: Session = Depends(get_db),
):
    """Get a report instance by ID."""
    return report_instance_service.get(db, str(instance_id))


@router.get("/instances", response_model=ListResponse[ReportInstanceRead])
def list_report_instances(
    organization_id: UUID = Depends(require_organization_id),
    report_def_id: Optional[UUID] = None,
    fiscal_period_id: Optional[UUID] = None,
    status: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("rpt:instances:read")),
    db: Session = Depends(get_db),
):
    """List report instances with filters."""
    status_value = None
    if status:
        try:
            status_value = ReportStatus(status)
        except ValueError:
            status_value = None
    instances = report_instance_service.list(
        db=db,
        organization_id=str(organization_id),
        report_def_id=str(report_def_id) if report_def_id else None,
        fiscal_period_id=str(fiscal_period_id) if fiscal_period_id else None,
        status=status_value,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=instances,
        count=len(instances),
        limit=limit,
        offset=offset,
    )


@router.post("/instances/{instance_id}/generate", response_model=ReportInstanceRead)
def generate_report(
    instance_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    generated_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("rpt:instances:generate")),
    db: Session = Depends(get_db),
):
    """Generate a report instance."""
    return report_instance_service.generate_report(
        db=db,
        organization_id=organization_id,
        instance_id=instance_id,
        generated_by_user_id=generated_by_user_id,
    )


@router.get("/instances/{instance_id}/data", response_model=ReportDataRead)
def get_report_data(
    instance_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("rpt:instances:data")),
    db: Session = Depends(get_db),
):
    """Get generated report data."""
    payload = report_instance_service.get_report_data(
        db=db,
        organization_id=str(organization_id),
        instance_id=str(instance_id),
    )
    instance = report_instance_service.get(db, str(instance_id))
    definition = report_definition_service.get(db, str(instance.report_def_id))
    return ReportDataRead(
        instance_id=instance.instance_id,
        report_def_id=definition.report_def_id,
        report_name=definition.report_name,
        payload=payload,
    )


# =============================================================================
# Disclosure Checklist
# =============================================================================


@router.post(
    "/disclosures/items",
    response_model=DisclosureItemRead,
    status_code=status.HTTP_201_CREATED,
)
def create_disclosure_item(
    payload: DisclosureItemCreate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("rpt:disclosures:create")),
    db: Session = Depends(get_db),
):
    """Create a disclosure checklist item."""
    input_data = DisclosureItemInput(
        fiscal_period_id=payload.fiscal_period_id,
        disclosure_code=payload.disclosure_code,
        disclosure_name=payload.disclosure_name,
        ifrs_standard=payload.ifrs_standard,
        sequence_number=payload.sequence_number,
        paragraph_reference=payload.paragraph_reference,
        description=payload.description,
        parent_checklist_id=payload.parent_checklist_id,
        indent_level=payload.indent_level,
        is_mandatory=payload.is_mandatory,
        applicability_criteria=payload.applicability_criteria,
    )
    return disclosure_checklist_service.create_item(
        db=db,
        organization_id=organization_id,
        input=input_data,
    )


@router.get("/disclosures/items", response_model=ListResponse[DisclosureItemRead])
def list_disclosure_items(
    organization_id: UUID = Depends(require_organization_id),
    fiscal_period_id: Optional[UUID] = None,
    ifrs_standard: Optional[str] = None,
    status: Optional[DisclosureStatus] = None,
    is_mandatory: Optional[bool] = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("rpt:disclosures:read")),
    db: Session = Depends(get_db),
):
    """List disclosure checklist items."""
    items = disclosure_checklist_service.list(
        db=db,
        organization_id=str(organization_id),
        fiscal_period_id=str(fiscal_period_id) if fiscal_period_id else None,
        ifrs_standard=ifrs_standard,
        status=status,
        is_mandatory=is_mandatory,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=items,
        count=len(items),
        limit=limit,
        offset=offset,
    )


@router.post(
    "/disclosures/completions",
    response_model=DisclosureCompletionRead,
    status_code=status.HTTP_201_CREATED,
)
def record_disclosure_completion(
    payload: DisclosureCompletionCreate,
    organization_id: UUID = Depends(require_organization_id),
    recorded_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("rpt:disclosures:complete")),
    db: Session = Depends(get_db),
):
    """Record disclosure item completion."""
    input_data = DisclosureCompletionInput(
        disclosure_location=payload.evidence_reference,
        notes=payload.notes,
    )
    if payload.is_complete:
        return disclosure_checklist_service.complete_item(
            db=db,
            organization_id=organization_id,
            checklist_id=payload.disclosure_item_id,
            completed_by_user_id=recorded_by_user_id,
            input=input_data,
        )
    return disclosure_checklist_service.mark_not_applicable(
        db=db,
        organization_id=organization_id,
        checklist_id=payload.disclosure_item_id,
        reason=payload.notes or "Not applicable",
        marked_by_user_id=recorded_by_user_id,
    )


@router.post(
    "/disclosures/completions/{completion_id}/review",
    response_model=DisclosureCompletionRead,
)
def review_disclosure_completion(
    completion_id: UUID,
    is_approved: bool = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    reviewed_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("rpt:disclosures:review")),
    db: Session = Depends(get_db),
):
    """Review disclosure completion (SoD enforced)."""
    return disclosure_checklist_service.review_item(
        db=db,
        organization_id=organization_id,
        checklist_id=completion_id,
        reviewed_by_user_id=reviewed_by_user_id,
    )


@router.get(
    "/disclosures/instances/{instance_id}/summary", response_model=DisclosureSummaryRead
)
def get_disclosure_summary(
    instance_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("rpt:disclosures:read")),
    db: Session = Depends(get_db),
):
    """Get disclosure checklist summary for a report instance."""
    instance = report_instance_service.get(db, str(instance_id))
    if not instance.fiscal_period_id:
        raise HTTPException(
            status_code=400, detail="Report instance has no fiscal period"
        )
    return disclosure_checklist_service.get_summary(
        db=db,
        organization_id=str(organization_id),
        fiscal_period_id=str(instance.fiscal_period_id),
    )


# =============================================================================
# Report Schedules
# =============================================================================


@router.post(
    "/schedules", response_model=ReportScheduleRead, status_code=status.HTTP_201_CREATED
)
def create_report_schedule(
    payload: ReportScheduleCreate,
    organization_id: UUID = Depends(require_organization_id),
    created_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("rpt:schedules:create")),
    db: Session = Depends(get_db),
):
    """Create a report schedule."""
    input_data = ScheduleInput(
        schedule_name=payload.schedule_name,
        report_def_id=payload.report_def_id,
        frequency=payload.frequency,
        output_format=payload.output_format,
        description=payload.description,
        cron_expression=payload.cron_expression,
        day_of_month=payload.day_of_month,
        day_of_week=payload.day_of_week,
        time_of_day=payload.time_of_day,
        timezone=payload.timezone,
        report_parameters=payload.report_parameters,
        email_recipients=payload.email_recipients,
        storage_path=payload.storage_path,
        retention_days=payload.retention_days,
    )
    return report_scheduler_service.create_schedule(
        db=db,
        organization_id=organization_id,
        input=input_data,
        created_by_user_id=created_by_user_id,
    )


@router.get("/schedules/{schedule_id}", response_model=ReportScheduleRead)
def get_report_schedule(
    schedule_id: UUID,
    auth: dict = Depends(require_tenant_permission("rpt:schedules:read")),
    db: Session = Depends(get_db),
):
    """Get a report schedule by ID."""
    return report_scheduler_service.get(db, str(schedule_id))


@router.get("/schedules", response_model=ListResponse[ReportScheduleRead])
def list_report_schedules(
    organization_id: UUID = Depends(require_organization_id),
    report_def_id: Optional[UUID] = None,
    frequency: Optional[ScheduleFrequency] = None,
    is_active: Optional[bool] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("rpt:schedules:read")),
    db: Session = Depends(get_db),
):
    """List report schedules with filters."""
    schedules = report_scheduler_service.list(
        db=db,
        organization_id=str(organization_id),
        report_def_id=str(report_def_id) if report_def_id else None,
        frequency=frequency,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=schedules,
        count=len(schedules),
        limit=limit,
        offset=offset,
    )


@router.post("/schedules/{schedule_id}/run", response_model=ScheduleExecutionRead)
def run_scheduled_report(
    schedule_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    triggered_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("rpt:schedules:run")),
    db: Session = Depends(get_db),
):
    """Manually trigger a scheduled report."""
    return report_scheduler_service.record_execution(
        db=db,
        schedule_id=schedule_id,
    )


@router.post("/schedules/{schedule_id}/toggle", response_model=ReportScheduleRead)
def toggle_schedule_status(
    schedule_id: UUID,
    is_active: bool = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    updated_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("rpt:schedules:manage")),
    db: Session = Depends(get_db),
):
    """Enable or disable a report schedule."""
    if is_active:
        return report_scheduler_service.activate(
            db=db,
            organization_id=organization_id,
            schedule_id=schedule_id,
        )
    return report_scheduler_service.deactivate(
        db=db,
        organization_id=organization_id,
        schedule_id=schedule_id,
    )


@router.get(
    "/schedules/{schedule_id}/executions",
    response_model=ListResponse[ScheduleExecutionRead],
)
def list_schedule_executions(
    schedule_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    status: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("rpt:schedules:read")),
    db: Session = Depends(get_db),
):
    """List execution history for a schedule."""
    executions = report_scheduler_service.get_upcoming_schedules(
        db=db,
        organization_id=str(organization_id),
        hours_ahead=24,
    )
    return ListResponse(
        items=executions,
        count=len(executions),
        limit=limit,
        offset=offset,
    )
