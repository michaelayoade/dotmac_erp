"""
IPSAS Enums - Shared enumerations for IPSAS models.
"""

import enum


class FundType(str, enum.Enum):
    GENERAL = "GENERAL"
    CAPITAL = "CAPITAL"
    SPECIAL = "SPECIAL"
    DONOR = "DONOR"
    TRUST = "TRUST"
    REVOLVING = "REVOLVING"
    CONSOLIDATED = "CONSOLIDATED"


class FundStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    FROZEN = "FROZEN"
    CLOSED = "CLOSED"


class AppropriationType(str, enum.Enum):
    ORIGINAL = "ORIGINAL"
    SUPPLEMENTARY = "SUPPLEMENTARY"
    VIREMENT_IN = "VIREMENT_IN"
    VIREMENT_OUT = "VIREMENT_OUT"
    REDUCTION = "REDUCTION"


class AppropriationStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    LAPSED = "LAPSED"
    CLOSED = "CLOSED"


class AllotmentStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    FROZEN = "FROZEN"
    CLOSED = "CLOSED"


class CommitmentType(str, enum.Enum):
    PURCHASE_ORDER = "PURCHASE_ORDER"
    CONTRACT = "CONTRACT"
    PAYROLL = "PAYROLL"
    OTHER = "OTHER"


class CommitmentStatus(str, enum.Enum):
    PENDING = "PENDING"
    COMMITTED = "COMMITTED"
    OBLIGATED = "OBLIGATED"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    EXPENDED = "EXPENDED"
    CANCELLED = "CANCELLED"
    LAPSED = "LAPSED"


class VirementStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"


class CoASegmentType(str, enum.Enum):
    ADMINISTRATIVE = "ADMINISTRATIVE"
    ECONOMIC = "ECONOMIC"
    FUND = "FUND"
    FUNCTIONAL = "FUNCTIONAL"
    PROGRAM = "PROGRAM"
    PROJECT = "PROJECT"
