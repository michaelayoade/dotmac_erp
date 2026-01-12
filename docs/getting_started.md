# Getting Started

This guide walks through local setup and first run.

## Prerequisites

- Python 3.11 or 3.12
- PostgreSQL 16
- Redis 7
- Node.js 18+ (for Tailwind build)
- Poetry (recommended) or pip

## Setup

1. Copy environment file:
   ```bash
   cp .env.example .env
   ```
2. Install Python dependencies:
   ```bash
   poetry install
   ```
3. Install frontend tooling (optional, for CSS rebuild):
   ```bash
   npm install
   ```
4. Run migrations:
   ```bash
   poetry run alembic upgrade head
   ```
5. Start the app:
   ```bash
   poetry run uvicorn app.main:app --reload
   ```

## Rebuild CSS

```bash
npm run build:css
```

For live changes:

```bash
npm run watch:css
```

## Default Credentials

See `README.md` for development accounts.

## Quick Checks

- App health: `GET /health`
- Metrics: `GET /metrics`
