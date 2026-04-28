"""
Database session management for the application.

Provides async SQLAlchemy engine, session factory, and utilities for managing
database connections and Row Level Security (RLS) context.
"""

import uuid
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.config import settings

# Create async SQLAlchemy engine using the database URL from settings
engine = create_async_engine(settings.database_url, future=True)

# Create async session maker with engine, disabling expire_on_commit for better performance
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    """
    Dependency function to provide an async database session.

    Yields a new session for each request, ensuring proper cleanup via context manager.
    Used as a FastAPI dependency to inject sessions into route handlers.
    """
    async with SessionLocal() as session:
        yield session


async def set_current_seller_id(session: AsyncSession, seller_id: uuid.UUID) -> None:
    """Set the RLS context for the current transaction.

    SET LOCAL scopes the setting to the current transaction so it cannot leak
    across requests when connections are reused from the pool.
    """
    # SET LOCAL does not accept bind parameters — UUID is safe to inline directly.
    # This sets the seller ID for Row Level Security policies in PostgreSQL
    await session.execute(text(f"SET LOCAL app.current_seller_id = '{seller_id}'"))
