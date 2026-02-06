"""
Fleet Management Enumerations.

Defines all status, type, and category enums for the fleet module.
"""

import enum


class VehicleStatus(str, enum.Enum):
    """Vehicle operational status."""

    ACTIVE = "ACTIVE"  # In service, available for use
    MAINTENANCE = "MAINTENANCE"  # Undergoing maintenance/repair
    OUT_OF_SERVICE = "OUT_OF_SERVICE"  # Temporarily unavailable
    RESERVED = "RESERVED"  # Reserved for specific purpose
    DISPOSED = "DISPOSED"  # Sold, scrapped, or returned


class VehicleType(str, enum.Enum):
    """Vehicle classification by body type."""

    SEDAN = "SEDAN"
    SUV = "SUV"
    PICKUP = "PICKUP"
    VAN = "VAN"
    TRUCK = "TRUCK"
    MOTORCYCLE = "MOTORCYCLE"
    BUS = "BUS"
    MINIBUS = "MINIBUS"
    HEAVY_EQUIPMENT = "HEAVY_EQUIPMENT"
    OTHER = "OTHER"


class FuelType(str, enum.Enum):
    """Fuel/power source type."""

    PETROL = "PETROL"
    DIESEL = "DIESEL"
    ELECTRIC = "ELECTRIC"
    HYBRID = "HYBRID"
    CNG = "CNG"  # Compressed Natural Gas
    LPG = "LPG"  # Liquefied Petroleum Gas


class OwnershipType(str, enum.Enum):
    """Vehicle ownership model."""

    OWNED = "OWNED"  # Organization owns the vehicle
    LEASED = "LEASED"  # Long-term lease agreement
    RENTED = "RENTED"  # Short-term rental


class AssignmentType(str, enum.Enum):
    """How vehicle is assigned for use."""

    PERSONAL = "PERSONAL"  # Assigned to specific employee
    DEPARTMENT = "DEPARTMENT"  # Assigned to department
    POOL = "POOL"  # Available for reservation by any employee


class MaintenanceType(str, enum.Enum):
    """Type of maintenance work."""

    PREVENTIVE = "PREVENTIVE"  # Scheduled service (oil change, etc.)
    CORRECTIVE = "CORRECTIVE"  # Repair to fix issue
    INSPECTION = "INSPECTION"  # Safety/regulatory inspection
    TIRE = "TIRE"  # Tire replacement/rotation
    BODY = "BODY"  # Body work/repairs
    ACCIDENT_REPAIR = "ACCIDENT_REPAIR"  # Post-accident repairs


class MaintenanceStatus(str, enum.Enum):
    """Maintenance record status."""

    SCHEDULED = "SCHEDULED"  # Planned, not started
    IN_PROGRESS = "IN_PROGRESS"  # Work ongoing
    COMPLETED = "COMPLETED"  # Work done
    CANCELLED = "CANCELLED"  # Cancelled


class IncidentType(str, enum.Enum):
    """Vehicle incident classification."""

    ACCIDENT = "ACCIDENT"  # Collision/crash
    THEFT = "THEFT"  # Vehicle stolen
    VANDALISM = "VANDALISM"  # Intentional damage
    BREAKDOWN = "BREAKDOWN"  # Mechanical failure
    TRAFFIC_VIOLATION = "TRAFFIC_VIOLATION"  # Speeding, parking, etc.
    OTHER = "OTHER"


class IncidentSeverity(str, enum.Enum):
    """Incident severity level."""

    MINOR = "MINOR"  # Cosmetic damage, no injury
    MODERATE = "MODERATE"  # Repairable damage, minor injury
    MAJOR = "MAJOR"  # Significant damage, injury
    TOTAL_LOSS = "TOTAL_LOSS"  # Vehicle write-off


class IncidentStatus(str, enum.Enum):
    """Incident investigation status."""

    REPORTED = "REPORTED"  # Initial report filed
    INVESTIGATING = "INVESTIGATING"  # Under investigation
    INSURANCE_FILED = "INSURANCE_FILED"  # Insurance claim submitted
    RESOLVED = "RESOLVED"  # Resolution complete
    CLOSED = "CLOSED"  # Case closed


class DocumentType(str, enum.Enum):
    """Vehicle document classification."""

    REGISTRATION = "REGISTRATION"  # Vehicle registration certificate
    INSURANCE = "INSURANCE"  # Insurance policy
    INSPECTION = "INSPECTION"  # Safety inspection certificate
    ROAD_WORTHINESS = "ROAD_WORTHINESS"  # Roadworthiness certificate
    PERMIT = "PERMIT"  # Operating permit
    LICENSE = "LICENSE"  # Driver/operator license
    OTHER = "OTHER"


class ReservationStatus(str, enum.Enum):
    """Pool vehicle reservation status."""

    PENDING = "PENDING"  # Awaiting approval
    APPROVED = "APPROVED"  # Approved, not yet started
    REJECTED = "REJECTED"  # Request rejected
    ACTIVE = "ACTIVE"  # Currently in use
    COMPLETED = "COMPLETED"  # Trip completed
    CANCELLED = "CANCELLED"  # Cancelled by requester
    NO_SHOW = "NO_SHOW"  # Requester didn't pick up


class DisposalMethod(str, enum.Enum):
    """Vehicle disposal method."""

    SOLD = "SOLD"  # Sold to third party
    SCRAPPED = "SCRAPPED"  # Sent for scrap/recycling
    TRADED_IN = "TRADED_IN"  # Traded in for new vehicle
    RETURNED = "RETURNED"  # Returned to lessor (for leased vehicles)
    DONATED = "DONATED"  # Donated to charity/organization
    TRANSFERRED = "TRANSFERRED"  # Transferred to another entity
