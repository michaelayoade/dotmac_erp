#!/bin/bash
# Sync static files from project to Nginx serving directory.
# Run this after every deploy: ./scripts/sync-static.sh
#
# Why: Nginx serves /static/ from /var/www/dotmac/static/ (as www-data).
# The project source is in /root/dotmac/static/ (owned by root).
# Nginx can't read /root/ directly, so we rsync to the serving directory.

set -euo pipefail

SRC="/root/dotmac/static/"
DEST="/var/www/dotmac/static/"

# Serialize concurrent runs (e.g. the periodic timer racing a manual deploy).
# Wait up to 60s for an in-flight sync rather than skipping — rsync is sub-second.
exec 9>"/var/lock/dotmac-static-sync.lock"
flock -w 60 9

echo "Syncing static files: $SRC → $DEST"
rsync -a --delete "$SRC" "$DEST"
echo "Done. $(find "$DEST" -type f | wc -l) files synced."
