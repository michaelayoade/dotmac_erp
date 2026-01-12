from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def get_engine():
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
    )


def get_async_engine():
    """Get async database engine."""
    # Convert postgresql:// to postgresql+asyncpg://
    async_url = settings.database_url.replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    return create_async_engine(
        async_url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
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
