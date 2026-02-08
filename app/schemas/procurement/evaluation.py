"""
Bid Evaluation Schemas.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.procurement.enums import EvaluationStatus


class EvaluationScoreCreate(BaseModel):
    """Schema for creating an evaluation score."""

    response_id: UUID
    criterion_name: str = Field(max_length=100)
    weight: Decimal = Field(ge=0, le=100)
    score: Decimal = Field(ge=0, le=100)
    comments: str | None = None


class EvaluationScoreResponse(BaseModel):
    """Schema for evaluation score response."""

    model_config = ConfigDict(from_attributes=True)

    score_id: UUID
    evaluation_id: UUID
    response_id: UUID
    criterion_name: str
    weight: Decimal
    score: Decimal
    weighted_score: Decimal
    comments: str | None = None


class EvaluationCreate(BaseModel):
    """Schema for creating a bid evaluation."""

    rfq_id: UUID
    evaluation_date: date
    evaluated_by_user_id: UUID
    evaluation_report: str | None = None
    scores: list[EvaluationScoreCreate] = Field(default_factory=list)


class EvaluationUpdate(BaseModel):
    """Schema for updating a bid evaluation."""

    evaluation_report: str | None = None
    recommended_supplier_id: UUID | None = None
    recommended_response_id: UUID | None = None


class EvaluationResponse(BaseModel):
    """Schema for bid evaluation response."""

    model_config = ConfigDict(from_attributes=True)

    evaluation_id: UUID
    rfq_id: UUID
    organization_id: UUID
    evaluation_date: date
    status: EvaluationStatus
    recommended_supplier_id: UUID | None = None
    recommended_response_id: UUID | None = None
    evaluation_report: str | None = None
    evaluated_by_user_id: UUID
    approved_by_user_id: UUID | None = None
    approved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None
    scores: list[EvaluationScoreResponse] = Field(default_factory=list)
