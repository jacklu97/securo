import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.user import User


class PairedDevice(Base):
    __tablename__ = "paired_devices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    platform: Mapped[str] = mapped_column(String(20), default="other")
    app_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # SHA-256 hex of the current refresh token; the raw token is only ever
    # returned once (at pairing or rotation) and lives in the device's
    # secure storage.
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # Hash of the token replaced by the last rotation. A token presented
    # after it was rotated means the raw token leaked — the device is
    # revoked on the spot (reuse detection).
    previous_token_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="paired_devices")
