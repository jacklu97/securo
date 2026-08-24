from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.paired_device import PairedDevice


class _RedisStore:
    def __init__(self):
        self.store = {}
        self.get = AsyncMock(side_effect=self._get)
        self.getdel = AsyncMock(side_effect=self._getdel)
        self.set = AsyncMock(side_effect=self._set)
        self.delete = AsyncMock(side_effect=self._delete)
        pipe = AsyncMock()
        pipe.zremrangebyscore = AsyncMock()
        pipe.zcard = AsyncMock()
        pipe.zadd = AsyncMock()
        pipe.expire = AsyncMock()
        pipe.execute = AsyncMock(return_value=[0, 0, True, True])
        self.pipeline = lambda: pipe

    async def _get(self, key):
        return self.store.get(key)

    async def _getdel(self, key):
        return self.store.pop(key, None)

    async def _set(self, key, value, ex=None):
        self.store[key] = value

    async def _delete(self, key):
        self.store.pop(key, None)


@pytest.fixture(autouse=True)
def _devices_redis_store(_mock_redis):
    redis = _RedisStore()

    async def _fake():
        return redis

    with patch("app.core.redis.get_redis", _fake), \
         patch("app.core.rate_limit.get_redis", _fake), \
         patch("app.api.devices.get_redis", _fake):
        yield redis


async def _pair(client: AsyncClient, auth_headers: dict, name: str = "Test Phone") -> dict:
    """Run the full pairing handshake, returning the /pair response body."""
    created = await client.post("/api/devices/pairings", headers=auth_headers)
    assert created.status_code == 201, created.text
    code = created.json()["code"]

    paired = await client.post(
        "/api/devices/pair",
        json={"code": code, "name": name, "platform": "android", "app_version": "0.1.0"},
    )
    assert paired.status_code == 200, paired.text
    return paired.json()


# ---------------------------------------------------------------------------
# pairing flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pairing_flow(client: AsyncClient, auth_headers):
    created = await client.post("/api/devices/pairings", headers=auth_headers)
    assert created.status_code == 201
    body = created.json()
    assert body["expires_in"] == 300
    pairing_id, code = body["pairing_id"], body["code"]

    pending = await client.get(f"/api/devices/pairings/{pairing_id}", headers=auth_headers)
    assert pending.status_code == 200
    assert pending.json() == {"status": "pending", "device_name": None}

    paired = await client.post(
        "/api/devices/pair",
        json={"code": code, "name": "Pixel 8", "platform": "android", "app_version": "0.1.0"},
    )
    assert paired.status_code == 200
    tokens = paired.json()
    assert tokens["token_type"] == "bearer"
    assert tokens["refresh_token"]

    claimed = await client.get(f"/api/devices/pairings/{pairing_id}", headers=auth_headers)
    assert claimed.json() == {"status": "claimed", "device_name": "Pixel 8"}

    # The device access token is a regular user JWT — existing endpoints accept it.
    device_headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    listed = await client.get("/api/devices", headers=device_headers)
    assert listed.status_code == 200
    devices = listed.json()
    assert len(devices) == 1
    assert devices[0]["name"] == "Pixel 8"
    assert devices[0]["platform"] == "android"
    assert devices[0]["connected"] is True


@pytest.mark.asyncio
async def test_pair_with_invalid_code(client: AsyncClient, test_user):
    response = await client.post(
        "/api/devices/pair", json={"code": "x" * 32, "name": "Phone"}
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_pairing_code_is_single_use(client: AsyncClient, auth_headers):
    created = await client.post("/api/devices/pairings", headers=auth_headers)
    code = created.json()["code"]

    first = await client.post("/api/devices/pair", json={"code": code, "name": "Phone"})
    assert first.status_code == 200
    second = await client.post("/api/devices/pair", json={"code": code, "name": "Phone"})
    assert second.status_code == 400


@pytest.mark.asyncio
async def test_pairing_status_hidden_from_other_users(
    client: AsyncClient, auth_headers, admin_auth_headers
):
    created = await client.post("/api/devices/pairings", headers=auth_headers)
    pairing_id = created.json()["pairing_id"]

    other = await client.get(f"/api/devices/pairings/{pairing_id}", headers=admin_auth_headers)
    assert other.status_code == 404


# ---------------------------------------------------------------------------
# refresh token rotation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_refresh_rotates(client: AsyncClient, auth_headers):
    tokens = await _pair(client, auth_headers)

    refreshed = await client.post(
        "/api/devices/token", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200
    rotated = refreshed.json()
    assert rotated["device_id"] == tokens["device_id"]
    assert rotated["refresh_token"] != tokens["refresh_token"]

    # Rotated token works again.
    again = await client.post(
        "/api/devices/token", json={"refresh_token": rotated["refresh_token"]}
    )
    assert again.status_code == 200


@pytest.mark.asyncio
async def test_refresh_token_reuse_revokes_device(client: AsyncClient, auth_headers):
    tokens = await _pair(client, auth_headers)

    refreshed = await client.post(
        "/api/devices/token", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200
    rotated = refreshed.json()

    # Presenting the pre-rotation token means it leaked — device is revoked.
    reused = await client.post(
        "/api/devices/token", json={"refresh_token": tokens["refresh_token"]}
    )
    assert reused.status_code == 401

    # The rotated token is dead too, and the device disappears from the list.
    after = await client.post(
        "/api/devices/token", json={"refresh_token": rotated["refresh_token"]}
    )
    assert after.status_code == 401
    listed = await client.get("/api/devices", headers=auth_headers)
    assert listed.json() == []


@pytest.mark.asyncio
async def test_refresh_with_unknown_token(client: AsyncClient, test_user):
    response = await client.post("/api/devices/token", json={"refresh_token": "y" * 64})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# device management
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rename_device(client: AsyncClient, auth_headers):
    tokens = await _pair(client, auth_headers)

    renamed = await client.patch(
        f"/api/devices/{tokens['device_id']}",
        json={"name": "My Phone"},
        headers=auth_headers,
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "My Phone"


@pytest.mark.asyncio
async def test_revoke_device(client: AsyncClient, auth_headers):
    tokens = await _pair(client, auth_headers)

    revoked = await client.delete(f"/api/devices/{tokens['device_id']}", headers=auth_headers)
    assert revoked.status_code == 204

    listed = await client.get("/api/devices", headers=auth_headers)
    assert listed.json() == []

    refreshed = await client.post(
        "/api/devices/token", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 401


@pytest.mark.asyncio
async def test_devices_scoped_to_user(client: AsyncClient, auth_headers, admin_auth_headers):
    tokens = await _pair(client, auth_headers)

    listed = await client.get("/api/devices", headers=admin_auth_headers)
    assert listed.json() == []

    renamed = await client.patch(
        f"/api/devices/{tokens['device_id']}",
        json={"name": "Hijack"},
        headers=admin_auth_headers,
    )
    assert renamed.status_code == 404

    revoked = await client.delete(
        f"/api/devices/{tokens['device_id']}", headers=admin_auth_headers
    )
    assert revoked.status_code == 404


# ---------------------------------------------------------------------------
# heartbeat / connection status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_updates_last_seen(
    client: AsyncClient, auth_headers, session: AsyncSession
):
    tokens = await _pair(client, auth_headers)

    result = await session.execute(select(PairedDevice))
    device = result.scalar_one()
    device.last_seen_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await session.commit()

    listed = await client.get("/api/devices", headers=auth_headers)
    assert listed.json()[0]["connected"] is False

    beat = await client.post(
        "/api/devices/heartbeat", json={"device_id": tokens["device_id"]}, headers=auth_headers
    )
    assert beat.status_code == 204

    listed = await client.get("/api/devices", headers=auth_headers)
    assert listed.json()[0]["connected"] is True


@pytest.mark.asyncio
async def test_heartbeat_for_foreign_device(
    client: AsyncClient, auth_headers, admin_auth_headers
):
    tokens = await _pair(client, auth_headers)

    beat = await client.post(
        "/api/devices/heartbeat",
        json={"device_id": tokens["device_id"]},
        headers=admin_auth_headers,
    )
    assert beat.status_code == 404
