#!/bin/bash
# Dotmac ERP Deployment Script
# Usage: ./scripts/deploy.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
STATIC_SRC="$PROJECT_DIR/static"
STATIC_DEST="/var/www/dotmac/static"

echo "==> Deploying Dotmac ERP..."

# Pull latest changes (if in git repo)
if [ -d "$PROJECT_DIR/.git" ]; then
    echo "==> Pulling latest changes..."
    cd "$PROJECT_DIR"
    git pull --ff-only || echo "Warning: git pull failed, continuing with local files"
fi

# Build CSS if npm is available
if command -v npm &> /dev/null && [ -f "$PROJECT_DIR/package.json" ]; then
    echo "==> Building CSS..."
    cd "$PROJECT_DIR"
    npm run build:css
fi

# Sync static files to nginx serving directory
echo "==> Syncing static files to $STATIC_DEST..."
mkdir -p "$STATIC_DEST"
rsync -av --delete "$STATIC_SRC/" "$STATIC_DEST/"
chown -R www-data:www-data "$STATIC_DEST"

# Rebuild and restart containers
echo "==> Rebuilding containers..."
cd "$PROJECT_DIR"
docker compose build app worker beat

echo "==> Restarting services..."
docker compose up -d app worker beat

# Wait for app to be healthy
echo "==> Waiting for app to be healthy..."
timeout 60 bash -c 'until curl -sf http://127.0.0.1:8002/health > /dev/null; do sleep 2; done' || {
    echo "Warning: Health check timed out"
}

# Run migrations
echo "==> Running database migrations..."
docker compose exec -T app alembic upgrade head

# Reload nginx (in case config changed)
echo "==> Reloading nginx..."
nginx -t && systemctl reload nginx

echo "==> Deployment complete!"
