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

## Static Assets

Rebuild CSS when templates or Tailwind config change:

```bash
npm run build:css
```
