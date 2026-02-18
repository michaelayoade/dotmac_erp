"""
Search suggestions service for auto-complete functionality.

Provides unified search across entity types with consistent response format.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.finance.ap.supplier import Supplier

# Import models for searchable entities
from app.models.finance.ar.customer import Customer
from app.models.finance.banking.bank_account import BankAccount, BankAccountStatus
from app.models.finance.gl.account import Account
from app.models.finance.tax.tax_code import TaxCode
from app.models.inventory.item import Item

logger = logging.getLogger(__name__)


@dataclass
class SearchSuggestion:
    """A single search suggestion."""

    id: str
    label: str
    subtitle: str | None = None
    category: str | None = None
    meta: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"id": self.id, "label": self.label}
        if self.subtitle:
            result["subtitle"] = self.subtitle
        if self.category:
            result["category"] = self.category
        if self.meta:
            result["meta"] = self.meta
        return result


@dataclass
class SearchResult:
    """Search result with suggestions and metadata."""

    suggestions: list[SearchSuggestion]
    query: str
    entity_type: str
    has_more: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "suggestions": [s.to_dict() for s in self.suggestions],
            "query": self.query,
            "entity_type": self.entity_type,
            "has_more": self.has_more,
        }


class SearchSuggestionsService:
    """
    Unified search suggestions across entity types.

    Supports: customers, suppliers, accounts, items, tax_codes, bank_accounts
    """

    # Maximum suggestions to return
    MAX_SUGGESTIONS = 10

    @classmethod
    def search(
        cls,
        db: Session,
        org_id: UUID,
        entity_type: str,
        query: str,
        limit: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> SearchResult:
        """
        Search for suggestions across an entity type.

        Args:
            db: Database session
            org_id: Organization ID for RLS
            entity_type: Type of entity to search (customers, suppliers, etc.)
            query: Search query string
            limit: Maximum results to return
            filters: Optional additional filters

        Returns:
            SearchResult with matching suggestions
        """
        limit = min(limit, cls.MAX_SUGGESTIONS)
        filters = filters or {}

        # Route to appropriate search method
        searchers = {
            "customers": cls._search_customers,
            "suppliers": cls._search_suppliers,
            "accounts": cls._search_accounts,
            "items": cls._search_items,
            "tax_codes": cls._search_tax_codes,
            "bank_accounts": cls._search_bank_accounts,
        }

        searcher = searchers.get(entity_type)
        if not searcher:
            return SearchResult(
                suggestions=[],
                query=query,
                entity_type=entity_type,
                has_more=False,
            )

        suggestions, has_more = searcher(db, org_id, query, limit, filters)
        return SearchResult(
            suggestions=suggestions,
            query=query,
            entity_type=entity_type,
            has_more=has_more,
        )

    @classmethod
    def _search_customers(
        cls, db: Session, org_id: UUID, query: str, limit: int, filters: dict
    ) -> tuple[list[SearchSuggestion], bool]:
        """Search customers by name or code."""
        q = query.lower()
        stmt = (
            select(Customer)
            .where(
                Customer.organization_id == org_id,
                Customer.is_active == True,
                or_(
                    func.lower(Customer.legal_name).contains(q),
                    func.lower(Customer.trading_name).contains(q),
                    func.lower(Customer.customer_code).contains(q),
                ),
            )
            .order_by(Customer.legal_name)
        )

        customers = db.scalars(stmt.limit(limit + 1)).all()
        has_more = len(customers) > limit
        customers = customers[:limit]

        suggestions = [
            SearchSuggestion(
                id=str(c.customer_id),
                label=c.legal_name,
                subtitle=c.customer_code,
                category="customer",
                meta={"trading_name": c.trading_name} if c.trading_name else None,
            )
            for c in customers
        ]
        return suggestions, has_more

    @classmethod
    def _search_suppliers(
        cls, db: Session, org_id: UUID, query: str, limit: int, filters: dict
    ) -> tuple[list[SearchSuggestion], bool]:
        """Search suppliers by name or code."""
        q = query.lower()
        stmt = (
            select(Supplier)
            .where(
                Supplier.organization_id == org_id,
                Supplier.is_active == True,
                or_(
                    func.lower(Supplier.legal_name).contains(q),
                    func.lower(Supplier.trading_name).contains(q),
                    func.lower(Supplier.supplier_code).contains(q),
                ),
            )
            .order_by(Supplier.legal_name)
        )

        suppliers = db.scalars(stmt.limit(limit + 1)).all()
        has_more = len(suppliers) > limit
        suppliers = suppliers[:limit]

        suggestions = [
            SearchSuggestion(
                id=str(s.supplier_id),
                label=s.legal_name,
                subtitle=s.supplier_code,
                category="supplier",
                meta={"trading_name": s.trading_name} if s.trading_name else None,
            )
            for s in suppliers
        ]
        return suggestions, has_more

    @classmethod
    def _search_accounts(
        cls, db: Session, org_id: UUID, query: str, limit: int, filters: dict
    ) -> tuple[list[SearchSuggestion], bool]:
        """Search GL accounts by code or name."""
        q = query.lower()
        stmt = (
            select(Account)
            .where(
                Account.organization_id == org_id,
                Account.is_active == True,
                or_(
                    func.lower(Account.account_code).contains(q),
                    func.lower(Account.account_name).contains(q),
                ),
            )
            .order_by(Account.account_code)
        )

        # Optional filter by account type
        if filters.get("account_type"):
            stmt = stmt.where(Account.account_type == filters["account_type"])

        accounts = db.scalars(stmt.limit(limit + 1)).all()
        has_more = len(accounts) > limit
        accounts = accounts[:limit]

        suggestions = [
            SearchSuggestion(
                id=str(a.account_id),
                label=f"{a.account_code} - {a.account_name}",
                subtitle=a.account_type.value if a.account_type else None,
                category="account",
            )
            for a in accounts
        ]
        return suggestions, has_more

    @classmethod
    def _search_items(
        cls, db: Session, org_id: UUID, query: str, limit: int, filters: dict
    ) -> tuple[list[SearchSuggestion], bool]:
        """Search inventory items by code or name."""
        q = query.lower()
        stmt = (
            select(Item)
            .where(
                Item.organization_id == org_id,
                Item.is_active == True,
                or_(
                    func.lower(Item.item_code).contains(q),
                    func.lower(Item.item_name).contains(q),
                ),
            )
            .order_by(Item.item_name)
        )

        # Optional filter by category
        if filters.get("category_id"):
            stmt = stmt.where(Item.category_id == filters["category_id"])

        items = db.scalars(stmt.limit(limit + 1)).all()
        has_more = len(items) > limit
        items = items[:limit]

        suggestions = [
            SearchSuggestion(
                id=str(i.item_id),
                label=i.item_name,
                subtitle=i.item_code,
                category="item",
                meta={"type": i.item_type.value if i.item_type else None},
            )
            for i in items
        ]
        return suggestions, has_more

    @classmethod
    def _search_tax_codes(
        cls, db: Session, org_id: UUID, query: str, limit: int, filters: dict
    ) -> tuple[list[SearchSuggestion], bool]:
        """Search tax codes by code or name."""
        q = query.lower()
        stmt = (
            select(TaxCode)
            .where(
                TaxCode.organization_id == org_id,
                TaxCode.is_active == True,
                or_(
                    func.lower(TaxCode.tax_code).contains(q),
                    func.lower(TaxCode.tax_name).contains(q),
                ),
            )
            .order_by(TaxCode.tax_code)
        )

        # Optional filter by tax type
        if filters.get("tax_type"):
            stmt = stmt.where(TaxCode.tax_type == filters["tax_type"])

        codes = db.scalars(stmt.limit(limit + 1)).all()
        has_more = len(codes) > limit
        codes = codes[:limit]

        suggestions = [
            SearchSuggestion(
                id=str(t.tax_code_id),
                label=f"{t.tax_code} - {t.tax_name}",
                subtitle=f"{t.tax_rate}%" if t.tax_rate else None,
                category="tax_code",
                meta={"type": t.tax_type.value if t.tax_type else None},
            )
            for t in codes
        ]
        return suggestions, has_more

    @classmethod
    def _search_bank_accounts(
        cls, db: Session, org_id: UUID, query: str, limit: int, filters: dict
    ) -> tuple[list[SearchSuggestion], bool]:
        """Search bank accounts by name or number."""
        q = query.lower()
        stmt = (
            select(BankAccount)
            .where(
                BankAccount.organization_id == org_id,
                BankAccount.status == BankAccountStatus.active,
                or_(
                    func.lower(BankAccount.account_name).contains(q),
                    func.lower(BankAccount.account_number).contains(q),
                    func.lower(BankAccount.bank_name).contains(q),
                ),
            )
            .order_by(BankAccount.account_name)
        )

        accounts = db.scalars(stmt.limit(limit + 1)).all()
        has_more = len(accounts) > limit
        accounts = accounts[:limit]

        suggestions = [
            SearchSuggestion(
                id=str(a.bank_account_id),
                label=a.account_name,
                subtitle=f"{a.bank_name} - {a.account_number[-4:]}"
                if a.account_number
                else a.bank_name,
                category="bank_account",
            )
            for a in accounts
        ]
        return suggestions, has_more


# Singleton instance
search_suggestions_service = SearchSuggestionsService()
