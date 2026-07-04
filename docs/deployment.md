# Deployment

## Environment

Use `.env.example` as a starting point. Required values include:

- `DATABASE_URL`
- `JWT_SECRET`
- `TOTP_ENCRYPTION_KEY`

## Docker Compose

For local containerized setup:

```bash
docker compose up --build
```

Apply migrations after containers are up:

```bash
docker compose exec app poetry run alembic upgrade heads
```

## Workers

Start Celery workers and scheduler in separate processes/containers:

```bash
poetry run celery -A app.celery_app worker -l info
poetry run celery -A app.celery_app beat -l info
```

## Quick Deploy

For production deployments, use the deploy script which handles everything:

```bash
./scripts/deploy.sh
```

This script will:
- Back up the database (pre-migration)
- Pull latest changes and the container image
- **Pin the image tag** to the deployed commit's immutable `sha-<short>` tag
  (published by CI) so `app`/`worker`/`beat` all run one reproducible artifact
  instead of the mutable `:latest`
- Run database migrations (`alembic upgrade heads`) on the pulled image
- Recreate the containers on the pinned image
- Health-gate the app, and on failure **auto-roll back** — code reset to the
  previous commit and the image tag restored to the previously-running one
- Sync static files to nginx

The pinned tag is controlled by `ERP_IMAGE_TAG` (see `.env.example`); the deploy
script sets it automatically. To deploy a specific build manually, export it
before running compose, e.g. `ERP_IMAGE_TAG=sha-abc1234 docker compose up -d app worker beat`.

## Static Assets

Rebuild CSS when templates or Tailwind config change:

```bash
npm run build:css
```

Static files are served by nginx from `/var/www/dotmac/static/` for better performance.
To manually sync static files:

```bash
rsync -av --delete static/ /var/www/dotmac/static/
chown -R www-data:www-data /var/www/dotmac/static/
```
