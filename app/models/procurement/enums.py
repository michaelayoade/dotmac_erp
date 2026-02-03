"""
Procurement Module Enumerations.

Defines all status, type, and category enums for the procurement module.
"""

import enum


class ProcurementPlanStatus(str, enum.Enum):
    """Procurement plan lifecycle status."""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class PlanItemStatus(str, enum.Enum):
    """Procurement plan line item status."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class ProcurementMethod(str, enum.Enum):
    """Procurement method per PPA 2007."""

    DIRECT = "DIRECT"
    SELECTIVE = "SELECTIVE"
    OPEN_COMPETITIVE = "OPEN_COMPETITIVE"


class RequisitionStatus(str, enum.Enum):
    """Purchase requisition status."""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    BUDGET_VERIFIED = "BUDGET_VERIFIED"
    APPROVED = "APPROVED"
    CONVERTED = "CONVERTED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class UrgencyLevel(str, enum.Enum):
    """Requisition urgency level."""

    NORMAL = "NORMAL"
    URGENT = "URGENT"
    EMERGENCY = "EMERGENCY"


class RFQStatus(str, enum.Enum):
    """Request for Quotation status."""

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    CLOSED = "CLOSED"
    EVALUATED = "EVALUATED"
    AWARDED = "AWARDED"
    CANCELLED = "CANCELLED"


class QuotationResponseStatus(str, enum.Enum):
    """Vendor quotation response status."""

    RECEIVED = "RECEIVED"
    UNDER_EVALUATION = "UNDER_EVALUATION"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class EvaluationStatus(str, enum.Enum):
    """Bid evaluation status."""

    DRAFT = "DRAFT"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    APPROVED = "APPROVED"


class ContractStatus(str, enum.Enum):
    """Procurement contract status."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    TERMINATED = "TERMINATED"
    EXPIRED = "EXPIRED"


class PrequalificationStatus(str, enum.Enum):
    """Vendor prequalification status."""

    PENDING = "PENDING"
    UNDER_REVIEW = "UNDER_REVIEW"
    QUALIFIED = "QUALIFIED"
    DISQUALIFIED = "DISQUALIFIED"
    EXPIRED = "EXPIRED"
    BLACKLISTED = "BLACKLISTED"
