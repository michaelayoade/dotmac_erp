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
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.people.discipline import (
    ViolationType,
    SeverityLevel,
    CaseStatus,
    ActionType,
    DocumentType,
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
    acknowledged_at: Optional[datetime] = None


# =============================================================================
# Case Witness Schemas
# =============================================================================


class CaseWitnessBase(BaseModel):
    """Base witness schema."""

    employee_id: Optional[UUID] = None
    external_name: Optional[str] = Field(default=None, max_length=200)
    external_contact: Optional[str] = Field(default=None, max_length=255)
    statement: Optional[str] = None


class CaseWitnessCreate(CaseWitnessBase):
    """Create witness schema."""

    pass


class CaseWitnessUpdate(BaseModel):
    """Update witness schema."""

    statement: Optional[str] = None
    statement_date: Optional[datetime] = None


class CaseWitnessRead(CaseWitnessBase):
    """Witness read schema."""

    model_config = ConfigDict(from_attributes=True)

    witness_id: UUID
    case_id: UUID
    statement_date: Optional[datetime] = None
    created_at: datetime
    # Nested employee info if available
    employee_name: Optional[str] = None


# =============================================================================
# Case Action Schemas
# =============================================================================


class CaseActionBase(BaseModel):
    """Base action schema."""

    action_type: ActionType
    description: Optional[str] = None
    effective_date: date
    end_date: Optional[date] = None
    warning_expiry_date: Optional[date] = None


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
    issued_by_id: Optional[UUID] = None
    created_at: datetime
    # Nested info
    issued_by_name: Optional[str] = None


# =============================================================================
# Case Document Schemas
# =============================================================================


class CaseDocumentBase(BaseModel):
    """Base document schema."""

    document_type: DocumentType
    title: str = Field(max_length=255)
    description: Optional[str] = None


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
    file_size: Optional[int] = None
    mime_type: Optional[str] = None
    uploaded_by_id: Optional[UUID] = None
    created_at: datetime
    uploaded_by_name: Optional[str] = None


# =============================================================================
# Disciplinary Case Schemas
# =============================================================================


class DisciplinaryCaseBase(BaseModel):
    """Base disciplinary case schema."""

    employee_id: UUID
    violation_type: ViolationType
    severity: SeverityLevel = SeverityLevel.MODERATE
    subject: str = Field(max_length=255)
    description: Optional[str] = None
    incident_date: Optional[date] = None
    reported_date: date


class DisciplinaryCaseCreate(DisciplinaryCaseBase):
    """Create disciplinary case schema."""

    reported_by_id: Optional[UUID] = None


class DisciplinaryCaseUpdate(BaseModel):
    """Update disciplinary case schema."""

    violation_type: Optional[ViolationType] = None
    severity: Optional[SeverityLevel] = None
    subject: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = None
    incident_date: Optional[date] = None
    investigating_officer_id: Optional[UUID] = None


class IssueQueryRequest(BaseModel):
    """Request to issue query to employee."""

    query_text: str = Field(min_length=10)
    response_due_date: date


class ScheduleHearingRequest(BaseModel):
    """Request to schedule a hearing."""

    hearing_date: datetime
    hearing_location: Optional[str] = Field(default=None, max_length=255)
    panel_chair_id: Optional[UUID] = None


class RecordHearingNotesRequest(BaseModel):
    """Request to record hearing notes."""

    hearing_notes: str


class RecordDecisionRequest(BaseModel):
    """Request to record decision after hearing."""

    decision_summary: str
    actions: List[CaseActionCreate] = Field(default_factory=list)


class FileAppealRequest(BaseModel):
    """Request for employee to file appeal."""

    appeal_reason: str = Field(min_length=10)


class DecideAppealRequest(BaseModel):
    """Request to decide on appeal."""

    appeal_decision: str
    # Optionally modify the original actions
    revised_actions: Optional[List[CaseActionCreate]] = None


class DisciplinaryCaseRead(DisciplinaryCaseBase):
    """Disciplinary case read schema."""

    model_config = ConfigDict(from_attributes=True)

    case_id: UUID
    organization_id: UUID
    case_number: str
    status: CaseStatus
    query_issued_date: Optional[date] = None
    response_due_date: Optional[date] = None
    hearing_date: Optional[datetime] = None
    decision_date: Optional[date] = None
    appeal_deadline: Optional[date] = None
    closed_date: Optional[date] = None
    query_text: Optional[str] = None
    hearing_location: Optional[str] = None
    hearing_notes: Optional[str] = None
    decision_summary: Optional[str] = None
    appeal_reason: Optional[str] = None
    appeal_decision: Optional[str] = None
    reported_by_id: Optional[UUID] = None
    investigating_officer_id: Optional[UUID] = None
    panel_chair_id: Optional[UUID] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    # Nested names for display
    employee_name: Optional[str] = None
    reported_by_name: Optional[str] = None
    investigating_officer_name: Optional[str] = None
    panel_chair_name: Optional[str] = None


class DisciplinaryCaseDetail(DisciplinaryCaseRead):
    """Detailed case view with related entities."""

    witnesses: List[CaseWitnessRead] = Field(default_factory=list)
    actions: List[CaseActionRead] = Field(default_factory=list)
    documents: List[CaseDocumentRead] = Field(default_factory=list)
    responses: List[CaseResponseRead] = Field(default_factory=list)


class DisciplinaryCaseListResponse(BaseModel):
    """Paginated case list response."""

    items: List[DisciplinaryCaseRead]
    total: int
    offset: int
    limit: int


# =============================================================================
# Filter Schemas
# =============================================================================


class CaseListFilter(BaseModel):
    """Filter parameters for listing cases."""

    status: Optional[CaseStatus] = None
    violation_type: Optional[ViolationType] = None
    severity: Optional[SeverityLevel] = None
    employee_id: Optional[UUID] = None
    investigating_officer_id: Optional[UUID] = None
    from_date: Optional[date] = None
    to_date: Optional[date] = None
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
    query_issued_date: Optional[date] = None
    response_due_date: Optional[date] = None
    hearing_date: Optional[datetime] = None
    decision_date: Optional[date] = None
    appeal_deadline: Optional[date] = None
    has_pending_response: bool = False


class EmployeeCaseDetail(BaseModel):
    """Detailed view for employee self-service."""

    model_config = ConfigDict(from_attributes=True)

    case_id: UUID
    case_number: str
    violation_type: ViolationType
    severity: SeverityLevel
    subject: str
    description: Optional[str] = None
    incident_date: Optional[date] = None
    status: CaseStatus
    query_text: Optional[str] = None
    query_issued_date: Optional[date] = None
    response_due_date: Optional[date] = None
    hearing_date: Optional[datetime] = None
    hearing_location: Optional[str] = None
    decision_summary: Optional[str] = None
    decision_date: Optional[date] = None
    appeal_deadline: Optional[date] = None
    appeal_decision: Optional[str] = None
    # Employee's own responses
    my_responses: List[CaseResponseRead] = Field(default_factory=list)
    # Actions taken
    actions: List[CaseActionRead] = Field(default_factory=list)
