"""
Support Web Service - Categories Module.

Handles category management template responses.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from fastapi import Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.common import coerce_uuid
from app.services.support.category import category_service
from app.templates import templates

if TYPE_CHECKING:
    from app.web.deps import WebAuthContext

logger = logging.getLogger(__name__)


class CategoryWebService:
    """Web service for category management operations."""

    def list_categories_response(
        self,
        request: Request,
        auth: "WebAuthContext",
        db: Session,
    ) -> HTMLResponse:
        """Render the categories list page."""
        from app.web.deps import base_context

        org_id = coerce_uuid(auth.organization_id)

        categories = category_service.list_categories(db, org_id, active_only=False)

        # Format categories for display
        formatted_categories = [
            {
                "category_id": str(c.category_id),
                "category_code": c.category_code,
                "category_name": c.category_name,
                "description": c.description,
                "color": c.color,
                "icon": c.icon,
                "is_active": c.is_active,
                "display_order": c.display_order,
                "default_priority": c.default_priority,
                "response_hours": c.response_hours,
                "resolution_hours": c.resolution_hours,
            }
            for c in categories
        ]

        context = {
            **base_context(request, auth, "Ticket Categories", "support", db=db),
            "categories": formatted_categories,
        }

        return templates.TemplateResponse(
            request, "support/categories.html", context
        )

    def category_form_response(
        self,
        request: Request,
        auth: "WebAuthContext",
        db: Session,
        category_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> HTMLResponse:
        """Render the category create/edit form."""
        from app.web.deps import base_context
        from app.models.support.ticket import TicketPriority

        category = None
        title = "New Category"
        org_id = coerce_uuid(auth.organization_id)

        if category_id:
            cid = coerce_uuid(category_id)
            category = category_service.get_category(db, org_id, cid)
            if category:
                title = f"Edit {category.category_name}"

        # Available icons (Heroicons names)
        icons = [
            {"value": "wifi", "label": "Network"},
            {"value": "credit-card", "label": "Billing"},
            {"value": "server", "label": "Hardware"},
            {"value": "code", "label": "Software"},
            {"value": "wrench", "label": "Installation"},
            {"value": "help-circle", "label": "Support"},
            {"value": "phone", "label": "Phone"},
            {"value": "mail", "label": "Email"},
            {"value": "user", "label": "Account"},
            {"value": "shield", "label": "Security"},
        ]

        # Available colors
        colors = [
            {"value": "#3B82F6", "label": "Blue"},
            {"value": "#10B981", "label": "Green"},
            {"value": "#F59E0B", "label": "Amber"},
            {"value": "#8B5CF6", "label": "Purple"},
            {"value": "#06B6D4", "label": "Cyan"},
            {"value": "#EF4444", "label": "Red"},
            {"value": "#F97316", "label": "Orange"},
            {"value": "#EC4899", "label": "Pink"},
            {"value": "#6B7280", "label": "Gray"},
        ]

        priorities = [
            {"value": p.value, "label": p.value.title()}
            for p in TicketPriority
        ]

        context = {
            **base_context(request, auth, title, "support", db=db),
            "category": category,
            "icons": icons,
            "colors": colors,
            "priorities": priorities,
            "error": error,
        }

        return templates.TemplateResponse(
            request, "support/category_form.html", context
        )

    def create_category_response(
        self,
        request: Request,
        auth: "WebAuthContext",
        db: Session,
        *,
        category_code: str,
        category_name: str,
        description: Optional[str] = None,
        color: Optional[str] = None,
        icon: Optional[str] = None,
        default_priority: Optional[str] = None,
        response_hours: Optional[int] = None,
        resolution_hours: Optional[int] = None,
    ) -> Response:
        """Create a new category."""
        org_id = coerce_uuid(auth.organization_id)

        try:
            category, error = category_service.create_category(
                db, org_id,
                category_code=category_code,
                category_name=category_name,
                description=description,
                color=color,
                icon=icon,
                default_priority=default_priority,
                response_hours=response_hours,
                resolution_hours=resolution_hours,
            )

            if error:
                return self.category_form_response(
                    request, auth, db, error=error
                )

            db.commit()

            if not category:
                return self.category_form_response(
                    request, auth, db, error="Category not created"
                )

            return RedirectResponse(
                url="/support/categories",
                status_code=303,
            )

        except Exception as e:
            db.rollback()
            logger.exception("Failed to create category")
            return self.category_form_response(
                request, auth, db, error=str(e)
            )

    def update_category_response(
        self,
        request: Request,
        auth: "WebAuthContext",
        db: Session,
        category_id: str,
        *,
        category_name: Optional[str] = None,
        description: Optional[str] = None,
        color: Optional[str] = None,
        icon: Optional[str] = None,
        default_priority: Optional[str] = None,
        response_hours: Optional[int] = None,
        resolution_hours: Optional[int] = None,
        is_active: Optional[bool] = None,
    ) -> Response:
        """Update a category."""
        org_id = coerce_uuid(auth.organization_id)
        cid = coerce_uuid(category_id)

        try:
            category = category_service.update_category(
                db, org_id, cid,
                category_name=category_name,
                description=description,
                color=color,
                icon=icon,
                default_priority=default_priority,
                response_hours=response_hours,
                resolution_hours=resolution_hours,
                is_active=is_active,
            )

            db.commit()

            return RedirectResponse(
                url="/support/categories",
                status_code=303,
            )

        except Exception as e:
            db.rollback()
            logger.exception("Failed to update category")
            return self.category_form_response(
                request, auth, db, category_id=category_id, error=str(e)
            )


# Singleton instance
category_web_service = CategoryWebService()
