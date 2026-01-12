# Development

This guide covers local workflows for development.

## Commands

- Run tests:
  ```bash
  poetry run pytest
  ```
- Run a single test file:
  ```bash
  poetry run pytest tests/test_config.py
  ```
- Rebuild Tailwind CSS:
  ```bash
  npm run build:css
  ```

## Web UI

Templates live in `templates/` and use the `base.html` layout. Shared context is provided by `app/web/deps.py`.

## API vs Web Routes

Most API routers are mounted both with and without the `/api/v1` prefix:

- `/api/v1/gl/accounts` and `/gl/accounts`

This is intentional for internal API access and UI calls.

## Migrations

Generate new migrations with Alembic and apply with:

```bash
poetry run alembic upgrade head
```

Avoid editing migrations that have already been applied to shared environments.
