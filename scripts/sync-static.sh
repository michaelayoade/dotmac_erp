#!/bin/bash
# Sync product and packaged UI static files to the Nginx serving directory.
# Run this after every deploy: ./scripts/sync-static.sh
#
# Why: Nginx serves /static/ from /var/www/dotmac/static/ (as www-data) and
# cannot read the application image directly, so assets are staged then synced.
#
# BOTH sources are now the running container, i.e. the IMAGE. This previously
# copied the product assets straight out of the /root/dotmac/static/ CHECKOUT,
# which meant the mutable git worktree -- not the deployed image -- decided what
# browsers actually received: nginx intercepts /static/ before FastAPI, so a
# `git checkout <ref>` changed the served stylesheet with no image change and no
# new digest. That is the same digest-identity defect as the compose bind mounts
# removed alongside this change, and removing only those mounts would have left
# it standing, because this path bypasses the container filesystem entirely.
# See docs/inventories/2026-08-30-erp-production-infrastructure-preflight.md D2.

set -euo pipefail

DEST="/var/www/dotmac/static/"
APP_CONTAINER="dotmac_erp_app"
# Product static as baked into the image, including the stylesheet compiled by
# the css-builder stage. The CI css-drift job proves the committed artifact
# equals a fresh build, so this and the checkout agree by construction.
APP_STATIC_SOURCE="/app/static"

# Serialize concurrent runs (e.g. the periodic timer racing a manual deploy).
# Wait up to 60s for an in-flight sync rather than skipping — rsync is sub-second.
exec 9>"/var/lock/dotmac-static-sync.lock"
flock -w 60 9

echo "Syncing static files: ${APP_CONTAINER}:${APP_STATIC_SOURCE} → $DEST"
STAGING_DIR="$(mktemp -d)"
cleanup() {
    rm -rf -- "$STAGING_DIR"
}
trap cleanup EXIT

docker cp "$APP_CONTAINER:$APP_STATIC_SOURCE/." "$STAGING_DIR/"

# The final sync uses --delete. An empty staging tree would therefore empty the
# live web root and take every stylesheet and image offline, so refuse instead.
# The packaged-UI copy below already guards itself the same way.
if [[ -z "$(find "$STAGING_DIR" -type f -print -quit)" ]]; then
    echo "ERROR: No product static assets were copied from $APP_CONTAINER." >&2
    exit 1
fi

# Nginx intercepts /static/ before FastAPI, so package-owned assets must be in
# the same web root. Resolve the source and namespace through ERP's public UI
# composition boundary instead of depending on a site-packages layout.
mapfile -t UI_ASSET_CONTRACT < <(
    docker exec "$APP_CONTAINER" python -c \
        'from app.ui import UI_ASSET_DIRECTORY, UI_ASSET_MOUNT; print(UI_ASSET_DIRECTORY); print(UI_ASSET_MOUNT.rsplit("/", 1)[-1])'
)

if [[ "${#UI_ASSET_CONTRACT[@]}" -ne 2 ]]; then
    echo "ERROR: Unable to resolve the packaged dotmac-ui asset contract." >&2
    exit 1
fi

UI_ASSET_SOURCE="${UI_ASSET_CONTRACT[0]}"
UI_ASSET_NAMESPACE="${UI_ASSET_CONTRACT[1]}"
if [[ "$UI_ASSET_SOURCE" != /* || ! "$UI_ASSET_NAMESPACE" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "ERROR: Invalid packaged dotmac-ui asset contract." >&2
    exit 1
fi

mkdir -p "$STAGING_DIR/$UI_ASSET_NAMESPACE"
docker cp \
    "$APP_CONTAINER:$UI_ASSET_SOURCE/." \
    "$STAGING_DIR/$UI_ASSET_NAMESPACE/"

if [[ -z "$(find "$STAGING_DIR/$UI_ASSET_NAMESPACE" -type f -print -quit)" ]]; then
    echo "ERROR: No packaged dotmac-ui assets were copied." >&2
    exit 1
fi

rsync -a --delete "$STAGING_DIR/" "$DEST"
echo "Done. $(find "$DEST" -type f | wc -l) files synced."
