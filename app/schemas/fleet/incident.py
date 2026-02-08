"""
Vehicle Incident Pydantic Schemas.

Schemas for incident API endpoints.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.fleet.enums import IncidentSeverity, IncidentStatus, IncidentType


class IncidentBase(BaseModel):
    """Base incident schema."""

    vehicle_id: UUID
    incident_type: IncidentType
    severity: IncidentSeverity
    incident_date: date
    incident_time: str | None = Field(default=None, max_length=10)
    location: str | None = Field(default=None, max_length=300)
    description: str
    driver_id: UUID | None = None
    third_party_involved: bool = False
    third_party_details: str | None = None
    notes: str | None = None


class IncidentCreate(IncidentBase):
    """Create incident request."""

    reported_by_id: UUID


class IncidentUpdate(BaseModel):
    """Update incident request."""

    incident_type: IncidentType | None = None
    severity: IncidentSeverity | None = None
    incident_date: date | None = None
    incident_time: str | None = Field(default=None, max_length=10)
    location: str | None = Field(default=None, max_length=300)
    description: str | None = None
    driver_id: UUID | None = None
    third_party_involved: bool | None = None
    third_party_details: str | None = None
    police_report_number: str | None = Field(default=None, max_length=50)
    police_report_date: date | None = None
    insurance_claim_number: str | None = Field(default=None, max_length=50)
    insurance_claim_date: date | None = None
    insurance_claim_status: str | None = Field(default=None, max_length=30)
    estimated_repair_cost: Decimal | None = Field(default=None, ge=0)
    notes: str | None = None


class IncidentRead(IncidentBase):
    """Incident response."""

    model_config = ConfigDict(from_attributes=True)

    incident_id: UUID
    organization_id: UUID
    reported_by_id: UUID
    status: IncidentStatus
    police_report_number: str | None = None
    police_report_date: date | None = None
    insurance_claim_number: str | None = None
    insurance_claim_date: date | None = None
    insurance_claim_status: str | None = None
    insurance_payout: Decimal | None = None
    estimated_repair_cost: Decimal | None = None
    actual_repair_cost: Decimal | None = None
    other_costs: Decimal | None = None
    expense_claim_id: UUID | None = None
    resolution_date: date | None = None
    resolution_notes: str | None = None
    is_deleted: bool = False
    created_at: datetime
    updated_at: datetime | None = None


class IncidentBrief(BaseModel):
    """Brief incident summary for lists."""

    model_config = ConfigDict(from_attributes=True)

    incident_id: UUID
    vehicle_id: UUID
    incident_type: IncidentType
    severity: IncidentSeverity
    incident_date: date
    status: IncidentStatus
    estimated_repair_cost: Decimal | None = None
    actual_repair_cost: Decimal | None = None


class IncidentResolve(BaseModel):
    """Request to resolve an incident."""

    resolution_date: date | None = None
    resolution_notes: str
    actual_repair_cost: Decimal | None = Field(default=None, ge=0)
    other_costs: Decimal | None = Field(default=None, ge=0)
    insurance_payout: Decimal | None = Field(default=None, ge=0)


class IncidentListResponse(BaseModel):
    """Paginated incident list response."""

    items: list[IncidentBrief]
    total: int
    offset: int
    limit: int
