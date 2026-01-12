# Architecture

## Overview

DotMac Books is a multi-tenant IFRS accounting system with:

- FastAPI for API and web UI
- PostgreSQL with schema separation
- Row Level Security (RLS) for tenant isolation
- Celery + Redis for background jobs

## Key Concepts

### Tenancy

Tenant data is scoped by `organization_id` and enforced by RLS. Web/API dependencies set the session variable to the current organization when a user is authenticated.

### Domain Schemas

PostgreSQL schemas separate modules (examples):

- `gl`: general ledger
- `ar`: accounts receivable
- `ap`: accounts payable
- `tax`: tax configuration
- `banking`: banking and reconciliation

### Services Layer

Business logic lives in `app/services/` and is shared by API and web routes to keep behavior consistent.

### Templates

Web UI uses Jinja2 templates in `templates/`. Global template helpers and shared context live in `app/templates.py` and `app/web/deps.py`.
