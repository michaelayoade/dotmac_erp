"""
Dropdown Service - Common dropdown data for forms.

Provides standardized dropdown data used across web forms and API autocomplete endpoints.
Keeps "active items" filtering logic in one place.
"""

import logging
from typing import Any, Dict, List
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)


class DropdownService:
    """Service for retrieving dropdown/autocomplete data."""

    def get_employees(
        self,
        db: Session,
        organization_id: UUID,
        *,
        include_inactive: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Get employees for dropdown selection.

        Args:
            db: Database session
            organization_id: Organization UUID
            include_inactive: Include inactive employees

        Returns:
            List of employee dicts with id, code, full_name
        """
        from app.models.people.hr import Employee, EmployeeStatus
        from app.models.person import Person

        org_id = coerce_uuid(organization_id)

        query = (
            select(Employee, Person)
            .join(Person, Person.id == Employee.person_id)
            .where(Employee.organization_id == org_id)
            .order_by(Person.first_name, Person.last_name)
        )

        if not include_inactive:
            query = query.where(Employee.status == EmployeeStatus.ACTIVE)

        results = db.execute(query).all()

        return [
            {
                "employee_id": str(emp.employee_id),
                "employee_code": emp.employee_code,
                "full_name": f"{person.first_name or ''} {person.last_name or ''}".strip(),
            }
            for emp, person in results
        ]

    def get_projects(
        self,
        db: Session,
        organization_id: UUID,
        *,
        include_inactive: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Get projects for dropdown selection.

        Args:
            db: Database session
            organization_id: Organization UUID
            include_inactive: Include inactive/closed projects

        Returns:
            List of project dicts with id, code, name
        """
        from app.models.finance.core_org.project import Project, ProjectStatus

        org_id = coerce_uuid(organization_id)

        try:
            query = (
                select(Project)
                .where(Project.organization_id == org_id)
                .order_by(Project.project_code)
            )

            if not include_inactive:
                query = query.where(Project.status == ProjectStatus.ACTIVE)

            results = db.execute(query).scalars().all()

            return [
                {
                    "project_id": str(p.project_id),
                    "project_code": p.project_code,
                    "project_name": p.project_name,
                }
                for p in results
            ]
        except Exception as e:
            logger.warning("Failed to load projects: %s", e)
            return []

    def get_customers(
        self,
        db: Session,
        organization_id: UUID,
        *,
        include_inactive: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Get customers for dropdown selection.

        Args:
            db: Database session
            organization_id: Organization UUID
            include_inactive: Include inactive customers

        Returns:
            List of customer dicts with id, code, name, contact info
        """
        from app.models.finance.ar.customer import Customer

        org_id = coerce_uuid(organization_id)

        try:
            query = (
                select(Customer)
                .where(Customer.organization_id == org_id)
                .order_by(Customer.legal_name)
            )

            if not include_inactive:
                query = query.where(Customer.is_active == True)  # noqa: E712

            results = db.execute(query).scalars().all()

            return [
                {
                    "customer_id": str(c.customer_id),
                    "customer_code": c.customer_code,
                    "customer_name": c.trading_name or c.legal_name,
                    "customer_email": (c.primary_contact or {}).get("email"),
                    "customer_phone": (c.primary_contact or {}).get("phone"),
                    "billing_address": (c.billing_address or {}).get("address", ""),
                }
                for c in results
            ]
        except Exception as e:
            logger.warning("Failed to load customers: %s", e)
            return []

    def get_suppliers(
        self,
        db: Session,
        organization_id: UUID,
        *,
        include_inactive: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Get suppliers for dropdown selection.

        Args:
            db: Database session
            organization_id: Organization UUID
            include_inactive: Include inactive suppliers

        Returns:
            List of supplier dicts with id, code, name
        """
        from app.models.finance.ap.supplier import Supplier

        org_id = coerce_uuid(organization_id)

        try:
            query = (
                select(Supplier)
                .where(Supplier.organization_id == org_id)
                .order_by(Supplier.legal_name)
            )

            if not include_inactive:
                query = query.where(Supplier.is_active == True)  # noqa: E712

            results = db.execute(query).scalars().all()

            return [
                {
                    "supplier_id": str(s.supplier_id),
                    "supplier_code": s.supplier_code,
                    "supplier_name": s.trading_name or s.legal_name,
                }
                for s in results
            ]
        except Exception as e:
            logger.warning("Failed to load suppliers: %s", e)
            return []

    def get_warehouses(
        self,
        db: Session,
        organization_id: UUID,
        *,
        include_inactive: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Get warehouses for dropdown selection.

        Args:
            db: Database session
            organization_id: Organization UUID
            include_inactive: Include inactive warehouses

        Returns:
            List of warehouse dicts with id, code, name
        """
        from app.models.inventory.warehouse import Warehouse

        org_id = coerce_uuid(organization_id)

        try:
            query = (
                select(Warehouse)
                .where(Warehouse.organization_id == org_id)
                .order_by(Warehouse.warehouse_code)
            )

            if not include_inactive:
                query = query.where(Warehouse.is_active == True)  # noqa: E712

            results = db.execute(query).scalars().all()

            return [
                {
                    "warehouse_id": str(w.warehouse_id),
                    "warehouse_code": w.warehouse_code,
                    "warehouse_name": w.warehouse_name,
                }
                for w in results
            ]
        except Exception as e:
            logger.warning("Failed to load warehouses: %s", e)
            return []

    def get_accounts(
        self,
        db: Session,
        organization_id: UUID,
        *,
        account_type: str | None = None,
        include_inactive: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Get GL accounts for dropdown selection.

        Args:
            db: Database session
            organization_id: Organization UUID
            account_type: Filter by account type (ASSET, LIABILITY, etc.)
            include_inactive: Include inactive accounts

        Returns:
            List of account dicts with id, code, name, type
        """
        from app.models.finance.gl.account import Account

        org_id = coerce_uuid(organization_id)

        try:
            query = (
                select(Account)
                .where(Account.organization_id == org_id)
                .order_by(Account.account_code)
            )

            if not include_inactive:
                query = query.where(Account.is_active == True)  # noqa: E712

            if account_type:
                query = query.where(Account.account_type == account_type)

            results = db.execute(query).scalars().all()

            return [
                {
                    "account_id": str(a.account_id),
                    "account_code": a.account_code,
                    "account_name": a.account_name,
                    "account_type": a.account_type.value if a.account_type else None,
                }
                for a in results
            ]
        except Exception as e:
            logger.warning("Failed to load accounts: %s", e)
            return []


# Module-level singleton
dropdown_service = DropdownService()
