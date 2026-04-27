import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
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


class ItemCondition(str, enum.Enum):
    new = "new"
    like_new = "like_new"
    good = "good"
    fair = "fair"
    poor = "poor"


class ItemStatus(str, enum.Enum):
    pending = "pending"
    intake_in_progress = "intake_in_progress"
    intake_complete = "intake_complete"
    priced = "priced"
    publishing = "publishing"
    live = "live"
    sold = "sold"
    removed = "removed"
    error = "error"


class ChatRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"


class Platform(str, enum.Enum):
    ebay = "ebay"


class Seller(Base):
    __tablename__ = "sellers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    items: Mapped[list["Item"]] = relationship("Item", back_populates="seller", cascade="all, delete-orphan")
    platform_credentials: Mapped[list["PlatformCredential"]] = relationship("PlatformCredential", back_populates="seller", cascade="all, delete-orphan")
    chat_messages: Mapped[list["ChatMessage"]] = relationship("ChatMessage", back_populates="seller", cascade="all, delete-orphan")


class Item(Base):
    __tablename__ = "items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sellers.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    subcategory: Mapped[str | None] = mapped_column(String(100))
    condition: Mapped[ItemCondition] = mapped_column(Enum(ItemCondition, name="item_condition"), nullable=False)
    age_months: Mapped[int | None] = mapped_column(SmallInteger)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    seller_floor_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    status: Mapped[ItemStatus] = mapped_column(Enum(ItemStatus, name="item_status"), nullable=False, default=ItemStatus.pending)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    seller: Mapped["Seller"] = relationship("Seller", back_populates="items")
    images: Mapped[list["ItemImage"]] = relationship("ItemImage", back_populates="item", cascade="all, delete-orphan", order_by="ItemImage.position")
    chat_messages: Mapped[list["ChatMessage"]] = relationship("ChatMessage", back_populates="item")


class ItemImage(Base):
    __tablename__ = "item_images"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), nullable=False, index=True)
    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sellers.id", ondelete="CASCADE"), nullable=False, index=True)
    s3_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    item: Mapped["Item"] = relationship("Item", back_populates="images")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sellers.id", ondelete="CASCADE"), nullable=False, index=True)
    item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id", ondelete="SET NULL"), index=True)
    role: Mapped[ChatRole] = mapped_column(Enum(ChatRole, name="chat_role"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    seller: Mapped["Seller"] = relationship("Seller", back_populates="chat_messages")
    item: Mapped["Item | None"] = relationship("Item", back_populates="chat_messages")


class PlatformCredential(Base):
    __tablename__ = "platform_credentials"
    __table_args__ = (UniqueConstraint("seller_id", "platform", name="uq_platform_credentials_seller_platform"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sellers.id", ondelete="CASCADE"), nullable=False, index=True)
    platform: Mapped[Platform] = mapped_column(Enum(Platform, name="platform"), nullable=False)
    oauth_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    seller: Mapped["Seller"] = relationship("Seller", back_populates="platform_credentials")
