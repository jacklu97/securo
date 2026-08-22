import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class DeviceRead(BaseModel):
    id: uuid.UUID
    name: str
    platform: str
    app_version: Optional[str] = None
    created_at: datetime
    last_seen_at: Optional[datetime] = None
    connected: bool = False

    model_config = {"from_attributes": True}


class DeviceUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class PairingCreateResponse(BaseModel):
    pairing_id: str
    code: str
    expires_in: int


class PairingStatusResponse(BaseModel):
    status: Literal["pending", "claimed"]
    device_name: Optional[str] = None


class PairRequest(BaseModel):
    code: str = Field(min_length=16, max_length=128)
    name: str = Field(min_length=1, max_length=100)
    platform: Literal["ios", "android", "other"] = "other"
    app_version: Optional[str] = Field(default=None, max_length=50)


class PairResponse(BaseModel):
    device_id: uuid.UUID
    refresh_token: str
    access_token: str
    token_type: str = "bearer"


class DeviceTokenRequest(BaseModel):
    refresh_token: str = Field(min_length=16, max_length=256)


class DeviceTokenResponse(BaseModel):
    device_id: uuid.UUID
    refresh_token: str
    access_token: str
    token_type: str = "bearer"


class HeartbeatRequest(BaseModel):
    device_id: uuid.UUID
