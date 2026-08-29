# Deployment

## Environment

Use `.env.example` as a starting point. Required values include:

- `DATABASE_URL`
- `JWT_SECRET`
- `TOTP_ENCRYPTION_KEY`

## Docker Compose

For local containerized setup using the published runtime image:

```bash
docker compose up -d app worker beat redis
```

To build the source-backed development profile, point Compose at an existing
local file containing the approved Forgejo read token. The file is a BuildKit
secret and its value never belongs in `.env`, Compose, or Git:

```bash
FORGEJO_TOKEN_FILE=/absolute/path/to/forgejo-read-token \
  docker compose --profile dev up -d --build app-dev redis
```

Apply migrations after containers are up:

```bash
docker compose exec app alembic upgrade heads
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
- Enforce the stable Docker Compose project name `dotmac`, including when the
  script runs from a revision-named release worktree. A conflicting
  `COMPOSE_PROJECT_NAME` is rejected before backup or container changes.
- **Pin the image tag** to the deployed commit's immutable `sha-<short>` tag
  (published by CI) so `app`/`worker`/`beat` all run one reproducible artifact
  instead of the mutable `:latest`
- Run database migrations (`alembic upgrade heads`) on the pulled image
- Recreate the containers on the pinned image
- Health-gate the app and admit the worker ping plus Beat heartbeat. Ordinary
  compatible deployments auto-roll back on failure — code resets to the
  previous commit and the image tag returns to the previously-running one.
  The explicit Employment Type authority activation is the exception: after
  its migration commits, every later failure is forward-fix-only because the
  previous image contains retired legacy writers. See `deploy/README.md`.
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
