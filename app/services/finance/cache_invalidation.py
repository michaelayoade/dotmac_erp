"""
CacheInvalidation - Centralized cache invalidation triggers.

Provides a consistent way to invalidate caches when data changes,
ensuring stale data is never served to users.
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from app.services.cache import cache_service, CacheKeys

logger = logging.getLogger(__name__)


class CacheInvalidation:
    """
    Centralized cache invalidation triggers.

    Call these methods when data changes to ensure caches are invalidated.

    Usage:
        # After posting an invoice
        CacheInvalidation.on_invoice_posted(org_id)

        # After updating organization settings
        CacheInvalidation.on_organization_updated(org_id)
    """

    @staticmethod
    def on_invoice_posted(organization_id: UUID) -> None:
        """
        Invalidate caches when an invoice is posted.

        Affects:
        - Dashboard statistics
        - Dashboard balances
        - Dashboard trend data
        """
        logger.debug("Cache invalidation: invoice posted for org %s", organization_id)
        cache_service.delete_pattern(f"org:{organization_id}:dashboard:*")

    @staticmethod
    def on_payment_recorded(organization_id: UUID) -> None:
        """
        Invalidate caches when a payment is recorded.

        Affects:
        - Dashboard statistics
        - Dashboard balances
        """
        logger.debug("Cache invalidation: payment recorded for org %s", organization_id)
        cache_service.delete_pattern(f"org:{organization_id}:dashboard:*")

    @staticmethod
    def on_journal_posted(organization_id: UUID) -> None:
        """
        Invalidate caches when a journal is posted.

        Affects:
        - Dashboard balances
        - Dashboard trend data
        """
        logger.debug("Cache invalidation: journal posted for org %s", organization_id)
        cache_service.delete_pattern(f"org:{organization_id}:dashboard:*")

    @staticmethod
    def on_organization_updated(organization_id: UUID) -> None:
        """
        Invalidate caches when organization settings change.

        Affects:
        - Organization context
        - Currency settings
        """
        logger.debug("Cache invalidation: organization updated %s", organization_id)
        cache_service.delete(CacheKeys.org_context(organization_id))
        cache_service.delete(CacheKeys.org_currency(organization_id))

    @staticmethod
    def on_fiscal_period_changed(organization_id: UUID) -> None:
        """
        Invalidate caches when fiscal periods change.

        Affects:
        - Dashboard statistics (all years)
        - Dashboard balances (all years)
        """
        logger.debug("Cache invalidation: fiscal period changed for org %s", organization_id)
        cache_service.delete_pattern(f"org:{organization_id}:dashboard:*")

    @staticmethod
    def on_account_modified(organization_id: UUID) -> None:
        """
        Invalidate caches when chart of accounts changes.

        Affects:
        - Dashboard balances
        - Dashboard trend data
        """
        logger.debug("Cache invalidation: account modified for org %s", organization_id)
        cache_service.delete_pattern(f"org:{organization_id}:dashboard:*")

    @staticmethod
    def invalidate_all(organization_id: UUID) -> int:
        """
        Invalidate all caches for an organization.

        Use sparingly - this is a heavy operation.

        Returns:
            Number of keys invalidated
        """
        logger.info("Cache invalidation: all caches for org %s", organization_id)
        return cache_service.invalidate_org(organization_id)


# Module-level singleton
cache_invalidation = CacheInvalidation()
