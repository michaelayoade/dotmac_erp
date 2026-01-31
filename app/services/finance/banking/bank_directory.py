"""
Bank Directory Service.

Provides lookup services for Nigerian bank codes from bank names.
Supports exact matching, alias matching, and fuzzy matching.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import or_, select, func
from sqlalchemy.orm import Session

from app.models.finance.core_org.bank_directory import BankDirectory

logger = logging.getLogger(__name__)


class BankDirectoryService:
    """
    Service for looking up bank codes from bank names.

    Supports multiple matching strategies:
    1. Exact match on bank_name
    2. Match within aliases array
    3. Partial/fuzzy match on bank_name
    """

    def __init__(self, db: Session):
        self.db = db

    def get_by_code(self, bank_code: str) -> Optional[BankDirectory]:
        """Get bank entry by code."""
        return self.db.get(BankDirectory, bank_code)

    def get_by_name(self, bank_name: str) -> Optional[BankDirectory]:
        """
        Look up bank by exact name match.

        Args:
            bank_name: Bank name to look up

        Returns:
            BankDirectory entry if found, None otherwise
        """
        stmt = select(BankDirectory).where(
            func.lower(BankDirectory.bank_name) == func.lower(bank_name),
            BankDirectory.is_active == True,  # noqa: E712
        )
        return self.db.scalar(stmt)

    def lookup_bank_code(self, bank_name: str) -> Optional[str]:
        """
        Look up bank code from bank name using multiple matching strategies.

        Tries in order:
        1. Exact match on bank_name (case-insensitive)
        2. Match within aliases array
        3. Partial match on bank_name (contains)

        Args:
            bank_name: Bank name to look up (e.g., "Zenith", "GTBank", "First Bank")

        Returns:
            Bank code if found (e.g., "057"), None otherwise
        """
        if not bank_name:
            return None

        normalized = bank_name.strip()
        if not normalized:
            return None

        # Strategy 1: Exact match on bank_name
        bank = self.get_by_name(normalized)
        if bank:
            return bank.bank_code

        # Strategy 2: Match in aliases array
        # PostgreSQL: check if any alias matches (case-insensitive)
        stmt = select(BankDirectory).where(
            func.lower(func.array_to_string(BankDirectory.aliases, ",")).contains(
                func.lower(normalized)
            ),
            BankDirectory.is_active == True,  # noqa: E712
        )
        bank = self.db.scalar(stmt)
        if bank:
            return bank.bank_code

        # Strategy 3: Partial match on bank_name (contains)
        stmt = select(BankDirectory).where(
            or_(
                BankDirectory.bank_name.ilike(f"%{normalized}%"),
                func.lower(func.array_to_string(BankDirectory.aliases, ",")).contains(
                    func.lower(normalized)
                ),
            ),
            BankDirectory.is_active == True,  # noqa: E712
        )
        bank = self.db.scalar(stmt)
        if bank:
            return bank.bank_code

        logger.warning("Bank code not found for: %s", bank_name)
        return None

    def lookup_bank(self, bank_name: str) -> Optional[BankDirectory]:
        """
        Look up full bank entry from bank name.

        Uses same matching strategies as lookup_bank_code.

        Args:
            bank_name: Bank name to look up

        Returns:
            BankDirectory entry if found, None otherwise
        """
        code = self.lookup_bank_code(bank_name)
        if code:
            return self.get_by_code(code)
        return None

    def list_active_banks(self) -> list[BankDirectory]:
        """
        List all active banks.

        Returns:
            List of active BankDirectory entries sorted by name
        """
        stmt = (
            select(BankDirectory)
            .where(BankDirectory.is_active == True)  # noqa: E712
            .order_by(BankDirectory.bank_name)
        )
        return list(self.db.scalars(stmt).all())

    def search_banks(self, query: str, limit: int = 10) -> list[BankDirectory]:
        """
        Search banks by name or alias.

        Args:
            query: Search query
            limit: Maximum results to return

        Returns:
            List of matching BankDirectory entries
        """
        if not query or not query.strip():
            return self.list_active_banks()[:limit]

        normalized = query.strip()
        stmt = (
            select(BankDirectory)
            .where(
                or_(
                    BankDirectory.bank_name.ilike(f"%{normalized}%"),
                    func.lower(func.array_to_string(BankDirectory.aliases, ",")).contains(
                        func.lower(normalized)
                    ),
                ),
                BankDirectory.is_active == True,  # noqa: E712
            )
            .order_by(BankDirectory.bank_name)
            .limit(limit)
        )
        return list(self.db.scalars(stmt).all())


# Module-level service factory
def bank_directory_service(db: Session) -> BankDirectoryService:
    """Create a BankDirectoryService instance."""
    return BankDirectoryService(db)
