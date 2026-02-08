from collections.abc import Generator
from contextlib import contextmanager

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
        connect_args["options"] = (
            f"-c statement_timeout={settings.db_statement_timeout_ms}"
        )

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
        connect_args["options"] = (
            f"-c statement_timeout={settings.db_statement_timeout_ms}"
        )

    return connect_args


def get_async_engine():
    """Get async database engine."""
    # Convert postgresql:// to postgresql+psycopg:// for async psycopg
    async_url = settings.database_url.replace(
        "postgresql://", "postgresql+psycopg://"
    ).replace("postgresql+asyncpg://", "postgresql+psycopg://")
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


def get_db() -> Generator[Session, None, None]:
    """Dependency that provides a transactional DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Shared Auth Database Session (for SSO)
# ---------------------------------------------------------------------------
# When SSO is enabled and this app is an SSO client (not provider),
# auth queries are made against the shared auth database on the SSO provider.

# Lazy initialization - only create engine when actually needed
_auth_engine = None
_auth_session_local = None


def get_auth_engine():
    """Get engine for shared auth database (lazy initialization).

    For SSO Provider (App #1): Uses main DATABASE_URL
    For SSO Clients (App #2, #3): Uses AUTH_DATABASE_URL

    The auth engine is used for validating sessions and tokens against
    the shared auth database that lives on the SSO provider.

    Engine is created lazily on first use to avoid startup failures
    when the auth database is temporarily unavailable.
    """
    global _auth_engine
    if _auth_engine is not None:
        return _auth_engine

    url = settings.auth_database_url or settings.database_url

    # Build connect_args for SSL and timeouts
    connect_args: dict = {}
    if settings.db_statement_timeout_ms > 0:
        connect_args["options"] = (
            f"-c statement_timeout={settings.db_statement_timeout_ms}"
        )

    # Require SSL when connecting to remote auth database
    if settings.auth_database_url and "postgresql" in settings.auth_database_url:
        connect_args["sslmode"] = "require"

    _auth_engine = create_engine(
        url,
        pool_pre_ping=True,
        pool_size=3,  # Smaller pool for auth queries
        max_overflow=5,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
        connect_args=connect_args if connect_args else {},
    )
    return _auth_engine


def _get_auth_session_maker():
    """Get auth session maker (lazy initialization)."""
    global _auth_session_local
    if _auth_session_local is not None:
        return _auth_session_local

    _auth_session_local = sessionmaker(
        bind=get_auth_engine(),
        autoflush=False,
        autocommit=False,
    )
    return _auth_session_local


class _AuthSessionLocalProxy:
    """Proxy for lazy auth session creation.

    Delays engine/sessionmaker creation until first use.
    """

    def __call__(self):
        return _get_auth_session_maker()()


# Alias for consistent naming with SessionLocal
AuthSessionLocal = _AuthSessionLocalProxy()


def get_auth_db():
    """Dependency for auth database session.

    Use this when validating tokens/sessions against the shared auth database
    in SSO client mode.
    """
    db = _get_auth_session_maker()()
    try:
        yield db
    finally:
        db.close()


def get_auth_db_session() -> Session:
    """Get a direct auth database session (not a generator).

    Use this in non-dependency contexts where you need a session directly.
    Caller is responsible for closing the session.
    """
    session: Session = _get_auth_session_maker()()
    return session


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
    if db.in_transaction():
        nested = db.begin_nested()
        try:
            yield db
            nested.commit()
        except Exception:
            nested.rollback()
            raise
        return

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
