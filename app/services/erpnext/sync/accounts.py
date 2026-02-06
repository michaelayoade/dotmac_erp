"""
Account Sync Service - ERPNext to DotMac ERP.
"""

import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Maximum account code length in the database
MAX_ACCOUNT_CODE_LENGTH = 20

from app.models.finance.gl.account import Account, AccountType, NormalBalance
from app.models.finance.gl.account_category import AccountCategory
from app.services.erpnext.mappings.accounts import ROOT_TYPE_MAP, AccountMapping

from .base import BaseSyncService


class AccountSyncService(BaseSyncService[Account]):
    """Sync Chart of Accounts from ERPNext."""

    source_doctype = "Account"
    target_table = "gl.account"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        super().__init__(db, organization_id, user_id)
        self._mapping = AccountMapping()
        self._account_cache: dict[str, Account] = {}
        self._category_cache: dict[str, AccountCategory] = {}

    def fetch_records(self, client: Any, since: Optional[datetime] = None):
        """Fetch accounts from ERPNext."""
        if since:
            yield from client.get_modified_since(
                doctype="Account",
                since=since,
                fields=self._get_fields(),
            )
        else:
            yield from client.get_chart_of_accounts()

    def _get_fields(self) -> list[str]:
        """Get fields to fetch."""
        return [
            "name",
            "account_name",
            "account_number",
            "root_type",
            "account_type",
            "is_group",
            "parent_account",
            "account_currency",
            "disabled",
            "modified",
        ]

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform ERPNext account to DotMac ERP format."""
        return self._mapping.transform_record(record)

    def _get_or_create_category(self, root_type: str) -> uuid.UUID:
        """Get or create account category for ERPNext root_type."""
        # Map ERPNext root_type to our category
        category_code = ROOT_TYPE_MAP.get(root_type, "ASSETS")

        # Check cache
        if category_code in self._category_cache:
            return self._category_cache[category_code].category_id

        # Try to find existing
        result = self.db.execute(
            select(AccountCategory).where(
                AccountCategory.organization_id == self.organization_id,
                AccountCategory.category_code == category_code,
            )
        ).scalar_one_or_none()

        if result:
            self._category_cache[category_code] = result
            return result.category_id

        # Create new category
        category = AccountCategory(
            organization_id=self.organization_id,
            category_code=category_code,
            category_name=root_type or "Assets",
            ifrs_category=category_code,
            is_active=True,
        )
        self.db.add(category)
        self.db.flush()
        self._category_cache[category_code] = category
        return category.category_id

    def create_entity(self, data: dict[str, Any]) -> Account:
        """Create Account entity."""
        # Remove internal fields
        data.pop("_parent_source_name", None)
        data.pop("_source_modified", None)

        # Get or create category
        category_id = self._get_or_create_category(
            data.get("account_category", "Asset")
        )

        # Map is_header to is_posting_allowed (inverted)
        is_header = data.get("is_header", False)

        # Determine account_type - must be CONTROL, POSTING, or STATISTICAL
        # Group accounts (is_header=True) are CONTROL, posting accounts are POSTING
        if is_header:
            account_type = "CONTROL"
        elif data.get("subledger_type"):
            account_type = "CONTROL"  # Subledger accounts are control accounts
        else:
            account_type = "POSTING"

        account_code = data["account_code"]
        if len(account_code) > MAX_ACCOUNT_CODE_LENGTH:
            logger.warning(
                "Account code '%s' truncated to %d chars (was %d). "
                "This may cause lookup collisions. Consider extending gl.account.account_code column.",
                account_code[:MAX_ACCOUNT_CODE_LENGTH],
                MAX_ACCOUNT_CODE_LENGTH,
                len(account_code),
            )
            account_code = account_code[:MAX_ACCOUNT_CODE_LENGTH]

        account = Account(
            organization_id=self.organization_id,
            account_code=account_code,
            account_name=data["account_name"][:200],
            category_id=category_id,
            account_type=AccountType(account_type),
            normal_balance=NormalBalance(data.get("normal_balance", "DEBIT")[:6]),
            is_posting_allowed=not is_header,  # Header accounts don't allow posting
            is_active=data.get("is_active", True),
            default_currency_code=data.get("currency_code", "NGN")[:3],
            subledger_type=data.get("subledger_type"),
            created_by_user_id=self.user_id,
        )
        return account

    def update_entity(self, entity: Account, data: dict[str, Any]) -> Account:
        """Update existing Account."""
        data.pop("_parent_source_name", None)
        data.pop("_source_modified", None)

        # Get or create category
        category_id = self._get_or_create_category(
            data.get("account_category", "Asset")
        )
        is_header = data.get("is_header", False)

        # Determine account_type - must be CONTROL, POSTING, or STATISTICAL
        if is_header:
            account_type = "CONTROL"
        elif data.get("subledger_type"):
            account_type = "CONTROL"
        else:
            account_type = "POSTING"

        entity.account_name = data["account_name"][:200]
        entity.category_id = category_id
        entity.account_type = AccountType(account_type)
        entity.normal_balance = NormalBalance(data.get("normal_balance", "DEBIT")[:6])
        entity.is_posting_allowed = not is_header
        entity.is_active = data.get("is_active", True)
        entity.default_currency_code = data.get("currency_code", "NGN")[:3]
        entity.subledger_type = data.get("subledger_type")

        return entity

    def get_entity_id(self, entity: Account) -> uuid.UUID:
        """Get account ID."""
        return entity.account_id

    def find_existing_entity(self, source_name: str) -> Optional[Account]:
        """Find existing account by sync entity or code.

        Primary lookup is via sync_entity (stores full source_name).
        Fallback lookup by truncated code is for legacy compatibility only.
        """
        # Check cache first
        if source_name in self._account_cache:
            return self._account_cache[source_name]

        # Primary: check sync entity (stores full source_name, avoids truncation issues)
        sync_entity = self.get_sync_entity(source_name)
        if sync_entity and sync_entity.target_id:
            account = self.db.get(Account, sync_entity.target_id)
            if account:
                self._account_cache[source_name] = account
                return account

        # Fallback: try to find by code (may be truncated - legacy compatibility only)
        code = source_name[:MAX_ACCOUNT_CODE_LENGTH] if source_name else ""
        if len(source_name) > MAX_ACCOUNT_CODE_LENGTH:
            logger.debug(
                "Looking up account by truncated code '%s' (full name: '%s'). "
                "Prefer sync_entity lookup for accuracy.",
                code,
                source_name,
            )
        result = self.db.execute(
            select(Account).where(
                Account.organization_id == self.organization_id,
                Account.account_code == code,
            )
        ).scalar_one_or_none()

        if result:
            self._account_cache[source_name] = result

        return result
