"""
Row Level Security (RLS) context utilities.

This module provides utilities for managing tenant context in PostgreSQL RLS policies.

Usage:
    # In a request middleware or dependency:
    async def set_tenant_context(
        db: AsyncSession,
        organization_id: UUID
    ):
        await set_current_organization(db, organization_id)

    # For admin/system operations that need to bypass RLS:
    async with bypass_rls(db):
        # queries here see all data across tenants
        all_orgs = await db.execute(select(Organization))

    # Or using the context manager:
    async with tenant_context(db, organization_id):
        # queries here are scoped to the organization
        pass
"""

import uuid
from contextlib import asynccontextmanager, contextmanager
from typing import AsyncGenerator, Generator, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session


async def set_current_organization(
    db: AsyncSession,
    organization_id: uuid.UUID,
) -> None:
    """
    Set the current organization context for RLS policies.

    This should be called at the beginning of each request/transaction
    to scope all subsequent queries to the specified organization.

    Args:
        db: The database session
        organization_id: The UUID of the current organization/tenant
    """
    # SET LOCAL doesn't support parameterized queries, so we use string formatting
    # The UUID type ensures the value is safe (no SQL injection possible)
    org_id_str = str(organization_id)
    await db.execute(text(f"SET LOCAL app.current_organization_id = '{org_id_str}'"))


async def clear_organization_context(db: AsyncSession) -> None:
    """
    Clear the current organization context.

    Args:
        db: The database session
    """
    await db.execute(text("RESET app.current_organization_id"))


async def enable_rls_bypass(db: AsyncSession) -> None:
    """
    Enable RLS bypass for admin/system operations.

    WARNING: Use with caution! This allows access to all tenant data.

    Args:
        db: The database session
    """
    await db.execute(text("SET LOCAL app.bypass_rls = 'true'"))


async def disable_rls_bypass(db: AsyncSession) -> None:
    """
    Disable RLS bypass (re-enable tenant isolation).

    Args:
        db: The database session
    """
    await db.execute(text("SET LOCAL app.bypass_rls = 'false'"))


@asynccontextmanager
async def bypass_rls(db: AsyncSession) -> AsyncGenerator[None, None]:
    """
    Context manager to temporarily bypass RLS for admin operations.

    Usage:
        async with bypass_rls(db):
            # All queries here bypass RLS
            all_data = await db.execute(select(SomeModel))

    Args:
        db: The database session
    """
    await enable_rls_bypass(db)
    try:
        yield
    finally:
        await disable_rls_bypass(db)


@asynccontextmanager
async def tenant_context(
    db: AsyncSession,
    organization_id: uuid.UUID,
) -> AsyncGenerator[None, None]:
    """
    Context manager to set tenant context for a block of code.

    Usage:
        async with tenant_context(db, org_id):
            # All queries here are scoped to org_id
            data = await db.execute(select(SomeModel))

    Args:
        db: The database session
        organization_id: The UUID of the organization/tenant
    """
    await set_current_organization(db, organization_id)
    try:
        yield
    finally:
        await clear_organization_context(db)


async def get_current_organization_id(db: AsyncSession) -> Optional[uuid.UUID]:
    """
    Get the current organization ID from the session context.

    Args:
        db: The database session

    Returns:
        The current organization UUID, or None if not set
    """
    result = await db.execute(
        text("SELECT current_setting('app.current_organization_id', true)")
    )
    value = result.scalar()
    if value:
        try:
            return uuid.UUID(value)
        except ValueError:
            return None
    return None


# Synchronous versions for non-async code


def set_current_organization_sync(
    db: Session,
    organization_id: uuid.UUID,
) -> None:
    """
    Synchronous version of set_current_organization.

    Args:
        db: The database session
        organization_id: The UUID of the current organization/tenant
    """
    # SET LOCAL doesn't support parameterized queries, so we use string formatting
    # The UUID type ensures the value is safe (no SQL injection possible)
    org_id_str = str(organization_id)
    db.execute(text(f"SET LOCAL app.current_organization_id = '{org_id_str}'"))


def clear_organization_context_sync(db: Session) -> None:
    """Clear the current organization context (sync version)."""
    db.execute(text("RESET app.current_organization_id"))


def enable_rls_bypass_sync(db: Session) -> None:
    """
    Synchronous version of enable_rls_bypass.

    Args:
        db: The database session
    """
    db.execute(text("SET LOCAL app.bypass_rls = 'true'"))


def disable_rls_bypass_sync(db: Session) -> None:
    """
    Synchronous version of disable_rls_bypass.

    Args:
        db: The database session
    """
    db.execute(text("SET LOCAL app.bypass_rls = 'false'"))


@contextmanager
def bypass_rls_sync(db: Session) -> Generator[None, None, None]:
    """
    Synchronous context manager to temporarily bypass RLS.

    Usage:
        with bypass_rls_sync(db):
            # All queries here bypass RLS
            all_data = db.execute(select(SomeModel))

    Args:
        db: The database session
    """
    enable_rls_bypass_sync(db)
    try:
        yield
    finally:
        disable_rls_bypass_sync(db)


@contextmanager
def tenant_context_sync(
    db: Session,
    organization_id: uuid.UUID,
) -> Generator[None, None, None]:
    """
    Synchronous context manager to set tenant context.

    Usage:
        with tenant_context_sync(db, org_id):
            # All queries here are scoped to org_id
            data = db.execute(select(SomeModel))

    Args:
        db: The database session
        organization_id: The UUID of the organization/tenant
    """
    set_current_organization_sync(db, organization_id)
    try:
        yield
    finally:
        clear_organization_context_sync(db)


def get_current_organization_id_sync(db: Session) -> Optional[uuid.UUID]:
    """
    Get the current organization ID from the session context (sync version).

    Args:
        db: The database session

    Returns:
        The current organization UUID, or None if not set
    """
    result = db.execute(
        text("SELECT current_setting('app.current_organization_id', true)")
    )
    value = result.scalar()
    if value:
        try:
            return uuid.UUID(value)
        except ValueError:
            return None
    return None
