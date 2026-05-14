"""Phase 5 — seller settings API tests."""

import socket
import uuid
from urllib.parse import urlparse

import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.main import app
from packages.config import settings
from packages.db.models import AutonomyLevel, Seller
from packages.db.session import SessionLocal


def _postgres_reachable() -> bool:
    try:
        parsed = urlparse(settings.database_url.replace("+asyncpg", ""))
        host = parsed.hostname or "localhost"
        port = parsed.port or 5432
        with socket.create_connection((host, port), timeout=1):
            return True
    except (OSError, ValueError):
        return False


pytestmark = [pytest.mark.skipif(not _postgres_reachable(), reason="Postgres not reachable")]


@pytest.mark.asyncio
async def test_get_settings_returns_defaults():
    from apps.api.deps import get_current_seller

    async with SessionLocal() as session:
        seller = Seller(
            email=f"settings-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="fakehash",
        )
        session.add(seller)
        await session.commit()

        app.dependency_overrides[get_current_seller] = lambda: seller
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
                resp = await ac.get("/settings/seller")
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        body = resp.json()
        assert body["autonomy_level"] == "draft"
        assert body["stale_threshold_days"] == 7
        assert body["max_reprice_count"] == 3


@pytest.mark.asyncio
async def test_patch_settings_updates_fields():
    from apps.api.deps import get_current_seller

    async with SessionLocal() as session:
        seller = Seller(
            email=f"settings-patch-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="fakehash",
        )
        session.add(seller)
        await session.commit()

        app.dependency_overrides[get_current_seller] = lambda: seller
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
                resp = await ac.patch(
                    "/settings/seller",
                    json={
                        "autonomy_level": "auto_low_risk",
                        "stale_threshold_days": 14,
                        "max_reprice_count": 5,
                    },
                )
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        body = resp.json()
        assert body["autonomy_level"] == "auto_low_risk"
        assert body["stale_threshold_days"] == 14
        assert body["max_reprice_count"] == 5

        await session.refresh(seller)
        assert seller.autonomy_level == AutonomyLevel.auto_low_risk
        assert seller.stale_threshold_days == 14
        assert seller.max_reprice_count == 5


@pytest.mark.asyncio
async def test_patch_settings_rejects_out_of_range():
    from apps.api.deps import get_current_seller

    async with SessionLocal() as session:
        seller = Seller(
            email=f"settings-bad-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="fakehash",
        )
        session.add(seller)
        await session.commit()

        app.dependency_overrides[get_current_seller] = lambda: seller
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
                resp = await ac.patch(
                    "/settings/seller",
                    json={"stale_threshold_days": 999},
                )
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_patch_settings_empty_body_rejected():
    from apps.api.deps import get_current_seller

    async with SessionLocal() as session:
        seller = Seller(
            email=f"settings-empty-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="fakehash",
        )
        session.add(seller)
        await session.commit()

        app.dependency_overrides[get_current_seller] = lambda: seller
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
                resp = await ac.patch("/settings/seller", json={})
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 400
