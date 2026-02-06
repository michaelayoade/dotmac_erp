"""
Ticket Service - Support Module.

Core business logic for ticket management including CRUD operations,
status transitions, and assignment handling.
"""

import logging
from datetime import date
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.expense.expense_claim import ExpenseClaim
from app.models.people.hr import Employee
from app.models.support.ticket import Ticket, TicketPriority, TicketStatus
from app.services.common import coerce_uuid
from app.services.notification import notification_service
from app.services.support.comment import comment_service

logger = logging.getLogger(__name__)


class TicketService:
    """Service for ticket management operations."""

    # Valid status transitions
    STATUS_TRANSITIONS = {
        TicketStatus.OPEN: [
            TicketStatus.REPLIED,
            TicketStatus.ON_HOLD,
            TicketStatus.RESOLVED,
            TicketStatus.CLOSED,
        ],
        TicketStatus.REPLIED: [
            TicketStatus.OPEN,
            TicketStatus.ON_HOLD,
            TicketStatus.RESOLVED,
            TicketStatus.CLOSED,
        ],
        TicketStatus.ON_HOLD: [
            TicketStatus.OPEN,
            TicketStatus.REPLIED,
            TicketStatus.RESOLVED,
            TicketStatus.CLOSED,
        ],
        TicketStatus.RESOLVED: [TicketStatus.OPEN, TicketStatus.CLOSED],
        TicketStatus.CLOSED: [TicketStatus.OPEN],  # Can reopen
    }

    def list_tickets(
        self,
        db: Session,
        organization_id: UUID,
        *,
        status: Optional[str] = None,
        priority: Optional[str] = None,
        assigned_to_id: Optional[UUID] = None,
        category_id: Optional[UUID] = None,
        team_id: Optional[UUID] = None,
        search: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        include_deleted: bool = False,
        page: int = 1,
        per_page: int = 50,
    ) -> Tuple[List[Ticket], int]:
        """
        List tickets with filtering and pagination.

        Returns:
            Tuple of (tickets list, total count)
        """
        org_id = coerce_uuid(organization_id)

        # Base query with eager loading
        query = (
            select(Ticket)
            .where(Ticket.organization_id == org_id)
            .options(
                joinedload(Ticket.raised_by),
                joinedload(Ticket.assigned_to),
                joinedload(Ticket.project),
            )
        )

        # Exclude soft-deleted by default
        if not include_deleted:
            query = query.where(Ticket.is_deleted == False)  # noqa: E712

        # Apply filters
        if status:
            try:
                status_enum = TicketStatus(status.upper())
                query = query.where(Ticket.status == status_enum)
            except ValueError:
                pass  # Invalid status, ignore filter

        if priority:
            try:
                priority_enum = TicketPriority(priority.upper())
                query = query.where(Ticket.priority == priority_enum)
            except ValueError:
                pass

        if assigned_to_id:
            query = query.where(Ticket.assigned_to_id == assigned_to_id)

        if category_id:
            query = query.where(Ticket.category_id == coerce_uuid(category_id))

        if team_id:
            query = query.where(Ticket.team_id == coerce_uuid(team_id))

        if search:
            search_term = f"%{search}%"
            query = query.where(
                or_(
                    Ticket.ticket_number.ilike(search_term),
                    Ticket.subject.ilike(search_term),
                    Ticket.description.ilike(search_term),
                    Ticket.raised_by_email.ilike(search_term),
                )
            )

        if date_from:
            query = query.where(Ticket.opening_date >= date_from)

        if date_to:
            query = query.where(Ticket.opening_date <= date_to)

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total = db.scalar(count_query) or 0

        # Apply ordering and pagination
        query = query.order_by(Ticket.opening_date.desc(), Ticket.created_at.desc())
        offset = (page - 1) * per_page
        query = query.offset(offset).limit(per_page)

        tickets = list(db.execute(query).scalars().unique().all())

        return tickets, total

    def list_archived_tickets(
        self,
        db: Session,
        organization_id: UUID,
        *,
        search: Optional[str] = None,
        page: int = 1,
        per_page: int = 50,
    ) -> Tuple[List[Ticket], int]:
        """
        List archived (soft-deleted) tickets with pagination.

        Returns:
            Tuple of (tickets list, total count)
        """
        org_id = coerce_uuid(organization_id)

        # Query for archived tickets only
        query = (
            select(Ticket)
            .where(
                Ticket.organization_id == org_id,
                Ticket.is_deleted == True,  # noqa: E712
            )
            .options(
                joinedload(Ticket.raised_by),
                joinedload(Ticket.assigned_to),
            )
        )

        # Apply search filter
        if search:
            search_term = f"%{search}%"
            query = query.where(
                or_(
                    Ticket.ticket_number.ilike(search_term),
                    Ticket.subject.ilike(search_term),
                )
            )

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total = db.scalar(count_query) or 0

        # Apply ordering and pagination
        query = query.order_by(Ticket.updated_at.desc())
        offset = (page - 1) * per_page
        query = query.offset(offset).limit(per_page)

        tickets = list(db.execute(query).scalars().unique().all())

        return tickets, total

    def get_ticket(
        self,
        db: Session,
        organization_id: UUID,
        ticket_id: UUID,
    ) -> Optional[Ticket]:
        """Get a single ticket by ID with all relationships loaded."""
        org_id = coerce_uuid(organization_id)
        tid = coerce_uuid(ticket_id)

        query = (
            select(Ticket)
            .where(
                Ticket.organization_id == org_id,
                Ticket.ticket_id == tid,
            )
            .options(
                joinedload(Ticket.raised_by),
                joinedload(Ticket.assigned_to),
                joinedload(Ticket.project),
                joinedload(Ticket.organization),
            )
        )

        return db.execute(query).scalar_one_or_none()

    def get_ticket_by_number(
        self,
        db: Session,
        organization_id: UUID,
        ticket_number: str,
    ) -> Optional[Ticket]:
        """Get a ticket by its ticket number."""
        org_id = coerce_uuid(organization_id)

        return db.execute(
            select(Ticket).where(
                Ticket.organization_id == org_id,
                Ticket.ticket_number == ticket_number,
            )
        ).scalar_one_or_none()

    def create_ticket(
        self,
        db: Session,
        organization_id: UUID,
        user_id: UUID,
        *,
        subject: str,
        description: Optional[str] = None,
        priority: str = "MEDIUM",
        raised_by_email: Optional[str] = None,
        raised_by_id: Optional[UUID] = None,
        assigned_to_id: Optional[UUID] = None,
        project_id: Optional[UUID] = None,
        customer_id: Optional[UUID] = None,
        category_id: Optional[UUID] = None,
        team_id: Optional[UUID] = None,
        opening_date: Optional[date] = None,
        contact_email: Optional[str] = None,
        contact_phone: Optional[str] = None,
        contact_address: Optional[str] = None,
    ) -> Ticket:
        """
        Create a new support ticket.

        For manually created tickets (not synced from ERPNext).
        """
        org_id = coerce_uuid(organization_id)
        uid = coerce_uuid(user_id)

        # Generate ticket number
        ticket_number = self._generate_ticket_number(db, org_id)

        # Map priority
        try:
            priority_enum = TicketPriority(priority.upper())
        except ValueError:
            priority_enum = TicketPriority.MEDIUM

        ticket = Ticket(
            organization_id=org_id,
            ticket_number=ticket_number,
            subject=subject[:255],
            description=description,
            status=TicketStatus.OPEN,
            priority=priority_enum,
            raised_by_email=raised_by_email,
            raised_by_id=coerce_uuid(raised_by_id) if raised_by_id else None,
            assigned_to_id=coerce_uuid(assigned_to_id) if assigned_to_id else None,
            project_id=coerce_uuid(project_id) if project_id else None,
            customer_id=coerce_uuid(customer_id) if customer_id else None,
            category_id=coerce_uuid(category_id) if category_id else None,
            team_id=coerce_uuid(team_id) if team_id else None,
            opening_date=opening_date or date.today(),
            created_by_id=uid,
            contact_email=contact_email,
            contact_phone=contact_phone,
            contact_address=contact_address,
        )

        db.add(ticket)
        db.flush()

        logger.info(f"Created ticket {ticket_number} for org {org_id}")
        return ticket

    def update_ticket(
        self,
        db: Session,
        organization_id: UUID,
        ticket_id: UUID,
        user_id: UUID,
        *,
        subject: Optional[str] = None,
        description: Optional[str] = None,
        priority: Optional[str] = None,
        raised_by_email: Optional[str] = None,
        raised_by_id: Optional[UUID] = None,
        assigned_to_id: Optional[UUID] = None,
        project_id: Optional[UUID] = None,
        customer_id: Optional[UUID] = None,
        category_id: Optional[UUID] = None,
        team_id: Optional[UUID] = None,
        contact_email: Optional[str] = None,
        contact_phone: Optional[str] = None,
        contact_address: Optional[str] = None,
    ) -> Optional[Ticket]:
        """Update ticket details (not status - use update_status for that)."""
        ticket = self.get_ticket(db, organization_id, ticket_id)
        if not ticket:
            return None

        uid = coerce_uuid(user_id)

        if subject is not None:
            ticket.subject = subject[:255]
        if description is not None:
            ticket.description = description

        # Track priority change for logging
        if priority is not None:
            try:
                new_priority = TicketPriority(priority.upper())
                if ticket.priority != new_priority:
                    old_priority = ticket.priority.value if ticket.priority else "None"
                    ticket.priority = new_priority
                    comment_service.log_priority_change(
                        db,
                        ticket_id=ticket.ticket_id,
                        old_priority=old_priority,
                        new_priority=new_priority.value,
                        author_id=uid,
                    )
            except ValueError:
                pass

        if raised_by_email is not None:
            ticket.raised_by_email = raised_by_email
        if raised_by_id is not None:
            ticket.raised_by_id = coerce_uuid(raised_by_id) if raised_by_id else None
        if assigned_to_id is not None:
            ticket.assigned_to_id = (
                coerce_uuid(assigned_to_id) if assigned_to_id else None
            )
        if project_id is not None:
            ticket.project_id = coerce_uuid(project_id) if project_id else None
        if customer_id is not None:
            ticket.customer_id = coerce_uuid(customer_id) if customer_id else None

        # Contact info fields (can be auto-populated from customer or manually entered)
        if contact_email is not None:
            ticket.contact_email = contact_email if contact_email else None
        if contact_phone is not None:
            ticket.contact_phone = contact_phone if contact_phone else None
        if contact_address is not None:
            ticket.contact_address = contact_address if contact_address else None

        # Track category change for logging
        if category_id is not None:
            from app.models.support.category import TicketCategory

            org_id = coerce_uuid(organization_id)
            old_category_name = None
            if ticket.category_id:
                old_cat = (
                    db.query(TicketCategory)
                    .filter(
                        TicketCategory.category_id == ticket.category_id,
                        TicketCategory.organization_id == org_id,
                    )
                    .first()
                )
                if old_cat:
                    old_category_name = old_cat.category_name

            new_cat_id = coerce_uuid(category_id) if category_id else None
            if ticket.category_id != new_cat_id:
                ticket.category_id = new_cat_id
                new_category_name = None
                if new_cat_id:
                    new_cat = (
                        db.query(TicketCategory)
                        .filter(
                            TicketCategory.category_id == new_cat_id,
                            TicketCategory.organization_id == org_id,
                        )
                        .first()
                    )
                    if new_cat:
                        new_category_name = new_cat.category_name
                if new_category_name:
                    comment_service.log_category_change(
                        db,
                        ticket_id=ticket.ticket_id,
                        old_category=old_category_name,
                        new_category=new_category_name,
                        author_id=uid,
                    )

        # Track team change for logging
        if team_id is not None:
            from app.models.support.team import SupportTeam

            org_id = coerce_uuid(organization_id)
            old_team_name = None
            if ticket.team_id:
                old_team = (
                    db.query(SupportTeam)
                    .filter(
                        SupportTeam.team_id == ticket.team_id,
                        SupportTeam.organization_id == org_id,
                    )
                    .first()
                )
                if old_team:
                    old_team_name = old_team.team_name

            new_team_id = coerce_uuid(team_id) if team_id else None
            if ticket.team_id != new_team_id:
                ticket.team_id = new_team_id
                new_team_name = None
                if new_team_id:
                    new_team = (
                        db.query(SupportTeam)
                        .filter(
                            SupportTeam.team_id == new_team_id,
                            SupportTeam.organization_id == org_id,
                        )
                        .first()
                    )
                    if new_team:
                        new_team_name = new_team.team_name
                if new_team_name:
                    comment_service.log_team_change(
                        db,
                        ticket_id=ticket.ticket_id,
                        old_team=old_team_name,
                        new_team=new_team_name,
                        author_id=uid,
                    )

        ticket.updated_by_id = uid
        db.flush()

        return ticket

    def update_status(
        self,
        db: Session,
        organization_id: UUID,
        ticket_id: UUID,
        user_id: UUID,
        new_status: str,
        notes: Optional[str] = None,
    ) -> Tuple[Optional[Ticket], Optional[str]]:
        """
        Update ticket status with validation.

        Returns:
            Tuple of (updated ticket, error message if any)
        """
        ticket = self.get_ticket(db, organization_id, ticket_id)
        if not ticket:
            return None, "Ticket not found"

        try:
            new_status_enum = TicketStatus(new_status.upper())
        except ValueError:
            return None, f"Invalid status: {new_status}"

        # Validate transition
        allowed = self.STATUS_TRANSITIONS.get(ticket.status, [])
        if new_status_enum not in allowed:
            return (
                None,
                f"Cannot transition from {ticket.status.value} to {new_status_enum.value}",
            )

        old_status = ticket.status
        ticket.status = new_status_enum
        ticket.updated_by_id = coerce_uuid(user_id)

        # Handle resolution
        if new_status_enum == TicketStatus.RESOLVED:
            ticket.resolution_date = date.today()
            if notes:
                ticket.resolution = notes
        elif new_status_enum == TicketStatus.CLOSED and not ticket.resolution_date:
            ticket.resolution_date = date.today()

        db.flush()
        logger.info(
            f"Ticket {ticket.ticket_number} status changed from {old_status.value} to {new_status_enum.value}"
        )

        # Log activity
        comment_service.log_status_change(
            db,
            ticket_id=ticket.ticket_id,
            old_status=old_status.value,
            new_status=new_status_enum.value,
            author_id=coerce_uuid(user_id),
            notes=notes,
        )

        # Send notifications
        uid = coerce_uuid(user_id)

        # Notify assignee of status change (if not self)
        if ticket.assigned_to_id and ticket.assigned_to_id != uid:
            assignee = db.get(Employee, ticket.assigned_to_id)
            if assignee and assignee.person_id:
                notification_service.notify_ticket_status_change(
                    db,
                    organization_id=ticket.organization_id,
                    ticket_id=ticket.ticket_id,
                    ticket_number=ticket.ticket_number,
                    recipient_id=assignee.person_id,
                    old_status=old_status.value,
                    new_status=new_status_enum.value,
                    actor_id=uid,
                )

        # For resolved tickets, notify the person who raised it
        if new_status_enum == TicketStatus.RESOLVED and ticket.raised_by_id:
            raiser = db.get(Employee, ticket.raised_by_id)
            if raiser and raiser.person_id:
                # Get resolver name
                resolver_employee = None
                for emp in (
                    db.execute(select(Employee).where(Employee.person_id == uid))
                    .scalars()
                    .all()
                ):
                    resolver_employee = emp
                    break

                resolver_name = (
                    resolver_employee.full_name if resolver_employee else "Support"
                )
                notification_service.notify_ticket_resolved(
                    db,
                    organization_id=ticket.organization_id,
                    ticket_id=ticket.ticket_id,
                    ticket_number=ticket.ticket_number,
                    ticket_subject=ticket.subject,
                    recipient_id=raiser.person_id,
                    resolver_name=resolver_name,
                    actor_id=uid,
                )

        return ticket, None

    def assign_ticket(
        self,
        db: Session,
        organization_id: UUID,
        ticket_id: UUID,
        user_id: UUID,
        assigned_to_id: UUID,
    ) -> Optional[Ticket]:
        """Assign ticket to an employee."""
        ticket = self.get_ticket(db, organization_id, ticket_id)
        if not ticket:
            return None

        # Capture old assignee for logging
        old_assignee_name = None
        if ticket.assigned_to_id:
            old_assignee = db.get(Employee, ticket.assigned_to_id)
            if old_assignee:
                old_assignee_name = old_assignee.full_name

        ticket.assigned_to_id = coerce_uuid(assigned_to_id)
        ticket.updated_by_id = coerce_uuid(user_id)
        db.flush()

        # Get new assignee name for logging
        new_assignee = db.get(Employee, coerce_uuid(assigned_to_id))
        new_assignee_name = (
            new_assignee.full_name if new_assignee else str(assigned_to_id)
        )

        # Log activity
        comment_service.log_assignment(
            db,
            ticket_id=ticket.ticket_id,
            assignee_name=new_assignee_name,
            previous_assignee=old_assignee_name,
            author_id=coerce_uuid(user_id),
        )

        # Send notification to the assignee
        if new_assignee and new_assignee.person_id:
            notification_service.notify_ticket_assigned(
                db,
                organization_id=ticket.organization_id,
                ticket_id=ticket.ticket_id,
                ticket_number=ticket.ticket_number,
                ticket_subject=ticket.subject,
                assignee_id=new_assignee.person_id,
                actor_id=coerce_uuid(user_id),
            )

        return ticket

    def resolve_ticket(
        self,
        db: Session,
        organization_id: UUID,
        ticket_id: UUID,
        user_id: UUID,
        resolution: str,
    ) -> Tuple[Optional[Ticket], Optional[str]]:
        """
        Mark ticket as resolved with resolution notes.

        Convenience method that combines update_status with resolution.
        """
        ticket = self.get_ticket(db, organization_id, ticket_id)
        if not ticket:
            return None, "Ticket not found"

        ticket.resolution = resolution
        return self.update_status(
            db, organization_id, ticket_id, user_id, "RESOLVED", notes=resolution
        )

    def get_linked_expenses(
        self,
        db: Session,
        organization_id: UUID,
        ticket_id: UUID,
    ) -> List[ExpenseClaim]:
        """Get all expense claims linked to this ticket."""
        org_id = coerce_uuid(organization_id)
        tid = coerce_uuid(ticket_id)

        return list(
            db.execute(
                select(ExpenseClaim)
                .where(
                    ExpenseClaim.organization_id == org_id,
                    ExpenseClaim.ticket_id == tid,
                )
                .order_by(ExpenseClaim.claim_date.desc())
            )
            .scalars()
            .all()
        )

    def search_tickets(
        self,
        db: Session,
        organization_id: UUID,
        query: str,
        *,
        status_filter: Optional[List[str]] = None,
        limit: int = 20,
    ) -> List[Ticket]:
        """
        Search tickets for typeahead/autocomplete.

        Returns minimal ticket info for quick selection.
        """
        org_id = coerce_uuid(organization_id)
        search_term = f"%{query}%"

        q = select(Ticket).where(
            Ticket.organization_id == org_id,
            or_(
                Ticket.ticket_number.ilike(search_term),
                Ticket.subject.ilike(search_term),
            ),
        )

        if status_filter:
            status_enums = []
            for s in status_filter:
                try:
                    status_enums.append(TicketStatus(s.upper()))
                except ValueError:
                    pass
            if status_enums:
                q = q.where(Ticket.status.in_(status_enums))

        q = q.order_by(Ticket.opening_date.desc()).limit(limit)

        return list(db.execute(q).scalars().all())

    def get_stats(
        self,
        db: Session,
        organization_id: UUID,
    ) -> Dict[str, Any]:
        """Get ticket statistics for dashboard."""
        org_id = coerce_uuid(organization_id)

        # Total tickets (excluding deleted)
        not_deleted = Ticket.is_deleted == False  # noqa: E712

        total = (
            db.scalar(
                select(func.count(Ticket.ticket_id)).where(
                    Ticket.organization_id == org_id, not_deleted
                )
            )
            or 0
        )

        # By status
        open_count = (
            db.scalar(
                select(func.count(Ticket.ticket_id)).where(
                    Ticket.organization_id == org_id,
                    Ticket.status == TicketStatus.OPEN,
                    not_deleted,
                )
            )
            or 0
        )

        on_hold = (
            db.scalar(
                select(func.count(Ticket.ticket_id)).where(
                    Ticket.organization_id == org_id,
                    Ticket.status == TicketStatus.ON_HOLD,
                    not_deleted,
                )
            )
            or 0
        )

        resolved = (
            db.scalar(
                select(func.count(Ticket.ticket_id)).where(
                    Ticket.organization_id == org_id,
                    Ticket.status == TicketStatus.RESOLVED,
                    not_deleted,
                )
            )
            or 0
        )

        closed = (
            db.scalar(
                select(func.count(Ticket.ticket_id)).where(
                    Ticket.organization_id == org_id,
                    Ticket.status == TicketStatus.CLOSED,
                    not_deleted,
                )
            )
            or 0
        )

        # Urgent tickets
        urgent = (
            db.scalar(
                select(func.count(Ticket.ticket_id)).where(
                    Ticket.organization_id == org_id,
                    Ticket.priority == TicketPriority.URGENT,
                    Ticket.status.in_(
                        [TicketStatus.OPEN, TicketStatus.REPLIED, TicketStatus.ON_HOLD]
                    ),
                    not_deleted,
                )
            )
            or 0
        )

        # Unassigned
        unassigned = (
            db.scalar(
                select(func.count(Ticket.ticket_id)).where(
                    Ticket.organization_id == org_id,
                    Ticket.assigned_to_id.is_(None),
                    Ticket.status.in_([TicketStatus.OPEN, TicketStatus.REPLIED]),
                    not_deleted,
                )
            )
            or 0
        )

        return {
            "total": total,
            "open": open_count,
            "on_hold": on_hold,
            "resolved": resolved,
            "closed": closed,
            "urgent": urgent,
            "unassigned": unassigned,
            "active": open_count + on_hold,  # Active = needs attention
        }

    def delete_ticket(
        self,
        db: Session,
        organization_id: UUID,
        ticket_id: UUID,
        user_id: UUID,
        hard_delete: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        """
        Delete a ticket (soft delete by default).

        Args:
            db: Database session
            organization_id: Organization UUID
            ticket_id: Ticket UUID
            user_id: User performing the action
            hard_delete: If true, permanently delete the ticket

        Returns:
            (success, error_message)
        """
        ticket = self.get_ticket(db, organization_id, ticket_id)
        if not ticket:
            return False, "Ticket not found"

        if hard_delete:
            # Delete all related records first
            from app.models.support.attachment import TicketAttachment
            from app.models.support.comment import TicketComment

            db.execute(
                select(TicketComment).where(TicketComment.ticket_id == ticket.ticket_id)
            )
            for comment in (
                db.execute(
                    select(TicketComment).where(
                        TicketComment.ticket_id == ticket.ticket_id
                    )
                )
                .scalars()
                .all()
            ):
                db.delete(comment)

            for attachment in (
                db.execute(
                    select(TicketAttachment).where(
                        TicketAttachment.ticket_id == ticket.ticket_id
                    )
                )
                .scalars()
                .all()
            ):
                db.delete(attachment)

            db.delete(ticket)
            logger.info(f"Hard deleted ticket {ticket.ticket_number}")
        else:
            ticket.is_deleted = True
            ticket.updated_by_id = coerce_uuid(user_id)
            db.flush()
            logger.info(f"Soft deleted ticket {ticket.ticket_number}")

        return True, None

    def restore_ticket(
        self,
        db: Session,
        organization_id: UUID,
        ticket_id: UUID,
        user_id: UUID,
    ) -> Optional[Ticket]:
        """Restore a soft-deleted ticket."""
        org_id = coerce_uuid(organization_id)
        tid = coerce_uuid(ticket_id)

        # Get ticket including deleted ones
        ticket = db.execute(
            select(Ticket).where(
                Ticket.organization_id == org_id,
                Ticket.ticket_id == tid,
            )
        ).scalar_one_or_none()

        if not ticket:
            return None

        ticket.is_deleted = False
        ticket.updated_by_id = coerce_uuid(user_id)
        db.flush()

        logger.info(f"Restored ticket {ticket.ticket_number}")
        return ticket

    def _generate_ticket_number(self, db: Session, organization_id: UUID) -> str:
        """Generate a unique ticket number for manual tickets."""
        from app.models.finance.core_config import SequenceType
        from app.services.finance.common.numbering import SyncNumberingService

        numbering_service = SyncNumberingService(db)
        return numbering_service.generate_next_number(
            organization_id=organization_id,
            sequence_type=SequenceType.SUPPORT_TICKET,
        )

    # =========================================================================
    # Bulk Operations
    # =========================================================================

    def bulk_update_status(
        self,
        db: Session,
        organization_id: UUID,
        ticket_ids: List[UUID],
        user_id: UUID,
        *,
        new_status: str,
        notes: Optional[str] = None,
    ) -> Dict[str, int]:
        """Update status for multiple tickets at once."""
        org_id = coerce_uuid(organization_id)
        uid = coerce_uuid(user_id)

        success_count = 0
        error_count = 0

        for tid in ticket_ids:
            try:
                result = self.update_status(
                    db,
                    org_id,
                    tid,
                    uid,
                    new_status=new_status,
                    notes=notes,
                )
                if result:
                    success_count += 1
                else:
                    error_count += 1
            except Exception as e:
                logger.warning(f"Failed to update status for ticket {tid}: {e}")
                error_count += 1

        db.flush()
        return {"success": success_count, "error": error_count}

    def bulk_assign(
        self,
        db: Session,
        organization_id: UUID,
        ticket_ids: List[UUID],
        user_id: UUID,
        *,
        assigned_to_id: UUID,
    ) -> Dict[str, int]:
        """Assign multiple tickets to an employee."""
        org_id = coerce_uuid(organization_id)
        uid = coerce_uuid(user_id)
        assignee_id = coerce_uuid(assigned_to_id)

        success_count = 0
        error_count = 0

        for tid in ticket_ids:
            try:
                result = self.assign_ticket(db, org_id, tid, uid, assignee_id)
                if result:
                    success_count += 1
                else:
                    error_count += 1
            except Exception as e:
                logger.warning(f"Failed to assign ticket {tid}: {e}")
                error_count += 1

        db.flush()
        return {"success": success_count, "error": error_count}

    def bulk_archive(
        self,
        db: Session,
        organization_id: UUID,
        ticket_ids: List[UUID],
        user_id: UUID,
    ) -> Dict[str, int]:
        """Archive multiple tickets at once."""
        org_id = coerce_uuid(organization_id)
        uid = coerce_uuid(user_id)

        success_count = 0
        error_count = 0

        for tid in ticket_ids:
            try:
                result = self.delete_ticket(db, org_id, tid, uid, hard_delete=False)
                if result:
                    success_count += 1
                else:
                    error_count += 1
            except Exception as e:
                logger.warning(f"Failed to archive ticket {tid}: {e}")
                error_count += 1

        db.flush()
        return {"success": success_count, "error": error_count}


# Singleton instance
ticket_service = TicketService()
