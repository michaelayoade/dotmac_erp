"""
Document Service - Vehicle document management.

Handles document tracking, expiry monitoring, and reminders.
"""

import logging
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.fleet.enums import DocumentType
from app.models.fleet.vehicle import Vehicle
from app.models.fleet.vehicle_document import VehicleDocument
from app.schemas.fleet.document import DocumentCreate, DocumentUpdate
from app.services.common import (
    NotFoundError,
    PaginatedResult,
    PaginationParams,
    paginate,
)

logger = logging.getLogger(__name__)


class DocumentService:
    """Service for vehicle document operations."""

    def __init__(self, db: Session, organization_id: UUID):
        self.db = db
        self.organization_id = organization_id

    def get_by_id(self, document_id: UUID) -> VehicleDocument | None:
        """Get document by ID."""
        return self.db.get(VehicleDocument, document_id)

    def get_or_raise(self, document_id: UUID) -> VehicleDocument:
        """Get document or raise NotFoundError."""
        doc = self.get_by_id(document_id)
        if not doc or doc.organization_id != self.organization_id:
            raise NotFoundError(f"Document {document_id} not found")
        return doc

    def list_documents(
        self,
        *,
        vehicle_id: UUID | None = None,
        document_type: DocumentType | None = None,
        expired_only: bool = False,
        expiring_soon: bool = False,
        params: PaginationParams | None = None,
    ) -> PaginatedResult[VehicleDocument]:
        """List documents with filtering."""
        stmt = (
            select(VehicleDocument)
            .where(VehicleDocument.organization_id == self.organization_id)
            .options(selectinload(VehicleDocument.vehicle))
            .order_by(VehicleDocument.expiry_date.asc().nullslast())
        )

        if vehicle_id:
            stmt = stmt.where(VehicleDocument.vehicle_id == vehicle_id)

        if document_type:
            stmt = stmt.where(VehicleDocument.document_type == document_type)

        if expired_only:
            stmt = stmt.where(VehicleDocument.expiry_date < date.today())

        if expiring_soon:
            stmt = stmt.where(
                VehicleDocument.expiry_date.isnot(None),
                VehicleDocument.expiry_date >= date.today(),
                VehicleDocument.expiry_date <= date.today() + timedelta(days=30),
            )

        return paginate(self.db, stmt, params)

    def get_expiring_documents(
        self, days_before: int = 30, *, limit: int | None = None
    ) -> list[VehicleDocument]:
        """Get documents expiring within specified days."""
        cutoff = date.today() + timedelta(days=days_before)
        stmt = (
            select(VehicleDocument)
            .where(
                VehicleDocument.organization_id == self.organization_id,
                VehicleDocument.expiry_date.isnot(None),
                VehicleDocument.expiry_date >= date.today(),
                VehicleDocument.expiry_date <= cutoff,
                VehicleDocument.reminder_sent == False,  # noqa: E712
            )
            .options(selectinload(VehicleDocument.vehicle))
            .order_by(VehicleDocument.expiry_date.asc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all())

    def get_expired_documents(
        self, *, limit: int | None = None
    ) -> list[VehicleDocument]:
        """Get all expired documents."""
        stmt = (
            select(VehicleDocument)
            .where(
                VehicleDocument.organization_id == self.organization_id,
                VehicleDocument.expiry_date < date.today(),
            )
            .options(selectinload(VehicleDocument.vehicle))
            .order_by(VehicleDocument.expiry_date.asc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all())

    def create(self, data: DocumentCreate) -> VehicleDocument:
        """Create a new document record."""
        # Verify vehicle exists
        vehicle = self.db.get(Vehicle, data.vehicle_id)
        if not vehicle or vehicle.organization_id != self.organization_id:
            raise NotFoundError(f"Vehicle {data.vehicle_id} not found")

        doc = VehicleDocument(
            organization_id=self.organization_id,
            **data.model_dump(),
        )

        self.db.add(doc)
        self.db.flush()

        logger.info(
            "Created document for vehicle %s: %s",
            vehicle.vehicle_code,
            data.document_type.value,
        )
        return doc

    def update(self, document_id: UUID, data: DocumentUpdate) -> VehicleDocument:
        """Update a document record."""
        doc = self.get_or_raise(document_id)

        update_data = data.model_dump(exclude_unset=True)

        # Reset reminder_sent if expiry_date changes
        if "expiry_date" in update_data:
            doc.reminder_sent = False

        for field, value in update_data.items():
            setattr(doc, field, value)

        logger.info("Updated document %s", document_id)
        return doc

    def delete(self, document_id: UUID) -> None:
        """Delete a document record."""
        doc = self.get_or_raise(document_id)
        self.db.delete(doc)
        logger.info("Deleted document %s", document_id)

    def mark_reminder_sent(self, document_id: UUID) -> VehicleDocument:
        """Mark that expiry reminder has been sent."""
        doc = self.get_or_raise(document_id)
        doc.reminder_sent = True
        return doc

    def get_fleet_managers(self) -> list[UUID]:
        """Get list of fleet manager user IDs scoped to the current organization."""
        # Import here to avoid circular imports
        from app.models.person import Person
        from app.models.rbac import PersonRole, Role

        stmt = (
            select(PersonRole.person_id)
            .join(Role, PersonRole.role_id == Role.id)
            .join(Person, PersonRole.person_id == Person.id)
            .where(
                Person.organization_id == self.organization_id,
                Role.name.in_(["fleet_manager", "operations_manager", "admin"]),
                Role.is_active.is_(True),
            )
        )
        return list(self.db.scalars(stmt).all())
