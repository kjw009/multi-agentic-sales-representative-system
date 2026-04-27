import uuid
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.config import settings

engine = create_async_engine(settings.database_url, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def set_current_seller_id(session: AsyncSession, seller_id: uuid.UUID) -> None:
    """Set the RLS context for the current transaction.

    SET LOCAL scopes the setting to the current transaction so it cannot leak
    across requests when connections are reused from the pool.
    """
    # SET LOCAL does not accept bind parameters — UUID is safe to inline directly.
    await session.execute(text(f"SET LOCAL app.current_seller_id = '{seller_id}'"))
