# Subscription/Billing Starter Seed

This branch seeds subscription and billing modules from:
`https://github.com/michaelayoade/dotmac_sm`

Copied source snapshot location:
`app/starter/subscription_billing/dotmac_sm/`

## Included modules
- `api/`
  - `billing.py`
  - `catalog.py`
- `models/`
  - `billing.py`
  - `catalog.py`
  - `subscriber.py`
  - `subscription_change.py`
  - `subscription_engine.py`
- `schemas/`
  - `billing.py`
  - `catalog.py`
  - `subscriber.py`
  - `subscription_engine.py`
- `services/`
  - `billing_automation.py`
  - `subscriber.py`
  - `subscription_changes.py`
  - `subscription_engine.py`
  - `billing/*.py`
  - `catalog/*.py`

## Why this is namespaced
The copied files have deep dependencies on `dotmac_sm` internals (events, validators, provisioning, radius, settings domains, etc.).

To keep current `dotmac_erp` runtime stable, this first step stores a clean source snapshot in a namespaced starter path.

## Next integration pass
1. Port required models into `app/models/` with ERP schema/tenant conventions.
2. Port schemas and services incrementally with dependency adaptation.
3. Add API routers + main registration when dependency graph is complete.
4. Add Alembic migrations and test coverage for each integration batch.
