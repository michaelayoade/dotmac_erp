# Dotmac ERP App Guide

This document describes the runtime architecture, configuration, and core workflows for the Dotmac ERP application. It is intended for developers and operators.

## Overview

Dotmac ERP is a unified, multi-tenant ERP system built on FastAPI covering finance, HR, and operations. It ships both a JSON API (`/api/v1/...`) and a server-rendered web UI (`/finance/...`, `/people/...`) with shared business logic and models.

## Architecture

- **API**: FastAPI routers under `/api/v1` with tenant-scoped dependencies.
- **Web UI**: Jinja2 templates served by FastAPI, using the same services layer as the API.
- **Database**: PostgreSQL 16 with schema separation for domains and RLS policies for tenant isolation.
- **Async jobs**: Celery workers + Redis broker, plus DB-backed Beat scheduling.
- **Observability**: Prometheus metrics, OpenTelemetry tracing, and structured logging.

## Key Directories

- `app/api/`: JSON API routers (including `finance/` and `people/`).
- `app/web/`: Web UI routes (including `finance/` and `people/`).
- `app/models/`: SQLAlchemy ORM models (including `finance/` and `people/`).
- `app/services/`: Business logic and orchestration.
- `templates/`: Jinja2 templates.
- `static/`: Compiled CSS and assets.
- `alembic/`: Database migrations.

## Configuration

All configuration is driven by environment variables (see `.env.example`). The most important groups:

### Database and Redis

- `DATABASE_URL`: PostgreSQL connection string.
- `REDIS_URL`: Redis connection string.

### Auth and Security

- `JWT_SECRET`, `JWT_ALGORITHM`
- `JWT_ACCESS_TTL_MINUTES`, `JWT_REFRESH_TTL_DAYS`
- `TOTP_ISSUER`, `TOTP_ENCRYPTION_KEY`

### Branding and Landing Page

- `BRAND_NAME`, `BRAND_TAGLINE`, `BRAND_LOGO_URL`
- `BRAND_MARK`: optional two-letter mark override.
- `LANDING_HERO_BADGE`, `LANDING_HERO_TITLE`, `LANDING_HERO_SUBTITLE`
- `LANDING_CTA_PRIMARY`, `LANDING_CTA_SECONDARY`
- `LANDING_CONTENT_JSON`: optional JSON override for landing page copy blocks.

### Observability

- `OTEL_ENABLED`, `OTEL_SERVICE_NAME`, `OTEL_EXPORTER_OTLP_ENDPOINT`

## Running Locally

1. Copy environment file:
   ```bash
   cp .env.example .env
   ```
2. Install dependencies:
   ```bash
   poetry install
   ```
3. Run DB migrations:
   ```bash
   poetry run alembic upgrade head
   ```
4. Start the app:
   ```bash
   poetry run uvicorn app.main:app --reload
   ```

## API

All API endpoints are available with and without `/api/v1` prefixes. For example:

- `/api/v1/auth/login` and `/auth/login`
- `/api/v1/gl/accounts` and `/gl/accounts`

Most routers require tenant auth. The API layer uses:

- `require_tenant_auth` for tenant-scoped access
- `require_role("admin")` for admin-only routes

## Web UI

The web UI is rendered via Jinja2 templates and uses the same services layer as the API. It receives:

- `brand` context via `app/web/deps.py:brand_context`
- `user` context from web authentication
- `content` for landing page via `app/web/deps.py:landing_content`

## Database and Migrations

Database migrations live in `alembic/versions`. Run:

```bash
poetry run alembic upgrade head
```

The schema uses Row Level Security (RLS) for tenant isolation and dedicated domain schemas (e.g., `gl`, `ar`, `ap`).

## Background Jobs

Celery is configured in `app/celery_app.py`. The scheduler runs from the DB-backed Beat implementation. Ensure Redis is running before starting workers.

## Metrics and Health

- Health check: `GET /health`
- Prometheus metrics: `GET /metrics`

## Testing

Run tests with:

```bash
poetry run pytest
```

There are unit tests and Playwright e2e tests under `tests/` and `tests/e2e/`.
