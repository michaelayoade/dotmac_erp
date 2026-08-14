#!/bin/bash
# Deploy DotMac ERP — hardened: backup -> pull -> migrate -> recreate ->
# health gate -> auto-rollback on failure.
#
# Usage:
#   MIGRATION_DATABASE_URL=<app_admin DSN> ./scripts/deploy.sh
#   MIGRATION_DATABASE_URL=<app_admin DSN> ./scripts/deploy.sh --quick
#   SKIP_BACKUP=1 ./scripts/deploy.sh   # skip the pre-migration DB backup (NOT recommended)
#
# `MIGRATION_DATABASE_URL` comes from the approved secret source and is passed
# only to one-off preflight/migration containers. Runtime services keep only
# `DATABASE_URL`; Alembic never falls back to it.
#
# Notes on erp's deploy model: the container runs app code from the mounted
# ./app volume, but alembic/ is NOT mounted — migrations ship inside the image.
# So a real deploy must pull the image (refresh the baked alembic + deps) before
# running migrations, not just `git pull`.
#
# Image pinning: app/worker/beat run ghcr.io/.../dotmac_erp:${ERP_IMAGE_TAG}.
# This deploy pins ERP_IMAGE_TAG to the deployed commit's immutable `sha-<short>`
# tag (published by CI) so the running artifact is reproducible and rollback is
# exact — instead of the mutable `:latest`. --quick keeps the current tag.
#
# On a failed health gate the code is reset to the previous commit AND the image
# tag is restored to the previously-running one, then the containers are
# recreated. Migrations are NOT auto-reverted — new revisions must be
# backward-compatible with the previous release, and the pre-migration backup is
# the recovery path if they are not.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-150}"
HEALTH_URL="${HEALTH_URL:-http://localhost:8003/health}"
DEPLOY_COMPOSE_PROJECT_NAME="dotmac"

# Production uses fixed container names (dotmac_erp_app, dotmac_erp_redis, etc.).
# Compose otherwise derives its project name from the current directory, which
# changes for revision-named release worktrees and causes container-name
# conflicts before migrations can run. Fail closed on a conflicting caller
# value, then export the stable name for every compose command and rollback.
if [[ -n "${COMPOSE_PROJECT_NAME:-}" && \
      "$COMPOSE_PROJECT_NAME" != "$DEPLOY_COMPOSE_PROJECT_NAME" ]]; then
    echo "ERROR: COMPOSE_PROJECT_NAME must be '$DEPLOY_COMPOSE_PROJECT_NAME' for production deploys (got '$COMPOSE_PROJECT_NAME')." >&2
    exit 2
fi
export COMPOSE_PROJECT_NAME="$DEPLOY_COMPOSE_PROJECT_NAME"

cd "$PROJECT_DIR"
PREV_SHA="$(git rev-parse HEAD)"

if [[ -z "${MIGRATION_DATABASE_URL:-}" ]]; then
    echo "ERROR: MIGRATION_DATABASE_URL is required and must connect as the" >&2
    echo "non-superuser app_admin role. Alembic never uses DATABASE_URL." >&2
    exit 2
fi

# .env carries two values that merely RESTATE facts owned by the deployed
# commit: ERP_IMAGE_TAG (the immutable image) and APP_VERSION (pyproject's
# version, which docker-compose.yml already defaults to). Nothing kept them in
# step, so both drifted — and .env wins over the compose default, so the drift
# is what actually runs:
#
#   - ERP_IMAGE_TAG sat 5 weeks behind the running image, so a bare
#     `docker compose up -d` by anyone not using this script would have
#     silently DOWNGRADED production.
#   - APP_VERSION sat two releases behind, so the app misreported its own
#     version — which is how a deploy gap got mis-sized from a stale note.
#
# This script pins ERP_IMAGE_TAG for its own compose calls via `export`, which
# is why the drift stayed invisible to the deploy path. Making the deploy the
# single writer of both keys is what stops it recurring.
ENV_FILE="$PROJECT_DIR/.env"
ENV_BACKUP="${ENV_FILE}.deploy-bak"
env_synced=0

# Set key=value in .env, replacing the first existing occurrence or appending.
# Writes via a temp file so an interrupted deploy cannot leave a half-written
# .env, and preserves the original mode (it is 0600 and holds secrets).
set_env_var() {
    local key="$1" value="$2" tmp
    [[ -f "$ENV_FILE" ]] || return 0
    tmp="$(mktemp "${ENV_FILE}.XXXXXX")"
    chmod --reference="$ENV_FILE" "$tmp" 2>/dev/null || chmod 600 "$tmp"
    if grep -qE "^${key}=" "$ENV_FILE"; then
        awk -v k="$key" -v v="$value" '
            $0 ~ "^" k "=" && !seen { print k "=" v; seen = 1; next }
            { print }
        ' "$ENV_FILE" > "$tmp"
    else
        cat "$ENV_FILE" > "$tmp"
        printf '%s=%s\n' "$key" "$value" >> "$tmp"
    fi
    mv "$tmp" "$ENV_FILE"
}

# Image tag the app container is currently running — restored on rollback so a
# failed deploy reverts to the exact previously-running image, not just :latest.
PREV_IMAGE_TAG="$(docker inspect --format '{{.Config.Image}}' dotmac_erp_app 2>/dev/null | sed 's/.*://')"
PREV_IMAGE_TAG="${PREV_IMAGE_TAG:-latest}"
export ERP_IMAGE_TAG="$PREV_IMAGE_TAG"

echo "=== DotMac ERP Deploy ==="
echo "Project: $PROJECT_DIR   (compose: ${COMPOSE_PROJECT_NAME}, current: ${PREV_SHA:0:12}, image: ${PREV_IMAGE_TAG})"
echo ""

rollback() {
    echo "!! Rolling back code to ${PREV_SHA:0:12} and image to ${PREV_IMAGE_TAG}..."
    git reset --hard "$PREV_SHA" || true
    export ERP_IMAGE_TAG="$PREV_IMAGE_TAG"
    # Undo the .env pins written for the failed deploy, then point
    # ERP_IMAGE_TAG at the image actually being restored. Restoring the backup
    # alone would reinstate whatever drift was there before, which is the very
    # landmine this change exists to remove.
    if [[ "$env_synced" == "1" && -f "$ENV_BACKUP" ]]; then
        cp -a "$ENV_BACKUP" "$ENV_FILE"
        set_env_var ERP_IMAGE_TAG "$PREV_IMAGE_TAG"
        echo "!! Reverted .env pins to the restored image (${PREV_IMAGE_TAG})."
    fi
    docker compose up -d app worker beat || { docker stop dotmac_erp_app || true; docker start dotmac_erp_app || true; }
    echo "!! Rolled back. NOTE: DB migrations were NOT reverted — restore from the"
    echo "!! pre-migration backup if the new revisions are not backward-compatible."
}

# Step 1: pre-migration DB backup (SKIP_BACKUP=1 to skip)
if [[ "${SKIP_BACKUP:-0}" != "1" ]]; then
    echo "→ Backing up database (SKIP_BACKUP=1 to skip)..."
    bash "$SCRIPT_DIR/backup_erp_db.sh"
    echo ""
fi

# Step 2: pull latest code (mounted ./app) + image (baked alembic + deps), and
# pin the image tag to the newly-deployed commit's immutable sha-<short> tag.
if [[ "${1:-}" != "--quick" ]]; then
    echo "→ Pulling latest code + image..."
    git pull --rebase
    NEW_IMAGE_TAG="sha-$(git rev-parse --short=7 HEAD)"
    export ERP_IMAGE_TAG="$NEW_IMAGE_TAG"
    echo "  Pinning image tag: ${ERP_IMAGE_TAG} (rollback target: ${PREV_IMAGE_TAG})"

    # Persist both pins into .env from the freshly-pulled commit. This runs
    # BEFORE migrate/recreate deliberately: APP_VERSION only reaches the app
    # container if .env is correct at `docker compose up` time, so writing it
    # after the health gate would leave the running container reporting the
    # previous release until the *next* deploy.
    if [[ -f "$ENV_FILE" ]]; then
        cp -a "$ENV_FILE" "$ENV_BACKUP"
        env_synced=1
        NEW_APP_VERSION="$(awk -F'"' '/^version = "/ { print $2; exit }' pyproject.toml)"
        set_env_var ERP_IMAGE_TAG "$NEW_IMAGE_TAG"
        if [[ -n "$NEW_APP_VERSION" ]]; then
            set_env_var APP_VERSION "$NEW_APP_VERSION"
        fi
        echo "  .env synced: ERP_IMAGE_TAG=${NEW_IMAGE_TAG} APP_VERSION=${NEW_APP_VERSION:-<unchanged>}"
    fi

    docker compose pull app worker beat
    echo ""
fi

# From here a failure triggers an automatic rollback.
trap 'echo "Deploy FAILED"; rollback; exit 1' ERR

# Step 3a: PREFLIGHT migration identity, role posture and ownership before DDL.
#
# `20260814_database_roles` fails closed when `app_admin`, `app_user` or
# `platform_api` is missing or wrong-shaped. Discovering that mid-chain means a
# half-applied upgrade and an automatic rollback; discovering it here costs
# nothing. This deliberately does NOT create the roles: creation needs superuser
# or CREATEROLE, and the deploy path must never hold those. Run the explicitly
# privileged bootstrap once, as an operator, then re-run the deploy.
echo "→ Preflight: migration executor contract..."
if ! docker compose run --rm --entrypoint "" \
    -e MIGRATION_DATABASE_URL app \
    poetry run python scripts/bootstrap_database_roles.py --verify-only
then
    echo ""
    echo "DEPLOY STOPPED: the migration identity, role posture, or database" >&2
    echo "ownership contract is unsatisfied. No migration was attempted." >&2
    echo "" >&2
    echo "If roles are missing or wrong-shaped, run the explicitly privileged" >&2
    echo "bootstrap once with superuser or CREATEROLE credentials:" >&2
    echo "" >&2
    echo "  BOOTSTRAP_DATABASE_URL=postgresql://<superuser>@<host>/<db> \\" >&2
    echo "      python scripts/bootstrap_database_roles.py --dry-run" >&2
    echo "  # review, then drop --dry-run" >&2
    echo "" >&2
    echo "That script never sets passwords or transfers object ownership." >&2
    echo "For an existing database whose objects have another owner, complete" >&2
    echo "a separately reviewed ownership cutover before re-running deploy." >&2
    exit 1
fi
echo "  roles present and correctly shaped"
echo ""

# Step 3b: apply migrations on the freshly-pulled image (multi-head safe — erp has
# hit multi-head states, so `heads` (plural), never `head`).
echo "→ Applying migrations (alembic upgrade heads)..."
docker compose run --rm --entrypoint "" -e MIGRATION_DATABASE_URL app \
    poetry run alembic upgrade heads
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
# Recreate (not just restart) so worker/beat pick up the newly-pinned image.
echo "→ Recreating worker and beat on the pinned image..."
docker compose up -d worker beat
echo "→ Enforcing Docker image retention (keep last ${DOCKER_IMAGE_KEEP_LAST:-5})..."
if ! KEEP_LAST="${DOCKER_IMAGE_KEEP_LAST:-5}" \
  IMAGE_REPOSITORY="${DOCKER_IMAGE_REPOSITORY:-ghcr.io/michaelayoade/dotmac_erp}" \
  "$SCRIPT_DIR/prune_docker_images.sh" --execute; then
    echo "  WARNING: Docker image retention failed; deploy remains healthy."
fi

echo ""
echo "=== Deploy complete ==="
echo "Verify: https://erp.dotmac.io/health"
