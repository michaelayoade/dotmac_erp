"""
Performance Management Pydantic Schemas.

Pydantic schemas for Performance APIs including:
- Appraisal Cycle
- KRA (Key Result Area)
- KPI (Key Performance Indicator)
- Appraisal
- Scorecard
"""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.people.perf import (
    AppraisalCycleStatus,
    AppraisalStatus,
    KPIStatus,
)


# =============================================================================
# Appraisal Cycle Schemas
# =============================================================================


class AppraisalCycleBase(BaseModel):
    """Base appraisal cycle schema."""

    cycle_code: str = Field(max_length=30)
    cycle_name: str = Field(max_length=200)
    description: Optional[str] = None
    review_period_start: date
    review_period_end: date
    start_date: date
    end_date: date
    self_assessment_deadline: Optional[date] = None
    manager_review_deadline: Optional[date] = None
    calibration_deadline: Optional[date] = None
    include_probation_employees: bool = False
    min_tenure_months: int = 3


class AppraisalCycleCreate(AppraisalCycleBase):
    """Create appraisal cycle request."""

    pass


class AppraisalCycleUpdate(BaseModel):
    """Update appraisal cycle request."""

    cycle_code: Optional[str] = Field(default=None, max_length=30)
    cycle_name: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = None
    review_period_start: Optional[date] = None
    review_period_end: Optional[date] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    self_assessment_deadline: Optional[date] = None
    manager_review_deadline: Optional[date] = None
    calibration_deadline: Optional[date] = None
    include_probation_employees: Optional[bool] = None
    min_tenure_months: Optional[int] = None
    status: Optional[AppraisalCycleStatus] = None


class AppraisalCycleRead(AppraisalCycleBase):
    """Appraisal cycle response."""

    model_config = ConfigDict(from_attributes=True)

    cycle_id: UUID
    organization_id: UUID
    status: AppraisalCycleStatus
    created_at: datetime
    updated_at: Optional[datetime] = None


class AppraisalCycleListResponse(BaseModel):
    """Paginated appraisal cycle list response."""

    items: List[AppraisalCycleRead]
    total: int
    offset: int
    limit: int


class AppraisalCycleBrief(BaseModel):
    """Brief appraisal cycle info."""

    model_config = ConfigDict(from_attributes=True)

    cycle_id: UUID
    cycle_code: str
    cycle_name: str
    status: AppraisalCycleStatus


# =============================================================================
# KRA Schemas
# =============================================================================


class KRABase(BaseModel):
    """Base KRA schema."""

    kra_code: str = Field(max_length=30)
    kra_name: str = Field(max_length=200)
    description: Optional[str] = None
    department_id: Optional[UUID] = None
    designation_id: Optional[UUID] = None
    default_weightage: Decimal = Decimal("0.00")
    category: Optional[str] = Field(default=None, max_length=50)
    measurement_criteria: Optional[str] = None
    is_active: bool = True


class KRACreate(KRABase):
    """Create KRA request."""

    pass


class KRAUpdate(BaseModel):
    """Update KRA request."""

    kra_code: Optional[str] = Field(default=None, max_length=30)
    kra_name: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = None
    department_id: Optional[UUID] = None
    designation_id: Optional[UUID] = None
    default_weightage: Optional[Decimal] = None
    category: Optional[str] = Field(default=None, max_length=50)
    measurement_criteria: Optional[str] = None
    is_active: Optional[bool] = None


class DepartmentBrief(BaseModel):
    """Brief department info."""

    model_config = ConfigDict(from_attributes=True)

    department_id: UUID
    department_code: str
    department_name: str


class DesignationBrief(BaseModel):
    """Brief designation info."""

    model_config = ConfigDict(from_attributes=True)

    designation_id: UUID
    designation_code: str
    designation_name: str


class KRARead(KRABase):
    """KRA response."""

    model_config = ConfigDict(from_attributes=True)

    kra_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None


class KRAListResponse(BaseModel):
    """Paginated KRA list response."""

    items: List[KRARead]
    total: int
    offset: int
    limit: int


class KRABrief(BaseModel):
    """Brief KRA info."""

    model_config = ConfigDict(from_attributes=True)

    kra_id: UUID
    kra_code: str
    kra_name: str


# =============================================================================
# KPI Schemas
# =============================================================================


class KPIBase(BaseModel):
    """Base KPI schema."""

    employee_id: UUID
    kra_id: Optional[UUID] = None
    kpi_name: str = Field(max_length=200)
    description: Optional[str] = None
    period_start: date
    period_end: date
    target_value: Decimal
    unit_of_measure: Optional[str] = Field(default=None, max_length=30)
    threshold_value: Optional[Decimal] = None
    stretch_value: Optional[Decimal] = None
    weightage: Decimal = Decimal("0.00")
    notes: Optional[str] = None


class KPICreate(KPIBase):
    """Create KPI request."""

    pass


class KPIUpdate(BaseModel):
    """Update KPI request."""

    kra_id: Optional[UUID] = None
    kpi_name: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    target_value: Optional[Decimal] = None
    unit_of_measure: Optional[str] = Field(default=None, max_length=30)
    threshold_value: Optional[Decimal] = None
    stretch_value: Optional[Decimal] = None
    actual_value: Optional[Decimal] = None
    weightage: Optional[Decimal] = None
    status: Optional[KPIStatus] = None
    notes: Optional[str] = None
    evidence: Optional[str] = None


class EmployeeBrief(BaseModel):
    """Brief employee info."""

    model_config = ConfigDict(from_attributes=True)

    employee_id: UUID
    employee_code: str


class KPIRead(KPIBase):
    """KPI response."""

    model_config = ConfigDict(from_attributes=True)

    kpi_id: UUID
    organization_id: UUID
    actual_value: Optional[Decimal] = None
    achievement_percentage: Optional[Decimal] = None
    status: KPIStatus
    evidence: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    employee: Optional[EmployeeBrief] = None
    kra: Optional[KRABrief] = None


class KPIListResponse(BaseModel):
    """Paginated KPI list response."""

    items: List[KPIRead]
    total: int
    offset: int
    limit: int


class KPIProgressUpdateRequest(BaseModel):
    """Update KPI progress."""

    actual_value: Decimal
    evidence: Optional[str] = None
    notes: Optional[str] = None


# =============================================================================
# Appraisal Schemas
# =============================================================================


class AppraisalKRAScoreBase(BaseModel):
    """Base appraisal KRA score schema."""

    kra_id: UUID
    weightage: Decimal


class AppraisalKRAScoreCreate(AppraisalKRAScoreBase):
    """Create appraisal KRA score request."""

    pass


class AppraisalKRAScoreRead(BaseModel):
    """Appraisal KRA score response."""

    model_config = ConfigDict(from_attributes=True)

    score_id: UUID
    appraisal_id: UUID
    kra_id: UUID
    weightage: Decimal
    self_rating: Optional[int] = None
    self_comments: Optional[str] = None
    manager_rating: Optional[int] = None
    manager_comments: Optional[str] = None
    final_rating: Optional[int] = None
    weighted_score: Optional[Decimal] = None

    kra: Optional[KRABrief] = None


class AppraisalFeedbackBase(BaseModel):
    """Base appraisal feedback schema."""

    feedback_from_id: UUID
    feedback_type: str
    is_anonymous: bool = False


class AppraisalFeedbackCreate(AppraisalFeedbackBase):
    """Create appraisal feedback request."""

    pass


class AppraisalFeedbackRead(BaseModel):
    """Appraisal feedback response."""

    model_config = ConfigDict(from_attributes=True)

    feedback_id: UUID
    appraisal_id: UUID
    feedback_from_id: UUID
    feedback_type: str
    overall_rating: Optional[int] = None
    strengths: Optional[str] = None
    areas_for_improvement: Optional[str] = None
    general_comments: Optional[str] = None
    is_anonymous: bool
    submitted_on: Optional[date] = None


class AppraisalBase(BaseModel):
    """Base appraisal schema."""

    employee_id: UUID
    cycle_id: UUID
    template_id: Optional[UUID] = None
    manager_id: UUID


class AppraisalCreate(AppraisalBase):
    """Create appraisal request."""

    kra_scores: List[AppraisalKRAScoreCreate] = []


class AppraisalUpdate(BaseModel):
    """Update appraisal request."""

    template_id: Optional[UUID] = None
    manager_id: Optional[UUID] = None


class AppraisalRead(AppraisalBase):
    """Appraisal response."""

    model_config = ConfigDict(from_attributes=True)

    appraisal_id: UUID
    organization_id: UUID
    status: AppraisalStatus

    # Self assessment
    self_assessment_date: Optional[date] = None
    self_overall_rating: Optional[int] = None
    self_summary: Optional[str] = None
    achievements: Optional[str] = None
    challenges: Optional[str] = None
    development_needs: Optional[str] = None

    # Manager review
    manager_review_date: Optional[date] = None
    manager_overall_rating: Optional[int] = None
    manager_summary: Optional[str] = None
    manager_recommendations: Optional[str] = None

    # Calibration
    calibration_date: Optional[date] = None
    calibrated_rating: Optional[int] = None
    calibration_notes: Optional[str] = None

    # Final scores
    final_score: Optional[Decimal] = None
    final_rating: Optional[int] = None
    rating_label: Optional[str] = None
    completed_on: Optional[date] = None

    created_at: datetime
    updated_at: Optional[datetime] = None

    cycle: Optional[AppraisalCycleBrief] = None
    employee: Optional[EmployeeBrief] = None
    manager: Optional[EmployeeBrief] = None
    kra_scores: List[AppraisalKRAScoreRead] = []


class AppraisalListResponse(BaseModel):
    """Paginated appraisal list response."""

    items: List[AppraisalRead]
    total: int
    offset: int
    limit: int


class SelfAssessmentRequest(BaseModel):
    """Submit self assessment."""

    self_overall_rating: int = Field(ge=1, le=5)
    self_summary: Optional[str] = None
    achievements: Optional[str] = None
    challenges: Optional[str] = None
    development_needs: Optional[str] = None
    kra_ratings: List["KRASelfRatingRequest"] = []


class KRASelfRatingRequest(BaseModel):
    """Self rating for a KRA."""

    score_id: UUID
    rating: int = Field(ge=1, le=5)
    comments: Optional[str] = None


class ManagerReviewRequest(BaseModel):
    """Submit manager review."""

    manager_overall_rating: int = Field(ge=1, le=5)
    manager_summary: Optional[str] = None
    manager_recommendations: Optional[str] = None
    kra_ratings: List["KRAManagerRatingRequest"] = []


class KRAManagerRatingRequest(BaseModel):
    """Manager rating for a KRA."""

    score_id: UUID
    rating: int = Field(ge=1, le=5)
    comments: Optional[str] = None


class CalibrationRequest(BaseModel):
    """HR calibration request."""

    calibrated_rating: int = Field(ge=1, le=5)
    calibration_notes: Optional[str] = None
    rating_label: Optional[str] = None


class FeedbackSubmitRequest(BaseModel):
    """Submit feedback for an appraisal."""

    overall_rating: int = Field(ge=1, le=5)
    strengths: Optional[str] = None
    areas_for_improvement: Optional[str] = None
    general_comments: Optional[str] = None


# =============================================================================
# Appraisal Template Schemas
# =============================================================================


class AppraisalTemplateKRABase(BaseModel):
    """Base template KRA schema."""

    kra_id: UUID
    weightage: Decimal
    sequence: int = 0


class AppraisalTemplateKRACreate(AppraisalTemplateKRABase):
    """Create template KRA request."""

    pass


class AppraisalTemplateKRARead(AppraisalTemplateKRABase):
    """Template KRA response."""

    model_config = ConfigDict(from_attributes=True)

    template_kra_id: UUID
    template_id: UUID
    organization_id: UUID
    created_at: datetime
    kra: Optional[KRABrief] = None


class AppraisalTemplateBase(BaseModel):
    """Base appraisal template schema."""

    template_code: str = Field(max_length=30)
    template_name: str = Field(max_length=200)
    description: Optional[str] = None
    department_id: Optional[UUID] = None
    designation_id: Optional[UUID] = None
    rating_scale_max: int = 5
    is_active: bool = True


class AppraisalTemplateCreate(AppraisalTemplateBase):
    """Create appraisal template request."""

    kras: List[AppraisalTemplateKRACreate] = []


class AppraisalTemplateUpdate(BaseModel):
    """Update appraisal template request."""

    template_code: Optional[str] = Field(default=None, max_length=30)
    template_name: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = None
    department_id: Optional[UUID] = None
    designation_id: Optional[UUID] = None
    rating_scale_max: Optional[int] = None
    is_active: Optional[bool] = None
    kras: Optional[List[AppraisalTemplateKRACreate]] = None


class AppraisalTemplateRead(AppraisalTemplateBase):
    """Appraisal template response."""

    model_config = ConfigDict(from_attributes=True)

    template_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None
    kras: List[AppraisalTemplateKRARead] = []

    department: Optional[DepartmentBrief] = None
    designation: Optional[DesignationBrief] = None


class AppraisalTemplateListResponse(BaseModel):
    """Paginated appraisal template list response."""

    items: List[AppraisalTemplateRead]
    total: int
    offset: int
    limit: int


# =============================================================================
# Scorecard Schemas
# =============================================================================


class ScorecardItemBase(BaseModel):
    """Base scorecard item schema."""

    perspective: str
    metric_name: str = Field(max_length=200)
    description: Optional[str] = None
    target_value: Optional[Decimal] = None
    unit_of_measure: Optional[str] = Field(default=None, max_length=30)
    weightage: Decimal = Decimal("0.00")
    sequence: int = 0


class ScorecardItemCreate(ScorecardItemBase):
    """Create scorecard item request."""

    pass


class ScorecardItemRead(ScorecardItemBase):
    """Scorecard item response."""

    model_config = ConfigDict(from_attributes=True)

    item_id: UUID
    scorecard_id: UUID
    actual_value: Optional[Decimal] = None
    score: Optional[Decimal] = None
    weighted_score: Optional[Decimal] = None
    status: Optional[str] = None


class ScorecardBase(BaseModel):
    """Base scorecard schema."""

    employee_id: UUID
    period_start: date
    period_end: date
    period_label: Optional[str] = Field(default=None, max_length=50)


class ScorecardCreate(ScorecardBase):
    """Create scorecard request."""

    items: List[ScorecardItemCreate] = []


class ScorecardUpdate(BaseModel):
    """Update scorecard request."""

    period_start: Optional[date] = None
    period_end: Optional[date] = None
    period_label: Optional[str] = Field(default=None, max_length=50)
    financial_score: Optional[Decimal] = None
    customer_score: Optional[Decimal] = None
    process_score: Optional[Decimal] = None
    learning_score: Optional[Decimal] = None
    overall_score: Optional[Decimal] = None
    overall_rating: Optional[int] = None
    rating_label: Optional[str] = None
    summary: Optional[str] = None


class ScorecardRead(ScorecardBase):
    """Scorecard response."""

    model_config = ConfigDict(from_attributes=True)

    scorecard_id: UUID
    organization_id: UUID
    financial_score: Optional[Decimal] = None
    customer_score: Optional[Decimal] = None
    process_score: Optional[Decimal] = None
    learning_score: Optional[Decimal] = None
    overall_score: Optional[Decimal] = None
    overall_rating: Optional[int] = None
    rating_label: Optional[str] = None
    previous_score: Optional[Decimal] = None
    score_change: Optional[Decimal] = None
    summary: Optional[str] = None
    is_finalized: bool
    finalized_on: Optional[date] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    employee: Optional[EmployeeBrief] = None
    items: List[ScorecardItemRead] = []


class ScorecardListResponse(BaseModel):
    """Paginated scorecard list response."""

    items: List[ScorecardRead]
    total: int
    offset: int
    limit: int


class FinalizeScorecardRequest(BaseModel):
    """Finalize scorecard request."""

    summary: Optional[str] = None


class PerformanceStats(BaseModel):
    """Performance statistics for dashboard."""

    active_cycles: int
    pending_self_assessment: int
    pending_manager_review: int
    pending_calibration: int
    completed_appraisals: int
    average_rating: Optional[Decimal] = None


class PerformanceTrend(BaseModel):
    """Performance trend data."""

    employee_id: UUID
    periods: List[str]
    scores: List[Optional[Decimal]]
