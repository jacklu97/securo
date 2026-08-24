import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_active_user
from app.core.database import get_async_session
from app.core.rate_limit import login_rate_limit
from app.core.redis import get_redis
from app.models.user import User
from app.schemas.device import (
    DeviceRead,
    DeviceTokenRequest,
    DeviceTokenResponse,
    DeviceUpdate,
    HeartbeatRequest,
    PairingCreateResponse,
    PairingStatusResponse,
    PairRequest,
    PairResponse,
)
from app.services import device_service

router = APIRouter(prefix="/api/devices", tags=["devices"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _device_read(device) -> DeviceRead:
    read = DeviceRead.model_validate(device)
    read.connected = device_service.is_connected(device)
    return read


# Literal-prefix routes are registered before the `/{device_id}` catch-all.


@router.post("/pairings", response_model=PairingCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_pairing(
    user: User = Depends(current_active_user),
):
    redis = await get_redis()
    pairing_id, code = await device_service.create_pairing(redis, user.id)
    return PairingCreateResponse(
        pairing_id=pairing_id,
        code=code,
        expires_in=device_service.PAIRING_TTL_SECONDS,
    )


@router.get("/pairings/{pairing_id}", response_model=PairingStatusResponse)
async def get_pairing_status(
    pairing_id: str,
    user: User = Depends(current_active_user),
):
    redis = await get_redis()
    pairing = await device_service.get_pairing_status(redis, pairing_id, user.id)
    if pairing is None:
        raise HTTPException(status_code=404, detail="Pairing not found or expired")
    return PairingStatusResponse(
        status=pairing["status"], device_name=pairing.get("device_name")
    )


@router.post("/pair", response_model=PairResponse, dependencies=[Depends(login_rate_limit)])
async def pair_device(
    body: PairRequest,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
):
    redis = await get_redis()
    try:
        device, refresh_token = await device_service.redeem_pairing(
            session,
            redis,
            code=body.code,
            name=body.name,
            platform=body.platform,
            app_version=body.app_version,
            ip=_client_ip(request),
        )
    except device_service.PairingError:
        raise HTTPException(status_code=400, detail="Invalid or expired pairing code")

    user = await session.get(User, device.user_id)
    access_token = await device_service.mint_access_token_strategy().write_token(user)
    return PairResponse(
        device_id=device.id,
        refresh_token=refresh_token,
        access_token=access_token,
    )


@router.post("/token", response_model=DeviceTokenResponse, dependencies=[Depends(login_rate_limit)])
async def refresh_device_token(
    body: DeviceTokenRequest,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
):
    try:
        device, user, refresh_token = await device_service.refresh_device_token(
            session, body.refresh_token, ip=_client_ip(request)
        )
    except device_service.DeviceTokenError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    access_token = await device_service.mint_access_token_strategy().write_token(user)
    return DeviceTokenResponse(
        device_id=device.id,
        refresh_token=refresh_token,
        access_token=access_token,
    )


@router.post("/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
async def heartbeat(
    body: HeartbeatRequest,
    request: Request,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    device = await device_service.get_device(session, body.device_id, user.id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    await device_service.touch_device(session, device, ip=_client_ip(request))


@router.get("", response_model=list[DeviceRead])
async def list_devices(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    devices = await device_service.list_devices(session, user.id)
    return [_device_read(d) for d in devices]


@router.patch("/{device_id}", response_model=DeviceRead)
async def update_device(
    device_id: uuid.UUID,
    body: DeviceUpdate,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    device = await device_service.get_device(session, device_id, user.id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    device.name = body.name
    await session.commit()
    await session.refresh(device)
    return _device_read(device)


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_device(
    device_id: uuid.UUID,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    device = await device_service.get_device(session, device_id, user.id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    await device_service.revoke_device(session, device)
