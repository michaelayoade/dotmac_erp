# Troubleshooting

## Migration errors

- **Duplicate table**: A previous run partially applied a migration. Inspect current schema, then re-run:
  ```bash
  poetry run alembic upgrade head
  ```

## Authentication issues

- **401 in UI**: Ensure cookies are set and `JWT_SECRET` matches across instances.
- **RLS errors**: Check that `organization_id` is set in the session and that your user belongs to an organization.

## Static styling issues

- UI styles missing: rebuild Tailwind output:
  ```bash
  npm run build:css
  ```

## Metrics not exposed

- `/metrics` uses Prometheus client. Confirm dependencies are installed and the route is not blocked by middleware.
