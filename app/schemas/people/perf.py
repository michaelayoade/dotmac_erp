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
    description: str | None = None
    review_period_start: date
    review_period_end: date
    start_date: date
    end_date: date
    self_assessment_deadline: date | None = None
    manager_review_deadline: date | None = None
    calibration_deadline: date | None = None
    include_probation_employees: bool = False
    min_tenure_months: int = 3


class AppraisalCycleCreate(AppraisalCycleBase):
    """Create appraisal cycle request."""

    pass


class AppraisalCycleUpdate(BaseModel):
    """Update appraisal cycle request."""

    cycle_code: str | None = Field(default=None, max_length=30)
    cycle_name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    review_period_start: date | None = None
    review_period_end: date | None = None
    start_date: date | None = None
    end_date: date | None = None
    self_assessment_deadline: date | None = None
    manager_review_deadline: date | None = None
    calibration_deadline: date | None = None
    include_probation_employees: bool | None = None
    min_tenure_months: int | None = None
    status: AppraisalCycleStatus | None = None


class AppraisalCycleRead(AppraisalCycleBase):
    """Appraisal cycle response."""

    model_config = ConfigDict(from_attributes=True)

    cycle_id: UUID
    organization_id: UUID
    status: AppraisalCycleStatus
    created_at: datetime
    updated_at: datetime | None = None


class AppraisalCycleListResponse(BaseModel):
    """Paginated appraisal cycle list response."""

    items: list[AppraisalCycleRead]
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
    description: str | None = None
    department_id: UUID | None = None
    designation_id: UUID | None = None
    default_weightage: Decimal = Decimal("0.00")
    category: str | None = Field(default=None, max_length=50)
    measurement_criteria: str | None = None
    is_active: bool = True


class KRACreate(KRABase):
    """Create KRA request."""

    pass


class KRAUpdate(BaseModel):
    """Update KRA request."""

    kra_code: str | None = Field(default=None, max_length=30)
    kra_name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    department_id: UUID | None = None
    designation_id: UUID | None = None
    default_weightage: Decimal | None = None
    category: str | None = Field(default=None, max_length=50)
    measurement_criteria: str | None = None
    is_active: bool | None = None


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
    updated_at: datetime | None = None


class KRAListResponse(BaseModel):
    """Paginated KRA list response."""

    items: list[KRARead]
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
    kra_id: UUID | None = None
    kpi_name: str = Field(max_length=200)
    description: str | None = None
    period_start: date
    period_end: date
    target_value: Decimal
    unit_of_measure: str | None = Field(default=None, max_length=30)
    threshold_value: Decimal | None = None
    stretch_value: Decimal | None = None
    weightage: Decimal = Decimal("0.00")
    notes: str | None = None


class KPICreate(KPIBase):
    """Create KPI request."""

    pass


class KPIUpdate(BaseModel):
    """Update KPI request."""

    kra_id: UUID | None = None
    kpi_name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    target_value: Decimal | None = None
    unit_of_measure: str | None = Field(default=None, max_length=30)
    threshold_value: Decimal | None = None
    stretch_value: Decimal | None = None
    actual_value: Decimal | None = None
    weightage: Decimal | None = None
    status: KPIStatus | None = None
    notes: str | None = None
    evidence: str | None = None


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
    actual_value: Decimal | None = None
    achievement_percentage: Decimal | None = None
    status: KPIStatus
    evidence: str | None = None
    created_at: datetime
    updated_at: datetime | None = None

    employee: EmployeeBrief | None = None
    kra: KRABrief | None = None


class KPIListResponse(BaseModel):
    """Paginated KPI list response."""

    items: list[KPIRead]
    total: int
    offset: int
    limit: int


class KPIProgressUpdateRequest(BaseModel):
    """Update KPI progress."""

    actual_value: Decimal
    evidence: str | None = None
    notes: str | None = None


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
    self_rating: int | None = None
    self_comments: str | None = None
    manager_rating: int | None = None
    manager_comments: str | None = None
    final_rating: int | None = None
    weighted_score: Decimal | None = None

    kra: KRABrief | None = None


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
    overall_rating: int | None = None
    strengths: str | None = None
    areas_for_improvement: str | None = None
    general_comments: str | None = None
    is_anonymous: bool
    submitted_on: date | None = None


class AppraisalBase(BaseModel):
    """Base appraisal schema."""

    employee_id: UUID
    cycle_id: UUID
    template_id: UUID | None = None
    manager_id: UUID


class AppraisalCreate(AppraisalBase):
    """Create appraisal request."""

    kra_scores: list[AppraisalKRAScoreCreate] = []


class AppraisalUpdate(BaseModel):
    """Update appraisal request."""

    template_id: UUID | None = None
    manager_id: UUID | None = None


class AppraisalRead(AppraisalBase):
    """Appraisal response."""

    model_config = ConfigDict(from_attributes=True)

    appraisal_id: UUID
    organization_id: UUID
    status: AppraisalStatus

    # Self assessment
    self_assessment_date: date | None = None
    self_overall_rating: int | None = None
    self_summary: str | None = None
    achievements: str | None = None
    challenges: str | None = None
    development_needs: str | None = None

    # Manager review
    manager_review_date: date | None = None
    manager_overall_rating: int | None = None
    manager_summary: str | None = None
    manager_recommendations: str | None = None

    # Calibration
    calibration_date: date | None = None
    calibrated_rating: int | None = None
    calibration_notes: str | None = None

    # Final scores
    final_score: Decimal | None = None
    final_rating: int | None = None
    rating_label: str | None = None
    completed_on: date | None = None

    created_at: datetime
    updated_at: datetime | None = None

    cycle: AppraisalCycleBrief | None = None
    employee: EmployeeBrief | None = None
    manager: EmployeeBrief | None = None
    kra_scores: list[AppraisalKRAScoreRead] = []


class AppraisalListResponse(BaseModel):
    """Paginated appraisal list response."""

    items: list[AppraisalRead]
    total: int
    offset: int
    limit: int


class SelfAssessmentRequest(BaseModel):
    """Submit self assessment."""

    self_overall_rating: int = Field(ge=1, le=5)
    self_summary: str | None = None
    achievements: str | None = None
    challenges: str | None = None
    development_needs: str | None = None
    kra_ratings: list["KRASelfRatingRequest"] = []


class KRASelfRatingRequest(BaseModel):
    """Self rating for a KRA."""

    score_id: UUID
    rating: int = Field(ge=1, le=5)
    comments: str | None = None


class ManagerReviewRequest(BaseModel):
    """Submit manager review."""

    manager_overall_rating: int = Field(ge=1, le=5)
    manager_summary: str | None = None
    manager_recommendations: str | None = None
    kra_ratings: list["KRAManagerRatingRequest"] = []


class KRAManagerRatingRequest(BaseModel):
    """Manager rating for a KRA."""

    score_id: UUID
    rating: int = Field(ge=1, le=5)
    comments: str | None = None


class CalibrationRequest(BaseModel):
    """HR calibration request."""

    calibrated_rating: int = Field(ge=1, le=5)
    calibration_notes: str | None = None
    rating_label: str | None = None


class FeedbackSubmitRequest(BaseModel):
    """Submit feedback for an appraisal."""

    overall_rating: int = Field(ge=1, le=5)
    strengths: str | None = None
    areas_for_improvement: str | None = None
    general_comments: str | None = None


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
    kra: KRABrief | None = None


class AppraisalTemplateBase(BaseModel):
    """Base appraisal template schema."""

    template_code: str = Field(max_length=30)
    template_name: str = Field(max_length=200)
    description: str | None = None
    department_id: UUID | None = None
    designation_id: UUID | None = None
    rating_scale_max: int = 5
    is_active: bool = True


class AppraisalTemplateCreate(AppraisalTemplateBase):
    """Create appraisal template request."""

    kras: list[AppraisalTemplateKRACreate] = []


class AppraisalTemplateUpdate(BaseModel):
    """Update appraisal template request."""

    template_code: str | None = Field(default=None, max_length=30)
    template_name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    department_id: UUID | None = None
    designation_id: UUID | None = None
    rating_scale_max: int | None = None
    is_active: bool | None = None
    kras: list[AppraisalTemplateKRACreate] | None = None


class AppraisalTemplateRead(AppraisalTemplateBase):
    """Appraisal template response."""

    model_config = ConfigDict(from_attributes=True)

    template_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: datetime | None = None
    kras: list[AppraisalTemplateKRARead] = []

    department: DepartmentBrief | None = None
    designation: DesignationBrief | None = None


class AppraisalTemplateListResponse(BaseModel):
    """Paginated appraisal template list response."""

    items: list[AppraisalTemplateRead]
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
    description: str | None = None
    target_value: Decimal | None = None
    unit_of_measure: str | None = Field(default=None, max_length=30)
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
    actual_value: Decimal | None = None
    score: Decimal | None = None
    weighted_score: Decimal | None = None
    status: str | None = None


class ScorecardBase(BaseModel):
    """Base scorecard schema."""

    employee_id: UUID
    period_start: date
    period_end: date
    period_label: str | None = Field(default=None, max_length=50)


class ScorecardCreate(ScorecardBase):
    """Create scorecard request."""

    items: list[ScorecardItemCreate] = []


class ScorecardUpdate(BaseModel):
    """Update scorecard request."""

    period_start: date | None = None
    period_end: date | None = None
    period_label: str | None = Field(default=None, max_length=50)
    financial_score: Decimal | None = None
    customer_score: Decimal | None = None
    process_score: Decimal | None = None
    learning_score: Decimal | None = None
    overall_score: Decimal | None = None
    overall_rating: int | None = None
    rating_label: str | None = None
    summary: str | None = None


class ScorecardRead(ScorecardBase):
    """Scorecard response."""

    model_config = ConfigDict(from_attributes=True)

    scorecard_id: UUID
    organization_id: UUID
    financial_score: Decimal | None = None
    customer_score: Decimal | None = None
    process_score: Decimal | None = None
    learning_score: Decimal | None = None
    overall_score: Decimal | None = None
    overall_rating: int | None = None
    rating_label: str | None = None
    previous_score: Decimal | None = None
    score_change: Decimal | None = None
    summary: str | None = None
    is_finalized: bool
    finalized_on: date | None = None
    created_at: datetime
    updated_at: datetime | None = None

    employee: EmployeeBrief | None = None
    items: list[ScorecardItemRead] = []


class ScorecardListResponse(BaseModel):
    """Paginated scorecard list response."""

    items: list[ScorecardRead]
    total: int
    offset: int
    limit: int


class FinalizeScorecardRequest(BaseModel):
    """Finalize scorecard request."""

    summary: str | None = None


class PerformanceStats(BaseModel):
    """Performance statistics for dashboard."""

    active_cycles: int
    pending_self_assessment: int
    pending_manager_review: int
    pending_calibration: int
    completed_appraisals: int
    average_rating: Decimal | None = None


class PerformanceTrend(BaseModel):
    """Performance trend data."""

    employee_id: UUID
    periods: list[str]
    scores: list[Decimal | None]
