from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def _get_connect_args() -> dict:
    """Get database connection arguments including timeout settings."""
    connect_args: dict = {}

    # Add statement timeout if configured (prevents runaway queries)
    if settings.db_statement_timeout_ms > 0:
        # For psycopg (both 2 and 3), use options parameter
        connect_args["options"] = f"-c statement_timeout={settings.db_statement_timeout_ms}"

    return connect_args


def get_engine():
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
        connect_args=_get_connect_args(),
    )


def _get_async_connect_args() -> dict:
    """Get async database connection arguments including timeout settings."""
    connect_args: dict = {}

    if settings.db_statement_timeout_ms > 0:
        # psycopg uses options for statement timeout
        connect_args["options"] = f"-c statement_timeout={settings.db_statement_timeout_ms}"

    return connect_args


def get_async_engine():
    """Get async database engine."""
    # Convert postgresql:// to postgresql+psycopg:// for async psycopg
    async_url = settings.database_url.replace(
        "postgresql://", "postgresql+psycopg://"
    ).replace(
        "postgresql+asyncpg://", "postgresql+psycopg://"
    )
    return create_async_engine(
        async_url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
        connect_args=_get_async_connect_args(),
    )


SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)

# Lazy initialization of async session
_async_session_local = None


def get_async_session_local():
    """Get async session maker (lazy initialization)."""
    global _async_session_local
    if _async_session_local is None:
        _async_session_local = async_sessionmaker(
            bind=get_async_engine(),
            class_=AsyncSession,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
    return _async_session_local


# Alias for backwards compatibility
class AsyncSessionLocalProxy:
    """Proxy class for lazy async session creation."""

    def __call__(self):
        return get_async_session_local()()


AsyncSessionLocal = AsyncSessionLocalProxy()


def get_db_session():
    """
    Dependency that provides a database session.

    Usage:
        @router.post("/items")
        async def create_item(db: Session = Depends(get_db_session)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def transaction(db: Session) -> Generator[Session, None, None]:
    """Context manager for explicit transaction handling.

    Ensures proper commit/rollback semantics:
    - Commits on successful completion
    - Rolls back on any exception
    - Re-raises the original exception

    Usage:
        with transaction(db):
            service.create_item(db, data)
            service.create_related(db, more_data)
            # Both committed together, or both rolled back on error

    For nested transactions (savepoints):
        with transaction(db):
            service.create_parent(db, parent)
            with transaction(db):  # Creates savepoint
                service.create_child(db, child)
                # Can rollback just this without affecting parent
    """
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise


@contextmanager
def atomic_operation(db: Session) -> Generator[Session, None, None]:
    """Context manager for atomic database operations with savepoint.

    Uses database savepoints for nested transaction support.
    If the operation fails, only changes within this block are rolled back.

    Usage:
        # In a service method already within a transaction:
        with atomic_operation(db):
            # These changes can be rolled back independently
            db.add(item1)
            db.add(item2)
            if some_condition:
                raise ValueError("Rollback just these items")
    """
    savepoint = db.begin_nested()
    try:
        yield db
        savepoint.commit()
    except Exception:
        savepoint.rollback()
        raise
