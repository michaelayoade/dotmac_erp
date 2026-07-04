#!/bin/bash
# Deploy DotMac ERP — hardened: backup -> pull -> migrate -> recreate ->
# health gate -> auto-rollback on failure.
#
# Usage:
#   ./scripts/deploy.sh              # full deploy (backup, pull, migrate, restart)
#   ./scripts/deploy.sh --quick      # skip git pull + image pull (restart + migrate + sync)
#   SKIP_BACKUP=1 ./scripts/deploy.sh   # skip the pre-migration DB backup (NOT recommended)
#
# Notes on erp's deploy model: the container runs app code from the mounted
# ./app volume, but alembic/ is NOT mounted — migrations ship inside the image.
# So a real deploy must `docker compose pull app` (refresh the baked alembic +
# deps) before running migrations, not just `git pull`.
#
# On a failed health gate the code is reset to the previous commit and the app
# is recreated. Migrations are NOT auto-reverted — new revisions must be
# backward-compatible with the previous release, and the pre-migration backup is
# the recovery path if they are not.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-150}"
HEALTH_URL="${HEALTH_URL:-http://localhost:8003/health}"

cd "$PROJECT_DIR"
PREV_SHA="$(git rev-parse HEAD)"

echo "=== DotMac ERP Deploy ==="
echo "Project: $PROJECT_DIR   (current: ${PREV_SHA:0:12})"
echo ""

rollback() {
    echo "!! Rolling back code to ${PREV_SHA:0:12} and recreating app..."
    git reset --hard "$PREV_SHA" || true
    docker compose up -d app || { docker stop dotmac_erp_app || true; docker start dotmac_erp_app || true; }
    echo "!! Rolled back. NOTE: DB migrations were NOT reverted — restore from the"
    echo "!! pre-migration backup if the new revisions are not backward-compatible."
}

# Step 1: pre-migration DB backup (SKIP_BACKUP=1 to skip)
if [[ "${SKIP_BACKUP:-0}" != "1" ]]; then
    echo "→ Backing up database (SKIP_BACKUP=1 to skip)..."
    bash "$SCRIPT_DIR/backup_erp_db.sh"
    echo ""
fi

# Step 2: pull latest code (mounted ./app) + image (baked alembic + deps)
if [[ "${1:-}" != "--quick" ]]; then
    echo "→ Pulling latest code + image..."
    git pull --rebase
    docker compose pull app
    echo ""
fi

# From here a failure triggers an automatic rollback.
trap 'echo "Deploy FAILED"; rollback; exit 1' ERR

# Step 3: apply migrations on the freshly-pulled image (multi-head safe — erp has
# hit multi-head states, so `heads` (plural), never `head`).
echo "→ Applying migrations (alembic upgrade heads)..."
docker compose run --rm --entrypoint "" app poetry run alembic upgrade heads
echo ""

# Step 4: recreate the app container on the new image + code
echo "→ Recreating app container..."
docker compose up -d app

# Step 5: health gate
echo "→ Waiting for health check (up to ${HEALTH_TIMEOUT}s)..."
healthy=0
for i in $(seq 1 "$HEALTH_TIMEOUT"); do
    if curl -sf "$HEALTH_URL" > /dev/null 2>&1; then
        echo "  App healthy after ${i}s"
        healthy=1
        break
    fi
    sleep 1
done

if [[ "$healthy" != "1" ]]; then
    trap - ERR
    echo "  ERROR: App not healthy after ${HEALTH_TIMEOUT}s!"
    docker logs dotmac_erp_app --tail 20 || true
    rollback
    exit 1
fi
trap - ERR

# Step 6: sync static files + restart worker/beat (only on a healthy deploy)
echo "→ Syncing static files to Nginx..."
"$SCRIPT_DIR/sync-static.sh"
echo "→ Restarting worker and beat..."
docker restart dotmac_erp_worker dotmac_erp_beat

echo ""
echo "=== Deploy complete ==="
echo "Verify: https://erp.dotmac.io/health"
