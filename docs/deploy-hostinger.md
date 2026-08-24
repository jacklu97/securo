# Deploying the alpha fork on a Hostinger VPS

The stock `docker-compose.prod.yml` pulls upstream's images, which do NOT
contain the mobile pairing feature. This deployment uses the fork's own
images, built by `.github/workflows/alpha-images.yml` from `alpha-testing`
and published to `ghcr.io/jacklu97/securo-{backend,frontend}:alpha`.

## Requirements

- Hostinger **VPS** plan (KVM 1 / 4 GB is plenty; shared or website hosting
  cannot run Docker). Pick the Ubuntu 24.04 + Docker template, or install
  docker + the compose plugin on plain Ubuntu.
- A domain (or subdomain) with an **A record** pointing at the VPS IP.
  Hostinger's DNS panel works; TTL low until things stabilize.
- Ports **80 and 443 open** (Hostinger firewall panel). Nothing else needs
  exposing — backend/db/redis stay on the compose network.
- The GHCR packages must be pullable from the VPS: either make
  `securo-backend`/`securo-frontend` packages **public** on GitHub
  (Package settings → Change visibility), or `docker login ghcr.io` on the
  VPS with a classic PAT that has `read:packages`.

## Files on the VPS

Clone the fork (alpha branch) or copy just the three files this needs:

    git clone -b alpha-testing https://github.com/jacklu97/securo.git
    cd securo

Create `.env` next to the compose files:

    SECRET_KEY=<openssl rand -hex 32>
    DOMAIN=securo.example.com
    FRONTEND_URL=https://securo.example.com

`FRONTEND_URL` is the backend's CORS allow-origin; it must match the public
URL exactly. Everything else in the prod compose has workable defaults.

## Start

    docker compose -f docker-compose.prod.yml -f docker-compose.alpha.yml up -d

First boot runs `alembic upgrade head` automatically. Caddy obtains the
Let's Encrypt certificate on first request — DNS must already resolve.

## First-run checklist

1. Visit `https://$DOMAIN` → setup page → create the admin user (first user
   becomes superuser; the endpoint refuses once any user exists).
2. Pair your phone: Settings → Devices → the QR now carries the public HTTPS
   URL, so the mobile app works from anywhere with no tunnel.
3. After friends have registered: Admin → Settings → set
   `registration_enabled` to `false`.

## Mobile PWA (same origin)

The securo-mobile web build is served at `https://$DOMAIN/app/` (iOS users:
Safari → Share → Add to Home Screen — no App Store build needed). Caddy
serves it from `./pwa`, which the securo-mobile repo's `deploy-web`
workflow rsyncs into on every push to its main. One-time setup:

    mkdir -p pwa
    # on GitHub (securo-mobile repo): add VPS_HOST, VPS_USER, VPS_SSH_KEY
    # secrets; VPS_PWA_PATH must be <securo checkout>/pwa if the checkout
    # is not at /opt/securo

Pairing from the PWA uses the same public URL, and being same-origin it
needs no CORS changes.

## Updating

Merging to `alpha-testing` rebuilds the `:alpha` images. On the VPS:

    docker compose -f docker-compose.prod.yml -f docker-compose.alpha.yml pull
    docker compose -f docker-compose.prod.yml -f docker-compose.alpha.yml up -d

## Backups

Postgres lives in the `pgdata` volume. Minimum viable backup:

    docker compose -f docker-compose.prod.yml exec db \
      pg_dump -U postgres securo | gzip > securo-$(date +%F).sql.gz

Cron it and copy off the VPS. Attachments live in the `attachments` volume.
