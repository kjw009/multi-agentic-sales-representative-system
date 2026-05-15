import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from packages.db.base import Base

# --- ENUMS ---


class ItemCondition(enum.StrEnum):
    # Standardized condition grading for resale items
    new = "new"
    like_new = "like_new"
    good = "good"
    fair = "fair"
    poor = "poor"


class ItemStatus(enum.StrEnum):
    # Item lifecycle through ingestion → listing → sale
    pending = "pending"  # Created, not processed
    intake_in_progress = "intake_in_progress"  # Metadata/images being prepared
    intake_complete = "intake_complete"  # Ready for pricing
    priced = "priced"  # Price assigned
    publishing = "publishing"  # Being pushed to marketplace
    needs_specifics = "needs_specifics"  # eBay rejected — seller must supply more item specifics
    live = "live"  # Visible for sale
    sold = "sold"  # Completed sale
    removed = "removed"  # Withdrawn or deleted
    error = "error"  # Failure state


class ChatRole(enum.StrEnum):
    # Role in conversational context (LLM or user)
    user = "user"
    assistant = "assistant"


class Platform(enum.StrEnum):
    # External sales channels (expandable)
    ebay = "ebay"


class ListingStatus(enum.StrEnum):
    # Listing lifecycle through publishing → sale
    publishing = "publishing"  # Being pushed to marketplace
    live = "live"  # Visible and active on marketplace
    ended = "ended"  # Closed (sold, withdrawn, etc.)
    error = "error"  # Failed to publish


class MessageDirection(enum.StrEnum):
    # Direction of eBay buyer message
    inbound = "inbound"
    outbound = "outbound"


class NegotiationStatus(enum.StrEnum):
    # State machine for buyer-seller negotiation threads
    active = "active"  # Offer received, awaiting agent decision
    countered = "countered"  # Counter-offer sent to buyer
    accepted = "accepted"  # Offer accepted → sale confirmation
    declined = "declined"  # Offer declined by agent
    expired = "expired"  # Timed out with no response
    seller_review = "seller_review"  # Agent escalated to seller for approval


class ModelStatus(enum.StrEnum):
    training = "training"
    shadow = "shadow"
    active = "active"
    archived = "archived"
    failed = "failed"


class PlanTier(enum.StrEnum):
    free = "free"
    pro = "pro"


class SubscriptionStatus(enum.StrEnum):
    none = "none"
    trialing = "trialing"
    active = "active"
    past_due = "past_due"
    canceled = "canceled"


class AutonomyLevel(enum.StrEnum):
    # Per-seller cap on how much Agent 4 may send without approval.
    draft = "draft"  # Every reply requires seller approval
    auto_low_risk = "auto_low_risk"  # Auto-send send_info + decline_offer
    full_auto = "full_auto"  # Auto-send everything except sale acceptance and seller escalation


# --- MODELS ---
class Seller(Base):
    __tablename__ = "sellers"

    # UUID primary key (avoids sequential ID exposure)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Authentication fields
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    # Soft account enable/disable
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # SNS notification topic (if enabled)
    sns_topic_arn: Mapped[str | None] = mapped_column(String(2048))

    # Phase 5 — autonomy & stale-reprice settings
    autonomy_level: Mapped[AutonomyLevel] = mapped_column(
        Enum(AutonomyLevel, name="autonomy_level"),
        nullable=False,
        default=AutonomyLevel.draft,
    )
    # Days a listing can sit without buyer interaction before it qualifies for reprice
    stale_threshold_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    # Hard cap on automatic reprices per listing
    max_reprice_count: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    # Onboarding + demo flags (Phase 7)
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Stripe billing (Phase 7)
    stripe_customer_id: Mapped[str | None] = mapped_column(Text)
    plan: Mapped[PlanTier] = mapped_column(
        Enum(PlanTier, name="plan_tier"),
        nullable=False,
        default=PlanTier.free,
    )
    subscription_status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, name="subscription_status"),
        nullable=False,
        default=SubscriptionStatus.none,
    )
    stripe_subscription_id: Mapped[str | None] = mapped_column(Text)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Auto-managed timestamps (DB-side defaults)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),  # Updates automatically on row modification
    )

    # Relationships
    items: Mapped[list["Item"]] = relationship(
        "Item",
        back_populates="seller",
        cascade="all, delete-orphan",  # Deletes items when seller is deleted
    )
    platform_credentials: Mapped[list["PlatformCredential"]] = relationship(
        "PlatformCredential",
        back_populates="seller",
        cascade="all, delete-orphan",
    )
    chat_messages: Mapped[list["ChatMessage"]] = relationship(
        "ChatMessage",
        back_populates="seller",
        cascade="all, delete-orphan",
    )
    listings: Mapped[list["Listing"]] = relationship(
        "Listing",
        back_populates="seller",
        cascade="all, delete-orphan",
    )
    negotiations: Mapped[list["Negotiation"]] = relationship(
        "Negotiation",
        back_populates="seller",
        cascade="all, delete-orphan",
    )
    sales: Mapped[list["Sale"]] = relationship(
        "Sale",
        back_populates="seller",
        cascade="all, delete-orphan",
    )
    clarification_requests: Mapped[list["ClarificationRequest"]] = relationship(
        "ClarificationRequest",
        back_populates="seller",
        cascade="all, delete-orphan",
    )


class Item(Base):
    __tablename__ = "items"

    # Primary key
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Owner reference
    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sellers.id", ondelete="CASCADE"),  # Delete items if seller is deleted
        nullable=False,
        index=True,
    )

    # Core listing data
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    subcategory: Mapped[str | None] = mapped_column(String(100))

    # Enum stored as PostgreSQL enum type
    condition: Mapped[ItemCondition] = mapped_column(
        Enum(ItemCondition, name="item_condition"),
        nullable=False,
    )

    # Optional metadata
    age_months: Mapped[int | None] = mapped_column(SmallInteger)  # Age in months (if known)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Flexible attributes (e.g. size, color, material)
    # NOTE: default=dict is safe here because SQLAlchemy handles callable defaults
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    # Vision-derived condition analysis from uploaded item photos.
    visual_condition_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    visual_condition_analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    visual_condition_needs_confirmation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # Minimum acceptable price from seller
    seller_floor_price: Mapped[float | None] = mapped_column(Numeric(12, 2))

    # Pricing agent output (written by Agent 2 after pipeline runs)
    recommended_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    min_acceptable_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    confidence_score: Mapped[float | None] = mapped_column(Numeric(5, 4))
    price_low: Mapped[float | None] = mapped_column(Numeric(12, 2))  # CI lower bound
    price_high: Mapped[float | None] = mapped_column(Numeric(12, 2))  # CI upper bound
    pricing_comparables: Mapped[list[Any] | None] = mapped_column(JSONB)  # raw comparable listings

    # eBay item-specific names that the seller still owes us before the
    # listing can publish. Populated by the publisher when AddFixedPriceItem
    # rejects with "item specific X is missing"; cleared one-by-one by
    # intake as the seller answers each one.
    required_specifics: Mapped[list[str] | None] = mapped_column(JSONB)

    # Workflow state machine
    status: Mapped[ItemStatus] = mapped_column(
        Enum(ItemStatus, name="item_status"),
        nullable=False,
        default=ItemStatus.pending,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    seller: Mapped["Seller"] = relationship("Seller", back_populates="items")

    images: Mapped[list["ItemImage"]] = relationship(
        "ItemImage",
        back_populates="item",
        cascade="all, delete-orphan",
        order_by="ItemImage.position",  # Ensures consistent image ordering
    )

    chat_messages: Mapped[list["ChatMessage"]] = relationship(
        "ChatMessage",
        back_populates="item",
    )

    listings: Mapped[list["Listing"]] = relationship(
        "Listing",
        back_populates="item",
        cascade="all, delete-orphan",
    )


class ItemImage(Base):
    __tablename__ = "item_images"

    # Primary key
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Relationships
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("items.id", ondelete="CASCADE"),  # Delete images with item
        nullable=False,
        index=True,
    )
    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sellers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Storage references (e.g. S3)
    s3_key: Mapped[str] = mapped_column(String(1024), nullable=False)  # Internal storage key
    url: Mapped[str] = mapped_column(String(2048), nullable=False)  # Public access URL

    # Ordering (0 = primary image)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationship
    item: Mapped["Item"] = relationship("Item", back_populates="images")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    # Primary key
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Conversation ownership
    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sellers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Optional association to an item (chat can be global or item-specific)
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("items.id", ondelete="SET NULL"),  # Preserve chat if item is deleted
        index=True,
    )

    # Role in the conversation
    role: Mapped[ChatRole] = mapped_column(
        Enum(ChatRole, name="chat_role"),
        nullable=False,
    )

    # Message content (could be user input or LLM response)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    seller: Mapped["Seller"] = relationship("Seller", back_populates="chat_messages")
    item: Mapped["Item | None"] = relationship("Item", back_populates="chat_messages")


class PlatformCredential(Base):
    __tablename__ = "platform_credentials"

    # Enforces one credential per seller per platform
    __table_args__ = (
        UniqueConstraint("seller_id", "platform", name="uq_platform_credentials_seller_platform"),
    )

    # Primary key
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Owner
    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sellers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Platform identifier (enum-backed)
    platform: Mapped[Platform] = mapped_column(
        Enum(Platform, name="platform"),
        nullable=False,
    )

    # Encrypted tokens (never store raw OAuth tokens)
    oauth_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)

    # Expiration tracking for access token
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Supports key rotation (which encryption key version was used)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationship
    seller: Mapped["Seller"] = relationship("Seller", back_populates="platform_credentials")


class Listing(Base):
    __tablename__ = "listings"

    # Enforces one listing per item per platform
    __table_args__ = (UniqueConstraint("item_id", "platform", name="uq_listings_item_platform"),)

    # Primary key
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Item being listed
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Owner reference (required for RLS)
    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sellers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Platform
    platform: Mapped[Platform] = mapped_column(
        Enum(Platform, name="platform", create_type=False),
        nullable=False,
    )

    # External identifiers from the marketplace
    external_id: Mapped[str | None] = mapped_column(String(255))  # eBay listing ID
    # eBay offer ID — needed by /sell/inventory/v1/offer/{offer_id} for repricing.
    # Null for Trading-API-only listings (sandbox fallback path), which can't be
    # repriced through update_offer_price.
    external_offer_id: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(String(2048))  # Live listing URL

    # Listing lifecycle status
    status: Mapped[ListingStatus] = mapped_column(
        Enum(ListingStatus, name="listing_status"),
        nullable=False,
        default=ListingStatus.publishing,
    )

    # Price at which the item was listed
    posted_price: Mapped[float | None] = mapped_column(Numeric(12, 2))

    # Reason the listing was closed (sold, withdrawn, expired, etc.)
    close_reason: Mapped[str | None] = mapped_column(String(255))

    # Reprice tracking
    reprice_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_repriced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Buyer interaction tracking
    last_buyer_interaction_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Lifecycle timestamps
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Auto-managed timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    item: Mapped["Item"] = relationship("Item", back_populates="listings")
    seller: Mapped["Seller"] = relationship("Seller", back_populates="listings")
    reprice_events: Mapped[list["RepriceEvent"]] = relationship(
        "RepriceEvent",
        back_populates="listing",
        cascade="all, delete-orphan",
        order_by="RepriceEvent.repriced_at.desc()",
    )


class RepriceEvent(Base):
    """One row per successful automatic reprice.

    Written by packages/agents/pricing/reprice.py after the eBay
    update_offer_price call returns OK. Surfaced by GET /listings/reprice-history.
    """

    __tablename__ = "reprice_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("listings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sellers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    old_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    new_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    repriced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    listing: Mapped["Listing"] = relationship("Listing", back_populates="reprice_events")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Context
    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sellers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    listing_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("listings.id", ondelete="SET NULL"),
        index=True,
    )

    buyer_handle: Mapped[str] = mapped_column(String(255), nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    messages: Mapped[list["BuyerMessage"]] = relationship(
        "BuyerMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="BuyerMessage.received_at",
    )


class EbayOAuthState(Base):
    """Short-lived CSRF state nonce for the eBay OAuth flow.

    Replaces Redis: rows expire after _STATE_TTL seconds and are deleted
    atomically when the callback consumes them.
    """

    __tablename__ = "ebay_oauth_states"

    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sellers.id", ondelete="CASCADE"),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BuyerMessage(Base):
    __tablename__ = "buyer_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # RLS-required denormalized seller reference
    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sellers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ID from eBay
    message_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)

    direction: Mapped[MessageDirection] = mapped_column(
        Enum(MessageDirection, name="message_direction"),
        nullable=False,
    )

    raw_text: Mapped[str] = mapped_column(Text, nullable=False)

    draft_reply: Mapped[str | None] = mapped_column(Text)
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Phase 5 — draft-edit rate tracking. NULL until the seller acts on the
    # draft: True when they edited the text before sending, False when they
    # approved or dismissed it as-is. Used by GET /conversations/stats.
    seller_edited: Mapped[bool | None] = mapped_column(Boolean)

    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Set by the SQS worker after NLP + Agent 4 have processed this message.
    # Prevents re-processing on worker retries.
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")
    nlp_annotation: Mapped["NlpAnnotation | None"] = relationship(
        "NlpAnnotation",
        back_populates="buyer_message",
        uselist=False,
        cascade="all, delete-orphan",
    )
    offer_signals: Mapped[list["OfferSignal"]] = relationship(
        "OfferSignal",
        back_populates="buyer_message",
        cascade="all, delete-orphan",
    )
    entity_mentions: Mapped[list["EntityMention"]] = relationship(
        "EntityMention",
        back_populates="buyer_message",
        cascade="all, delete-orphan",
    )


# --- PHASE 4: NEGOTIATION & NLP MODELS ---


class Negotiation(Base):
    """Tracks active offer threads between a buyer and the system."""

    __tablename__ = "negotiations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sellers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("listings.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Offer tracking
    current_offer: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    counter_offer: Mapped[float | None] = mapped_column(Numeric(12, 2))
    walk_away_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    status: Mapped[NegotiationStatus] = mapped_column(
        Enum(NegotiationStatus, name="negotiation_status"),
        nullable=False,
        default=NegotiationStatus.active,
    )
    rounds_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    seller: Mapped["Seller"] = relationship("Seller", back_populates="negotiations")
    conversation: Mapped["Conversation"] = relationship("Conversation")


class Sale(Base):
    """Finalized sale record — created when Agent 4 accepts an offer."""

    __tablename__ = "sales"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sellers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("listings.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    negotiation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("negotiations.id", ondelete="SET NULL"),
        index=True,
    )

    sale_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    buyer_handle: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[Platform] = mapped_column(
        Enum(Platform, name="platform", create_type=False),
        nullable=False,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    seller: Mapped["Seller"] = relationship("Seller", back_populates="sales")
    item: Mapped["Item"] = relationship("Item")
    negotiation: Mapped["Negotiation | None"] = relationship("Negotiation")


class ClarificationRequest(Base):
    """Tracks when Agent 4 requires seller input to answer a buyer question."""

    __tablename__ = "clarification_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sellers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    buyer_message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("buyer_messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    seller: Mapped["Seller"] = relationship("Seller", back_populates="clarification_requests")
    conversation: Mapped["Conversation"] = relationship("Conversation")
    buyer_message: Mapped["BuyerMessage"] = relationship("BuyerMessage")


class NlpAnnotation(Base):
    """NLP analysis results for a single buyer message."""

    __tablename__ = "nlp_annotations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    buyer_message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("buyer_messages.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # One annotation per message
        index=True,
    )
    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sellers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    intent: Mapped[str] = mapped_column(String(50), nullable=False)
    intent_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    sentiment: Mapped[str] = mapped_column(String(20), nullable=False)
    sentiment_score: Mapped[float] = mapped_column(Float, nullable=False)

    # Full model output for debugging/auditing
    raw_output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    buyer_message: Mapped["BuyerMessage"] = relationship(
        "BuyerMessage", back_populates="nlp_annotation"
    )


class OfferSignal(Base):
    """Extracted price offer from a buyer message (regex or NLP)."""

    __tablename__ = "offer_signals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    buyer_message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("buyer_messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sellers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # "regex" or "nlp"

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    buyer_message: Mapped["BuyerMessage"] = relationship(
        "BuyerMessage", back_populates="offer_signals"
    )


class EntityMention(Base):
    """spaCy NER extraction from a buyer message."""

    __tablename__ = "entity_mentions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    buyer_message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("buyer_messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sellers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. PERSON, MONEY, ORG
    entity_value: Mapped[str] = mapped_column(String(255), nullable=False)
    start_char: Mapped[int] = mapped_column(Integer, nullable=False)
    end_char: Mapped[int] = mapped_column(Integer, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    buyer_message: Mapped["BuyerMessage"] = relationship(
        "BuyerMessage", back_populates="entity_mentions"
    )


# ---------------------------------------------------------------------------
# Phase 6.0 — ML retraining loop data capture
# ---------------------------------------------------------------------------


class ModelVersion(Base):
    """Registry of LightGBM model artifacts."""

    __tablename__ = "model_versions"
    __table_args__ = (
        Index(
            "ix_model_versions_active_unique",
            "status",
            unique=True,
            postgresql_where="status = 'active'",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    algorithm: Mapped[str] = mapped_column(Text, nullable=False, default="lightgbm")
    artifact_s3_key: Mapped[str | None] = mapped_column(Text)
    feature_cols: Mapped[list[Any] | None] = mapped_column(JSONB)
    train_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    shadow_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    training_row_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[ModelStatus] = mapped_column(
        Enum(ModelStatus, name="model_status"),
        nullable=False,
        default=ModelStatus.training,
    )
    trained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    predictions: Mapped[list["PricePrediction"]] = relationship(
        "PricePrediction", back_populates="model_version"
    )


class PricePrediction(Base):
    """Point-in-time feature snapshot for one Agent 2 pricing decision."""

    __tablename__ = "price_predictions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sellers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    listing_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("listings.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    model_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("model_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    features: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    features_partial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    model_prediction: Mapped[float | None] = mapped_column(Numeric(12, 2))
    comparable_median: Mapped[float | None] = mapped_column(Numeric(12, 2))
    recommended_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    min_acceptable_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    confidence_score: Mapped[float | None] = mapped_column(Numeric(5, 4))
    is_shadow: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    realized_sale_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    realized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    model_version: Mapped["ModelVersion | None"] = relationship(
        "ModelVersion", back_populates="predictions"
    )
    comparables: Mapped[list["ComparableListing"]] = relationship(
        "ComparableListing", back_populates="prediction", cascade="all, delete-orphan"
    )


class ComparableListing(Base):
    """Persisted eBay comparable snapshot linked to a pricing prediction."""

    __tablename__ = "comparable_listings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    price_prediction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("price_predictions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sellers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_item_id: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(Text)
    condition: Mapped[str | None] = mapped_column(Text)
    listing_url: Mapped[str | None] = mapped_column(Text)
    relevance: Mapped[str | None] = mapped_column(Text)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    prediction: Mapped["PricePrediction"] = relationship(
        "PricePrediction", back_populates="comparables"
    )
