"""
Support API Endpoints.

REST API for support ticket management.
"""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import (
    require_organization_id,
    require_tenant_auth,
    require_tenant_permission,
)
from app.db import SessionLocal
from app.schemas.support import (
    TicketAssign,
    TicketCreate,
    TicketListResponse,
    TicketRead,
    TicketResolve,
    TicketSearchResult,
    TicketStats,
    TicketStatusUpdate,
    TicketSummary,
    TicketUpdate,
)
from app.services.common import coerce_uuid
from app.services.support.ticket import ticket_service

router = APIRouter(
    prefix="/support",
    tags=["support"],
    dependencies=[
        Depends(require_tenant_auth),
        Depends(require_tenant_permission("support:access")),
    ],
)

MANUAL_TICKET_CREATION_API_ENABLED = False


def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ============================================================================
# Ticket CRUD
# ============================================================================


@router.get("/tickets", response_model=TicketListResponse)
def list_tickets(
    organization_id: UUID = Depends(require_organization_id),
    status_filter: str | None = Query(None, alias="status"),
    priority: str | None = None,
    assigned_to_id: str | None = None,
    search: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """List tickets with filtering and pagination."""
    tickets, total = ticket_service.list_tickets(
        db,
        organization_id,
        status=status_filter,
        priority=priority,
        assigned_to_id=coerce_uuid(assigned_to_id) if assigned_to_id else None,
        search=search,
        date_from=date_from,
        date_to=date_to,
        page=page,
        per_page=per_page,
    )

    items = [
        TicketSummary(
            ticket_id=t.ticket_id,
            ticket_number=t.ticket_number,
            subject=t.subject,
            status=t.status.value,
            priority=t.priority.value,
            opening_date=t.opening_date,
            resolution_date=t.resolution_date,
        )
        for t in tickets
    ]

    return TicketListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/tickets/search", response_model=list[TicketSearchResult])
def search_tickets(
    q: str = Query(..., min_length=1),
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(20, ge=1, le=100),
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Search tickets for typeahead/autocomplete."""
    # Parse status filter
    status_list = None
    if status_filter:
        status_list = [s.strip() for s in status_filter.split(",") if s.strip()]

    tickets = ticket_service.search_tickets(
        db,
        organization_id,
        q,
        status_filter=status_list,
        limit=limit,
    )

    return [
        TicketSearchResult(
            ticket_id=t.ticket_id,
            ticket_number=t.ticket_number,
            subject=t.subject,
            status=t.status.value,
        )
        for t in tickets
    ]


@router.get("/tickets/stats", response_model=TicketStats)
def get_ticket_stats(
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get ticket statistics for dashboard."""
    stats = ticket_service.get_stats(db, organization_id)
    return TicketStats(**stats)


@router.get("/tickets/{ticket_id}", response_model=TicketRead)
def get_ticket(
    ticket_id: str,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a ticket by ID."""
    tid = coerce_uuid(ticket_id)

    ticket = ticket_service.get_ticket(db, organization_id, tid)
    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ticket not found",
        )

    return TicketRead.model_validate(ticket)


@router.post("/tickets", response_model=TicketRead, status_code=status.HTTP_201_CREATED)
def create_ticket(
    data: TicketCreate,
    auth: dict = Depends(require_tenant_permission("support:tickets:create")),
    db: Session = Depends(get_db),
):
    """Create a new support ticket."""
    if not MANUAL_TICKET_CREATION_API_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manual ticket creation is disabled. Tickets are synced from CRM.",
        )

    org_id = coerce_uuid(auth["organization_id"])
    user_id = coerce_uuid(auth["person_id"])

    ticket = ticket_service.create_ticket(
        db,
        org_id,
        user_id,
        subject=data.subject,
        description=data.description,
        priority=data.priority,
        raised_by_email=data.raised_by_email,
        raised_by_id=data.raised_by_id,
        assigned_to_id=data.assigned_to_id,
        project_id=data.project_id,
        opening_date=data.opening_date,
    )

    return TicketRead.model_validate(ticket)


@router.patch("/tickets/{ticket_id}", response_model=TicketRead)
def update_ticket(
    ticket_id: str,
    data: TicketUpdate,
    auth: dict = Depends(require_tenant_permission("support:tickets:update")),
    db: Session = Depends(get_db),
):
    """Update a ticket."""
    org_id = coerce_uuid(auth["organization_id"])
    user_id = coerce_uuid(auth["person_id"])
    tid = coerce_uuid(ticket_id)

    ticket = ticket_service.update_ticket(
        db,
        org_id,
        tid,
        user_id,
        subject=data.subject,
        description=data.description,
        priority=data.priority,
        raised_by_email=data.raised_by_email,
        project_id=data.project_id,
        category_id=data.category_id,
        team_id=data.team_id,
    )

    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ticket not found",
        )

    return TicketRead.model_validate(ticket)


# ============================================================================
# Ticket Actions
# ============================================================================


@router.post("/tickets/{ticket_id}/status", response_model=TicketRead)
def update_ticket_status(
    ticket_id: str,
    data: TicketStatusUpdate,
    auth: dict = Depends(require_tenant_permission("support:tickets:update")),
    db: Session = Depends(get_db),
):
    """Update ticket status."""
    org_id = coerce_uuid(auth["organization_id"])
    user_id = coerce_uuid(auth["person_id"])
    tid = coerce_uuid(ticket_id)

    ticket, error = ticket_service.update_status(
        db,
        org_id,
        tid,
        user_id,
        data.status,
        data.notes,
    )

    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error,
        )

    return TicketRead.model_validate(ticket)


@router.post("/tickets/{ticket_id}/assign", response_model=TicketRead)
def assign_ticket(
    ticket_id: str,
    data: TicketAssign,
    auth: dict = Depends(require_tenant_permission("support:tickets:assign")),
    db: Session = Depends(get_db),
):
    """Assign a ticket to an employee."""
    org_id = coerce_uuid(auth["organization_id"])
    user_id = coerce_uuid(auth["person_id"])
    tid = coerce_uuid(ticket_id)

    ticket = ticket_service.assign_ticket(
        db,
        org_id,
        tid,
        user_id,
        data.assigned_to_id,
    )

    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ticket not found",
        )

    return TicketRead.model_validate(ticket)


@router.post("/tickets/{ticket_id}/resolve", response_model=TicketRead)
def resolve_ticket(
    ticket_id: str,
    data: TicketResolve,
    auth: dict = Depends(require_tenant_permission("support:tickets:resolve")),
    db: Session = Depends(get_db),
):
    """Mark a ticket as resolved."""
    org_id = coerce_uuid(auth["organization_id"])
    user_id = coerce_uuid(auth["person_id"])
    tid = coerce_uuid(ticket_id)

    ticket, error = ticket_service.resolve_ticket(
        db,
        org_id,
        tid,
        user_id,
        data.resolution,
    )

    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error,
        )

    return TicketRead.model_validate(ticket)


# ============================================================================
# Related Entities
# ============================================================================


@router.get("/tickets/{ticket_id}/expenses")
def get_ticket_expenses(
    ticket_id: str,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get expense claims linked to a ticket."""
    tid = coerce_uuid(ticket_id)

    # Verify ticket exists
    ticket = ticket_service.get_ticket(db, organization_id, tid)
    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ticket not found",
        )

    expenses = ticket_service.get_linked_expenses(db, organization_id, tid)

    return {
        "ticket_id": str(tid),
        "ticket_number": ticket.ticket_number,
        "expenses": [
            {
                "claim_id": str(e.claim_id),
                "claim_number": e.claim_number,
                "purpose": e.purpose,
                "status": e.status.value if e.status else "DRAFT",
                "total_claimed_amount": float(e.total_claimed_amount)
                if e.total_claimed_amount
                else 0,
                "currency_code": e.currency_code,
                "claim_date": e.claim_date.isoformat() if e.claim_date else None,
            }
            for e in expenses
        ],
    }
