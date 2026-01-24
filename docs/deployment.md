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
docker compose exec app poetry run alembic upgrade head
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
- Pull latest changes
- Build CSS assets
- Sync static files to nginx (`/var/www/dotmac/static/`)
- Rebuild and restart Docker containers
- Run database migrations
- Reload nginx

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
