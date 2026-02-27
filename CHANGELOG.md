# Changelog

All notable changes to DotMac ERP are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

## [2026-02-27]

### Changed
- **Frontend accessibility and responsive fixes** — template audit across 11 templates: dark-mode pairing corrected on `macros.html`, fleet detail pages (`document_detail.html`, `incident_detail.html`, `maintenance_detail.html`), `people/hr/employees.html`, `procurement/contracts/detail.html`, and `projects/templates/form.html`; responsive layout and form structure fixes in `expense/claim_item_detail.html`, `finance/banking/rule_form.html`, `finance/reports/analysis.html`, and `admin/settings/branding.html` (PR #20)
- **Web route handlers converted from `async def` to `def`** — ~394 `async def` handlers across ~44 files in `app/web/` converted to synchronous `def` to comply with CLAUDE.md architecture rules; SQLAlchemy sessions are sync and `async def` route handlers were unnecessary overhead that could mask session lifecycle issues (PR #21)

### Security
- **Jinja2 upgraded to >=3.1.6** — fixes CVE-2024-56201 and CVE-2024-56326 (CVSS 8.1 HIGH sandbox bypass allowing arbitrary code execution via crafted templates) (PR #4)
- **cryptography upgraded to >=44.0.1** — fixes CVE-2024-12797 (CVSS 8.1 HIGH OpenSSL RSA-PSS authentication bypass when using mTLS or certificate verification) (PR #3)
- **python-jose replaced with PyJWT 2.x** — CVE-2024-33663 (algorithm confusion attack: ECDSA key could forge EdDSA tokens); python-jose is abandoned; PyJWT is actively maintained (PR #6)
- **passlib replaced with direct bcrypt>=4.0.0** — passlib 1.7.4 is abandoned and misreads bcrypt>=4.0.0 version strings, risking silent fallback to insecure hashing (PR #11)
- **CRM webhook now requires CRM_WEBHOOK_SECRET** — previously `verify_crm_signature()` returned `True` unconditionally when the secret was not configured, allowing unauthenticated injection of CRM events; now returns HTTP 503 if unconfigured (PR #5)
- **Host header injection in password reset fixed** — `_resolve_app_url()` previously read `X-Forwarded-Host` without trusted proxy validation; now uses `app.net.get_request_host()` which enforces `TRUSTED_PROXY_IPS` (PR #9)
- **Added X-Frame-Options, X-Content-Type-Options, HSTS, Referrer-Policy headers** — four standard security response headers were missing from all responses (PR #8)
- **Webhook allowlist default-deny when unconfigured** — `_host_matches_allowlist()` previously returned `True` (allow all) when `WEBHOOK_ALLOWED_HOSTS`/`WEBHOOK_ALLOWED_DOMAINS` were unset, enabling SSRF; now returns `False` (deny all) with a startup warning if active webhook rules exist (PR #10)
- **SSRF protection applied to service hook webhook handler** — `registry.py` WEBHOOK handler now calls `_validate_webhook_target()` before dispatching HTTP requests, blocking private IPs and loopback regardless of allowlist state (PR #13)
- **RLS `SET LOCAL` uses parameterized `set_config()`** — `app/rls.py` previously interpolated organization UUID via f-string into a `SET LOCAL` statement; replaced with `SELECT set_config(...)` to eliminate injection vector (defense-in-depth) (PR #16)
- **Materialized view refresh uses safe identifier** — `analysis_cube.py` f-string interpolation in `REFRESH MATERIALIZED VIEW` replaced with a safely-composed identifier (PR #12)
- **DATABASE_URL default credentials removed** — `app/config.py` no longer falls back to `postgres:postgres`; a missing `DATABASE_URL` now raises a Pydantic validation error at startup (PR #17)
- **DNS rebinding TOCTOU mitigated in webhook validation** — `_validate_webhook_target()` resolved the hostname at validation time but passed the original URL to httpx, allowing a malicious domain to switch its DNS from a public IP (validation pass) to an internal RFC-1918 address (actual request); fix performs a second socket resolution immediately before dispatch and re-validates the resolved IP (PR #19)
- **INTERNAL_SERVICE hook targets restricted to explicit allowlist** — `registry.py` INTERNAL_SERVICE handler used `importlib.import_module()` with only a `startswith('app.services.')` prefix check, letting any tenant with hook-creation permission invoke arbitrary callables with attacker-controlled kwargs; replaced with an explicit `ALLOWED_INTERNAL_HOOK_TARGETS` frozenset that rejects any unlisted target before import (PR #18)

### Fixed
- **Narrow exception handling in `secrets.py`** — bare `except Exception` replaced with `except SQLAlchemyError` so unexpected errors are not silently swallowed when reading the `openbao_allow_insecure` setting (PR #14)
- **Sanitized exception messages in import/export API** — internal file paths and schema details no longer leaked in HTTP 500 responses; `ValueError` remains user-visible via 400, unexpected errors return a generic message and log via `logger.exception()` (PR #15)

## [2026-02-26]

### Added
- **Service hooks registry** — web management UI for registering and managing service hooks; enables event-driven integrations across modules
- **Structured error pages** — 400 (Bad Request) and 403 (Forbidden) pages with proper templates and user-friendly messaging
- **Expense approval workflow** — weekly budget limits per approver, team expense visibility for managers, approve/reject/query actions
- **Self-service portal** — manager team expense views aggregated from direct reports
- **Payroll dashboard** — run stats, recent runs, and quick-action cards (generate slips, submit all, bank upload)
- **Employment type filter** — payroll runs can now target permanent staff vs contractors separately
- **AR dedup script** — one-shot cleanup script for Splynx/ERPNext duplicate AR transactions (merged 27 customers, voided 15 185 duplicate payments and 17 958 duplicate invoices, removed ₦1.13B GL inflation)

### Fixed
- AP/AR invoice forms now prefill supplier/customer context from query params
- Payroll: February joiners no longer excluded — filter changed from `start_date` to `end_date` so mid-month hires are included
- Payroll: salary advance loan deductions now wired to slip creation with automatic loan balance updates on approval
- Payroll: permanent staff `gross_pay=0` bug — `include_employer_contributions=True` correctly passed in both create and calculate paths
- Automation recurring form enhancements and edge-case fixes

## [2026-02-25]

### Added
- Odoo adaptation phase: finance module inventory valuation improvements, advanced reporting hooks, and webhook dispatch patterns

### Changed
- AP/AR subledger reconciliation reports now include more granular breakdown columns

## [2026-02-24]

### Added
- **Expense approver budget adjustments** — model, service, migration, and detail page for tracking per-approver budget caps
- **Employee filter engine** — contract-based filtering with composable filter criteria for HR list pages
- **FIFO allocation service** — AR payment-to-invoice matching using First-In-First-Out allocation
- **DotMac CRM sync** — extended sync service with new schemas, org-level endpoints, and contact/deal mapping
- **Banking reconciliation scripts** — Jan 2026 NGN bank reconciliation and Splynx transaction matching utilities

### Fixed
- Integration test suite: all 70/70 integration tests now passing
- CI: migration FK guard on empty DB; semgrep excluded from pre-commit job (moved to CI)
- CI: mypy type errors, bandit security findings, migration chain ordering, SQLite compatibility

### Changed
- Notification service now supports digest aggregation — batches related notifications before dispatch

## [2026-02-23]

### Fixed
- **Notification email spam** — tax overdue and bank reconciliation reminders now send one digest per org/recipient/day instead of one per entity (reduced from 97 + 10 individual emails to 2 digests daily)
- Added 90-day age cap to split actionable vs stale overdue periods in reminder logic

## [2026-02-21]

### Added
- **Public Sector module (IPSAS)** — standalone `/public-sector/` module with cyan-accented sidebar, dedicated base template (`base_public_sector.html`), and `require_public_sector_access` permission gate. Routes: dashboard, funds, appropriations, commitments, virements, reports. Services remain under `app/services/finance/ipsas/`; API at `/api/v1/ipsas`
- **Approve-with-corrections for expense claims** — approvers can now fix line-item amounts, categories, and descriptions inline without full rejection. Original values are snapshotted and displayed on the detail page for audit trail

### Changed
- Finance sidebar no longer includes IPSAS section (`is_ipsas` conditionals removed from `base_finance.html`)

## [2026-02-20]

### Fixed
- **WCAG 2.2 AA modal compliance** — Alpine.js `x-trap` focus-trap directive added to all 18 modals across 13 templates; 5 modals were missing `role="dialog"`, `aria-modal`, `aria-labelledby`, or escape-key handler — all corrected

## [2026-02-18]

### Added
- **Outbox relay** — handler registry + `relay_outbox_events` Celery task dispatching `ledger.posting.completed` events to `AccountBalanceService` for near-real-time GL balance updates (30-second polling interval)
- **PG observability** — `pg_stat_statements` extension setup, `pg_monitor` grants, and `/pg-observe` Claude skill for slow query / bloat / lock diagnostics
- **Semgrep rules** — custom anti-pattern rulesets for security (XSS, SQL injection), architecture (route/service layer violations), and SQLAlchemy 2.0 patterns
- **Claude skills** — `/pg-observe`, `/celery-status`, `/db-health`, `/deploy-check` for operational diagnostics
- **AP supplier invoice comments** — model, migration, and UI for internal notes on supplier invoices
- **GL balance check trigger** — database-level migration ensuring double-entry balance integrity
- **Audit indexes migration** — performance indexes on audit log tables for faster compliance queries
- **Rotating work pattern** — employee scheduling model, schema, generator, and migration for rotating shift patterns

### Fixed
- **P0 multi-tenancy** — missing `organization_id` filters on several queries; CSRF token missing from `fetch()` API calls in templates
- **psycopg3 compatibility** — fixed 3× `AmbiguousParameter` bug in raw SQL queries in audit integrity health checks
- Test suite realigned with domain exception hierarchy and SQLAlchemy 2.0 `select()` patterns

### Changed
- Type hints and return types added across 80+ service files for full mypy compliance
- Tax calculation and transaction service error handling hardened
- AR/AP web service improvements with proper `NotFoundError` propagation

## [2026-02-17 and earlier]

See git log for prior history: `git log --oneline`.
