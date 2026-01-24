"""
Ticket Category Service.

Handles category management for support tickets.
"""

import logging
import uuid
from typing import List, Optional, Tuple

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
    ) -> List[TicketCategory]:
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
        category_id: uuid.UUID,
    ) -> Optional[TicketCategory]:
        """Get a category by ID."""
        return db.get(TicketCategory, category_id)

    def get_category_by_code(
        self,
        db: Session,
        organization_id: uuid.UUID,
        category_code: str,
    ) -> Optional[TicketCategory]:
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
        description: Optional[str] = None,
        color: Optional[str] = None,
        icon: Optional[str] = None,
        default_team_id: Optional[uuid.UUID] = None,
        default_priority: Optional[str] = None,
        response_hours: Optional[int] = None,
        resolution_hours: Optional[int] = None,
    ) -> Tuple[Optional[TicketCategory], Optional[str]]:
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
        max_order = db.execute(
            select(TicketCategory.display_order)
            .where(TicketCategory.organization_id == organization_id)
            .order_by(TicketCategory.display_order.desc())
        ).scalar_one_or_none() or 0

        category = TicketCategory(
            organization_id=organization_id,
            category_code=category_code.upper(),
            category_name=category_name,
            description=description,
            color=color,
            icon=icon,
            display_order=max_order + 1,
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
        category_id: uuid.UUID,
        category_name: Optional[str] = None,
        description: Optional[str] = None,
        color: Optional[str] = None,
        icon: Optional[str] = None,
        display_order: Optional[int] = None,
        default_team_id: Optional[uuid.UUID] = None,
        default_priority: Optional[str] = None,
        response_hours: Optional[int] = None,
        resolution_hours: Optional[int] = None,
        is_active: Optional[bool] = None,
    ) -> Optional[TicketCategory]:
        """Update a category."""
        category = self.get_category(db, category_id)
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
        category_id: uuid.UUID,
        hard_delete: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        """
        Delete a category.

        Args:
            db: Database session
            category_id: Category UUID
            hard_delete: If true, permanently delete

        Returns:
            (success, error_message)
        """
        category = self.get_category(db, category_id)
        if not category:
            return False, "Category not found"

        # Check for tickets using this category
        from app.models.support.ticket import Ticket

        ticket_count = db.execute(
            select(Ticket.ticket_id)
            .where(Ticket.category_id == category_id)
            .limit(1)
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
    ) -> List[TicketCategory]:
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
                "category_code": "NET",
                "category_name": "Network Issue",
                "description": "Connectivity, speed, and network-related issues",
                "color": "#3B82F6",  # Blue
                "icon": "wifi",
            },
            {
                "category_code": "BILL",
                "category_name": "Billing",
                "description": "Payment, invoicing, and account balance issues",
                "color": "#10B981",  # Green
                "icon": "credit-card",
            },
            {
                "category_code": "HW",
                "category_name": "Hardware",
                "description": "Equipment, device, and physical infrastructure issues",
                "color": "#F59E0B",  # Amber
                "icon": "server",
            },
            {
                "category_code": "SW",
                "category_name": "Software",
                "description": "Application, portal, and software-related issues",
                "color": "#8B5CF6",  # Purple
                "icon": "code",
            },
            {
                "category_code": "INST",
                "category_name": "Installation",
                "description": "New installations and setup requests",
                "color": "#06B6D4",  # Cyan
                "icon": "wrench",
            },
            {
                "category_code": "OTHER",
                "category_name": "Other",
                "description": "General inquiries and other issues",
                "color": "#6B7280",  # Gray
                "icon": "help-circle",
            },
        ]

        created = []
        for i, cat_data in enumerate(defaults):
            cat_data["display_order"] = i + 1
            category, _ = self.create_category(db, organization_id, **cat_data)
            if category:
                created.append(category)

        return created


# Singleton instance
category_service = CategoryService()
