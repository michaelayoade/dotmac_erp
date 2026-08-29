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
# Notes on erp's deploy model: application code, migrations and dependencies all
# ship inside the tested image. The checkout supplies Compose configuration,
# operator scripts and the existing static/template/Gunicorn read-only overlays;
# it does not replace application Python or migrations. A real deploy therefore
# pulls the immutable image before running its migrations.
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
HEALTH_URL="${HEALTH_URL:-http://localhost:8003/health/ready}"
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

# Step 2: pull the deployment checkout + the image carrying app, migrations and
# dependencies, then pin it to the new commit's immutable sha-<short> tag.
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
if ! docker compose run --rm \
    -e MIGRATION_DATABASE_URL app \
    python scripts/bootstrap_database_roles.py --verify-only
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
docker compose run --rm -e MIGRATION_DATABASE_URL app \
    alembic upgrade heads
echo ""

# Step 3c: ADMIT the RUNTIME connection, after the DDL and before the app runs.
#
# This is the one one-off in this script that is deliberately NOT given
# `-e MIGRATION_DATABASE_URL`, and the omission is the entire point. Every
# other step here verifies the migration executor; this one verifies the
# credential the APPLICATION will serve requests on, which the one-off inherits
# from the `app` service's own `environment:` block (`DATABASE_URL:
# ${DATABASE_URL}`, itself read from `env_file: - .env`). Passing the migration
# URL would re-verify `app_admin` — a role that is BYPASSRLS by contract — and
# prove nothing about the connection that reads tenant rows.
#
# Invoked in exactly the shape steps 3a and 3b use: the runtime image carries
# its own virtualenv on PATH and its own default command, so no builder tool
# and no command override belongs in this script.
# `scripts/verify_runtime_admission.py` is a NAMED runtime surface of that
# image (see the Dockerfile's explicit COPY) — the checkout is not mounted over
# /app, so a script absent from the image is not reachable from this step.
#
# It runs AFTER migrations because it inspects grants and row-level security
# those migrations have just (re)created, and BEFORE `up -d app` because
# refusing a runtime identity is only useful while the old container still
# serves.
#
# Read-only, and scoped to the modules a deployment has DECLARED active. With
# no module active it asserts nothing and says so loudly rather than passing in
# silence — see app/runtime_admission.py.
#
# NOTE FOR THE OPERATOR — what a failure here does and does not undo. The `if
# !` guard is the same shape step 3a uses, and a command in an `if` condition
# does NOT fire the ERR trap set above, so this step exits WITHOUT the
# automatic rollback. Nothing is reverted: the migrations applied by step 3b
# stay applied (that trap's rollback would not have reverted them either — see
# the rollback function's closing message), the working tree stays at the
# freshly pulled commit with the new .env pins, and the PREVIOUS app container
# keeps serving on the previous image because step 4 never ran. Repair the
# runtime credential or unset the module flag and re-run deploy; if the new
# revisions are not backward-compatible with the running release, restore the
# step-1 backup.
echo "→ Admission: runtime database identity (runtime credential, read-only)..."
if ! docker compose run --rm app \
    python scripts/verify_runtime_admission.py
then
    echo ""
    echo "DEPLOY STOPPED: the runtime database connection is not admissible" >&2
    echo "for at least one ACTIVE module. The app container was NOT recreated." >&2
    echo "" >&2
    echo "The refusal lines above name each failure. The usual causes are:" >&2
    echo "  - DATABASE_URL still connects as a legacy login rather than" >&2
    echo "    app_user, whose name the module GRANTs and RLS policies carry;" >&2
    echo "  - a module was activated before its app_user grants were applied;" >&2
    echo "  - RUNTIME_ADMISSION_TENANT_ID / RUNTIME_ADMISSION_OTHER_TENANT_ID" >&2
    echo "    are unset, so tenant isolation could not be proved." >&2
    echo "" >&2
    echo "Migrations from step 3b have ALREADY been applied and are NOT rolled" >&2
    echo "back. Repair the runtime credential, or unset the module's" >&2
    echo "activation flag, then re-run deploy." >&2
    exit 1
fi
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
if [[ "${SKIP_STATIC_SYNC:-0}" == "1" ]]; then
    echo "-> Skipping static file sync (SKIP_STATIC_SYNC=1)."
else
    echo "→ Syncing static files to Nginx..."
    "$SCRIPT_DIR/sync-static.sh"
fi
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
echo "Verify: https://erp.dotmac.io/health/ready"
