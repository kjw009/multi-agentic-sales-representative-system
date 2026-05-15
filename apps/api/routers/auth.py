from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.auth import create_access_token, hash_password, verify_password
from packages.config import settings
from packages.db.models import Seller
from packages.db.session import get_session
from packages.notifications import create_seller_topic
from packages.schemas.auth import LoginRequest, SignupRequest, TokenResponse

_DEMO_EMAIL = "demo@salesrep.app"
_DEMO_PASSWORD = "demo-salesrep-2025"

# Create APIRouter for authentication endpoints with /auth prefix
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    body: SignupRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    """
    Handle user signup by creating a new seller account.

    Checks if the email is already registered, hashes the password,
    creates and commits the new seller to the database, then returns an access token.
    """
    # Check if a seller with this email already exists
    existing = await session.scalar(select(Seller).where(Seller.email == body.email))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    # Create new seller with hashed password
    seller = Seller(email=body.email, hashed_password=hash_password(body.password))
    session.add(seller)
    await session.commit()
    await session.refresh(seller)

    if settings.sns_enabled:
        topic_arn = create_seller_topic(str(seller.id), seller.email)
        seller.sns_topic_arn = topic_arn
        await session.commit()

    # Return access token for the new seller
    return TokenResponse(access_token=create_access_token(seller.id), seller_id=seller.id)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, session: AsyncSession = Depends(get_session)) -> TokenResponse:
    """
    Handle user login by verifying credentials and returning an access token.

    Queries for an active seller with matching email, verifies the password,
    and returns an access token if authentication succeeds.
    """
    # Query for active seller with matching email
    seller = await session.scalar(
        select(Seller).where(Seller.email == body.email, Seller.is_active)
    )
    if seller is None or not verify_password(body.password, seller.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )

    # Return access token for the authenticated seller
    return TokenResponse(access_token=create_access_token(seller.id), seller_id=seller.id)


@router.get("/demo", response_model=TokenResponse)
async def demo_login(session: AsyncSession = Depends(get_session)) -> TokenResponse:
    """Return a token for the shared read-only demo account.

    The demo seller is created on first call and seeded with fixtures.
    Mutating endpoints guard against is_demo=True sellers via the
    `require_not_demo` dependency in apps/api/deps.py.
    """
    demo = await session.scalar(select(Seller).where(Seller.email == _DEMO_EMAIL))
    if demo is None:
        demo = Seller(
            email=_DEMO_EMAIL,
            hashed_password=hash_password(_DEMO_PASSWORD),
            is_demo=True,
            onboarding_completed=True,
        )
        session.add(demo)
        await session.commit()
        await session.refresh(demo)
    return TokenResponse(access_token=create_access_token(demo.id), seller_id=demo.id)
