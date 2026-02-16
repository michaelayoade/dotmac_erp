# Starters

This repo currently has two starter tracks:

## 1) Base Starter (`main`)

Use this when you want the core ERP baseline without subscription seeding.

What it includes:
- Core platform + finance/people modules
- Starter hardening and UI enhancements already merged into `main`

How to use:
```bash
git checkout main
git pull
```

## 2) Subscription/Billing Starter (`starter/subscription-billing`)

Use this when you want to start from a seeded subscription/billing module set copied from `dotmac_sm`.

What it includes:
- Everything in `main`
- Seeded subscription/billing snapshot under:
  - `app/starter/subscription_billing/dotmac_sm/`
- Seed manifest and integrity checks:
  - `app/starter/subscription_billing/manifest.py`
  - `tests/starter/test_subscription_billing_seed.py`

How to use locally:
```bash
git checkout starter/subscription-billing
```

Run starter seed validation:
```bash
pytest -q tests/starter/test_subscription_billing_seed.py
```

## Seeded Subscription/Billing Inventory

The seeded snapshot contains:
- API: `billing.py`, `catalog.py`
- Models: `billing.py`, `catalog.py`, `subscriber.py`, `subscription_change.py`, `subscription_engine.py`
- Schemas: `billing.py`, `catalog.py`, `subscriber.py`, `subscription_engine.py`
- Services:
  - `billing_automation.py`
  - `subscriber.py`
  - `subscription_changes.py`
  - `subscription_engine.py`
  - `services/billing/*.py`
  - `services/catalog/*.py`

See also:
- `app/starter/subscription_billing/README.md`

## Integration Guidance

The seeded files are intentionally namespaced and not wired into runtime imports yet.

Recommended integration order:
1. Port models into `app/models/...` with tenant/schema conventions.
2. Add/update Alembic migrations.
3. Port schemas and services incrementally, adapting dependencies.
4. Register API routers after dependency graph is complete.
5. Add unit/integration tests for each integration batch.
