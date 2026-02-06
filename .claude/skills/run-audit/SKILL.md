---
name: run-audit
description: Run a comprehensive code quality and security audit on a module
arguments:
  - name: module
    description: "Module to audit (e.g. 'finance', 'people/hr', 'inventory', or 'all')"
---

# Run Code Audit

Perform a comprehensive audit of DotMac ERP code quality and security.

## Steps

### 1. Type safety audit
```bash
poetry run mypy app/services/$ARGUMENTS/ app/models/$ARGUMENTS/ app/web/$ARGUMENTS/ --ignore-missing-imports 2>&1
```
Report all type errors. For each error, explain the fix.

### 2. Lint audit
```bash
poetry run ruff check app/services/$ARGUMENTS/ app/web/$ARGUMENTS/ --statistics 2>&1
```
Report rule violation counts and patterns.

### 3. Multi-tenancy audit
Search for queries that may leak data across tenants:
- Look for `select(Model)` or `db.query(Model)` without `.where(Model.organization_id == ...)`
- Check every route handler has `auth.organization_id` passed to services
- Flag any raw SQL queries

### 4. Security audit
Check for:
- **Path traversal**: File operations without `.resolve()` + `.relative_to()` validation
- **SQL injection**: String formatting in queries instead of parameterized
- **CSRF**: POST/PUT/DELETE routes missing CSRF token validation
- **Auth bypass**: Routes missing `Depends(require_auth)` or equivalent
- **Secrets in code**: API keys, passwords, tokens hardcoded instead of in env vars
- **File upload**: Size validated BEFORE write, not after

### 5. Service layer violations
Check for business logic in wrong places:
- Routes (`app/api/`, `app/web/`) should NOT contain: `db.query()`, `select()`, `db.add()`, `if/else` business logic
- Tasks (`app/tasks/`) should NOT contain: direct DB queries, business logic (only orchestration)
- All complex logic should be in `app/services/`

### 6. Test coverage gaps
```bash
poetry run pytest tests/ -k "$ARGUMENTS" --cov=app/services/$ARGUMENTS --cov-report=term-missing 2>&1
```
Identify untested service methods.

### 7. Generate report
Output a markdown report with:
- **P0 (Critical)**: Security vulnerabilities, data leaks
- **P1 (High)**: Type errors, missing auth, service layer violations
- **P2 (Medium)**: Missing tests, lint warnings
- **P3 (Low)**: Style inconsistencies, dead code

Save report to `/root/dotmac/audit-{module}-{date}.md`
