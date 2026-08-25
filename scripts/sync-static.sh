#!/bin/bash
# Sync product and packaged UI static files to the Nginx serving directory.
# Run this after every deploy: ./scripts/sync-static.sh
#
# Why: Nginx serves /static/ from /var/www/dotmac/static/ (as www-data).
# The project source is in /root/dotmac/static/ (owned by root), while the
# released dotmac-ui assets live inside the application image. Nginx cannot
# read either location directly, so both are staged before the final rsync.

set -euo pipefail

SRC="/root/dotmac/static/"
DEST="/var/www/dotmac/static/"
APP_CONTAINER="dotmac_erp_app"

# Serialize concurrent runs (e.g. the periodic timer racing a manual deploy).
# Wait up to 60s for an in-flight sync rather than skipping — rsync is sub-second.
exec 9>"/var/lock/dotmac-static-sync.lock"
flock -w 60 9

echo "Syncing static files: $SRC → $DEST"
STAGING_DIR="$(mktemp -d)"
cleanup() {
    rm -rf -- "$STAGING_DIR"
}
trap cleanup EXIT

rsync -a "$SRC" "$STAGING_DIR/"

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
