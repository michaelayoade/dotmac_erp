"""
Support Team Service.

Handles team management, assignment, and workload distribution.
"""

import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.support.team import SupportTeam, SupportTeamMember
from app.models.support.ticket import Ticket

logger = logging.getLogger(__name__)


class TeamService:
    """Service for managing support teams."""

    def list_teams(
        self,
        db: Session,
        organization_id: uuid.UUID,
        active_only: bool = True,
    ) -> list[SupportTeam]:
        """
        List support teams for an organization.

        Args:
            db: Database session
            organization_id: Organization UUID
            active_only: Only return active teams

        Returns:
            List of teams
        """
        query = (
            select(SupportTeam)
            .where(SupportTeam.organization_id == organization_id)
            .options(selectinload(SupportTeam.members))
        )

        if active_only:
            query = query.where(SupportTeam.is_active == True)  # noqa: E712

        query = query.order_by(SupportTeam.team_name)

        return list(db.execute(query).scalars().all())

    def get_team(
        self,
        db: Session,
        team_id: uuid.UUID,
    ) -> SupportTeam | None:
        """Get a team by ID with members loaded."""
        return db.execute(
            select(SupportTeam)
            .where(SupportTeam.team_id == team_id)
            .options(selectinload(SupportTeam.members))
        ).scalar_one_or_none()

    def get_team_by_code(
        self,
        db: Session,
        organization_id: uuid.UUID,
        team_code: str,
    ) -> SupportTeam | None:
        """Get a team by code."""
        return db.execute(
            select(SupportTeam).where(
                SupportTeam.organization_id == organization_id,
                SupportTeam.team_code == team_code,
            )
        ).scalar_one_or_none()

    def create_team(
        self,
        db: Session,
        organization_id: uuid.UUID,
        team_code: str,
        team_name: str,
        description: str | None = None,
        lead_id: uuid.UUID | None = None,
        auto_assign: bool = False,
    ) -> tuple[SupportTeam | None, str | None]:
        """
        Create a new support team.

        Args:
            db: Database session
            organization_id: Organization UUID
            team_code: Short code (e.g., FIBER)
            team_name: Display name
            description: Optional description
            lead_id: Optional team lead employee UUID
            auto_assign: Enable auto-assignment

        Returns:
            (team, error_message)
        """
        # Check for duplicate code
        existing = self.get_team_by_code(db, organization_id, team_code)
        if existing:
            return None, f"Team code '{team_code}' already exists"

        team = SupportTeam(
            organization_id=organization_id,
            team_code=team_code.upper(),
            team_name=team_name,
            description=description,
            lead_id=lead_id,
            auto_assign=auto_assign,
        )
        db.add(team)
        db.flush()

        logger.info("Created team %s: %s", team_code, team_name)

        return team, None

    def update_team(
        self,
        db: Session,
        team_id: uuid.UUID,
        team_name: str | None = None,
        description: str | None = None,
        lead_id: uuid.UUID | None = None,
        is_active: bool | None = None,
        auto_assign: bool | None = None,
    ) -> SupportTeam | None:
        """Update a team."""
        team = self.get_team(db, team_id)
        if not team:
            return None

        if team_name is not None:
            team.team_name = team_name
        if description is not None:
            team.description = description
        if lead_id is not None:
            team.lead_id = lead_id
        if is_active is not None:
            team.is_active = is_active
        if auto_assign is not None:
            team.auto_assign = auto_assign

        db.flush()

        return team

    def add_member(
        self,
        db: Session,
        team_id: uuid.UUID,
        employee_id: uuid.UUID,
        role: str | None = None,
        is_available: bool = True,
        assignment_weight: int = 1,
    ) -> tuple[SupportTeamMember | None, str | None]:
        """
        Add a member to a team.

        Args:
            db: Database session
            team_id: Team UUID
            employee_id: Employee UUID
            role: Optional role (member, senior, specialist)
            is_available: Available for auto-assignment
            assignment_weight: Weight for assignment distribution

        Returns:
            (member, error_message)
        """
        # Check if already a member
        existing = db.execute(
            select(SupportTeamMember).where(
                SupportTeamMember.team_id == team_id,
                SupportTeamMember.employee_id == employee_id,
            )
        ).scalar_one_or_none()

        if existing:
            return None, "Employee is already a member of this team"

        member = SupportTeamMember(
            team_id=team_id,
            employee_id=employee_id,
            role=role,
            is_available=is_available,
            assignment_weight=assignment_weight,
        )
        db.add(member)
        db.flush()

        logger.info("Added employee %s to team %s", employee_id, team_id)

        return member, None

    def remove_member(
        self,
        db: Session,
        team_id: uuid.UUID,
        employee_id: uuid.UUID,
    ) -> bool:
        """Remove a member from a team."""
        member = db.execute(
            select(SupportTeamMember).where(
                SupportTeamMember.team_id == team_id,
                SupportTeamMember.employee_id == employee_id,
            )
        ).scalar_one_or_none()

        if not member:
            return False

        db.delete(member)
        db.flush()

        logger.info("Removed employee %s from team %s", employee_id, team_id)

        return True

    def update_member(
        self,
        db: Session,
        member_id: uuid.UUID,
        role: str | None = None,
        is_available: bool | None = None,
        assignment_weight: int | None = None,
    ) -> SupportTeamMember | None:
        """Update a team member."""
        member = db.get(SupportTeamMember, member_id)
        if not member:
            return None

        if role is not None:
            member.role = role
        if is_available is not None:
            member.is_available = is_available
        if assignment_weight is not None:
            member.assignment_weight = assignment_weight

        db.flush()

        return member

    def get_next_assignee(
        self,
        db: Session,
        team_id: uuid.UUID,
    ) -> uuid.UUID | None:
        """
        Get the next employee to assign a ticket to (weighted round-robin).

        Uses assigned_count and assignment_weight for fair distribution.

        Args:
            db: Database session
            team_id: Team UUID

        Returns:
            Employee UUID or None if no available members
        """
        # Get available members ordered by (assigned_count / weight)
        members = (
            db.execute(
                select(SupportTeamMember)
                .where(
                    SupportTeamMember.team_id == team_id,
                    SupportTeamMember.is_available == True,  # noqa: E712
                )
                .order_by(
                    SupportTeamMember.assigned_count
                    / SupportTeamMember.assignment_weight
                )
            )
            .scalars()
            .all()
        )

        if not members:
            return None

        # Get the member with lowest weighted count
        member = members[0]

        # Increment their count
        member.assigned_count += 1
        db.flush()

        return member.employee_id

    def assign_to_team(
        self,
        db: Session,
        ticket: Ticket,
        team_id: uuid.UUID,
        auto_assign_member: bool = False,
    ) -> tuple[bool, str | None]:
        """
        Assign a ticket to a team.

        Args:
            db: Database session
            ticket: Ticket to assign
            team_id: Team UUID
            auto_assign_member: Also assign to a team member

        Returns:
            (success, error_message)
        """
        team = self.get_team(db, team_id)
        if not team:
            return False, "Team not found"

        if not team.is_active:
            return False, "Team is not active"

        ticket.team_id = team_id

        if auto_assign_member or team.auto_assign:
            employee_id = self.get_next_assignee(db, team_id)
            if employee_id:
                ticket.assigned_to_id = employee_id
                logger.info(
                    "Auto-assigned ticket %s to employee %s",
                    ticket.ticket_id,
                    employee_id,
                )

        db.flush()

        return True, None

    def get_team_stats(
        self,
        db: Session,
        team_id: uuid.UUID,
    ) -> dict:
        """
        Get statistics for a team.

        Args:
            db: Database session
            team_id: Team UUID

        Returns:
            Dictionary with team statistics
        """
        team = self.get_team(db, team_id)
        if not team:
            return {}

        # Count tickets by status
        ticket_counts = db.execute(
            select(Ticket.status, func.count(Ticket.ticket_id))
            .where(
                Ticket.team_id == team_id,
                Ticket.is_deleted == False,  # noqa: E712
            )
            .group_by(Ticket.status)
        ).all()

        status_counts = {status.value: count for status, count in ticket_counts}

        return {
            "team_id": str(team_id),
            "team_name": team.team_name,
            "member_count": len(team.members),
            "available_members": sum(1 for m in team.members if m.is_available),
            "open_tickets": status_counts.get("OPEN", 0),
            "in_progress": status_counts.get("REPLIED", 0)
            + status_counts.get("ON_HOLD", 0),
            "resolved": status_counts.get("RESOLVED", 0)
            + status_counts.get("CLOSED", 0),
            "total_tickets": sum(status_counts.values()),
        }


# Singleton instance
team_service = TeamService()
