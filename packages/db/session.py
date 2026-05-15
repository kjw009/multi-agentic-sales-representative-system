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

# --- 1. Engine Setup ---
# create_async_engine manages the low-level connection pool to PostgreSQL.
#
# pool_pre_ping: asyncpg connections are bound to the event loop that opened
# them. If a pooled connection is ever handed to a different loop (or RDS
# silently drops an idle connection), using it fails with "another operation
# is in progress". Pre-ping issues a cheap liveness check on checkout and
# transparently discards + replaces a dead connection instead of crashing.
engine = create_async_engine(
    settings.database_url,
    future=True,
    pool_pre_ping=True,
)

# --- 2. Session Configuration ---
# SessionLocal is a factory for creating individual database transactions
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
    """
    Set the RLS context for the current transaction.

    Security Layer: Injects the current user's ID into the Postgres session.

    This allows the database itself to filter data so that users can
    only see rows belonging to their specific 'seller_id'.

    SET LOCAL scopes the setting to the current transaction so it cannot leak
    across requests when connections are reused from the pool.
    """
    # SET LOCAL does not accept bind parameters — UUID is safe to inline directly.
    # This sets the seller ID for Row Level Security policies in PostgreSQL
    await session.execute(text(f"SET LOCAL app.current_seller_id = '{seller_id}'"))
