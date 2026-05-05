import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
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
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Minimum acceptable price from seller
    seller_floor_price: Mapped[float | None] = mapped_column(Numeric(12, 2))

    # Pricing agent output (written by Agent 2 after pipeline runs)
    recommended_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    min_acceptable_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    confidence_score: Mapped[float | None] = mapped_column(Numeric(5, 4))
    price_low: Mapped[float | None] = mapped_column(Numeric(12, 2))  # CI lower bound
    price_high: Mapped[float | None] = mapped_column(Numeric(12, 2))  # CI upper bound
    pricing_comparables: Mapped[list | None] = mapped_column(JSONB)  # raw comparable listings

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


class BuyerMessage(Base):
    __tablename__ = "buyer_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
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

    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")
