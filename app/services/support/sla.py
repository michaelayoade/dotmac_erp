"""
SLA Service - Support Module.

Calculates SLA metrics, breach detection, and reporting for support tickets.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models.support.category import TicketCategory
from app.models.support.comment import CommentType, TicketComment
from app.models.support.team import SupportTeam
from app.models.support.ticket import Ticket, TicketPriority, TicketStatus
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)


# Default SLA hours if not configured anywhere
DEFAULT_RESPONSE_HOURS = 24
DEFAULT_RESOLUTION_HOURS = 72


@dataclass
class SLATarget:
    """SLA targets for a ticket."""

    response_hours: int
    resolution_hours: int
    source: str  # 'category', 'team', or 'default'


@dataclass
class TicketSLAStatus:
    """SLA status for a single ticket."""

    ticket_id: UUID
    ticket_number: str
    subject: str
    status: TicketStatus
    priority: TicketPriority
    created_at: datetime

    # Response SLA
    response_target_hours: int
    response_due_at: datetime
    first_response_at: datetime | None
    response_hours: float | None  # Actual response time in hours
    response_breached: bool
    response_breach_hours: float | None  # How many hours overdue

    # Resolution SLA
    resolution_target_hours: int
    resolution_due_at: datetime
    resolved_at: datetime | None
    resolution_hours: float | None  # Actual resolution time in hours
    resolution_breached: bool
    resolution_breach_hours: float | None  # How many hours overdue

    # Metadata
    category_name: str | None
    team_name: str | None
    assigned_to_name: str | None


@dataclass
class SLAMetrics:
    """Aggregated SLA metrics for a time period."""

    # Totals
    total_tickets: int
    resolved_tickets: int
    open_tickets: int

    # Response SLA
    response_met: int  # Tickets that met response SLA
    response_breached: int  # Tickets that breached response SLA
    response_pending: int  # Open tickets not yet responded
    response_compliance_pct: float  # Percentage meeting SLA
    avg_response_hours: float | None

    # Resolution SLA
    resolution_met: int
    resolution_breached: int
    resolution_pending: int  # Open tickets
    resolution_compliance_pct: float
    avg_resolution_hours: float | None

    # By priority breakdown
    by_priority: dict[str, dict[str, Any]]

    # By team breakdown
    by_team: dict[str, dict[str, Any]]

    # By category breakdown
    by_category: dict[str, dict[str, Any]]


@dataclass
class AgingBucket:
    """Aging bucket for ticket age analysis."""

    label: str
    min_hours: int
    max_hours: int | None
    count: int
    tickets: list[dict[str, Any]]


class SLAService:
    """Service for SLA calculations and reporting."""

    def get_sla_target(
        self,
        db: Session,
        ticket: Ticket,
    ) -> SLATarget:
        """
        Get SLA target for a ticket.

        Lookup order:
        1. Category SLA settings
        2. Team default SLA settings
        3. System defaults
        """
        response_hours = None
        resolution_hours = None
        source = "default"

        # Try category first
        if ticket.category_id:
            category = db.get(TicketCategory, ticket.category_id)
            if category:
                if category.response_hours:
                    response_hours = category.response_hours
                    source = "category"
                if category.resolution_hours:
                    resolution_hours = category.resolution_hours
                    if source != "category":
                        source = "category"

        # Fall back to team
        if ticket.team_id and (response_hours is None or resolution_hours is None):
            team = db.get(SupportTeam, ticket.team_id)
            if team:
                if response_hours is None and team.default_response_hours:
                    response_hours = team.default_response_hours
                    source = "team" if source == "default" else source
                if resolution_hours is None and team.default_resolution_hours:
                    resolution_hours = team.default_resolution_hours
                    source = "team" if source == "default" else source

        # System defaults
        if response_hours is None:
            response_hours = DEFAULT_RESPONSE_HOURS
        if resolution_hours is None:
            resolution_hours = DEFAULT_RESOLUTION_HOURS

        return SLATarget(
            response_hours=response_hours,
            resolution_hours=resolution_hours,
            source=source,
        )

    def get_first_response_time(
        self,
        db: Session,
        ticket_id: UUID,
    ) -> datetime | None:
        """
        Get the timestamp of the first response to a ticket.

        First response is when:
        1. Status changed to REPLIED (from system comment)
        2. Or first non-internal comment was added
        """
        tid = coerce_uuid(ticket_id)

        # Look for first status change to REPLIED
        status_change = db.execute(
            select(TicketComment.created_at)
            .where(
                TicketComment.ticket_id == tid,
                TicketComment.comment_type == CommentType.SYSTEM,
                TicketComment.action == "status_change",
                TicketComment.new_value == "REPLIED",
            )
            .order_by(TicketComment.created_at)
            .limit(1)
        ).scalar_one_or_none()

        if status_change:
            return status_change

        # Fall back to first non-internal comment
        first_comment = db.execute(
            select(TicketComment.created_at)
            .where(
                TicketComment.ticket_id == tid,
                TicketComment.comment_type == CommentType.COMMENT,
                TicketComment.is_internal == False,  # noqa: E712
                TicketComment.is_deleted == False,  # noqa: E712
            )
            .order_by(TicketComment.created_at)
            .limit(1)
        ).scalar_one_or_none()

        return first_comment

    @staticmethod
    def _get_sla_target_from_ticket(ticket: Ticket) -> SLATarget:
        """Get SLA target using already-loaded relationships (no DB queries)."""
        response_hours = None
        resolution_hours = None
        source = "default"

        if ticket.category:
            if ticket.category.response_hours:
                response_hours = ticket.category.response_hours
                source = "category"
            if ticket.category.resolution_hours:
                resolution_hours = ticket.category.resolution_hours
                if source != "category":
                    source = "category"

        if ticket.team and (response_hours is None or resolution_hours is None):
            if response_hours is None and ticket.team.default_response_hours:
                response_hours = ticket.team.default_response_hours
                source = "team" if source == "default" else source
            if resolution_hours is None and ticket.team.default_resolution_hours:
                resolution_hours = ticket.team.default_resolution_hours
                source = "team" if source == "default" else source

        return SLATarget(
            response_hours=response_hours or DEFAULT_RESPONSE_HOURS,
            resolution_hours=resolution_hours or DEFAULT_RESOLUTION_HOURS,
            source=source,
        )

    def _batch_first_response_times(
        self,
        db: Session,
        ticket_ids: list[UUID],
    ) -> dict[UUID, datetime]:
        """Get first response times for multiple tickets in 2 queries total."""
        if not ticket_ids:
            return {}

        # Query 1: first REPLIED status change per ticket
        status_changes = db.execute(
            select(
                TicketComment.ticket_id,
                func.min(TicketComment.created_at).label("first_at"),
            )
            .where(
                TicketComment.ticket_id.in_(ticket_ids),
                TicketComment.comment_type == CommentType.SYSTEM,
                TicketComment.action == "status_change",
                TicketComment.new_value == "REPLIED",
            )
            .group_by(TicketComment.ticket_id)
        ).all()
        result: dict[UUID, datetime] = {
            row.ticket_id: row.first_at for row in status_changes
        }

        # Query 2: first non-internal comment for tickets not yet found
        remaining = [tid for tid in ticket_ids if tid not in result]
        if remaining:
            first_comments = db.execute(
                select(
                    TicketComment.ticket_id,
                    func.min(TicketComment.created_at).label("first_at"),
                )
                .where(
                    TicketComment.ticket_id.in_(remaining),
                    TicketComment.comment_type == CommentType.COMMENT,
                    TicketComment.is_internal == False,  # noqa: E712
                    TicketComment.is_deleted == False,  # noqa: E712
                )
                .group_by(TicketComment.ticket_id)
            ).all()
            for row in first_comments:
                result[row.ticket_id] = row.first_at

        return result

    @staticmethod
    def _compute_sla_status(
        ticket: Ticket,
        sla_target: SLATarget,
        first_response: datetime | None,
        now: datetime,
    ) -> TicketSLAStatus:
        """Compute SLA status from preloaded data (no DB queries)."""
        response_due_at = ticket.created_at + timedelta(hours=sla_target.response_hours)
        response_hours = None
        response_breached = False
        response_breach_hours = None

        if first_response:
            response_hours = (first_response - ticket.created_at).total_seconds() / 3600
            response_breached = response_hours > sla_target.response_hours
            if response_breached:
                response_breach_hours = response_hours - sla_target.response_hours
        else:
            if now > response_due_at and ticket.status not in (
                TicketStatus.RESOLVED,
                TicketStatus.CLOSED,
            ):
                response_breached = True
                response_breach_hours = (now - response_due_at).total_seconds() / 3600

        resolution_due_at = ticket.created_at + timedelta(
            hours=sla_target.resolution_hours
        )
        resolution_hours = None
        resolution_breached = False
        resolution_breach_hours = None

        resolved_at = None
        if ticket.resolution_date:
            resolved_at = datetime.combine(ticket.resolution_date, datetime.min.time())
            if ticket.created_at.tzinfo:
                resolved_at = resolved_at.replace(tzinfo=ticket.created_at.tzinfo)
            resolution_hours = (resolved_at - ticket.created_at).total_seconds() / 3600
            resolution_breached = resolution_hours > sla_target.resolution_hours
            if resolution_breached:
                resolution_breach_hours = resolution_hours - sla_target.resolution_hours
        else:
            if now > resolution_due_at and ticket.status not in (
                TicketStatus.RESOLVED,
                TicketStatus.CLOSED,
            ):
                resolution_breached = True
                resolution_breach_hours = (
                    now - resolution_due_at
                ).total_seconds() / 3600

        return TicketSLAStatus(
            ticket_id=ticket.ticket_id,
            ticket_number=ticket.ticket_number,
            subject=ticket.subject,
            status=ticket.status,
            priority=ticket.priority,
            created_at=ticket.created_at,
            response_target_hours=sla_target.response_hours,
            response_due_at=response_due_at,
            first_response_at=first_response,
            response_hours=round(response_hours, 2) if response_hours else None,
            response_breached=response_breached,
            response_breach_hours=round(response_breach_hours, 2)
            if response_breach_hours
            else None,
            resolution_target_hours=sla_target.resolution_hours,
            resolution_due_at=resolution_due_at,
            resolved_at=resolved_at,
            resolution_hours=round(resolution_hours, 2) if resolution_hours else None,
            resolution_breached=resolution_breached,
            resolution_breach_hours=round(resolution_breach_hours, 2)
            if resolution_breach_hours
            else None,
            category_name=ticket.category.category_name if ticket.category else None,
            team_name=ticket.team.team_name if ticket.team else None,
            assigned_to_name=ticket.assigned_to.full_name
            if ticket.assigned_to
            else None,
        )

    def get_ticket_sla_status(
        self,
        db: Session,
        ticket: Ticket,
    ) -> TicketSLAStatus:
        """Calculate SLA status for a single ticket."""
        sla_target = self.get_sla_target(db, ticket)
        first_response = self.get_first_response_time(db, ticket.ticket_id)

        now = (
            datetime.now(ticket.created_at.tzinfo)
            if ticket.created_at.tzinfo
            else datetime.now()
        )

        # Response calculations
        response_due_at = ticket.created_at + timedelta(hours=sla_target.response_hours)
        response_hours = None
        response_breached = False
        response_breach_hours = None

        if first_response:
            response_hours = (first_response - ticket.created_at).total_seconds() / 3600
            response_breached = response_hours > sla_target.response_hours
            if response_breached:
                response_breach_hours = response_hours - sla_target.response_hours
        else:
            # Not yet responded - check if overdue
            if now > response_due_at and ticket.status not in (
                TicketStatus.RESOLVED,
                TicketStatus.CLOSED,
            ):
                response_breached = True
                response_breach_hours = (now - response_due_at).total_seconds() / 3600

        # Resolution calculations
        resolution_due_at = ticket.created_at + timedelta(
            hours=sla_target.resolution_hours
        )
        resolution_hours = None
        resolution_breached = False
        resolution_breach_hours = None

        resolved_at = None
        if ticket.resolution_date:
            # Use resolution_date but as datetime (start of day)
            resolved_at = datetime.combine(ticket.resolution_date, datetime.min.time())
            if ticket.created_at.tzinfo:
                resolved_at = resolved_at.replace(tzinfo=ticket.created_at.tzinfo)
            resolution_hours = (resolved_at - ticket.created_at).total_seconds() / 3600
            resolution_breached = resolution_hours > sla_target.resolution_hours
            if resolution_breached:
                resolution_breach_hours = resolution_hours - sla_target.resolution_hours
        else:
            # Not yet resolved - check if overdue
            if now > resolution_due_at and ticket.status not in (
                TicketStatus.RESOLVED,
                TicketStatus.CLOSED,
            ):
                resolution_breached = True
                resolution_breach_hours = (
                    now - resolution_due_at
                ).total_seconds() / 3600

        return TicketSLAStatus(
            ticket_id=ticket.ticket_id,
            ticket_number=ticket.ticket_number,
            subject=ticket.subject,
            status=ticket.status,
            priority=ticket.priority,
            created_at=ticket.created_at,
            response_target_hours=sla_target.response_hours,
            response_due_at=response_due_at,
            first_response_at=first_response,
            response_hours=round(response_hours, 2) if response_hours else None,
            response_breached=response_breached,
            response_breach_hours=round(response_breach_hours, 2)
            if response_breach_hours
            else None,
            resolution_target_hours=sla_target.resolution_hours,
            resolution_due_at=resolution_due_at,
            resolved_at=resolved_at,
            resolution_hours=round(resolution_hours, 2) if resolution_hours else None,
            resolution_breached=resolution_breached,
            resolution_breach_hours=round(resolution_breach_hours, 2)
            if resolution_breach_hours
            else None,
            category_name=ticket.category.category_name if ticket.category else None,
            team_name=ticket.team.team_name if ticket.team else None,
            assigned_to_name=ticket.assigned_to.full_name
            if ticket.assigned_to
            else None,
        )

    def get_breached_tickets(
        self,
        db: Session,
        organization_id: UUID,
        *,
        breach_type: str = "all",  # 'response', 'resolution', 'all'
        include_resolved: bool = False,
        limit: int = 50,
    ) -> list[TicketSLAStatus]:
        """
        Get tickets that have breached SLA.

        Args:
            breach_type: 'response', 'resolution', or 'all'
            include_resolved: Include resolved/closed tickets that breached
            limit: Max tickets to return
        """
        org_id = coerce_uuid(organization_id)

        query = (
            select(Ticket)
            .where(
                Ticket.organization_id == org_id,
                Ticket.is_deleted == False,  # noqa: E712
            )
            .options(
                joinedload(Ticket.category),
                joinedload(Ticket.team),
                joinedload(Ticket.assigned_to),
            )
            .order_by(Ticket.created_at.desc())
        )

        if not include_resolved:
            query = query.where(
                Ticket.status.in_(
                    [
                        TicketStatus.OPEN,
                        TicketStatus.REPLIED,
                        TicketStatus.ON_HOLD,
                    ]
                )
            )

        tickets = list(db.execute(query).scalars().unique().all())

        # Batch-fetch first response times
        ticket_ids = [t.ticket_id for t in tickets]
        first_responses = self._batch_first_response_times(db, ticket_ids)

        now = datetime.now()
        breached = []
        for ticket in tickets:
            sla_target = self._get_sla_target_from_ticket(ticket)
            first_response = first_responses.get(ticket.ticket_id)
            ticket_now = (
                datetime.now(ticket.created_at.tzinfo)
                if ticket.created_at.tzinfo
                else now
            )
            sla_status = self._compute_sla_status(
                ticket, sla_target, first_response, ticket_now
            )

            is_breached = False
            if (
                breach_type == "response"
                and sla_status.response_breached
                or breach_type == "resolution"
                and sla_status.resolution_breached
                or breach_type == "all"
                and (sla_status.response_breached or sla_status.resolution_breached)
            ):
                is_breached = True

            if is_breached:
                breached.append(sla_status)
                if len(breached) >= limit:
                    break

        return breached

    def get_aging_report(
        self,
        db: Session,
        organization_id: UUID,
        *,
        status_filter: list[str] | None = None,
    ) -> list[AgingBucket]:
        """
        Get ticket aging report grouped by age buckets.

        Default buckets: 0-24h, 1-3 days, 3-7 days, 7+ days
        """
        org_id = coerce_uuid(organization_id)

        query = (
            select(Ticket)
            .where(
                Ticket.organization_id == org_id,
                Ticket.is_deleted == False,  # noqa: E712
            )
            .options(
                joinedload(Ticket.category),
                joinedload(Ticket.team),
                joinedload(Ticket.assigned_to),
            )
        )

        # Default to open tickets only
        if status_filter:
            status_enums = []
            for s in status_filter:
                try:
                    status_enums.append(TicketStatus(s.upper()))
                except ValueError:
                    pass
            if status_enums:
                query = query.where(Ticket.status.in_(status_enums))
        else:
            query = query.where(
                Ticket.status.in_(
                    [
                        TicketStatus.OPEN,
                        TicketStatus.REPLIED,
                        TicketStatus.ON_HOLD,
                    ]
                )
            )

        tickets = list(db.execute(query).scalars().unique().all())

        # Define buckets
        buckets = [
            AgingBucket(
                label="0-24 hours", min_hours=0, max_hours=24, count=0, tickets=[]
            ),
            AgingBucket(
                label="1-3 days", min_hours=24, max_hours=72, count=0, tickets=[]
            ),
            AgingBucket(
                label="3-7 days", min_hours=72, max_hours=168, count=0, tickets=[]
            ),
            AgingBucket(
                label="7+ days", min_hours=168, max_hours=None, count=0, tickets=[]
            ),
        ]

        now = datetime.now()

        for ticket in tickets:
            created_at = ticket.created_at
            if created_at.tzinfo:
                now_tz = datetime.now(created_at.tzinfo)
            else:
                now_tz = now

            age_hours = (now_tz - created_at).total_seconds() / 3600

            ticket_info = {
                "ticket_id": str(ticket.ticket_id),
                "ticket_number": ticket.ticket_number,
                "subject": ticket.subject,
                "status": ticket.status.value,
                "priority": ticket.priority.value,
                "age_hours": round(age_hours, 1),
                "category": ticket.category.category_name if ticket.category else None,
                "team": ticket.team.team_name if ticket.team else None,
                "assigned_to": ticket.assigned_to.full_name
                if ticket.assigned_to
                else None,
            }

            for bucket in buckets:
                if bucket.max_hours is None:
                    if age_hours >= bucket.min_hours:
                        bucket.count += 1
                        bucket.tickets.append(ticket_info)
                        break
                elif bucket.min_hours <= age_hours < bucket.max_hours:
                    bucket.count += 1
                    bucket.tickets.append(ticket_info)
                    break

        return buckets

    def get_sla_metrics(
        self,
        db: Session,
        organization_id: UUID,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> SLAMetrics:
        """
        Calculate aggregated SLA metrics for a time period.

        Uses batch queries for first-response times (2 queries total)
        instead of per-ticket queries.
        """
        org_id = coerce_uuid(organization_id)

        if date_from is None:
            date_from = date.today() - timedelta(days=30)
        if date_to is None:
            date_to = date.today()

        query = (
            select(Ticket)
            .where(
                Ticket.organization_id == org_id,
                Ticket.is_deleted == False,  # noqa: E712
                Ticket.opening_date >= date_from,
                Ticket.opening_date <= date_to,
            )
            .options(
                joinedload(Ticket.category),
                joinedload(Ticket.team),
                joinedload(Ticket.assigned_to),
            )
        )

        tickets = list(db.execute(query).scalars().unique().all())

        # Batch-fetch first response times (2 queries instead of 2*N)
        ticket_ids = [t.ticket_id for t in tickets]
        first_responses = self._batch_first_response_times(db, ticket_ids)

        # Initialize counters
        total = len(tickets)
        resolved = 0
        open_count = 0

        response_met = 0
        response_breached = 0
        response_pending = 0
        response_times: list[float] = []

        resolution_met = 0
        resolution_breached = 0
        resolution_pending = 0
        resolution_times: list[float] = []

        by_priority: dict[str, dict[str, Any]] = {}
        by_team: dict[str, dict[str, Any]] = {}
        by_category: dict[str, dict[str, Any]] = {}

        now = datetime.now()

        for ticket in tickets:
            sla_target = self._get_sla_target_from_ticket(ticket)
            first_response = first_responses.get(ticket.ticket_id)

            # Use timezone-aware now if ticket has tz
            ticket_now = (
                datetime.now(ticket.created_at.tzinfo)
                if ticket.created_at.tzinfo
                else now
            )
            sla_status = self._compute_sla_status(
                ticket, sla_target, first_response, ticket_now
            )

            # Overall counts
            if ticket.status in (TicketStatus.RESOLVED, TicketStatus.CLOSED):
                resolved += 1
            else:
                open_count += 1

            # Response SLA
            if sla_status.first_response_at:
                if sla_status.response_breached:
                    response_breached += 1
                else:
                    response_met += 1
                if sla_status.response_hours:
                    response_times.append(sla_status.response_hours)
            else:
                if sla_status.response_breached:
                    response_breached += 1
                else:
                    response_pending += 1

            # Resolution SLA
            if sla_status.resolved_at:
                if sla_status.resolution_breached:
                    resolution_breached += 1
                else:
                    resolution_met += 1
                if sla_status.resolution_hours:
                    resolution_times.append(sla_status.resolution_hours)
            else:
                if sla_status.resolution_breached:
                    resolution_breached += 1
                else:
                    resolution_pending += 1

            # By priority
            priority_key = ticket.priority.value
            if priority_key not in by_priority:
                by_priority[priority_key] = {
                    "total": 0,
                    "response_met": 0,
                    "response_breached": 0,
                    "resolution_met": 0,
                    "resolution_breached": 0,
                }
            by_priority[priority_key]["total"] += 1
            if sla_status.first_response_at:
                if sla_status.response_breached:
                    by_priority[priority_key]["response_breached"] += 1
                else:
                    by_priority[priority_key]["response_met"] += 1
            if sla_status.resolved_at:
                if sla_status.resolution_breached:
                    by_priority[priority_key]["resolution_breached"] += 1
                else:
                    by_priority[priority_key]["resolution_met"] += 1

            # By team
            team_key = sla_status.team_name or "Unassigned"
            if team_key not in by_team:
                by_team[team_key] = {
                    "total": 0,
                    "response_met": 0,
                    "response_breached": 0,
                    "resolution_met": 0,
                    "resolution_breached": 0,
                    "response_times": [],
                    "resolution_times": [],
                }
            by_team[team_key]["total"] += 1
            if sla_status.first_response_at:
                if sla_status.response_breached:
                    by_team[team_key]["response_breached"] += 1
                else:
                    by_team[team_key]["response_met"] += 1
                if sla_status.response_hours:
                    by_team[team_key]["response_times"].append(
                        sla_status.response_hours
                    )
            if sla_status.resolved_at:
                if sla_status.resolution_breached:
                    by_team[team_key]["resolution_breached"] += 1
                else:
                    by_team[team_key]["resolution_met"] += 1
                if sla_status.resolution_hours:
                    by_team[team_key]["resolution_times"].append(
                        sla_status.resolution_hours
                    )

            # By category
            category_key = sla_status.category_name or "Uncategorized"
            if category_key not in by_category:
                by_category[category_key] = {
                    "total": 0,
                    "response_met": 0,
                    "response_breached": 0,
                    "resolution_met": 0,
                    "resolution_breached": 0,
                }
            by_category[category_key]["total"] += 1
            if sla_status.first_response_at:
                if sla_status.response_breached:
                    by_category[category_key]["response_breached"] += 1
                else:
                    by_category[category_key]["response_met"] += 1
            if sla_status.resolved_at:
                if sla_status.resolution_breached:
                    by_category[category_key]["resolution_breached"] += 1
                else:
                    by_category[category_key]["resolution_met"] += 1

        # Calculate averages for teams
        for team_data in by_team.values():
            times = team_data.pop("response_times")
            team_data["avg_response_hours"] = (
                round(sum(times) / len(times), 2) if times else None
            )
            times = team_data.pop("resolution_times")
            team_data["avg_resolution_hours"] = (
                round(sum(times) / len(times), 2) if times else None
            )

        # Calculate compliance percentages
        response_total = response_met + response_breached
        response_compliance = (
            (response_met / response_total * 100) if response_total > 0 else 100.0
        )

        resolution_total = resolution_met + resolution_breached
        resolution_compliance = (
            (resolution_met / resolution_total * 100) if resolution_total > 0 else 100.0
        )

        return SLAMetrics(
            total_tickets=total,
            resolved_tickets=resolved,
            open_tickets=open_count,
            response_met=response_met,
            response_breached=response_breached,
            response_pending=response_pending,
            response_compliance_pct=round(response_compliance, 1),
            avg_response_hours=round(sum(response_times) / len(response_times), 2)
            if response_times
            else None,
            resolution_met=resolution_met,
            resolution_breached=resolution_breached,
            resolution_pending=resolution_pending,
            resolution_compliance_pct=round(resolution_compliance, 1),
            avg_resolution_hours=round(sum(resolution_times) / len(resolution_times), 2)
            if resolution_times
            else None,
            by_priority=by_priority,
            by_team=by_team,
            by_category=by_category,
        )

    def get_team_performance(
        self,
        db: Session,
        organization_id: UUID,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        metrics: SLAMetrics | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get performance metrics per team.

        Returns list of team stats sorted by compliance rate.
        Pass pre-computed metrics to avoid redundant queries.
        """
        if metrics is None:
            metrics = self.get_sla_metrics(
                db, organization_id, date_from=date_from, date_to=date_to
            )

        results = []
        for team_name, data in metrics.by_team.items():
            total = data["total"]
            response_total = data["response_met"] + data["response_breached"]
            resolution_total = data["resolution_met"] + data["resolution_breached"]

            results.append(
                {
                    "team_name": team_name,
                    "total_tickets": total,
                    "response_compliance_pct": round(
                        data["response_met"] / response_total * 100, 1
                    )
                    if response_total > 0
                    else 100.0,
                    "resolution_compliance_pct": round(
                        data["resolution_met"] / resolution_total * 100, 1
                    )
                    if resolution_total > 0
                    else 100.0,
                    "avg_response_hours": data.get("avg_response_hours"),
                    "avg_resolution_hours": data.get("avg_resolution_hours"),
                    "response_met": data["response_met"],
                    "response_breached": data["response_breached"],
                    "resolution_met": data["resolution_met"],
                    "resolution_breached": data["resolution_breached"],
                }
            )

        # Sort by resolution compliance (descending)
        results.sort(key=lambda x: x["resolution_compliance_pct"], reverse=True)

        return results

    def get_category_performance(
        self,
        db: Session,
        organization_id: UUID,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        metrics: SLAMetrics | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get performance metrics per category.
        Pass pre-computed metrics to avoid redundant queries.
        """
        if metrics is None:
            metrics = self.get_sla_metrics(
                db, organization_id, date_from=date_from, date_to=date_to
            )

        results = []
        for category_name, data in metrics.by_category.items():
            total = data["total"]
            response_total = data["response_met"] + data["response_breached"]
            resolution_total = data["resolution_met"] + data["resolution_breached"]

            results.append(
                {
                    "category_name": category_name,
                    "total_tickets": total,
                    "response_compliance_pct": round(
                        data["response_met"] / response_total * 100, 1
                    )
                    if response_total > 0
                    else 100.0,
                    "resolution_compliance_pct": round(
                        data["resolution_met"] / resolution_total * 100, 1
                    )
                    if resolution_total > 0
                    else 100.0,
                    "response_met": data["response_met"],
                    "response_breached": data["response_breached"],
                    "resolution_met": data["resolution_met"],
                    "resolution_breached": data["resolution_breached"],
                }
            )

        results.sort(key=lambda x: x["total_tickets"], reverse=True)

        return results


# Singleton instance
sla_service = SLAService()
