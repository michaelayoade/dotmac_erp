"""
Support Web Service - Teams Module.

Handles team management template responses.
"""

import logging
from typing import TYPE_CHECKING, Any, TypedDict

from fastapi import Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.people.hr import Employee, EmployeeStatus
from app.models.person import Person
from app.services.common import coerce_uuid
from app.services.support.team import team_service
from app.templates import templates


class TeamMemberSummary(TypedDict):
    member_id: str
    employee_id: str
    employee_code: str
    full_name: str
    role: str
    is_available: bool
    assigned_count: int
    assignment_weight: int
    workload_percent: int
    weight_percent: int


if TYPE_CHECKING:
    from app.web.deps import WebAuthContext

logger = logging.getLogger(__name__)


class TeamWebService:
    """Web service for team management operations."""

    def list_teams_response(
        self,
        request: Request,
        auth: "WebAuthContext",
        db: Session,
    ) -> HTMLResponse:
        """Render the teams list page."""
        from app.web.deps import base_context

        org_id = coerce_uuid(auth.organization_id)

        teams = team_service.list_teams(db, org_id, active_only=False)

        # Format teams for display
        formatted_teams = []
        for team in teams:
            stats = team_service.get_team_stats(db, team.team_id)
            formatted_teams.append(
                {
                    "team_id": str(team.team_id),
                    "team_code": team.team_code,
                    "team_name": team.team_name,
                    "description": team.description,
                    "is_active": team.is_active,
                    "auto_assign": team.auto_assign,
                    "member_count": stats.get("member_count", 0),
                    "available_members": stats.get("available_members", 0),
                    "open_tickets": stats.get("open_tickets", 0),
                }
            )

        context = {
            **base_context(request, auth, "Support Teams", "support", db=db),
            "teams": formatted_teams,
        }

        return templates.TemplateResponse(request, "support/teams.html", context)

    def team_form_response(
        self,
        request: Request,
        auth: "WebAuthContext",
        db: Session,
        team_id: str | None = None,
        error: str | None = None,
    ) -> HTMLResponse:
        """Render the team create/edit form."""
        from app.web.deps import base_context

        org_id = coerce_uuid(auth.organization_id)

        team = None
        title = "New Team"

        if team_id:
            tid = coerce_uuid(team_id)
            team = team_service.get_team(db, tid)
            if team:
                title = f"Edit {team.team_name}"

        # Get employees for lead dropdown
        employees = self._get_employees_for_dropdown(db, org_id)

        context = {
            **base_context(request, auth, title, "support", db=db),
            "team": team,
            "employees": employees,
            "error": error,
        }

        return templates.TemplateResponse(request, "support/team_form.html", context)

    def create_team_response(
        self,
        request: Request,
        auth: "WebAuthContext",
        db: Session,
        *,
        team_code: str,
        team_name: str,
        description: str | None = None,
        lead_id: str | None = None,
        auto_assign: bool = False,
    ) -> Response:
        """Create a new team."""
        org_id = coerce_uuid(auth.organization_id)

        try:
            team, error = team_service.create_team(
                db,
                org_id,
                team_code=team_code,
                team_name=team_name,
                description=description,
                lead_id=coerce_uuid(lead_id) if lead_id else None,
                auto_assign=auto_assign,
            )

            if error:
                return self.team_form_response(request, auth, db, error=error)

            db.commit()

            if not team:
                return self.team_form_response(
                    request, auth, db, error="Team not created"
                )

            return RedirectResponse(
                url=f"/support/teams/{team.team_id}?saved=1",
                status_code=303,
            )

        except Exception as e:
            db.rollback()
            logger.exception("Failed to create team")
            return self.team_form_response(request, auth, db, error=str(e))

    def update_team_response(
        self,
        request: Request,
        auth: "WebAuthContext",
        db: Session,
        team_id: str,
        *,
        team_name: str | None = None,
        description: str | None = None,
        lead_id: str | None = None,
        auto_assign: bool = False,
        is_active: bool = True,
    ) -> Response:
        """Update a team."""
        tid = coerce_uuid(team_id)

        try:
            team = team_service.update_team(
                db,
                tid,
                team_name=team_name,
                description=description,
                lead_id=coerce_uuid(lead_id) if lead_id else None,
                auto_assign=auto_assign,
                is_active=is_active,
            )

            if not team:
                return RedirectResponse(
                    url="/support/teams?success=Record+updated+successfully",
                    status_code=303,
                )

            db.commit()

            return RedirectResponse(
                url=f"/support/teams/{team_id}?saved=1",
                status_code=303,
            )

        except Exception as e:
            db.rollback()
            logger.exception("Failed to update team")
            return self.team_form_response(
                request, auth, db, team_id=team_id, error=str(e)
            )

    def team_detail_response(
        self,
        request: Request,
        auth: "WebAuthContext",
        db: Session,
        team_id: str,
    ) -> HTMLResponse:
        """Render the team detail page."""
        from app.web.deps import base_context

        org_id = coerce_uuid(auth.organization_id)
        tid = coerce_uuid(team_id)

        team = team_service.get_team(db, tid)
        if not team:
            return templates.TemplateResponse(
                request,
                "support/team_detail.html",
                {
                    **base_context(request, auth, "Team Not Found", "support", db=db),
                    "team": None,
                    "error": "Team not found",
                },
                status_code=404,
            )

        stats = team_service.get_team_stats(db, tid)

        # Format members for display with workload data
        members: list[TeamMemberSummary] = []
        total_assigned = 0
        total_weight = 0
        max_assigned = 0

        for member in team.members:
            emp = db.get(Employee, member.employee_id)
            if emp and emp.person:
                total_assigned += member.assigned_count
                total_weight += member.assignment_weight
                if member.assigned_count > max_assigned:
                    max_assigned = member.assigned_count
                members.append(
                    {
                        "member_id": str(member.member_id),
                        "employee_id": str(member.employee_id),
                        "employee_code": emp.employee_code,
                        "full_name": emp.full_name,
                        "role": member.role or "Member",
                        "is_available": member.is_available,
                        "assigned_count": member.assigned_count,
                        "assignment_weight": member.assignment_weight,
                        "workload_percent": 0,
                        "weight_percent": 0,
                    }
                )

        # Calculate workload percentages for visualization
        for member_summary in members:
            if max_assigned > 0:
                member_summary["workload_percent"] = int(
                    (member_summary["assigned_count"] / max_assigned) * 100
                )
            else:
                member_summary["workload_percent"] = 0

            if total_weight > 0:
                member_summary["weight_percent"] = int(
                    (member_summary["assignment_weight"] / total_weight) * 100
                )
            else:
                member_summary["weight_percent"] = (
                    int(100 / len(members)) if members else 0
                )

        # Get employees not in team for adding
        available_employees = self._get_employees_not_in_team(db, org_id, tid)

        # Workload distribution stats
        workload_stats = {
            "total_assigned": total_assigned,
            "total_weight": total_weight,
            "max_assigned": max_assigned,
            "avg_assigned": round(total_assigned / len(members), 1) if members else 0,
        }

        context = {
            **base_context(request, auth, team.team_name, "support", db=db),
            "team": {
                "team_id": str(team.team_id),
                "team_code": team.team_code,
                "team_name": team.team_name,
                "description": team.description,
                "is_active": team.is_active,
                "auto_assign": team.auto_assign,
            },
            "members": members,
            "stats": stats,
            "workload_stats": workload_stats,
            "available_employees": available_employees,
        }

        return templates.TemplateResponse(request, "support/team_detail.html", context)

    def add_team_member_response(
        self,
        request: Request,
        auth: "WebAuthContext",
        db: Session,
        team_id: str,
        employee_id: str,
        role: str | None = None,
    ) -> RedirectResponse:
        """Add a member to a team."""
        tid = coerce_uuid(team_id)
        eid = coerce_uuid(employee_id)

        try:
            member, error = team_service.add_member(db, tid, eid, role=role)

            if error:
                logger.warning(f"Failed to add member: {error}")

            db.commit()

        except Exception:
            db.rollback()
            logger.exception("Failed to add team member")

        return RedirectResponse(
            url=f"/support/teams/{team_id}?saved=1",
            status_code=303,
        )

    def remove_team_member_response(
        self,
        request: Request,
        auth: "WebAuthContext",
        db: Session,
        team_id: str,
        member_id: str,
    ) -> RedirectResponse:
        """Remove a member from a team."""
        tid = coerce_uuid(team_id)
        mid = coerce_uuid(member_id)

        # Get member to find employee_id
        from app.models.support.team import SupportTeamMember

        member = db.get(SupportTeamMember, mid)

        if member:
            try:
                team_service.remove_member(db, tid, member.employee_id)
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("Failed to remove team member")

        return RedirectResponse(
            url=f"/support/teams/{team_id}?saved=1",
            status_code=303,
        )

    def toggle_member_availability_response(
        self,
        request: Request,
        auth: "WebAuthContext",
        db: Session,
        team_id: str,
        member_id: str,
    ) -> RedirectResponse:
        """Toggle a member's availability status."""
        from app.models.support.team import SupportTeamMember

        mid = coerce_uuid(member_id)
        member = db.get(SupportTeamMember, mid)

        if member:
            try:
                member.is_available = not member.is_available
                db.commit()
                logger.info(
                    f"Toggled availability for member {member_id}: {member.is_available}"
                )
            except Exception:
                db.rollback()
                logger.exception("Failed to toggle member availability")

        return RedirectResponse(
            url=f"/support/teams/{team_id}?saved=1",
            status_code=303,
        )

    def update_member_weight_response(
        self,
        request: Request,
        auth: "WebAuthContext",
        db: Session,
        team_id: str,
        member_id: str,
        weight: int,
    ) -> RedirectResponse:
        """Update a member's assignment weight."""
        from app.models.support.team import SupportTeamMember

        mid = coerce_uuid(member_id)
        member = db.get(SupportTeamMember, mid)

        if member:
            try:
                # Clamp weight between 1 and 10
                weight = max(1, min(10, weight))
                member.assignment_weight = weight
                db.commit()
                logger.info(f"Updated weight for member {member_id}: {weight}")
            except Exception:
                db.rollback()
                logger.exception("Failed to update member weight")

        return RedirectResponse(
            url=f"/support/teams/{team_id}?saved=1",
            status_code=303,
        )

    def _get_employees_for_dropdown(
        self,
        db: Session,
        organization_id,
    ) -> list[dict[str, Any]]:
        """Get employees for dropdown selection."""
        results = db.execute(
            select(Employee, Person)
            .join(Person, Person.id == Employee.person_id)
            .where(
                Employee.organization_id == organization_id,
                Employee.status == EmployeeStatus.ACTIVE,
            )
            .order_by(Person.first_name, Person.last_name)
        ).all()

        return [
            {
                "employee_id": str(emp.employee_id),
                "employee_code": emp.employee_code,
                "full_name": person.name,
            }
            for emp, person in results
        ]

    def _get_employees_not_in_team(
        self,
        db: Session,
        organization_id,
        team_id,
    ) -> list[dict[str, Any]]:
        """Get employees not already in a team."""
        from app.models.support.team import SupportTeamMember

        # Get current member employee IDs
        member_ids = set(
            db.execute(
                select(SupportTeamMember.employee_id).where(
                    SupportTeamMember.team_id == team_id
                )
            )
            .scalars()
            .all()
        )

        results = db.execute(
            select(Employee, Person)
            .join(Person, Person.id == Employee.person_id)
            .where(
                Employee.organization_id == organization_id,
                Employee.status == EmployeeStatus.ACTIVE,
            )
            .order_by(Person.first_name, Person.last_name)
        ).all()

        return [
            {
                "employee_id": str(emp.employee_id),
                "employee_code": emp.employee_code,
                "full_name": person.name,
            }
            for emp, person in results
            if emp.employee_id not in member_ids
        ]


# Singleton instance
team_web_service = TeamWebService()
