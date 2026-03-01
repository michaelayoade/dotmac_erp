# Seabone Memory — dotmac_erp

## Project Facts

### From CLAUDE.md
> # DotMac ERP
>
> IFRS-based multi-tenant ERP system. FastAPI + SQLAlchemy 2.0 + Celery + Jinja2/Alpine.js.
>
> ## Quick Commands
>
> ```bash
> # Quality (or use: make check)
> make lint                        # ruff check app/
> make format                      # ruff format + fix
> make type-check                  # mypy app/
>
> # Testing (or use: make test)
> pytest tests/path/test_file.py -v  # Specific test
> pytest -x --tb=short               # Stop on first failure
> make test-cov                      # With coverage
>
> # Database
> make migrate                     # alembic upgrade head
> make migrate-new msg="desc"      # New migration

### From README
> # Dotmac ERP
>
> Unified ERP for finance, HR, and operations. Multi-tenant business software built with FastAPI, featuring comprehensive financial modules, human resources, authentication, RBAC, audit logging, background jobs, and full observability.
>
> ## Features
>
> ### Financial Modules
>
> - **General Ledger (GL)**
>   - Chart of Accounts management

### Stack Detection
- Build: pyproject.toml detected

## Known Patterns

### Security Architecture (from 2026-02-27 scan)
- **Auth:** CSRF middleware (cookie+SameSite=Lax+Origin check), rate limiting via `app/middleware/rate_limit.py` (login 5/min, reset 3/min), JWT allowlist in `auth_flow.py` (rejects `alg:none`)
- **Multi-tenancy:** `organization_id` filter on every query — verified consistently across all services; also RLS via `app/rls.py` (`SET LOCAL app.current_organization_id`)
- **File uploads:** `app/services/file_upload.py` — `resolve_safe_path()` for traversal, magic byte validation, size check before write
- **Document templates:** `app/services/automation/safe_template.py` — `SecureSandboxedEnvironment` with `is_safe_attribute()` blocking dunder/dangerous attrs (SSTI mitigated)
- **Webhooks:** `app/services/finance/automation/workflow.py` — SSRF protection via `_validate_webhook_target()` with DNS resolution + private IP blocking; HOWEVER allowlist defaults to open when env vars unset (`workflow.py:181`)

### Known Security Gaps — Fix Status (as of 2026-02-27 cycle 3 scan)
All 32 cycle-1/2 findings triaged + 5 new cycle-3 findings. Status per gap:
- `app/api/auth_flow.py:363` — password reset poisoning (HIGH) → **PR #9** (fix-security-c2-1) ✅
- `app/services/hooks/registry.py:344` — SSRF in WEBHOOK handler (HIGH) → **PR #13** (fix-security-c2-2) ✅
- `app/services/hooks/registry.py:299` — INTERNAL_SERVICE arbitrary callable (MEDIUM) → **PR #18** (fix-security-c2-3) ✅
- `app/api/crm.py:115` — CRM webhook auth bypass (HIGH) → **PR #5** (fix-security-c1-1) ✅
- `app/services/finance/automation/workflow.py:181` — webhook allowlist default-open (MEDIUM) → **PR #10** (fix-security-c1-2) ✅
- `app/services/finance/automation/workflow.py:214` — DNS rebinding TOCTOU (MEDIUM) → **PR #19** (fix-security-c1-3) ✅
- `app/main.py` — missing security headers (MEDIUM) → **PR #8** (fix-security-c1-5) ✅
- `app/rls.py:51,169` — f-string SET LOCAL (LOW) → **PR #16** (fix-security-c1-7) ✅
- `app/services/finance/rpt/analysis_cube.py:156` — f-string REFRESH VIEW (LOW) → **PR #12** (fix-security-c1-6) ✅
- `app/config.py:13` — default DB creds (LOW) → **PR #17** (fix-security-c1-8) ✅
- `app/api/finance/import_export.py:360` — raw exception in 500 (LOW) → **PR #15** (fix-security-c2-4) ✅
- `app/services/secrets.py:53` — broad exception (LOW) → **PR #14** (fix-security-c1-10) ✅
- 2 SKIPPED: security-c1-4 (CSP nonce, large effort), security-c1-9 (tool scripts, low risk)

### New Cycle 3 Security Gaps (2026-02-27, OPEN)
- `app/api/scheduler.py:52` + `app/models/scheduler.py` — Celery task injection, no task_name allowlist, no RBAC (HIGH) → security-c3-1
- `app/models/scheduler.py:18` — ScheduledTask has no organization_id, cross-tenant access (HIGH) → security-c3-2
- `app/main.py:720` — metrics token `==` comparison, timing attack (MEDIUM) → security-c3-3
- `app/middleware/rate_limit.py:232` — in-memory rate limiter per-process, ineffective in multi-worker (MEDIUM) → security-c3-4
- `app/services/auth.py:65` — hash_api_key uses unsalted SHA-256 (LOW) → security-c3-5

### New Cycle 5 Security Gaps (2026-02-27, OPEN)
- `app/api/audit.py:68` — DELETE /audit-events/{id} only requires auditor role; audit logs are deletable (HIGH) → security-c5-1
- `templates/public_sector/` — All 9 POST forms (7 templates) missing CSRF token; public sector writes broken + CSRF risk (HIGH) → security-c5-2
- `app/services/finance/banking/categorization.py:465` — Admin regex applied without timeout; ReDoS risk (MEDIUM) → security-c5-3
- `app/services/careers/candidate_notifications.py:105` — Applicant names in email Subject without CRLF strip (MEDIUM) → security-c5-4
- `app/web/csrf.py:232` — CSRF token compare uses `==` not `secrets.compare_digest()` (LOW) → security-c5-5

### Auth Scope Design Note (from cycle 5)
- `require_audit_auth` designed for READ-only (`audit:read`) but applied at router level to ALL methods including DELETE — "wrong-level dependency" anti-pattern.
- `_should_enforce_csrf()` skips CSRF for pure Bearer (no cookies). Web sessions using `access_token` cookie ARE enforced. Public sector uses cookie web auth → CSRF enforced → missing tokens = broken forms (both CSRF risk AND functional breakage).

### Engine Reliability (2026-02-27)
- **codex-senior**: API unavailable — sessions spawn then die immediately. Route complex tasks to `claude` instead.
- **aider** (deepseek-chat): Fast and reliable for trivial/LOW fixes. Sub-minute completions.
- **claude** (sonnet): Reliable for MEDIUM+ complexity. Use for multi-file reasoning.
- **codex**: Reliable for backend fixes with API access.

### Service Hook Architecture (from 2026-02-27 cycle 2 scan)
- **Registry:** `app/services/hooks/registry.py` — executes hooks by `HookHandlerType`: WEBHOOK (httpx), NOTIFICATION (NotificationService), EMAIL (smtp), INTERNAL_SERVICE (importlib), EVENT_OUTBOX (outbox table)
- **SSRF gap:** WEBHOOK handler has zero URL validation; `workflow.py:_validate_webhook_target()` has the correct reference implementation (DNS resolve + RFC-1918 block + allowlist)
- **Trusted proxy:** `app/net.py` has `get_request_host()`, `get_request_scheme()`, `is_from_trusted_proxy()` — these check `TRUSTED_PROXY_IPS` env var before accepting forwarded headers; use these instead of reading forwarded headers directly

### Template Security
- Jinja2 auto-escaping enabled everywhere; `| safe` only on: csrf_form, tojson, admin-controlled branding CSS, system-generated document HTML
- `{{ error }}` query params rendered without `| safe` — correctly auto-escaped, no reflected XSS
- `confirm()` dialogs used for destructive actions (non-ideal, not a security flaw)

### Quality Debt Patterns (from 2026-02-27 quality-cycle2+3 scan)
- **SQLAlchemy 1.x migration incomplete**: 66 service files still use `db.query()` — affected modules: all of inventory/, finance/ar/ (invoice.py, quote.py, sales_order.py), finance/ap/, lease/, tax/, consolidation, payroll, fixed_assets/. `inventory/item_query.py` also returns a legacy `Query` typed interface. `salary_slip_service.py:133` has one remnant inside an otherwise-2.0 file.
- **async def in web routes**: 394 `async def` route handlers across 44 files in `app/web/` — should all be `def` since sessions are sync. Bulk fix: strip `async` keyword codebase-wide.
- **Untested modules (5 domains, 36 service files)**: Fleet (7), PM (7), IPSAS (8), Procurement (9), Careers (5) — all have zero unit tests.
- **Service layer commit violations (wider than previously known)**: Not just `audit.py` + `hooks/web.py` — also: all 5 IPSAS services use `_commit_and_refresh()` pattern (15+ commits), inventory/item.py (7 commits), inventory/fifo_valuation.py (4 commits), finance/ar/invoice_bulk.py, finance/ap/invoice_bulk.py, finance/gl/bulk.py, finance/payments/payment_service.py, finance/payments/webhook_service.py, careers/web.py. Total: ~30+ violations across 22+ files.
- **God functions**: `reconcile_paystack_payments()` 694L (splynx/sync.py:2219), `create_salary_slip()` 432L, `post_invoice()` 368L, `create_invoice()` 312L. 636 functions >80L across 254 files.
- **audit.py silent fallback**: `audit.py:111` bare `except Exception:` silently falls back from `db.query()` to `select()` with no logging — masks SA compatibility errors.
- **N+1 query patterns**: `payroll_service.py:763` loads Employee per-assignment in bulk payroll loop (N queries for N employees); `payroll_service.py:381+400` loads SalaryComponent per line in structure replace loops.
- **Incomplete feature branch**: `expense/limit_service.py:866` employment-type scope rules silently skipped with `pass`, causing limits for that scope to never be applied.
- **IPSAS service pattern**: All 5 mutation-capable IPSAS services use `_commit_and_refresh()` helper — fix by converting to `_flush_and_refresh()`.

### Naive Datetime Pattern (from 2026-02-27 quality-cycle6 scan)
- **29 instances of `datetime.now()` (naive) stored in DB columns across 21 service files** — systemic issue not previously tracked.
- Key affected files: `attendance_service.py:1167,1184,1202` (status_changed_at), `ipsas/appropriation_service.py:132` (approved_at), `ipsas/virement_service.py:120,178` (approved_at, applied_at), `automation/document_generator.py:471` (sent_at), `finance/common/numbering.py:301,520` (last_used_at), `support/sla.py:444` (SLA age baseline).
- **Fix pattern**: Replace `datetime.now()` with `datetime.now(UTC)` (importing `UTC` from `datetime` module, Python 3.11+, or `from datetime import timezone; UTC = timezone.utc` for <3.11).
- Bulk filename uses of `datetime.now().strftime()` (9 locations in 6 bulk services) are lower priority but should also be fixed for consistency.
- **CRM webhook 200-OK on exception**: `api/crm.py:428` — bare `except Exception` returns HTTP 200 with error payload; fix by raising `HTTPException(status_code=502)`.
- **Duplicate AR/AP bulk classes**: `finance/ar/bulk.py` and `finance/ap/bulk.py` share ~70 lines of identical logic (can_delete, export filename, contact field traversal) — extract to base class.

### Deps Patterns (from 2026-02-28 deps-cycle11 scan)
- **Exact version pins are systemic**: redis, celery, sqlalchemy, pydantic, psycopg, opentelemetry-* all use exact pins (no `^`). This prevents automated security patch uptake.
- **OTel 0.47b0 stale + incompatible**: When fastapi lockfile drift is fixed (Starlette 0.37→0.52), OTel 0.47b0 FastAPI instrumentation ALSO needs upgrading to 0.53b0 simultaneously — bundle both fixes.
- **observability.py:7 is MODULE-LEVEL jose import**: Highest-priority file in python-jose migration (deps-c4-3) — blocking import failure if jose removed before this file is migrated.
- **Pydantic 2.7.4 exact pin**: Misses Decimal trailing-zero fix in 2.10.0 — critical for financial ERP JSON output.
- **msoffcrypto-tool IS used**: `scripts/import_uba_statements.py:168` — NOT a zombie dep.
- **CI uses Python 3.12**: `.github/workflows/ci.yml` has `PYTHON_VERSION: "3.12"` — satisfies `>=3.11,<3.13` constraint.
- **app/services/imports/ and app/services/finance/posting/ are namespace packages** (no __init__.py) — inconsistent with rest of project but functionally correct.

### API Layer Patterns (from 2026-02-27 api-cycle3+7; updated api-cycle10 2026-02-28)
- **TWO ListResponse schemas exist**: `app/schemas/common.py` (correct — has `total: int`, used by non-finance modules) vs `app/schemas/finance/common.py` (deficient — only `count: int`, used by GL/AR/AP/RPT/Tax/Lease/Cons/Inventory/FixedAssets). Finance schema lacks `total` field entirely.
- **Systemic broken pagination (api-c7-1, HIGH)**: 46+ finance/inventory/fixed_assets list endpoints use `count=len(items)` — page size, not total. Services return `(items, total)` tuple but routes do `items, _ = svc.list_...()` discarding total. Fix: add `total` to finance `ListResponse`, capture second return value, use `total=total`.
- **parse_enum() unsafe (api-c7-2, MEDIUM)**: `app/api/finance/utils.py`'s `parse_enum()` raises unhandled ValueError → 500 on bad filter. Safe alternative `parse_enum_safe()` exists but underused. Affects GL, Lease, FixedAssets, Tax. Fix: replace `parse_enum()` with `parse_enum_safe()` or add try-except → HTTPException(422).
- **Procurement module (6 list endpoints) drops total** — discards second return value from service with `items, _ = svc.list_...()` (uses correct schema but wrong call pattern)
- **Enum validation gaps**: Fleet module (`vehicles.py:70`, `maintenance.py:55`, `incidents.py:57`, `reservations.py:66`) uses bare `EnumClass(str_value)` with no try-except → 500 on invalid input. Fix: either use FastAPI native enum type or wrap in try-except ValueError → 422.
- **response_model gaps (96 GET endpoints, cycle10)**: Core CRUD has response_model; analytics/stats/summary do NOT. Key gaps: `me.py` (11 self-service endpoints), `people/hr.py` designations+employment-types+grades (trivial fix), `finance/ipsas.py` 4 report endpoints (lines 518/534/550/566), `people/perf.py` (726,738), `people/recruit.py` (724,814,824), `people/expense.py` (1156,1166). Previously known: `banking.py:277`, `ipsas.py:121`, `workflow_tasks.py:56`, `pm/projects.py:164`, `support.py:339`, `contracts.py:161`.
- **Silent enum filter**: AR/AP list endpoints use `try: EnumClass(str) except ValueError: set_to_None` — typo'd status returns all records with no 422 error. Fix: use native FastAPI enum type annotation.
- **scheduler.py order_by (api-c7-3, LOW)**: `order_by: str` at route level has no allowlist; service-level validation raises error → 500. Fix: use `Literal[...]` type or `Query(pattern=...)`.
- **`detail=str(e)` leakage**: 199 instances across pm/, procurement/, fleet/ routes — safe for business exceptions but risky for unexpected errors. Highest density in pm/tasks.py, pm/resources.py, procurement/contracts.py.
- **Missing router tags**: 20+ routers have no `tags=` — pm/, people submodule files (recruit, payroll, hr, perf, expense, leave, scheduling, attendance, training, discipline), expense_limits.py, audit.py, scheduler.py, procurement/ — all endpoints appear uncategorized in Swagger.
- **Async def in api/ — most are LEGITIMATE**: banking.py, import_export files, auth_flow.py avatar, CRM/Paystack webhooks all use async for request.form()/UploadFile. TRUE violations (no async ops): `opening_balance.py:136` (get_template) and `opening_balance.py:350` (get_import_status).
- **Full API scan health score (cycle 10)**: 56/100. Score lower than cycle7 (61) due to expanded coverage of People module analytics gap.
