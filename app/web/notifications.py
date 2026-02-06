"""
Notification Web Routes.

HTML template routes for the notification center.
"""

from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.notification import EntityType, NotificationType
from app.services.notification import notification_service
from app.web.deps import (
    WebAuthContext,
    base_context,
    get_db,
    require_web_auth,
)
from app.templates import templates


router = APIRouter(tags=["notifications-web"])


def format_relative_time(dt: datetime) -> str:
    """Format datetime as relative time string."""
    now = datetime.utcnow()
    diff = now - dt

    if diff < timedelta(minutes=1):
        return "Just now"
    elif diff < timedelta(hours=1):
        minutes = int(diff.total_seconds() / 60)
        return f"{minutes} min ago"
    elif diff < timedelta(days=1):
        hours = int(diff.total_seconds() / 3600)
        return f"{hours}h ago"
    elif diff < timedelta(days=7):
        days = diff.days
        return f"{days}d ago"
    else:
        return dt.strftime("%b %d")


def notification_type_to_display(
    entity_type: EntityType, notification_type: NotificationType
) -> str:
    """Map notification types to display type for icon selection."""
    # Map to UI types: mention, invoice, payment, alert, info
    if notification_type in (
        NotificationType.MENTION,
        NotificationType.COMMENT,
        NotificationType.REPLY,
    ):
        return "mention"
    elif notification_type in (
        NotificationType.APPROVED,
        NotificationType.COMPLETED,
        NotificationType.RESOLVED,
    ):
        return "payment"  # Use green icon for approvals/completions
    elif notification_type in (
        NotificationType.REJECTED,
        NotificationType.OVERDUE,
        NotificationType.ALERT,
    ):
        return "alert"
    elif (
        entity_type in (EntityType.EXPENSE,)
        and notification_type == NotificationType.SUBMITTED
    ):
        return "invoice"
    else:
        return "info"


def format_notification_for_template(notification) -> dict:
    """Convert Notification model to template-friendly dict."""
    return {
        "id": str(notification.notification_id),
        "type": notification_type_to_display(
            notification.entity_type, notification.notification_type
        ),
        "entity_type": notification.entity_type.value,
        "notification_type": notification.notification_type.value,
        "title": notification.title,
        "message": notification.message,
        "url": notification.action_url or "#",
        "time": format_relative_time(notification.created_at),
        "created_at": notification.created_at.isoformat(),
        "read": notification.is_read,
        "actor_name": notification.actor.name if notification.actor else None,
    }


@router.get("/notifications", response_class=HTMLResponse)
async def notifications_list(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
    filter: Optional[str] = Query(
        None, description="Filter: all, unread, or entity type"
    ),
    page: int = Query(1, ge=1),
):
    """Display notification center with all notifications."""
    if not auth.is_authenticated:
        return RedirectResponse(url="/login", status_code=302)

    per_page = 20
    offset = (page - 1) * per_page

    # Determine filter settings
    unread_only = filter == "unread"
    entity_type = None
    if filter and filter not in ("all", "unread"):
        try:
            entity_type = EntityType(filter.upper())
        except ValueError:
            pass

    # Get notifications
    notifications = notification_service.list_notifications(
        db,
        recipient_id=auth.person_id,
        organization_id=auth.organization_id,
        unread_only=unread_only,
        entity_type=entity_type,
        limit=per_page + 1,  # Get one extra to check if there are more
        offset=offset,
    )

    has_more = len(notifications) > per_page
    notifications = notifications[:per_page]

    # Get unread count for badge
    unread_count = notification_service.get_unread_count(
        db, auth.person_id, auth.organization_id
    )

    # Format for template
    formatted_notifications = [
        format_notification_for_template(n) for n in notifications
    ]

    # Get counts by entity type for filter pills
    entity_counts = {}
    for et in EntityType:
        count = len(
            notification_service.list_notifications(
                db,
                recipient_id=auth.person_id,
                organization_id=auth.organization_id,
                entity_type=et,
                limit=1000,
            )
        )
        if count > 0:
            entity_counts[et.value] = count

    context = base_context(
        request=request,
        auth=auth,
        page_title="Notifications",
        active_module="notifications",
        notifications=formatted_notifications[:5],  # For header dropdown
        db=db,
    )
    context.update(
        {
            "notifications_list": formatted_notifications,
            "unread_count": unread_count,
            "current_filter": filter or "all",
            "entity_counts": entity_counts,
            "page": page,
            "has_more": has_more,
            "has_previous": page > 1,
        }
    )

    return templates.TemplateResponse(request, "notifications/list.html", context)


@router.post("/notifications/{notification_id}/read", response_class=HTMLResponse)
async def mark_notification_read(
    request: Request,
    notification_id: UUID,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Mark a single notification as read."""
    if not auth.is_authenticated:
        return RedirectResponse(url="/login", status_code=302)

    notification_service.mark_read(db, notification_id)
    db.commit()

    # Return updated unread count for HTMX
    unread_count = notification_service.get_unread_count(
        db, auth.person_id, auth.organization_id
    )

    return HTMLResponse(
        content=f'<span class="notification-count">{unread_count}</span>',
        headers={"HX-Trigger": "notificationRead"},
    )


@router.post("/notifications/mark-all-read", response_class=HTMLResponse)
async def mark_all_notifications_read(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Mark all notifications as read."""
    if not auth.is_authenticated:
        return RedirectResponse(url="/login", status_code=302)

    count = notification_service.mark_all_read(db, auth.person_id, auth.organization_id)
    db.commit()

    # Redirect back to notifications page
    return RedirectResponse(url="/notifications", status_code=302)


def get_notifications_for_context(
    db: Session,
    person_id: UUID,
    organization_id: Optional[UUID] = None,
    limit: int = 5,
) -> tuple[list[dict], int]:
    """
    Get formatted notifications for template context.

    Returns:
        Tuple of (notifications list, unread count)
    """
    notifications = notification_service.list_notifications(
        db,
        recipient_id=person_id,
        organization_id=organization_id,
        limit=limit,
    )

    unread_count = notification_service.get_unread_count(db, person_id, organization_id)

    formatted = [format_notification_for_template(n) for n in notifications]

    return formatted, unread_count
