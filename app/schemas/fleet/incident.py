"""
Vehicle Incident Pydantic Schemas.

Schemas for incident API endpoints.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.fleet.enums import IncidentSeverity, IncidentStatus, IncidentType


class IncidentBase(BaseModel):
    """Base incident schema."""

    vehicle_id: UUID
    incident_type: IncidentType
    severity: IncidentSeverity
    incident_date: date
    incident_time: Optional[str] = Field(default=None, max_length=10)
    location: Optional[str] = Field(default=None, max_length=300)
    description: str
    driver_id: Optional[UUID] = None
    third_party_involved: bool = False
    third_party_details: Optional[str] = None
    notes: Optional[str] = None


class IncidentCreate(IncidentBase):
    """Create incident request."""

    reported_by_id: UUID


class IncidentUpdate(BaseModel):
    """Update incident request."""

    incident_type: Optional[IncidentType] = None
    severity: Optional[IncidentSeverity] = None
    incident_date: Optional[date] = None
    incident_time: Optional[str] = Field(default=None, max_length=10)
    location: Optional[str] = Field(default=None, max_length=300)
    description: Optional[str] = None
    driver_id: Optional[UUID] = None
    third_party_involved: Optional[bool] = None
    third_party_details: Optional[str] = None
    police_report_number: Optional[str] = Field(default=None, max_length=50)
    police_report_date: Optional[date] = None
    insurance_claim_number: Optional[str] = Field(default=None, max_length=50)
    insurance_claim_date: Optional[date] = None
    insurance_claim_status: Optional[str] = Field(default=None, max_length=30)
    estimated_repair_cost: Optional[Decimal] = Field(default=None, ge=0)
    notes: Optional[str] = None


class IncidentRead(IncidentBase):
    """Incident response."""

    model_config = ConfigDict(from_attributes=True)

    incident_id: UUID
    organization_id: UUID
    reported_by_id: UUID
    status: IncidentStatus
    police_report_number: Optional[str] = None
    police_report_date: Optional[date] = None
    insurance_claim_number: Optional[str] = None
    insurance_claim_date: Optional[date] = None
    insurance_claim_status: Optional[str] = None
    insurance_payout: Optional[Decimal] = None
    estimated_repair_cost: Optional[Decimal] = None
    actual_repair_cost: Optional[Decimal] = None
    other_costs: Optional[Decimal] = None
    expense_claim_id: Optional[UUID] = None
    resolution_date: Optional[date] = None
    resolution_notes: Optional[str] = None
    is_deleted: bool = False
    created_at: datetime
    updated_at: Optional[datetime] = None


class IncidentBrief(BaseModel):
    """Brief incident summary for lists."""

    model_config = ConfigDict(from_attributes=True)

    incident_id: UUID
    vehicle_id: UUID
    incident_type: IncidentType
    severity: IncidentSeverity
    incident_date: date
    status: IncidentStatus
    estimated_repair_cost: Optional[Decimal] = None
    actual_repair_cost: Optional[Decimal] = None


class IncidentResolve(BaseModel):
    """Request to resolve an incident."""

    resolution_date: Optional[date] = None
    resolution_notes: str
    actual_repair_cost: Optional[Decimal] = Field(default=None, ge=0)
    other_costs: Optional[Decimal] = Field(default=None, ge=0)
    insurance_payout: Optional[Decimal] = Field(default=None, ge=0)


class IncidentListResponse(BaseModel):
    """Paginated incident list response."""

    items: List[IncidentBrief]
    total: int
    offset: int
    limit: int
