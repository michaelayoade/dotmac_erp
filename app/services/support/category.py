"""
Ticket Category Service.

Handles category management for support tickets.
"""

import logging
import uuid
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.support.category import TicketCategory

logger = logging.getLogger(__name__)


class CategoryService:
    """Service for managing ticket categories."""

    def list_categories(
        self,
        db: Session,
        organization_id: uuid.UUID,
        active_only: bool = True,
    ) -> list[TicketCategory]:
        """
        List categories for an organization.

        Args:
            db: Database session
            organization_id: Organization UUID
            active_only: Only return active categories

        Returns:
            List of categories ordered by display_order
        """
        query = select(TicketCategory).where(
            TicketCategory.organization_id == organization_id
        )

        if active_only:
            query = query.where(TicketCategory.is_active == True)  # noqa: E712

        query = query.order_by(
            TicketCategory.display_order,
            TicketCategory.category_name,
        )

        return list(db.execute(query).scalars().all())

    def get_category(
        self,
        db: Session,
        organization_id: uuid.UUID,
        category_id: uuid.UUID,
    ) -> TicketCategory | None:
        """Get a category by ID, scoped to organization."""
        return db.execute(
            select(TicketCategory).where(
                TicketCategory.category_id == category_id,
                TicketCategory.organization_id == organization_id,
            )
        ).scalar_one_or_none()

    def get_category_by_code(
        self,
        db: Session,
        organization_id: uuid.UUID,
        category_code: str,
    ) -> TicketCategory | None:
        """Get a category by code."""
        return db.execute(
            select(TicketCategory).where(
                TicketCategory.organization_id == organization_id,
                TicketCategory.category_code == category_code,
            )
        ).scalar_one_or_none()

    def create_category(
        self,
        db: Session,
        organization_id: uuid.UUID,
        category_code: str,
        category_name: str,
        description: str | None = None,
        color: str | None = None,
        icon: str | None = None,
        display_order: int | None = None,
        default_team_id: uuid.UUID | None = None,
        default_priority: str | None = None,
        response_hours: int | None = None,
        resolution_hours: int | None = None,
    ) -> tuple[TicketCategory | None, str | None]:
        """
        Create a new category.

        Args:
            db: Database session
            organization_id: Organization UUID
            category_code: Short code
            category_name: Display name
            description: Optional description
            color: Hex color for badge
            icon: Icon name
            default_team_id: Default team for this category
            default_priority: Default priority
            response_hours: SLA response time
            resolution_hours: SLA resolution time

        Returns:
            (category, error_message)
        """
        # Check for duplicate code
        existing = self.get_category_by_code(db, organization_id, category_code)
        if existing:
            return None, f"Category code '{category_code}' already exists"

        # Get max display order
        max_order = (
            db.execute(
                select(TicketCategory.display_order)
                .where(TicketCategory.organization_id == organization_id)
                .order_by(TicketCategory.display_order.desc())
            ).scalar_one_or_none()
            or 0
        )
        target_order = display_order if display_order is not None else max_order + 1

        category = TicketCategory(
            organization_id=organization_id,
            category_code=category_code.upper(),
            category_name=category_name,
            description=description,
            color=color,
            icon=icon,
            display_order=target_order,
            default_team_id=default_team_id,
            default_priority=default_priority,
            response_hours=response_hours,
            resolution_hours=resolution_hours,
        )
        db.add(category)
        db.flush()

        logger.info("Created category %s: %s", category_code, category_name)

        return category, None

    def update_category(
        self,
        db: Session,
        organization_id: uuid.UUID,
        category_id: uuid.UUID,
        category_name: str | None = None,
        description: str | None = None,
        color: str | None = None,
        icon: str | None = None,
        display_order: int | None = None,
        default_team_id: uuid.UUID | None = None,
        default_priority: str | None = None,
        response_hours: int | None = None,
        resolution_hours: int | None = None,
        is_active: bool | None = None,
    ) -> TicketCategory | None:
        """Update a category."""
        category = self.get_category(db, organization_id, category_id)
        if not category:
            return None

        if category_name is not None:
            category.category_name = category_name
        if description is not None:
            category.description = description
        if color is not None:
            category.color = color
        if icon is not None:
            category.icon = icon
        if display_order is not None:
            category.display_order = display_order
        if default_team_id is not None:
            category.default_team_id = default_team_id
        if default_priority is not None:
            category.default_priority = default_priority
        if response_hours is not None:
            category.response_hours = response_hours
        if resolution_hours is not None:
            category.resolution_hours = resolution_hours
        if is_active is not None:
            category.is_active = is_active

        db.flush()

        return category

    def delete_category(
        self,
        db: Session,
        organization_id: uuid.UUID,
        category_id: uuid.UUID,
        hard_delete: bool = False,
    ) -> tuple[bool, str | None]:
        """
        Delete a category.

        Args:
            db: Database session
            category_id: Category UUID
            hard_delete: If true, permanently delete

        Returns:
            (success, error_message)
        """
        category = self.get_category(db, organization_id, category_id)
        if not category:
            return False, "Category not found"

        # Check for tickets using this category
        from app.models.support.ticket import Ticket

        ticket_count = db.execute(
            select(Ticket.ticket_id).where(Ticket.category_id == category_id).limit(1)
        ).scalar_one_or_none()

        if ticket_count and hard_delete:
            return False, "Cannot delete category with existing tickets"

        if hard_delete:
            db.delete(category)
        else:
            category.is_active = False
            db.flush()

        logger.info("Deleted category %s", category_id)

        return True, None

    def seed_default_categories(
        self,
        db: Session,
        organization_id: uuid.UUID,
    ) -> list[TicketCategory]:
        """
        Create default categories for a new organization.

        Args:
            db: Database session
            organization_id: Organization UUID

        Returns:
            List of created categories
        """
        defaults = [
            {
                "category_code": "TECH",
                "category_name": "Technical Support",
                "description": "Technical issues, troubleshooting, and system problems",
                "color": "#3B82F6",  # Blue
                "icon": "wrench",
                "default_priority": "MEDIUM",
                "response_hours": 4,
                "resolution_hours": 24,
            },
            {
                "category_code": "BILL",
                "category_name": "Billing & Payments",
                "description": "Invoicing, payments, refunds, and account balance inquiries",
                "color": "#10B981",  # Green
                "icon": "credit-card",
                "default_priority": "MEDIUM",
                "response_hours": 8,
                "resolution_hours": 48,
            },
            {
                "category_code": "ACCT",
                "category_name": "Account Management",
                "description": "Account changes, access issues, and user management",
                "color": "#8B5CF6",  # Purple
                "icon": "user",
                "default_priority": "MEDIUM",
                "response_hours": 4,
                "resolution_hours": 24,
            },
            {
                "category_code": "SALES",
                "category_name": "Sales Inquiry",
                "description": "Product inquiries, quotes, and new service requests",
                "color": "#F59E0B",  # Amber
                "icon": "shopping-cart",
                "default_priority": "LOW",
                "response_hours": 24,
                "resolution_hours": 72,
            },
            {
                "category_code": "FEAT",
                "category_name": "Feature Request",
                "description": "New feature suggestions and enhancement requests",
                "color": "#EC4899",  # Pink
                "icon": "lightbulb",
                "default_priority": "LOW",
                "response_hours": 48,
                "resolution_hours": None,
            },
            {
                "category_code": "BUG",
                "category_name": "Bug Report",
                "description": "Software bugs, defects, and unexpected behavior",
                "color": "#EF4444",  # Red
                "icon": "bug",
                "default_priority": "HIGH",
                "response_hours": 2,
                "resolution_hours": 24,
            },
            {
                "category_code": "TRAIN",
                "category_name": "Training & Onboarding",
                "description": "Training requests, documentation, and user guidance",
                "color": "#06B6D4",  # Cyan
                "icon": "academic-cap",
                "default_priority": "LOW",
                "response_hours": 24,
                "resolution_hours": 72,
            },
            {
                "category_code": "URGENT",
                "category_name": "Urgent / Outage",
                "description": "Critical issues, service outages, and emergencies",
                "color": "#DC2626",  # Dark Red
                "icon": "exclamation-triangle",
                "default_priority": "URGENT",
                "response_hours": 1,
                "resolution_hours": 4,
            },
            {
                "category_code": "OTHER",
                "category_name": "General Inquiry",
                "description": "General questions and inquiries not covered by other categories",
                "color": "#6B7280",  # Gray
                "icon": "question-mark-circle",
                "default_priority": "LOW",
                "response_hours": 24,
                "resolution_hours": 72,
            },
        ]

        created = []
        for i, cat_data in enumerate(defaults):
            category_code = cast(str, cat_data["category_code"])
            category_name = cast(str, cat_data["category_name"])
            category, _ = self.create_category(
                db,
                organization_id,
                category_code,
                category_name,
                description=cast(str | None, cat_data.get("description")),
                color=cast(str | None, cat_data.get("color")),
                icon=cast(str | None, cat_data.get("icon")),
                display_order=i + 1,
                default_team_id=cast(uuid.UUID | None, cat_data.get("default_team_id")),
                default_priority=cast(str | None, cat_data.get("default_priority")),
                response_hours=cast(int | None, cat_data.get("response_hours")),
                resolution_hours=cast(int | None, cat_data.get("resolution_hours")),
            )
            if category:
                created.append(category)

        return created


# Singleton instance
category_service = CategoryService()
