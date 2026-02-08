"""
Expense Sync Services - ERPNext to DotMac ERP.

Sync services for Expense entities:
- Expense Category (Expense Claim Type)
- Expense Claim (with items)
"""

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.expense.expense_claim import (
    ExpenseCategory,
    ExpenseClaim,
    ExpenseClaimItem,
    ExpenseClaimStatus,
)
from app.services.erpnext.mappings.expense import (
    ExpenseCategoryMapping,
    ExpenseClaimItemMapping,
    ExpenseClaimMapping,
)

from .base import BaseSyncService

logger = logging.getLogger(__name__)


class ExpenseCategorySyncService(BaseSyncService[ExpenseCategory]):
    """Sync Expense Categories from ERPNext."""

    source_doctype = "Expense Claim Type"
    target_table = "expense.expense_category"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        super().__init__(db, organization_id, user_id)
        self._mapping = ExpenseCategoryMapping()
        self._category_cache: dict[str, ExpenseCategory] = {}

    def fetch_records(self, client: Any, since: datetime | None = None):
        if since:
            yield from client.get_modified_since(
                doctype="Expense Claim Type",
                since=since,
            )
        else:
            yield from client.get_expense_claim_types()

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        return self._mapping.transform_record(record)

    def create_entity(self, data: dict[str, Any]) -> ExpenseCategory:
        data.pop("_source_modified", None)
        data.pop("_source_name", None)

        category = ExpenseCategory(
            organization_id=self.organization_id,
            category_code=data["category_code"][:30],
            category_name=data["category_name"][:200],
            description=data.get("description"),
            requires_receipt=data.get("requires_receipt", True),
            is_active=data.get("is_active", True),
            # created_by_id not set for synced records
        )
        return category

    def update_entity(
        self, entity: ExpenseCategory, data: dict[str, Any]
    ) -> ExpenseCategory:
        data.pop("_source_modified", None)
        data.pop("_source_name", None)

        entity.category_name = data["category_name"][:200]
        entity.description = data.get("description")
        entity.requires_receipt = data.get("requires_receipt", entity.requires_receipt)
        entity.is_active = data.get("is_active", True)
        entity.updated_by_id = self.user_id
        return entity

    def get_entity_id(self, entity: ExpenseCategory) -> uuid.UUID:
        return entity.category_id

    def find_existing_entity(self, source_name: str) -> ExpenseCategory | None:
        if source_name in self._category_cache:
            return self._category_cache[source_name]

        sync_entity = self.get_sync_entity(source_name)
        if sync_entity and sync_entity.target_id:
            category = self.db.get(ExpenseCategory, sync_entity.target_id)
            if category:
                self._category_cache[source_name] = category
                return category

        return None


class ExpenseClaimSyncService(BaseSyncService[ExpenseClaim]):
    """Sync Expense Claims from ERPNext (including line items)."""

    source_doctype = "Expense Claim"
    target_table = "expense.expense_claim"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        super().__init__(db, organization_id, user_id)
        self._mapping = ExpenseClaimMapping()
        self._item_mapping = ExpenseClaimItemMapping()
        self._claim_cache: dict[str, ExpenseClaim] = {}

    def fetch_records(self, client: Any, since: datetime | None = None):
        """Fetch expense claims with their items."""
        if since:
            for claim in client.get_modified_since(
                doctype="Expense Claim",
                since=since,
            ):
                # Fetch items for each claim
                claim["expenses"] = client.list_documents(
                    doctype="Expense Claim Detail",
                    filters={"parent": claim["name"]},
                )
                yield claim
        else:
            yield from client.get_expense_claims()

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        result = self._mapping.transform_record(record)
        # Transform expense items
        result["_items"] = []
        for item in record.get("expenses", []):
            item_data = self._item_mapping.transform_record(item)
            result["_items"].append(item_data)
        return result

    def _resolve_entity_id(
        self, source_name: str | None, source_doctype: str
    ) -> uuid.UUID | None:
        if not source_name:
            return None

        from app.models.sync import SyncEntity

        sync_entity = self.db.execute(
            select(SyncEntity).where(
                SyncEntity.organization_id == self.organization_id,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == source_doctype,
                SyncEntity.source_name == source_name,
            )
        ).scalar_one_or_none()

        if sync_entity and sync_entity.target_id:
            return sync_entity.target_id
        return None

    def _create_claim_items(self, claim: ExpenseClaim, items_data: list[dict]) -> None:
        """Create expense claim items."""
        for seq, item_data in enumerate(items_data, 1):
            expense_type_source = item_data.pop("_expense_type_source_name", None)
            item_data.pop("_cost_center_source_name", None)
            item_data.pop("_source_modified", None)
            item_data.pop("_source_name", None)

            # Resolve expense category - required field
            category_id = self._resolve_entity_id(
                expense_type_source, "Expense Claim Type"
            )

            if not category_id:
                logger.warning(
                    "Skipping expense item: category '%s' not found for claim %s",
                    expense_type_source,
                    claim.claim_number,
                )
                continue

            item = ExpenseClaimItem(
                claim_id=claim.claim_id,
                expense_date=item_data["expense_date"],
                category_id=category_id,
                description=item_data.get("description")
                or f"Expense: {expense_type_source}",
                claimed_amount=item_data.get("claimed_amount", Decimal("0")),
                approved_amount=item_data.get("approved_amount"),
                sequence=seq,
            )
            self.db.add(item)

    def create_entity(self, data: dict[str, Any]) -> ExpenseClaim:
        emp_source = data.pop("_employee_source_name", None)
        data.pop("_approver_user", None)
        data.pop("_cost_center_source_name", None)
        data.pop("_project_source_name", None)
        items_data = data.pop("_items", [])
        data.pop("_source_modified", None)
        data.pop("_source_name", None)

        # Resolve foreign keys
        employee_id = self._resolve_entity_id(emp_source, "Employee")

        # Map status
        status_str = data.get("status", "DRAFT")
        try:
            status = ExpenseClaimStatus(status_str)
        except ValueError:
            status = ExpenseClaimStatus.DRAFT

        claim = ExpenseClaim(
            organization_id=self.organization_id,
            claim_number=data["claim_number"][:30],
            employee_id=employee_id,
            claim_date=data["claim_date"],
            purpose=data.get("purpose", "Expense Reimbursement")[:500],
            total_claimed_amount=data.get("total_claimed_amount", Decimal("0")),
            total_approved_amount=data.get("total_approved_amount"),
            net_payable_amount=data.get("net_payable_amount"),
            currency_code=data.get("currency_code", "NGN")[:3],
            status=status,
            # created_by_id not set for synced records
        )

        # Add claim to session to get ID
        self.db.add(claim)
        self.db.flush()

        # Create line items
        if items_data:
            self._create_claim_items(claim, items_data)

        return claim

    def update_entity(self, entity: ExpenseClaim, data: dict[str, Any]) -> ExpenseClaim:
        data.pop("_employee_source_name", None)
        data.pop("_approver_user", None)
        data.pop("_cost_center_source_name", None)
        data.pop("_project_source_name", None)
        items_data = data.pop("_items", [])
        data.pop("_source_modified", None)
        data.pop("_source_name", None)

        # Update claim fields
        entity.purpose = data.get("purpose", entity.purpose)[:500]
        entity.total_claimed_amount = data.get(
            "total_claimed_amount", entity.total_claimed_amount
        )
        entity.total_approved_amount = data.get("total_approved_amount")
        entity.net_payable_amount = data.get("net_payable_amount")

        # Map status
        status_str = data.get("status", "DRAFT")
        try:
            entity.status = ExpenseClaimStatus(status_str)
        except ValueError:
            pass

        entity.updated_by_id = self.user_id

        # Update items (delete and recreate for simplicity)
        if items_data:
            # Delete existing items
            for item in entity.items:
                self.db.delete(item)
            self.db.flush()

            # Create new items
            self._create_claim_items(entity, items_data)

        return entity

    def get_entity_id(self, entity: ExpenseClaim) -> uuid.UUID:
        return entity.claim_id

    def find_existing_entity(self, source_name: str) -> ExpenseClaim | None:
        if source_name in self._claim_cache:
            return self._claim_cache[source_name]

        sync_entity = self.get_sync_entity(source_name)
        if sync_entity and sync_entity.target_id:
            claim = self.db.get(ExpenseClaim, sync_entity.target_id)
            if claim:
                self._claim_cache[source_name] = claim
                return claim

        return None
