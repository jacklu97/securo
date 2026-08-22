"""Mobile device pairing: QR pairing codes, per-device refresh tokens,
and connection status (see docs/rfc-656-mobile-pairing.md).

Pairing codes live only in Redis (single-use via GETDEL, short TTL) —
the same pattern as 2FA temp tokens and passkey challenges. Refresh
tokens are opaque secrets stored as SHA-256 hashes; each exchange
rotates the token, and presenting an already-rotated token revokes the
device (reuse detection).
"""

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi_users.authentication import JWTStrategy
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.paired_device import PairedDevice
from app.models.user import User

PAIRING_TTL_SECONDS = 300
# A device counts as connected if it heartbeat within this window.
CONNECTED_WINDOW_SECONDS = 120
# Device access tokens are short-lived so revoking a device bites quickly
# despite stateless JWTs — revocation is enforced at refresh time.
DEVICE_ACCESS_TOKEN_SECONDS = 15 * 60

_PAIRING_KEY = "device_pairing:{code}"
_PAIRING_STATUS_KEY = "device_pairing_status:{pairing_id}"


class PairingError(Exception):
    """Invalid, expired, or already-used pairing code."""


class DeviceTokenError(Exception):
    """Invalid, revoked, or reused refresh token."""


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_connected(device: PairedDevice) -> bool:
    if device.last_seen_at is None:
        return False
    last_seen = device.last_seen_at
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    return _now() - last_seen <= timedelta(seconds=CONNECTED_WINDOW_SECONDS)


def mint_access_token_strategy() -> JWTStrategy:
    """Same JWT family the web app uses, with a shorter lifetime."""
    settings = get_settings()
    return JWTStrategy(
        secret=settings.secret_key.get_secret_value(),
        lifetime_seconds=DEVICE_ACCESS_TOKEN_SECONDS,
    )


async def create_pairing(redis: Redis, user_id: uuid.UUID) -> tuple[str, str]:
    """Mint a single-use pairing code for `user_id`. Returns (pairing_id, code)."""
    pairing_id = uuid.uuid4().hex
    code = secrets.token_urlsafe(32)
    payload = json.dumps({"user_id": str(user_id), "pairing_id": pairing_id})
    await redis.set(_PAIRING_KEY.format(code=code), payload, ex=PAIRING_TTL_SECONDS)
    await redis.set(
        _PAIRING_STATUS_KEY.format(pairing_id=pairing_id),
        json.dumps({"status": "pending", "user_id": str(user_id)}),
        ex=PAIRING_TTL_SECONDS,
    )
    return pairing_id, code


async def get_pairing_status(
    redis: Redis, pairing_id: str, user_id: uuid.UUID
) -> Optional[dict]:
    """Pairing status for the user who created it; None if expired/unknown."""
    raw = await redis.get(_PAIRING_STATUS_KEY.format(pairing_id=pairing_id))
    if raw is None:
        return None
    status = json.loads(raw)
    if status.get("user_id") != str(user_id):
        return None
    return status


async def redeem_pairing(
    session: AsyncSession,
    redis: Redis,
    code: str,
    name: str,
    platform: str,
    app_version: Optional[str],
    ip: Optional[str],
) -> tuple[PairedDevice, str]:
    """Exchange a pairing code for a new device + raw refresh token.

    The code is consumed atomically (GETDEL) so it can only be redeemed once.
    """
    raw = await redis.getdel(_PAIRING_KEY.format(code=code))
    if raw is None:
        raise PairingError("Invalid or expired pairing code")
    payload = json.loads(raw)

    user = await session.get(User, uuid.UUID(payload["user_id"]))
    if user is None or not user.is_active:
        raise PairingError("Invalid or expired pairing code")

    refresh_token = secrets.token_urlsafe(48)
    device = PairedDevice(
        user_id=user.id,
        name=name,
        platform=platform,
        app_version=app_version,
        refresh_token_hash=_hash_token(refresh_token),
        last_seen_at=_now(),
        last_ip=ip,
    )
    session.add(device)
    await session.commit()
    await session.refresh(device)

    await redis.set(
        _PAIRING_STATUS_KEY.format(pairing_id=payload["pairing_id"]),
        json.dumps(
            {"status": "claimed", "user_id": payload["user_id"], "device_name": name}
        ),
        ex=PAIRING_TTL_SECONDS,
    )
    return device, refresh_token


async def refresh_device_token(
    session: AsyncSession, token: str, ip: Optional[str]
) -> tuple[PairedDevice, User, str]:
    """Rotate a refresh token. Returns (device, user, new raw refresh token).

    Presenting a token that was already rotated is treated as theft and
    revokes the device.
    """
    token_hash = _hash_token(token)

    reused = await session.execute(
        select(PairedDevice).where(PairedDevice.previous_token_hash == token_hash)
    )
    reused_device = reused.scalar_one_or_none()
    if reused_device is not None:
        if reused_device.revoked_at is None:
            reused_device.revoked_at = _now()
            await session.commit()
        raise DeviceTokenError("Refresh token reuse detected; device revoked")

    result = await session.execute(
        select(PairedDevice).where(PairedDevice.refresh_token_hash == token_hash)
    )
    device = result.scalar_one_or_none()
    if device is None or device.revoked_at is not None:
        raise DeviceTokenError("Invalid refresh token")

    user = await session.get(User, device.user_id)
    if user is None or not user.is_active:
        raise DeviceTokenError("Invalid refresh token")

    new_token = secrets.token_urlsafe(48)
    device.previous_token_hash = device.refresh_token_hash
    device.refresh_token_hash = _hash_token(new_token)
    device.last_seen_at = _now()
    device.last_ip = ip
    await session.commit()
    await session.refresh(device)
    return device, user, new_token


async def list_devices(session: AsyncSession, user_id: uuid.UUID) -> list[PairedDevice]:
    result = await session.execute(
        select(PairedDevice)
        .where(PairedDevice.user_id == user_id, PairedDevice.revoked_at.is_(None))
        .order_by(PairedDevice.created_at.desc())
    )
    return list(result.scalars().all())


async def get_device(
    session: AsyncSession, device_id: uuid.UUID, user_id: uuid.UUID
) -> Optional[PairedDevice]:
    result = await session.execute(
        select(PairedDevice).where(
            PairedDevice.id == device_id,
            PairedDevice.user_id == user_id,
            PairedDevice.revoked_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def revoke_device(session: AsyncSession, device: PairedDevice) -> None:
    device.revoked_at = _now()
    await session.commit()


async def touch_device(
    session: AsyncSession, device: PairedDevice, ip: Optional[str]
) -> None:
    device.last_seen_at = _now()
    device.last_ip = ip
    await session.commit()
