# RFC — Mobile app pairing: QR connect, paired devices, connection status

Status: draft

## Problem

Securo is self-hosted; a companion mobile app (Tauri 2) needs a way to connect
to a **specific running instance** without the user typing a URL + email +
password + TOTP on a phone. We need:

1. A config section in the web UI that shows a **QR code** the mobile app scans
   to pair itself with this instance.
2. A **paired devices** list: what devices are linked, and whether each is
   currently connected.
3. Safe credentials for the phone: revocable per-device, long-lived (no
   re-login every 24h like the web JWT), never a copy of the user's password.

## Approach

Pairing = a short-lived, single-use **pairing code** displayed as a QR by the
web UI, redeemed by the phone for a per-device **refresh token**. The phone
then exchanges that refresh token for the same JWT access tokens the web app
already uses — so **every existing `/api/*` endpoint works unchanged** on
mobile. Connection status is derived from a heartbeat (`last_seen_at`), no
websockets.

This mirrors two patterns already in the codebase:

- **Passkeys** (`backend/app/api/passkeys.py`): server-side one-time challenge
  stored in Redis with TTL, user-owned credential rows with a management
  list/delete UI (`passkey-management-dialog.tsx`).
- **TOTP setup** (`frontend/src/components/two-factor-setup.tsx`): QR rendering
  with `qrcode.react` (already a dependency).

### Pairing flow

```
Web UI (logged in)                Backend                       Mobile app
──────────────────                ───────                       ──────────
POST /api/devices/pairings ────▶  code = 43-char urlsafe secret
                                  Redis: pairing:{code} →
                                    {user_id, pairing_id} TTL 5min
◀─ {pairing_id, code, expires_at}
render QR:
{v:1, url:<instance URL>, code}
                                                            scan QR
                                  ◀──────────  POST /api/devices/pair
                                               {code, name, platform, app_version}
                                  consume code (single use, rate-limited)
                                  create PairedDevice row
                                  ─────▶ {device_id, refresh_token, access_token}
poll GET /api/devices/pairings/{pairing_id}
→ status flips to "claimed" → show "✓ <device name> paired"
```

- The QR payload contains **no durable credential** — only the instance URL and
  a 5-minute single-use code bound to the generating user. Shoulder-surfing the
  QR is only exploitable within the TTL, once, and shows up immediately as a
  new device in the list (with revoke one tap away).
- The **instance URL** in the QR defaults to the browser's `window.location.origin`
  but is shown as an editable field above the QR: a user browsing
  `http://localhost:3000` must correct it to the LAN IP / domain the phone can
  actually reach. Persist the last-used value in `localStorage`. This is the
  same problem the external MCP panel already solves
  (`mcp-external-panel.tsx` uses `info.external_mcp_url || derived-from-hostname`);
  follow that precedent with an optional `EXTERNAL_APP_URL` setting surfaced
  through `GET /api/info` as the default when set.
- Redeeming is unauthenticated (the phone has no credentials yet) → reuse the
  existing Redis rate-limit dependency (`app/core/rate_limit.py`) like
  login/passkey endpoints.

### Token model

- **Device refresh token**: 256-bit opaque secret, returned once at pairing,
  stored **hashed (SHA-256)** on the `paired_devices` row. The phone keeps it
  in platform secure storage (iOS Keychain / Android Keystore via Tauri plugin).
- **Access token**: the phone calls `POST /api/devices/token` with the refresh
  token and gets a standard fastapi-users JWT (same `JWTStrategy`,
  same claims → all existing auth dependencies work untouched). On each
  exchange the refresh token is **rotated** and the old hash replaced; a
  presented-but-already-rotated token revokes the device (reuse detection).
- **Revocation**: deleting/revoking the device row makes the next refresh fail.
  Access JWTs stay stateless — revocation latency is bounded by the access
  token lifetime, so device-issued JWTs use a shorter lifetime (15 min) than
  the web's 24h.

### Connection status

- `POST /api/devices/heartbeat` (device JWT) updates `last_seen_at` + `last_ip`;
  the app sends it on foreground/resume and every ~60s while active. The token
  refresh endpoint also bumps `last_seen_at` for free.
- `GET /api/devices` computes `connected = last_seen_at > now() - 2min`.
- The web devices list polls with react-query `refetchInterval: 15s` while the
  dialog is open. No websocket/SSE infrastructure needed; if live push is ever
  wanted, the agents-chat SSE pattern (`app/agents/api/chat.py`) is the
  precedent.

### User-scoped, not workspace-scoped

A paired device belongs to a **user** (like `user_passkeys`), not a
`(user, workspace)` pair (like MCP tokens, which bind `ws_id` at mint time).
Rationale: the device JWT is the standard user JWT, so the mobile app selects
its workspace per request via the same `X-Workspace-Id` header the web
frontend sends — the phone gets the full workspace-switching UX instead of
being pinned to one tenant, and `WorkspaceContext` enforcement applies
unchanged. A read-only or single-workspace pairing is a listed follow-up.

## Data model

New table `paired_devices` (migration `074`, chained from `073`; model on
`062_passkeys.py`):

```
id UUID PK · user_id FK users (cascade) · name · platform (ios|android|other)
app_version · refresh_token_hash (unique) · created_at · last_seen_at (nullable)
last_ip (nullable) · revoked_at (nullable)
```

Pairing codes live only in Redis (`pairing:{code}`, TTL 300s) — nothing to
migrate, restart-safe, single-use via `GETDEL`.

## Surface

**Backend** (follows the per-resource module convention):

- `app/models/paired_device.py` (register in `app/models/__init__.py` or
  alembic won't see it), `app/schemas/device.py`,
  `app/services/device_service.py`, `app/api/devices.py` with
  `APIRouter(prefix="/api/devices", tags=["devices"])`, included in `main.py`;
  tests in `tests/test_devices_api.py`.
- Endpoints:
  - `POST /devices/pairings` (auth) — create pairing code
  - `GET  /devices/pairings/{id}` (auth) — pending | claimed, for the QR screen
  - `POST /devices/pair` (unauth, rate-limited) — redeem code → tokens
  - `POST /devices/token` (unauth, rate-limited) — refresh → rotated tokens
  - `GET/PATCH/DELETE /devices[/{id}]` (auth) — list w/ `connected`, rename, revoke
  - `POST /devices/heartbeat` (device JWT) — bump `last_seen_at`

**Frontend**:

- `components/device-management-dialog.tsx`, launched from the user menu in
  `app-layout.tsx` next to `PasskeyManagementDialog` — devices list
  (name, platform icon, "connected" badge / "last seen X ago", revoke) and a
  "Pair new device" view: editable instance URL, QR (`qrcode.react`), manual
  fallback code, pairing-status poll, success toast.
- API wrappers in `lib/api.ts`; i18n keys for en/pt-BR/es/it/pl.

**Mobile app** (separate repo, out of scope here, contract only):

- Tauri 2 (iOS/Android) using `barcode-scanner` plugin to read the QR and the
  **native HTTP plugin** for API calls — native requests bypass CORS, so the
  backend's `allow_origins=[frontend_url]` needs no change (a Tauri webview
  origin like `tauri://localhost` would never match it). Store refresh token
  via secure-storage plugin (Keychain/Keystore); refresh JWT on 401/expiry;
  send `X-Workspace-Id` per request exactly like the web frontend to select
  the active workspace; heartbeat on resume + 60s timer.

## Security notes

- Refresh tokens hashed at rest; raw value shown/transmitted exactly once.
- Pairing redeem + token refresh behind the existing login rate limiter.
- Pairing code is bound to the generating user; the device inherits exactly
  that user's permissions (workspace roles apply as usual).
- QR pairing implicitly satisfies 2FA: only an already-authenticated (and
  2FA-passed) web session can mint a code.
- Self-hosted instances often run plain HTTP on LAN; the pairing screen shows
  a warning when the instance URL is `http://` (tokens travel unencrypted).

## Out of scope (follow-ups)

- Push notifications to devices; per-device scopes (read-only or
  single-workspace pairing); mDNS/auto-discovery of the instance on LAN;
  SSE live presence (would reuse the `agents-stream.ts` fetch-based parser —
  native `EventSource` can't send the `Authorization` header).
