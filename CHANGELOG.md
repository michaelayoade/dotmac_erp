# Changelog

All notable changes to DotMac ERP are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

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
