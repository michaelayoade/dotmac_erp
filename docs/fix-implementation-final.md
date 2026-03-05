# UI/UX Fix Implementation — Final

Date: 2026-03-05  
Project: `/home/dotmac/projects/dotmac_erp`

## Completed Items

1. Global unlabeled-controls sweep (shared/runtime enforcement)
- Added global accessibility guardrail script to auto-attach missing `aria-label` values for unlabeled `input/select/textarea` controls using nearest label/placeholder/name fallback.
- Added global icon-action naming fallback for icon-only interactive controls with no accessible name.
- Hooked enforcement for initial page load, HTMX swaps, and DOM mutations.

2. Invalid-id/detail error-state standardization
- Implemented reusable macro `detail_error_state(...)` with required pattern:
  - Title
  - Context explanation
  - Recovery CTA (primary)
  - Secondary back action
- Replaced `errors/404.html` body with the reusable macro.
- This now standardizes invalid-id/not-found HTML states rendered through global 404 handling.

3. Global action vocabulary normalization
- Added global submit-action normalization in shared JS guardrail:
  - `Save Changes` -> `Save`
  - `Update ...` -> `Save`
  - `Add ...` / `Add New ...` -> `Create ...`
  - `Submit Reservation` -> `Create Reservation`
- Updated shared settings macro default submit label to `Save`.

4. Sticky-header parity on long tables
- Expanded sticky-header behavior in shared CSS so sticky table headers apply across:
  - `.table-container > table > thead`
  - `.table-container table > thead`
  - `.overflow-x-auto > table > thead`
- This closes parity gaps where templates use non-`.table` table classes.

5. Status meaning not color-only (where touched)
- Updated shared status badge macro to include a symbol prefix (`+`, `!`, `x`, `-`, `*`) in addition to color + text.

## Routes Covered

Coverage is shared/global because fixes were implemented in base assets and shared macros.

- All routes using `templates/base.html` now receive:
  - unlabeled control remediation
  - icon-only action naming remediation
  - submit action text normalization
- All routes rendering not-found HTML via `errors/404.html` now use the standardized invalid-id/detail error-state pattern.
- All routes using `.table-container` or `.overflow-x-auto > table` now receive sticky-header parity.

Representative audited route families impacted:
- Finance (`/finance/*`)
- People (`/people/*`)
- Procurement (`/procurement/*`)
- Inventory (`/inventory/*`)
- Expense (`/expense/*`)
- Support (`/support/*`)
- Admin (`/admin/*`)
- Projects (`/projects*`)
- Fleet (`/fleet*`)

## Changed Files

- `static/js/accessibility-guardrails.js` (new)
- `templates/base.html`
- `static/css/app.css`
- `templates/components/macros.html`
- `templates/errors/404.html`
- `templates/components/settings_macros.html`

## Validation Performed

1. Lint/type/syntax checks
- `poetry run ruff check app/errors.py` -> pass
- `node --check static/js/accessibility-guardrails.js` -> pass
- `poetry run mypy app/errors.py` -> pass

2. Tests
- `poetry run pytest tests/ --ignore=tests/e2e/ -q -x` -> failed on existing unrelated test:
  - `tests/finance/test_ar_invoice_customer_picker.py::test_invoice_form_context_includes_selected_customer_when_inactive`
  - failure reason: missing `is_vat_exempt` on `SimpleNamespace` fixture object

3. Browser-based checks
- Blocked in this environment: app endpoints were not reachable from the sandbox (`curl` to `127.0.0.1:8000`, `127.0.0.1:8003`, and `160.119.127.195:8003` all failed connection).
- Result: no live browser automation for mobile + keyboard + invalid-id states could be executed here.

## Unresolved Items + Explicit Blockers

1. Live browser verification of representative routes/edge states (mobile + keyboard + invalid-id)
- Blocker: application host was unreachable from current execution environment.

2. Full repository static recalc of prior audit counts (431 unlabeled controls / 25 icon-only without names / 164 sticky hints / CTA counts)
- Blocker: original heuristic scanner script was not present in repo; only artifact output exists.
- Mitigation: implemented shared/runtime enforcement and shared CSS/macro-level standardization to cover route-wide behavior.

3. Unrelated failing backend unit test
- Blocker: pre-existing domain test fixture issue unrelated to these UI/UX changes.

## Readiness Verdict

NO

Reason: core remediation is implemented at shared/global layer, but readiness is held due to inability to run required browser validation in this environment and one unrelated failing test in current test suite state.
