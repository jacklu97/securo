#!/usr/bin/env bash
# The one true deploy command for the VPS. Always uses the alpha overlay —
# running the prod file alone pulls UPSTREAM images, which lack the fork's
# migrations (alembic then fails with "Can't locate revision '074'") and
# drops the overlay-only services (caddy/ollama/telegram-rag). Data is never
# at risk in that failure mode; the fix is simply redeploying with this.
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE="docker compose -f docker-compose.prod.yml -f docker-compose.alpha.yml --profile agents"

git pull origin alpha-testing
$COMPOSE pull
$COMPOSE up -d --remove-orphans
$COMPOSE ps --format 'table {{.Name}}\t{{.Image}}\t{{.Status}}'
