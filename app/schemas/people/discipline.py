"""
Discipline Management Pydantic Schemas.

Pydantic schemas for Discipline APIs including:
- Disciplinary Case
- Case Witness
- Case Action
- Case Document
- Case Response
"""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.people.discipline import (
    ActionType,
    CaseStatus,
    DocumentType,
    SeverityLevel,
    ViolationType,
)

# =============================================================================
# Case Response Schemas (Employee Response to Query)
# =============================================================================


class CaseResponseBase(BaseModel):
    """Base case response schema."""

    response_text: str = Field(min_length=1)
    is_initial_response: bool = True
    is_appeal_response: bool = False


class CaseResponseCreate(CaseResponseBase):
    """Create case response (employee submitting response)."""

    pass


class CaseResponseRead(CaseResponseBase):
    """Case response read schema."""

    model_config = ConfigDict(from_attributes=True)

    response_id: UUID
    case_id: UUID
    submitted_at: datetime
    acknowledged_at: datetime | None = None


# =============================================================================
# Case Witness Schemas
# =============================================================================


class CaseWitnessBase(BaseModel):
    """Base witness schema."""

    employee_id: UUID | None = None
    external_name: str | None = Field(default=None, max_length=200)
    external_contact: str | None = Field(default=None, max_length=255)
    statement: str | None = None


class CaseWitnessCreate(CaseWitnessBase):
    """Create witness schema."""

    pass


class CaseWitnessUpdate(BaseModel):
    """Update witness schema."""

    statement: str | None = None
    statement_date: datetime | None = None


class CaseWitnessRead(CaseWitnessBase):
    """Witness read schema."""

    model_config = ConfigDict(from_attributes=True)

    witness_id: UUID
    case_id: UUID
    statement_date: datetime | None = None
    created_at: datetime
    # Nested employee info if available
    employee_name: str | None = None


# =============================================================================
# Case Action Schemas
# =============================================================================


class CaseActionBase(BaseModel):
    """Base action schema."""

    action_type: ActionType
    description: str | None = None
    effective_date: date
    end_date: date | None = None
    warning_expiry_date: date | None = None


class CaseActionCreate(CaseActionBase):
    """Create action schema."""

    pass


class CaseActionRead(CaseActionBase):
    """Action read schema."""

    model_config = ConfigDict(from_attributes=True)

    action_id: UUID
    case_id: UUID
    is_active: bool
    payroll_processed: bool
    lifecycle_triggered: bool
    issued_by_id: UUID | None = None
    created_at: datetime
    # Nested info
    issued_by_name: str | None = None


# =============================================================================
# Case Document Schemas
# =============================================================================


class CaseDocumentBase(BaseModel):
    """Base document schema."""

    document_type: DocumentType
    title: str = Field(max_length=255)
    description: str | None = None


class CaseDocumentCreate(CaseDocumentBase):
    """Create document schema (metadata only, file handled separately)."""

    file_name: str = Field(max_length=255)


class CaseDocumentRead(CaseDocumentBase):
    """Document read schema."""

    model_config = ConfigDict(from_attributes=True)

    document_id: UUID
    case_id: UUID
    file_path: str
    file_name: str
    file_size: int | None = None
    mime_type: str | None = None
    uploaded_by_id: UUID | None = None
    created_at: datetime
    uploaded_by_name: str | None = None


# =============================================================================
# Disciplinary Case Schemas
# =============================================================================


class DisciplinaryCaseBase(BaseModel):
    """Base disciplinary case schema."""

    employee_id: UUID
    violation_type: ViolationType
    severity: SeverityLevel = SeverityLevel.MODERATE
    subject: str = Field(max_length=255)
    description: str | None = None
    incident_date: date | None = None
    reported_date: date


class DisciplinaryCaseCreate(DisciplinaryCaseBase):
    """Create disciplinary case schema."""

    reported_by_id: UUID | None = None


class DisciplinaryCaseUpdate(BaseModel):
    """Update disciplinary case schema."""

    violation_type: ViolationType | None = None
    severity: SeverityLevel | None = None
    subject: str | None = Field(default=None, max_length=255)
    description: str | None = None
    incident_date: date | None = None
    investigating_officer_id: UUID | None = None


class IssueQueryRequest(BaseModel):
    """Request to issue query to employee."""

    query_text: str = Field(min_length=10)
    response_due_date: date


class ScheduleHearingRequest(BaseModel):
    """Request to schedule a hearing."""

    hearing_date: datetime
    hearing_location: str | None = Field(default=None, max_length=255)
    panel_chair_id: UUID | None = None


class RecordHearingNotesRequest(BaseModel):
    """Request to record hearing notes."""

    hearing_notes: str


class RecordDecisionRequest(BaseModel):
    """Request to record decision after hearing."""

    decision_summary: str
    actions: list[CaseActionCreate] = Field(default_factory=list)


class FileAppealRequest(BaseModel):
    """Request for employee to file appeal."""

    appeal_reason: str = Field(min_length=10)


class DecideAppealRequest(BaseModel):
    """Request to decide on appeal."""

    appeal_decision: str
    # Optionally modify the original actions
    revised_actions: list[CaseActionCreate] | None = None


class DisciplinaryCaseRead(DisciplinaryCaseBase):
    """Disciplinary case read schema."""

    model_config = ConfigDict(from_attributes=True)

    case_id: UUID
    organization_id: UUID
    case_number: str
    status: CaseStatus
    query_issued_date: date | None = None
    response_due_date: date | None = None
    hearing_date: datetime | None = None
    decision_date: date | None = None
    appeal_deadline: date | None = None
    closed_date: date | None = None
    query_text: str | None = None
    hearing_location: str | None = None
    hearing_notes: str | None = None
    decision_summary: str | None = None
    appeal_reason: str | None = None
    appeal_decision: str | None = None
    reported_by_id: UUID | None = None
    investigating_officer_id: UUID | None = None
    panel_chair_id: UUID | None = None
    created_at: datetime
    updated_at: datetime | None = None
    # Nested names for display
    employee_name: str | None = None
    reported_by_name: str | None = None
    investigating_officer_name: str | None = None
    panel_chair_name: str | None = None


class DisciplinaryCaseDetail(DisciplinaryCaseRead):
    """Detailed case view with related entities."""

    witnesses: list[CaseWitnessRead] = Field(default_factory=list)
    actions: list[CaseActionRead] = Field(default_factory=list)
    documents: list[CaseDocumentRead] = Field(default_factory=list)
    responses: list[CaseResponseRead] = Field(default_factory=list)


class DisciplinaryCaseListResponse(BaseModel):
    """Paginated case list response."""

    items: list[DisciplinaryCaseRead]
    total: int
    offset: int
    limit: int


# =============================================================================
# Filter Schemas
# =============================================================================


class CaseListFilter(BaseModel):
    """Filter parameters for listing cases."""

    status: CaseStatus | None = None
    violation_type: ViolationType | None = None
    severity: SeverityLevel | None = None
    employee_id: UUID | None = None
    investigating_officer_id: UUID | None = None
    from_date: date | None = None
    to_date: date | None = None
    include_closed: bool = False


# =============================================================================
# Self-Service Schemas (Employee View)
# =============================================================================


class EmployeeCaseSummary(BaseModel):
    """Summary view of case for employee self-service."""

    model_config = ConfigDict(from_attributes=True)

    case_id: UUID
    case_number: str
    violation_type: ViolationType
    subject: str
    status: CaseStatus
    query_issued_date: date | None = None
    response_due_date: date | None = None
    hearing_date: datetime | None = None
    decision_date: date | None = None
    appeal_deadline: date | None = None
    has_pending_response: bool = False


class EmployeeCaseDetail(BaseModel):
    """Detailed view for employee self-service."""

    model_config = ConfigDict(from_attributes=True)

    case_id: UUID
    case_number: str
    violation_type: ViolationType
    severity: SeverityLevel
    subject: str
    description: str | None = None
    incident_date: date | None = None
    status: CaseStatus
    query_text: str | None = None
    query_issued_date: date | None = None
    response_due_date: date | None = None
    hearing_date: datetime | None = None
    hearing_location: str | None = None
    decision_summary: str | None = None
    decision_date: date | None = None
    appeal_deadline: date | None = None
    appeal_decision: str | None = None
    # Employee's own responses
    my_responses: list[CaseResponseRead] = Field(default_factory=list)
    # Actions taken
    actions: list[CaseActionRead] = Field(default_factory=list)
