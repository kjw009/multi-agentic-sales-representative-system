import enum
# UUID: 128-bit unique identifier for distributed-safe primary keys
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
    pending = "pending"                     # Created, not processed
    intake_in_progress = "intake_in_progress"  # Metadata/images being prepared
    intake_complete = "intake_complete"     # Ready for pricing
    priced = "priced"                       # Price assigned
    publishing = "publishing"               # Being pushed to marketplace
    live = "live"                           # Visible for sale
    sold = "sold"                           # Completed sale
    removed = "removed"                     # Withdrawn or deleted
    error = "error"                         # Failure state


class ChatRole(enum.StrEnum):
    # Role in conversational context (LLM or user)
    user = "user"
    assistant = "assistant"


class Platform(enum.StrEnum):
    # External sales channels (expandable)
    ebay = "ebay"


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
    url: Mapped[str] = mapped_column(String(2048), nullable=False)     # Public access URL

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
