"""
Support Web Service.

Template response helpers for the support/helpdesk module.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import TYPE_CHECKING, Any
from urllib.parse import quote
from uuid import UUID

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app.models.expense.expense_claim import ExpenseClaim
from app.models.person import Person
from app.models.support.ticket import Ticket, TicketPriority, TicketStatus
from app.services.common import coerce_uuid
from app.services.dropdown import dropdown_service
from app.services.support.attachment import attachment_service
from app.services.support.category import category_service
from app.services.support.comment import comment_service
from app.services.support.sla import sla_service
from app.services.support.team import team_service
from app.services.support.ticket import ticket_service
from app.services.support.web_attachments import attachment_web_service
from app.services.support.web_categories import category_web_service

# Import delegated web services
from app.services.support.web_comments import comment_web_service
from app.services.support.web_teams import team_web_service
from app.templates import templates

if TYPE_CHECKING:
    from app.web.deps import WebAuthContext
from app.services.common_filters import build_active_filters

logger = logging.getLogger(__name__)


# Status display styles for templates
STATUS_STYLES = {
    TicketStatus.OPEN: {
        "badge": "bg-sky-50 text-sky-700 ring-sky-600/20 dark:bg-sky-900/30 dark:text-sky-400",
        "icon_bg": "bg-sky-100 text-sky-600 dark:bg-sky-900/30 dark:text-sky-400",
        "label": "Open",
    },
    TicketStatus.REPLIED: {
        "badge": "bg-indigo-50 text-indigo-700 ring-indigo-600/20 dark:bg-indigo-900/30 dark:text-indigo-400",
        "icon_bg": "bg-indigo-100 text-indigo-600 dark:bg-indigo-900/30 dark:text-indigo-400",
        "label": "Replied",
    },
    TicketStatus.ON_HOLD: {
        "badge": "bg-amber-50 text-amber-700 ring-amber-600/20 dark:bg-amber-900/30 dark:text-amber-400",
        "icon_bg": "bg-amber-100 text-amber-600 dark:bg-amber-900/30 dark:text-amber-400",
        "label": "On Hold",
    },
    TicketStatus.RESOLVED: {
        "badge": "bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-900/30 dark:text-emerald-400",
        "icon_bg": "bg-emerald-100 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-400",
        "label": "Resolved",
    },
    TicketStatus.CLOSED: {
        "badge": "bg-slate-50 text-slate-600 ring-slate-500/20 dark:bg-slate-800 dark:text-slate-400",
        "icon_bg": "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400",
        "label": "Closed",
    },
}

PRIORITY_STYLES = {
    TicketPriority.LOW: {
        "badge": "bg-slate-50 text-slate-600 ring-slate-500/20 dark:bg-slate-800 dark:text-slate-400",
        "label": "Low",
    },
    TicketPriority.MEDIUM: {
        "badge": "bg-blue-50 text-blue-700 ring-blue-600/20 dark:bg-blue-900/30 dark:text-blue-400",
        "label": "Medium",
    },
    TicketPriority.HIGH: {
        "badge": "bg-orange-50 text-orange-700 ring-orange-600/20 dark:bg-orange-900/30 dark:text-orange-400",
        "label": "High",
    },
    TicketPriority.URGENT: {
        "badge": "bg-rose-50 text-rose-700 ring-rose-600/20 dark:bg-rose-900/30 dark:text-rose-400",
        "label": "Urgent",
    },
}


def _strip_html(html: str) -> str:
    """Strip HTML tags and decode entities from text."""
    if not html:
        return ""
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", html)
    # Decode common HTML entities
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'")
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _is_numeric_subject(subject: str) -> bool:
    """Check if subject is purely numeric or a meaningless ID (e.g., '10231', '10231-2')."""
    if not subject:
        return True
    # Remove common separators and check if remainder is numeric
    cleaned = subject.replace("-", "").replace("_", "").replace(" ", "")
    return cleaned.isdigit()


def _format_ticket_for_list(ticket: Ticket) -> dict[str, Any]:
    """Format a ticket for list view display."""
    status_style = STATUS_STYLES.get(ticket.status, STATUS_STYLES[TicketStatus.OPEN])
    priority_style = PRIORITY_STYLES.get(
        ticket.priority, PRIORITY_STYLES[TicketPriority.MEDIUM]
    )

    # Get assigned employee name
    assigned_name = None
    if ticket.assigned_to and ticket.assigned_to.person:
        p = ticket.assigned_to.person
        assigned_name = f"{p.first_name or ''} {p.last_name or ''}".strip()

    # Get raised by name
    raised_name = None
    if ticket.raised_by and ticket.raised_by.person:
        p = ticket.raised_by.person
        raised_name = f"{p.first_name or ''} {p.last_name or ''}".strip()
    elif ticket.raised_by_email:
        raised_name = ticket.raised_by_email

    # Compute display_subject: use description if subject is meaningless
    subject = ticket.subject or ""
    if _is_numeric_subject(subject) and ticket.description:
        # Strip HTML and use first 80 chars of description
        desc = _strip_html(ticket.description)
        if desc:
            display_subject = desc[:80] + ("..." if len(desc) > 80 else "")
        else:
            display_subject = subject  # Fallback if description is just images/empty
    else:
        display_subject = subject

    return {
        "ticket_id": str(ticket.ticket_id),
        "ticket_number": ticket.ticket_number,
        "ticket_url": f"/support/tickets/{ticket.ticket_number or ticket.ticket_id}",
        "ticket_edit_url": f"/support/tickets/{ticket.ticket_number or ticket.ticket_id}/edit",
        "subject": ticket.subject,
        "display_subject": display_subject,
        "description_preview": (ticket.description[:100] + "...")
        if ticket.description and len(ticket.description) > 100
        else ticket.description,
        "status": ticket.status.value,
        "status_label": status_style["label"],
        "status_badge": status_style["badge"],
        "priority": ticket.priority.value,
        "priority_label": priority_style["label"],
        "priority_badge": priority_style["badge"],
        "opening_date": ticket.opening_date,
        "opening_date_formatted": ticket.opening_date.strftime("%b %d, %Y")
        if ticket.opening_date
        else "",
        "resolution_date": ticket.resolution_date,
        "assigned_to_id": str(ticket.assigned_to_id) if ticket.assigned_to_id else None,
        "assigned_to_name": assigned_name,
        "raised_by_name": raised_name,
        "raised_by_email": ticket.raised_by_email,
    }


def _format_ticket_for_detail(
    ticket: Ticket, linked_expenses: list[ExpenseClaim], db: Session = None
) -> dict[str, Any]:
    """Format a ticket for detail view display."""
    base = _format_ticket_for_list(ticket)
    status_style = STATUS_STYLES.get(ticket.status, STATUS_STYLES[TicketStatus.OPEN])

    customer_contact = ticket.customer.primary_contact if ticket.customer else None
    customer_billing_address = (
        (ticket.customer.billing_address or {}).get("address", "")
        if ticket.customer
        else ""
    )
    customer_shipping_address = (
        (ticket.customer.shipping_address or {}).get("address", "")
        if ticket.customer
        else ""
    )

    # Add full details
    base.update(
        {
            "description": ticket.description,
            "resolution": ticket.resolution,
            "resolution_date_formatted": ticket.resolution_date.strftime("%b %d, %Y")
            if ticket.resolution_date
            else None,
            "project_name": ticket.project.project_name if ticket.project else None,
            "project_code": ticket.project.project_code if ticket.project else None,
            "project_id": str(ticket.project_id) if ticket.project_id else None,
            "created_at": ticket.created_at.strftime("%b %d, %Y %H:%M")
            if ticket.created_at
            else "",
            "updated_at": ticket.updated_at.strftime("%b %d, %Y %H:%M")
            if ticket.updated_at
            else None,
            "status_icon_bg": status_style["icon_bg"],
            "erpnext_id": ticket.erpnext_id,
            "last_synced_at": ticket.last_synced_at.strftime("%b %d, %Y %H:%M")
            if ticket.last_synced_at
            else None,
            # Customer info
            "customer_id": str(ticket.customer_id) if ticket.customer_id else None,
            "customer_name": ticket.customer.trading_name or ticket.customer.legal_name
            if ticket.customer
            else None,
            "customer_code": ticket.customer.customer_code if ticket.customer else None,
            "customer_email": (customer_contact or {}).get("email"),
            "customer_phone": (customer_contact or {}).get("phone"),
            "customer_billing_address": customer_billing_address,
            "customer_shipping_address": customer_shipping_address,
            # Category info
            "category_id": str(ticket.category_id) if ticket.category_id else None,
            "category_name": ticket.category.category_name if ticket.category else None,
            "category_icon": ticket.category.icon if ticket.category else None,
            "category_color": ticket.category.color if ticket.category else None,
            # Team info
            "team_id": str(ticket.team_id) if ticket.team_id else None,
            "team_name": ticket.team.team_name if ticket.team else None,
            "team_code": ticket.team.team_code if ticket.team else None,
            # Available status transitions
            "can_transition_to": [
                {"value": s.value, "label": STATUS_STYLES[s]["label"]}
                for s in ticket_service.STATUS_TRANSITIONS.get(ticket.status, [])
            ],
            # Linked expenses
            "linked_expenses": [
                {
                    "claim_id": str(e.claim_id),
                    "claim_number": e.claim_number,
                    "purpose": e.purpose,
                    "status": e.status.value if e.status else "Draft",
                    "total_amount": f"{e.currency_code} {e.total_claimed_amount:,.2f}"
                    if e.total_claimed_amount
                    else "",
                    "claim_date": e.claim_date.strftime("%b %d, %Y")
                    if e.claim_date
                    else "",
                }
                for e in linked_expenses
            ],
            "linked_expense_count": len(linked_expenses),
            # Audit trail
            "created_by_id": str(ticket.created_by_id)
            if ticket.created_by_id
            else None,
            "updated_by_id": str(ticket.updated_by_id)
            if ticket.updated_by_id
            else None,
        }
    )

    # Look up creator and updater names from Person
    if db and ticket.created_by_id:
        try:
            creator = db.get(Person, ticket.created_by_id)
            if creator:
                base["created_by_name"] = (
                    f"{creator.first_name or ''} {creator.last_name or ''}".strip()
                    or "Unknown"
                )
        except Exception:
            logger.exception("Ignored exception")
    if db and ticket.updated_by_id:
        try:
            updater = db.get(Person, ticket.updated_by_id)
            if updater:
                base["updated_by_name"] = (
                    f"{updater.first_name or ''} {updater.last_name or ''}".strip()
                    or "Unknown"
                )
        except Exception:
            logger.exception("Ignored exception")

    return base


class SupportWebService:
    """Web service for support module template rendering."""

    def _resolve_ticket_ref(
        self,
        db: Session,
        organization_id: UUID,
        ticket_ref: str,
    ) -> Ticket | None:
        """Resolve ticket by UUID or ticket_number."""
        org_id = coerce_uuid(organization_id)
        try:
            tid = coerce_uuid(ticket_ref)
            ticket = ticket_service.get_ticket(db, org_id, tid)
            if ticket:
                return ticket
        except HTTPException:
            pass
        return ticket_service.get_ticket_by_number(db, org_id, ticket_ref)

    @staticmethod
    def _ticket_url(ticket: Ticket) -> str:
        return f"/support/tickets/{ticket.ticket_number or ticket.ticket_id}"

    def list_tickets_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        search: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        assigned_to: str | None = None,
        category_id: str | None = None,
        team_id: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> HTMLResponse:
        """Render the tickets list page."""
        from app.web.deps import base_context

        org_id = coerce_uuid(auth.organization_id)

        # Get tickets
        assigned_to_id = coerce_uuid(assigned_to) if assigned_to else None
        cat_id = coerce_uuid(category_id) if category_id else None
        t_id = coerce_uuid(team_id) if team_id else None

        # Parse date filters
        parsed_date_from = None
        parsed_date_to = None
        if date_from:
            try:
                parsed_date_from = date.fromisoformat(date_from)
            except ValueError:
                pass
        if date_to:
            try:
                parsed_date_to = date.fromisoformat(date_to)
            except ValueError:
                pass

        tickets, total = ticket_service.list_tickets(
            db,
            org_id,
            status=status,
            priority=priority,
            assigned_to_id=assigned_to_id,
            category_id=cat_id,
            team_id=t_id,
            search=search,
            date_from=parsed_date_from,
            date_to=parsed_date_to,
            page=page,
            per_page=per_page,
        )

        # Get stats for dashboard cards
        stats = ticket_service.get_stats(db, org_id)

        # Get employees for filter dropdown
        employees = dropdown_service.get_employees(db, org_id)

        # Get categories and teams for filter dropdowns
        categories = category_service.list_categories(db, org_id)
        teams = team_service.list_teams(db, org_id)

        # Format tickets for display
        formatted_tickets = [_format_ticket_for_list(t) for t in tickets]

        # Pagination
        total_pages = (total + per_page - 1) // per_page

        context = {
            **base_context(request, auth, "Support Tickets", "support", db=db),
            "tickets": formatted_tickets,
            "stats": stats,
            "employees": employees,
            "categories": [
                {"value": str(c.category_id), "label": c.category_name}
                for c in categories
            ],
            "teams": [{"value": str(t.team_id), "label": t.team_name} for t in teams],
            "statuses": [
                {"value": s.value, "label": STATUS_STYLES[s]["label"]}
                for s in TicketStatus
            ],
            "priorities": [
                {"value": p.value, "label": PRIORITY_STYLES[p]["label"]}
                for p in TicketPriority
            ],
            # Current filters
            "search": search or "",
            "selected_status": status or "",
            "selected_priority": priority or "",
            "selected_assigned_to": assigned_to or "",
            "selected_category": category_id or "",
            "selected_team": team_id or "",
            "selected_date_from": date_from or "",
            "selected_date_to": date_to or "",
            # Pagination
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "has_prev": page > 1,
            "has_next": page < total_pages,
        }

        return templates.TemplateResponse(request, "support/tickets.html", context)

    def ticket_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        ticket_id: str,
    ) -> HTMLResponse:
        """Render the ticket detail page."""
        from app.web.deps import base_context

        org_id = coerce_uuid(auth.organization_id)
        ticket = self._resolve_ticket_ref(db, org_id, ticket_id)
        if not ticket:
            return templates.TemplateResponse(
                request,
                "support/ticket_detail.html",
                {
                    **base_context(request, auth, "Ticket Not Found", "support", db=db),
                    "ticket": None,
                    "error": "Ticket not found",
                    "activity_timeline": [],
                },
                status_code=404,
            )

        # Get linked expenses
        linked_expenses = ticket_service.get_linked_expenses(
            db, org_id, ticket.ticket_id
        )

        # Get linked tasks (tasks that have this ticket_id)
        linked_tasks = self._get_linked_tasks(db, org_id, ticket.ticket_id)

        # Get employees for assignment dropdown
        employees = dropdown_service.get_employees(db, org_id)

        # Get attachments (split ticket vs comment attachments)
        all_attachments = attachment_service.list_attachments(db, ticket.ticket_id)
        ticket_attachments = []
        comment_attachments: dict[str, list[Any]] = {}
        for att in all_attachments:
            if att.comment_id:
                comment_attachments.setdefault(str(att.comment_id), []).append(att)
            else:
                ticket_attachments.append(att)

        # Get activity timeline
        activity = comment_service.get_activity_timeline(db, ticket.ticket_id, limit=50)
        activity_timeline = self._format_activity_timeline(activity)

        # Get SLA status for this ticket
        sla_status = sla_service.get_ticket_sla_status(db, ticket)
        sla_data = self._format_sla_status(sla_status)

        formatted = _format_ticket_for_detail(ticket, linked_expenses, db)

        context = {
            **base_context(
                request, auth, f"Ticket {ticket.ticket_number}", "support", db=db
            ),
            "ticket": formatted,
            "employees": employees,
            "linked_tasks": linked_tasks,
            "activity_timeline": activity_timeline,
            "sla": sla_data,
            "attachments": ticket_attachments,
            "comment_attachments": comment_attachments,
        }

        return templates.TemplateResponse(
            request, "support/ticket_detail.html", context
        )

    def ticket_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        ticket_id: str | None = None,
        error: str | None = None,
    ) -> HTMLResponse:
        """Render the ticket create/edit form."""
        from app.web.deps import base_context

        org_id = coerce_uuid(auth.organization_id)

        ticket = None
        title = "New Ticket"

        if ticket_id:
            ticket = self._resolve_ticket_ref(db, org_id, ticket_id)
            if ticket:
                title = f"Edit {ticket.ticket_number}"

        # Get employees for dropdowns
        employees = dropdown_service.get_employees(db, org_id)

        # Get projects for dropdown
        projects = dropdown_service.get_projects(db, org_id)

        # Get customers for dropdown
        customers = dropdown_service.get_customers(db, org_id)

        # Get categories and teams for dropdowns
        categories = category_service.list_categories(db, org_id)
        teams = team_service.list_teams(db, org_id)

        context = {
            **base_context(request, auth, title, "support", db=db),
            "ticket": ticket,
            "employees": employees,
            "projects": projects,
            "customers": customers,
            "categories": [
                {"value": str(c.category_id), "label": c.category_name}
                for c in categories
            ],
            "teams": [{"value": str(t.team_id), "label": t.team_name} for t in teams],
            "priorities": [
                {"value": p.value, "label": PRIORITY_STYLES[p]["label"]}
                for p in TicketPriority
            ],
            "today": date.today().isoformat(),
            "error": error,
        }

        return templates.TemplateResponse(request, "support/ticket_form.html", context)

    def create_ticket_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        subject: str,
        description: str | None = None,
        priority: str = "MEDIUM",
        raised_by_email: str | None = None,
        assigned_to_id: str | None = None,
        project_id: str | None = None,
        customer_id: str | None = None,
        category_id: str | None = None,
        team_id: str | None = None,
        opening_date: str | None = None,
        contact_email: str | None = None,
        contact_phone: str | None = None,
        contact_address: str | None = None,
        files: list[UploadFile] | None = None,
    ) -> HTMLResponse | RedirectResponse:
        """Create a new ticket and redirect to detail page."""
        org_id = coerce_uuid(auth.organization_id)
        user_id = coerce_uuid(auth.user_id)

        try:
            # Parse opening date
            parsed_date = None
            if opening_date:
                try:
                    parsed_date = date.fromisoformat(opening_date)
                except ValueError:
                    pass

            ticket = ticket_service.create_ticket(
                db,
                org_id,
                user_id,
                subject=subject,
                description=description,
                priority=priority,
                raised_by_email=raised_by_email,
                assigned_to_id=coerce_uuid(assigned_to_id) if assigned_to_id else None,
                project_id=coerce_uuid(project_id) if project_id else None,
                customer_id=coerce_uuid(customer_id) if customer_id else None,
                category_id=coerce_uuid(category_id) if category_id else None,
                team_id=coerce_uuid(team_id) if team_id else None,
                opening_date=parsed_date,
                contact_email=contact_email,
                contact_phone=contact_phone,
                contact_address=contact_address,
            )

            upload_files = [f for f in (files or []) if getattr(f, "filename", None)]
            for file in upload_files:
                attachment_service.save_file(
                    db,
                    ticket_id=ticket.ticket_id,
                    filename=file.filename or "unnamed",
                    file_data=file.file,
                    content_type=file.content_type or "application/octet-stream",
                    uploaded_by_id=user_id,
                )

            db.commit()

            return RedirectResponse(
                url=self._ticket_url(ticket) + "?saved=1",
                status_code=303,
            )

        except Exception as e:
            db.rollback()
            logger.exception("Failed to create ticket")
            error = self._format_ticket_error(e)
            return RedirectResponse(
                url=f"/support/tickets/new?error={quote(error)}",
                status_code=303,
            )

    @staticmethod
    def _format_ticket_error(exc: Exception) -> str:
        """Return a user-friendly error message for ticket actions."""
        if isinstance(exc, HTTPException):
            detail = getattr(exc, "detail", None)
            return detail or "Unable to create ticket. Please check your input."
        if isinstance(exc, IntegrityError):
            message = str(getattr(exc, "orig", exc))
            if "uq_ticket_org_number" in message:
                return "Ticket number already exists. Please try again."
            if "foreign key" in message.lower():
                return "Some selected references are invalid. Please reselect and try again."
            return (
                "Ticket could not be created due to a data conflict. Please try again."
            )
        if isinstance(exc, DataError):
            return "Some fields have invalid values or are too long. Please review and try again."
        return "Ticket could not be created. Please check your input and try again."

    def update_ticket_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        ticket_id: str,
        *,
        subject: str | None = None,
        description: str | None = None,
        priority: str | None = None,
        raised_by_email: str | None = None,
        assigned_to_id: str | None = None,
        project_id: str | None = None,
        customer_id: str | None = None,
        category_id: str | None = None,
        team_id: str | None = None,
        contact_email: str | None = None,
        contact_phone: str | None = None,
        contact_address: str | None = None,
        files: list[UploadFile] | None = None,
    ) -> HTMLResponse | RedirectResponse:
        """Update a ticket and redirect to detail page."""
        org_id = coerce_uuid(auth.organization_id)
        user_id = coerce_uuid(auth.user_id)
        ticket_ref = self._resolve_ticket_ref(db, org_id, ticket_id)
        if not ticket_ref:
            return RedirectResponse(
                url="/support/tickets?success=Record+updated+successfully",
                status_code=303,
            )

        try:
            ticket = ticket_service.update_ticket(
                db,
                org_id,
                ticket_ref.ticket_id,
                user_id,
                subject=subject,
                description=description,
                priority=priority,
                raised_by_email=raised_by_email,
                assigned_to_id=coerce_uuid(assigned_to_id) if assigned_to_id else None,
                project_id=coerce_uuid(project_id) if project_id else None,
                customer_id=coerce_uuid(customer_id) if customer_id else None,
                category_id=coerce_uuid(category_id) if category_id else None,
                team_id=coerce_uuid(team_id) if team_id else None,
                contact_email=contact_email,
                contact_phone=contact_phone,
                contact_address=contact_address,
            )

            if not ticket:
                return RedirectResponse(
                    url="/support/tickets?success=Record+saved+successfully",
                    status_code=303,
                )

            upload_files = [f for f in (files or []) if getattr(f, "filename", None)]
            for file in upload_files:
                attachment_service.save_file(
                    db,
                    ticket_id=ticket.ticket_id,
                    filename=file.filename or "unnamed",
                    file_data=file.file,
                    content_type=file.content_type or "application/octet-stream",
                    uploaded_by_id=user_id,
                )

            db.commit()

            return RedirectResponse(
                url=self._ticket_url(ticket) + "?saved=1",
                status_code=303,
            )

        except Exception as e:
            db.rollback()
            logger.exception("Failed to update ticket")
            return self.ticket_form_response(
                request, auth, db, ticket_id=ticket_id, error=str(e)
            )

    def update_status_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        ticket_id: str,
        new_status: str,
        notes: str | None = None,
    ) -> RedirectResponse:
        """Update ticket status."""
        org_id = coerce_uuid(auth.organization_id)
        user_id = coerce_uuid(auth.user_id)
        ticket_ref = self._resolve_ticket_ref(db, org_id, ticket_id)
        if not ticket_ref:
            return RedirectResponse(
                url="/support/tickets?success=Record+updated+successfully",
                status_code=303,
            )

        ticket, error = ticket_service.update_status(
            db, org_id, ticket_ref.ticket_id, user_id, new_status, notes
        )

        if error:
            # TODO: Flash message with error
            logger.warning(f"Status update failed: {error}")

        if ticket:
            db.commit()

        return RedirectResponse(
            url=self._ticket_url(ticket_ref) + "?saved=1",
            status_code=303,
        )

    def assign_ticket_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        ticket_id: str,
        assigned_to_id: str,
    ) -> RedirectResponse:
        """Assign ticket to employee."""
        org_id = coerce_uuid(auth.organization_id)
        user_id = coerce_uuid(auth.user_id)
        ticket_ref = self._resolve_ticket_ref(db, org_id, ticket_id)
        if not ticket_ref:
            return RedirectResponse(
                url="/support/tickets?success=Record+saved+successfully",
                status_code=303,
            )

        ticket = ticket_service.assign_ticket(
            db, org_id, ticket_ref.ticket_id, user_id, coerce_uuid(assigned_to_id)
        )

        if ticket:
            db.commit()

        return RedirectResponse(
            url=self._ticket_url(ticket_ref) + "?saved=1",
            status_code=303,
        )

    def resolve_ticket_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        ticket_id: str,
        resolution: str,
    ) -> RedirectResponse:
        """Mark ticket as resolved."""
        org_id = coerce_uuid(auth.organization_id)
        user_id = coerce_uuid(auth.user_id)
        ticket_ref = self._resolve_ticket_ref(db, org_id, ticket_id)
        if not ticket_ref:
            return RedirectResponse(
                url="/support/tickets?success=Record+saved+successfully",
                status_code=303,
            )

        ticket, error = ticket_service.resolve_ticket(
            db, org_id, ticket_ref.ticket_id, user_id, resolution
        )

        if ticket:
            db.commit()

        return RedirectResponse(
            url=self._ticket_url(ticket_ref) + "?saved=1",
            status_code=303,
        )

    def archive_ticket_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        ticket_id: str,
    ) -> RedirectResponse:
        """Archive (soft delete) a ticket."""
        org_id = coerce_uuid(auth.organization_id)
        user_id = coerce_uuid(auth.user_id)
        ticket_ref = self._resolve_ticket_ref(db, org_id, ticket_id)
        if not ticket_ref:
            return RedirectResponse(
                url="/support/tickets?success=Record+saved+successfully",
                status_code=303,
            )

        success, error = ticket_service.delete_ticket(
            db, org_id, ticket_ref.ticket_id, user_id, hard_delete=False
        )

        if success:
            db.commit()
            return RedirectResponse(
                url="/support/tickets?archived=success&success=Record+saved+successfully",
                status_code=303,
            )

        return RedirectResponse(
            url=f"{self._ticket_url(ticket_ref)}?error=archive_failed",
            status_code=303,
        )

    def delete_ticket_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        ticket_id: str,
    ) -> RedirectResponse:
        """Hard delete a ticket."""
        org_id = coerce_uuid(auth.organization_id)
        user_id = coerce_uuid(auth.user_id)
        ticket_ref = self._resolve_ticket_ref(db, org_id, ticket_id)
        if not ticket_ref:
            return RedirectResponse(
                url="/support/tickets?success=Record+deleted+successfully",
                status_code=303,
            )

        success, error = ticket_service.delete_ticket(
            db, org_id, ticket_ref.ticket_id, user_id, hard_delete=True
        )

        if success:
            db.commit()
            return RedirectResponse(
                url="/support/tickets?deleted=success",
                status_code=303,
            )

        return RedirectResponse(
            url=f"{self._ticket_url(ticket_ref)}?error=delete_failed",
            status_code=303,
        )

    def restore_ticket_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        ticket_id: str,
    ) -> RedirectResponse:
        """Restore an archived ticket."""
        org_id = coerce_uuid(auth.organization_id)
        user_id = coerce_uuid(auth.user_id)
        ticket_ref = self._resolve_ticket_ref(db, org_id, ticket_id)
        if not ticket_ref:
            return RedirectResponse(
                url="/support/tickets/archived?error=restore_failed",
                status_code=303,
            )

        ticket = ticket_service.restore_ticket(
            db, org_id, ticket_ref.ticket_id, user_id
        )

        if ticket:
            db.commit()
            return RedirectResponse(
                url=self._ticket_url(ticket_ref) + "?saved=1",
                status_code=303,
            )

        return RedirectResponse(
            url="/support/tickets/archived?error=restore_failed",
            status_code=303,
        )

    def archived_tickets_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        search: str | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> HTMLResponse:
        """Render the archived tickets list page."""
        from app.web.deps import base_context

        org_id = coerce_uuid(auth.organization_id)

        # Delegate to core service
        tickets, total = ticket_service.list_archived_tickets(
            db,
            org_id,
            search=search,
            page=page,
            per_page=per_page,
        )

        # Format tickets for template
        formatted_tickets = [_format_ticket_for_list(t) for t in tickets]

        total_pages = (total + per_page - 1) // per_page

        context = {
            **base_context(request, auth, "Archived Tickets", "support", db=db),
            "tickets": formatted_tickets,
            "search": search or "",
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "has_prev": page > 1,
            "has_next": page < total_pages,
        }

        return templates.TemplateResponse(
            request, "support/archived_tickets.html", context
        )

    def dashboard_context(
        self,
        db: Session,
        organization_id: str,
    ) -> dict[str, Any]:
        """Get support dashboard context for operations dashboard widget."""
        org_id = coerce_uuid(organization_id)

        stats = ticket_service.get_stats(db, org_id)

        # Get recent open tickets
        tickets, _ = ticket_service.list_tickets(
            db,
            org_id,
            status="OPEN",
            page=1,
            per_page=5,
        )

        return {
            "support_stats": stats,
            "recent_open_tickets": [_format_ticket_for_list(t) for t in tickets],
        }

    # ========================================================================
    # SLA Dashboard & Reports
    # ========================================================================

    def sla_dashboard_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> HTMLResponse:
        """Render the SLA dashboard page."""
        from datetime import timedelta

        from app.web.deps import base_context

        org_id = coerce_uuid(auth.organization_id)

        # Parse dates
        parsed_date_from = None
        parsed_date_to = None

        if date_from:
            try:
                parsed_date_from = date.fromisoformat(date_from)
            except ValueError:
                pass
        if date_to:
            try:
                parsed_date_to = date.fromisoformat(date_to)
            except ValueError:
                pass

        # Default to last 30 days
        if not parsed_date_from:
            parsed_date_from = date.today() - timedelta(days=30)
        if not parsed_date_to:
            parsed_date_to = date.today()

        # Get SLA metrics
        metrics = sla_service.get_sla_metrics(
            db,
            org_id,
            date_from=parsed_date_from,
            date_to=parsed_date_to,
        )

        # Get breached tickets (limited)
        breached = sla_service.get_breached_tickets(
            db,
            org_id,
            breach_type="all",
            include_resolved=False,
            limit=10,
        )

        # Get team performance
        team_performance = sla_service.get_team_performance(
            db,
            org_id,
            date_from=parsed_date_from,
            date_to=parsed_date_to,
        )

        # Get category performance
        category_performance = sla_service.get_category_performance(
            db,
            org_id,
            date_from=parsed_date_from,
            date_to=parsed_date_to,
        )

        # Get aging report
        aging = sla_service.get_aging_report(db, org_id)

        # Format breached tickets for display
        breached_formatted = []
        for b in breached:
            status_style = STATUS_STYLES.get(b.status, STATUS_STYLES[TicketStatus.OPEN])
            priority_style = PRIORITY_STYLES.get(
                b.priority, PRIORITY_STYLES[TicketPriority.MEDIUM]
            )
            breached_formatted.append(
                {
                    "ticket_id": str(b.ticket_id),
                    "ticket_number": b.ticket_number,
                    "subject": b.subject[:60] + "..."
                    if len(b.subject) > 60
                    else b.subject,
                    "status": b.status.value,
                    "status_label": status_style["label"],
                    "status_badge": status_style["badge"],
                    "priority": b.priority.value,
                    "priority_label": priority_style["label"],
                    "priority_badge": priority_style["badge"],
                    "response_breached": b.response_breached,
                    "response_breach_hours": b.response_breach_hours,
                    "resolution_breached": b.resolution_breached,
                    "resolution_breach_hours": b.resolution_breach_hours,
                    "assigned_to_name": b.assigned_to_name,
                    "team_name": b.team_name,
                    "category_name": b.category_name,
                    "created_at": b.created_at.strftime("%b %d, %Y %H:%M")
                    if b.created_at
                    else "",
                }
            )

        context = {
            **base_context(request, auth, "SLA Dashboard", "support", db=db),
            "metrics": {
                "total_tickets": metrics.total_tickets,
                "resolved_tickets": metrics.resolved_tickets,
                "open_tickets": metrics.open_tickets,
                "response_compliance_pct": metrics.response_compliance_pct,
                "resolution_compliance_pct": metrics.resolution_compliance_pct,
                "avg_response_hours": metrics.avg_response_hours,
                "avg_resolution_hours": metrics.avg_resolution_hours,
                "response_met": metrics.response_met,
                "response_breached": metrics.response_breached,
                "response_pending": metrics.response_pending,
                "resolution_met": metrics.resolution_met,
                "resolution_breached": metrics.resolution_breached,
                "resolution_pending": metrics.resolution_pending,
                "by_priority": metrics.by_priority,
            },
            "breached_tickets": breached_formatted,
            "breached_count": len(breached),
            "team_performance": team_performance,
            "category_performance": category_performance,
            "aging_buckets": [
                {
                    "label": b.label,
                    "count": b.count,
                    "tickets": b.tickets[:5],  # Limit to 5 per bucket for summary
                }
                for b in aging
            ],
            "date_from": parsed_date_from.isoformat(),
            "date_to": parsed_date_to.isoformat(),
        }

        return templates.TemplateResponse(
            request, "support/sla_dashboard.html", context
        )

    def breached_tickets_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        breach_type: str = "all",
        include_resolved: bool = False,
    ) -> HTMLResponse:
        """Render the breached tickets report page."""
        from app.web.deps import base_context

        org_id = coerce_uuid(auth.organization_id)

        breached = sla_service.get_breached_tickets(
            db,
            org_id,
            breach_type=breach_type,
            include_resolved=include_resolved,
            limit=100,
        )

        # Format for display
        breached_formatted = []
        for b in breached:
            status_style = STATUS_STYLES.get(b.status, STATUS_STYLES[TicketStatus.OPEN])
            priority_style = PRIORITY_STYLES.get(
                b.priority, PRIORITY_STYLES[TicketPriority.MEDIUM]
            )
            breached_formatted.append(
                {
                    "ticket_id": str(b.ticket_id),
                    "ticket_number": b.ticket_number,
                    "subject": b.subject,
                    "status": b.status.value,
                    "status_label": status_style["label"],
                    "status_badge": status_style["badge"],
                    "priority": b.priority.value,
                    "priority_label": priority_style["label"],
                    "priority_badge": priority_style["badge"],
                    "response_target_hours": b.response_target_hours,
                    "response_hours": b.response_hours,
                    "response_breached": b.response_breached,
                    "response_breach_hours": b.response_breach_hours,
                    "resolution_target_hours": b.resolution_target_hours,
                    "resolution_hours": b.resolution_hours,
                    "resolution_breached": b.resolution_breached,
                    "resolution_breach_hours": b.resolution_breach_hours,
                    "assigned_to_name": b.assigned_to_name,
                    "team_name": b.team_name,
                    "category_name": b.category_name,
                    "created_at": b.created_at.strftime("%b %d, %Y %H:%M")
                    if b.created_at
                    else "",
                    "response_due_at": b.response_due_at.strftime("%b %d, %Y %H:%M")
                    if b.response_due_at
                    else "",
                    "resolution_due_at": b.resolution_due_at.strftime("%b %d, %Y %H:%M")
                    if b.resolution_due_at
                    else "",
                }
            )

        active_filters = build_active_filters(
            params={
                "breach_type": breach_type,
                # Only consider this active if it's explicitly enabled.
                "include_resolved": "true" if include_resolved else None,
            },
            labels={"include_resolved": "Include resolved"},
            options={
                "breach_type": {
                    "all": "All",
                    "response": "Response",
                    "resolution": "Resolution",
                },
                "include_resolved": {"true": "Yes"},
            },
        )
        context = {
            **base_context(request, auth, "Breached Tickets", "support", db=db),
            "tickets": breached_formatted,
            "breach_type": breach_type,
            "include_resolved": include_resolved,
            "total_count": len(breached_formatted),
            "active_filters": active_filters,
        }

        return templates.TemplateResponse(
            request, "support/breached_tickets.html", context
        )

    def aging_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        status_filter: str | None = None,
    ) -> HTMLResponse:
        """Render the ticket aging report page."""
        from app.web.deps import base_context

        org_id = coerce_uuid(auth.organization_id)

        status_list = None
        if status_filter:
            status_list = [s.strip() for s in status_filter.split(",")]

        aging = sla_service.get_aging_report(
            db,
            org_id,
            status_filter=status_list,
        )

        context = {
            **base_context(request, auth, "Ticket Aging Report", "support", db=db),
            "aging_buckets": [
                {
                    "label": b.label,
                    "count": b.count,
                    "tickets": b.tickets,
                    "min_hours": b.min_hours,
                    "max_hours": b.max_hours,
                }
                for b in aging
            ],
            "total_count": sum(b.count for b in aging),
            "status_filter": status_filter or "",
            "statuses": [
                {"value": s.value, "label": STATUS_STYLES[s]["label"]}
                for s in [TicketStatus.OPEN, TicketStatus.REPLIED, TicketStatus.ON_HOLD]
            ],
        }

        return templates.TemplateResponse(request, "support/aging_report.html", context)

    def _format_sla_status(self, sla_status) -> dict[str, Any] | None:
        """Format SLA status for template display."""

        if not sla_status:
            return None

        # Determine response status
        if sla_status.first_response_at:
            if sla_status.response_breached:
                response_status = "breached"
                response_status_label = "Breached"
                response_status_class = (
                    "text-rose-600 bg-rose-50 dark:text-rose-400 dark:bg-rose-900/30"
                )
            else:
                response_status = "met"
                response_status_label = "Met"
                response_status_class = "text-emerald-600 bg-emerald-50 dark:text-emerald-400 dark:bg-emerald-900/30"
        elif sla_status.response_breached:
            response_status = "overdue"
            response_status_label = "Overdue"
            response_status_class = (
                "text-rose-600 bg-rose-50 dark:text-rose-400 dark:bg-rose-900/30"
            )
        else:
            response_status = "pending"
            response_status_label = "Pending"
            response_status_class = (
                "text-amber-600 bg-amber-50 dark:text-amber-400 dark:bg-amber-900/30"
            )

        # Determine resolution status
        if sla_status.resolved_at:
            if sla_status.resolution_breached:
                resolution_status = "breached"
                resolution_status_label = "Breached"
                resolution_status_class = (
                    "text-rose-600 bg-rose-50 dark:text-rose-400 dark:bg-rose-900/30"
                )
            else:
                resolution_status = "met"
                resolution_status_label = "Met"
                resolution_status_class = "text-emerald-600 bg-emerald-50 dark:text-emerald-400 dark:bg-emerald-900/30"
        elif sla_status.resolution_breached:
            resolution_status = "overdue"
            resolution_status_label = "Overdue"
            resolution_status_class = (
                "text-rose-600 bg-rose-50 dark:text-rose-400 dark:bg-rose-900/30"
            )
        else:
            resolution_status = "pending"
            resolution_status_label = "Pending"
            resolution_status_class = (
                "text-amber-600 bg-amber-50 dark:text-amber-400 dark:bg-amber-900/30"
            )

        # Format hours for display
        def format_hours(hours):
            if hours is None:
                return None
            if hours < 1:
                return f"{int(hours * 60)}m"
            elif hours < 24:
                return f"{hours:.1f}h"
            else:
                days = hours / 24
                return f"{days:.1f}d"

        return {
            # Response SLA
            "response_target_hours": sla_status.response_target_hours,
            "response_target_formatted": format_hours(sla_status.response_target_hours),
            "response_due_at": sla_status.response_due_at.strftime("%b %d, %Y %H:%M")
            if sla_status.response_due_at
            else None,
            "first_response_at": sla_status.first_response_at.strftime(
                "%b %d, %Y %H:%M"
            )
            if sla_status.first_response_at
            else None,
            "response_hours": sla_status.response_hours,
            "response_hours_formatted": format_hours(sla_status.response_hours),
            "response_breached": sla_status.response_breached,
            "response_breach_hours": sla_status.response_breach_hours,
            "response_breach_formatted": format_hours(sla_status.response_breach_hours),
            "response_status": response_status,
            "response_status_label": response_status_label,
            "response_status_class": response_status_class,
            # Resolution SLA
            "resolution_target_hours": sla_status.resolution_target_hours,
            "resolution_target_formatted": format_hours(
                sla_status.resolution_target_hours
            ),
            "resolution_due_at": sla_status.resolution_due_at.strftime(
                "%b %d, %Y %H:%M"
            )
            if sla_status.resolution_due_at
            else None,
            "resolved_at": sla_status.resolved_at.strftime("%b %d, %Y %H:%M")
            if sla_status.resolved_at
            else None,
            "resolution_hours": sla_status.resolution_hours,
            "resolution_hours_formatted": format_hours(sla_status.resolution_hours),
            "resolution_breached": sla_status.resolution_breached,
            "resolution_breach_hours": sla_status.resolution_breach_hours,
            "resolution_breach_formatted": format_hours(
                sla_status.resolution_breach_hours
            ),
            "resolution_status": resolution_status,
            "resolution_status_label": resolution_status_label,
            "resolution_status_class": resolution_status_class,
        }

    def _get_linked_tasks(
        self,
        db: Session,
        organization_id: UUID,
        ticket_id: UUID,
    ) -> list[Any]:
        """Get tasks linked to this ticket."""
        from sqlalchemy.orm import joinedload

        from app.models.pm.task import Task

        stmt = (
            select(Task)
            .options(joinedload(Task.project))
            .where(
                Task.organization_id == organization_id,
                Task.ticket_id == ticket_id,
            )
            .order_by(Task.created_at.desc())
        )
        return list(db.scalars(stmt).all())

    def _format_activity_timeline(
        self,
        activities: list[Any],
    ) -> list[dict[str, Any]]:
        """Format activity timeline for template display."""
        from app.models.support.comment import CommentType

        # Action type to display config
        ACTION_CONFIG = {
            "status_change": {
                "icon": "M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15",
                "icon_bg": "bg-sky-100 text-sky-600 dark:bg-sky-900/30 dark:text-sky-400",
                "label": "Status changed",
            },
            "assigned": {
                "icon": "M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z",
                "icon_bg": "bg-violet-100 text-violet-600 dark:bg-violet-900/30 dark:text-violet-400",
                "label": "Assigned",
            },
            "priority_change": {
                "icon": "M3 21v-4m0 0V5a2 2 0 012-2h6.5l1 1H21l-3 6 3 6h-8.5l-1-1H5a2 2 0 00-2 2zm9-13.5V9",
                "icon_bg": "bg-amber-100 text-amber-600 dark:bg-amber-900/30 dark:text-amber-400",
                "label": "Priority changed",
            },
            "category_change": {
                "icon": "M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z",
                "icon_bg": "bg-indigo-100 text-indigo-600 dark:bg-indigo-900/30 dark:text-indigo-400",
                "label": "Category changed",
            },
            "team_change": {
                "icon": "M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z",
                "icon_bg": "bg-teal-100 text-teal-600 dark:bg-teal-900/30 dark:text-teal-400",
                "label": "Team changed",
            },
        }

        COMMENT_CONFIG = {
            CommentType.COMMENT: {
                "icon": "M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z",
                "icon_bg": "bg-emerald-100 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-400",
                "label": "Comment",
            },
            CommentType.INTERNAL_NOTE: {
                "icon": "M15 12a3 3 0 11-6 0 3 3 0 016 0z M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z",
                "icon_bg": "bg-amber-100 text-amber-600 dark:bg-amber-900/30 dark:text-amber-400",
                "label": "Internal Note",
            },
        }

        formatted = []
        for activity in activities:
            author_name = None
            if activity.author:
                author_name = f"{activity.author.first_name or ''} {activity.author.last_name or ''}".strip()

            if activity.comment_type == CommentType.SYSTEM:
                config = ACTION_CONFIG.get(
                    activity.action,
                    {
                        "icon": "M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z",
                        "icon_bg": "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400",
                        "label": "Activity",
                    },
                )
                formatted.append(
                    {
                        "type": "system",
                        "action": activity.action,
                        "content": activity.content,
                        "old_value": activity.old_value,
                        "new_value": activity.new_value,
                        "author_name": author_name,
                        "created_at": activity.created_at.strftime("%b %d, %Y %H:%M")
                        if activity.created_at
                        else "",
                        "icon": config["icon"],
                        "icon_bg": config["icon_bg"],
                        "label": config["label"],
                    }
                )
            else:
                config = COMMENT_CONFIG.get(
                    activity.comment_type, COMMENT_CONFIG[CommentType.COMMENT]
                )
                formatted.append(
                    {
                        "type": "comment"
                        if activity.comment_type == CommentType.COMMENT
                        else "internal_note",
                        "content": activity.content,
                        "is_internal": activity.is_internal,
                        "comment_id": str(activity.comment_id),
                        "author_name": author_name or "Unknown",
                        "created_at": activity.created_at.strftime("%b %d, %Y %H:%M")
                        if activity.created_at
                        else "",
                        "icon": config["icon"],
                        "icon_bg": config["icon_bg"],
                        "label": config["label"],
                    }
                )

        return formatted

    # ========================================================================
    # Delegated Methods - Comments
    # ========================================================================

    def add_comment_response(self, *args, **kwargs):
        """Delegate to comment web service."""
        return comment_web_service.add_comment_response(*args, **kwargs)

    def delete_comment_response(self, *args, **kwargs):
        """Delegate to comment web service."""
        return comment_web_service.delete_comment_response(*args, **kwargs)

    # ========================================================================
    # Delegated Methods - Attachments
    # ========================================================================

    async def upload_attachment_response(self, *args, **kwargs):
        """Delegate to attachment web service."""
        return await attachment_web_service.upload_attachment_response(*args, **kwargs)

    def download_attachment_response(self, *args, **kwargs):
        """Delegate to attachment web service."""
        return attachment_web_service.download_attachment_response(*args, **kwargs)

    def delete_attachment_response(self, *args, **kwargs):
        """Delegate to attachment web service."""
        return attachment_web_service.delete_attachment_response(*args, **kwargs)

    # ========================================================================
    # Delegated Methods - Teams
    # ========================================================================

    def list_teams_response(self, *args, **kwargs):
        """Delegate to team web service."""
        return team_web_service.list_teams_response(*args, **kwargs)

    def team_form_response(self, *args, **kwargs):
        """Delegate to team web service."""
        return team_web_service.team_form_response(*args, **kwargs)

    def create_team_response(self, *args, **kwargs):
        """Delegate to team web service."""
        return team_web_service.create_team_response(*args, **kwargs)

    def team_detail_response(self, *args, **kwargs):
        """Delegate to team web service."""
        return team_web_service.team_detail_response(*args, **kwargs)

    def add_team_member_response(self, *args, **kwargs):
        """Delegate to team web service."""
        return team_web_service.add_team_member_response(*args, **kwargs)

    def remove_team_member_response(self, *args, **kwargs):
        """Delegate to team web service."""
        return team_web_service.remove_team_member_response(*args, **kwargs)

    def toggle_member_availability_response(self, *args, **kwargs):
        """Delegate to team web service."""
        return team_web_service.toggle_member_availability_response(*args, **kwargs)

    def update_member_weight_response(self, *args, **kwargs):
        """Delegate to team web service."""
        return team_web_service.update_member_weight_response(*args, **kwargs)

    # ========================================================================
    # Delegated Methods - Categories
    # ========================================================================

    def list_categories_response(self, *args, **kwargs):
        """Delegate to category web service."""
        return category_web_service.list_categories_response(*args, **kwargs)

    def category_form_response(self, *args, **kwargs):
        """Delegate to category web service."""
        return category_web_service.category_form_response(*args, **kwargs)

    def create_category_response(self, *args, **kwargs):
        """Delegate to category web service."""
        return category_web_service.create_category_response(*args, **kwargs)

    def update_category_response(self, *args, **kwargs):
        """Delegate to category web service."""
        return category_web_service.update_category_response(*args, **kwargs)

    # ========================================================================
    # Delegated Methods - Teams (continued)
    # ========================================================================

    def update_team_response(self, *args, **kwargs):
        """Delegate to team web service."""
        return team_web_service.update_team_response(*args, **kwargs)

    # ========================================================================
    # Bulk Operations
    # ========================================================================

    def bulk_update_status_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        ticket_ids: list[str],
        new_status: str,
        notes: str | None = None,
    ) -> RedirectResponse:
        """Bulk update status for multiple tickets."""
        org_id = coerce_uuid(auth.organization_id)
        user_id = coerce_uuid(auth.user_id)

        # Validate status is provided
        if not new_status or not new_status.strip():
            return RedirectResponse(
                url="/support/tickets?error=no_status_selected",
                status_code=303,
            )

        # Convert string IDs to UUIDs, skipping invalid ones
        uuids = []
        for tid in ticket_ids:
            if tid:
                try:
                    uuids.append(coerce_uuid(tid))
                except (ValueError, HTTPException):
                    logger.warning(f"Invalid ticket ID skipped: {tid}")

        if not uuids:
            return RedirectResponse(
                url="/support/tickets?error=no_tickets_selected",
                status_code=303,
            )

        try:
            result = ticket_service.bulk_update_status(
                db,
                org_id,
                uuids,
                user_id,
                new_status=new_status.strip(),
                notes=notes,
            )
            db.commit()

            return RedirectResponse(
                url=f"/support/tickets?bulk_status=success&updated={result['success']}&errors={result['error']}&saved=1",
                status_code=303,
            )
        except Exception:
            db.rollback()
            logger.exception("Bulk status update failed")
            return RedirectResponse(
                url="/support/tickets?error=bulk_failed",
                status_code=303,
            )

    def bulk_assign_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        ticket_ids: list[str],
        assigned_to_id: str,
    ) -> RedirectResponse:
        """Bulk assign multiple tickets to an employee."""
        org_id = coerce_uuid(auth.organization_id)
        user_id = coerce_uuid(auth.user_id)

        # Validate assignee is provided
        if not assigned_to_id or not assigned_to_id.strip():
            return RedirectResponse(
                url="/support/tickets?error=no_assignee_selected",
                status_code=303,
            )

        # Validate assignee UUID
        try:
            assignee_uuid = coerce_uuid(assigned_to_id)
        except (ValueError, HTTPException):
            return RedirectResponse(
                url="/support/tickets?error=invalid_assignee",
                status_code=303,
            )

        # Convert string IDs to UUIDs, skipping invalid ones
        uuids = []
        for tid in ticket_ids:
            if tid:
                try:
                    uuids.append(coerce_uuid(tid))
                except (ValueError, HTTPException):
                    logger.warning(f"Invalid ticket ID skipped: {tid}")

        if not uuids:
            return RedirectResponse(
                url="/support/tickets?error=no_tickets_selected",
                status_code=303,
            )

        try:
            result = ticket_service.bulk_assign(
                db,
                org_id,
                uuids,
                user_id,
                assigned_to_id=assignee_uuid,
            )
            db.commit()

            return RedirectResponse(
                url=f"/support/tickets?bulk_assign=success&updated={result['success']}&errors={result['error']}&saved=1",
                status_code=303,
            )
        except Exception:
            db.rollback()
            logger.exception("Bulk assign failed")
            return RedirectResponse(
                url="/support/tickets?error=bulk_failed",
                status_code=303,
            )

    def bulk_archive_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        ticket_ids: list[str],
    ) -> RedirectResponse:
        """Bulk archive multiple tickets."""
        org_id = coerce_uuid(auth.organization_id)
        user_id = coerce_uuid(auth.user_id)

        # Convert string IDs to UUIDs, skipping invalid ones
        uuids = []
        for tid in ticket_ids:
            if tid:
                try:
                    uuids.append(coerce_uuid(tid))
                except (ValueError, HTTPException):
                    logger.warning(f"Invalid ticket ID skipped: {tid}")

        if not uuids:
            return RedirectResponse(
                url="/support/tickets?error=no_tickets_selected",
                status_code=303,
            )

        try:
            result = ticket_service.bulk_archive(
                db,
                org_id,
                uuids,
                user_id,
            )
            db.commit()

            return RedirectResponse(
                url=f"/support/tickets?bulk_archive=success&archived={result['success']}&errors={result['error']}&saved=1",
                status_code=303,
            )
        except Exception:
            db.rollback()
            logger.exception("Bulk archive failed")
            return RedirectResponse(
                url="/support/tickets?error=bulk_failed",
                status_code=303,
            )


# Singleton instance
support_web_service = SupportWebService()
