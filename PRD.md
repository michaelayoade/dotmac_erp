# HTMX Implementation — Status & Remaining Work

> **Note on filename.** This file is misnamed `PRD.md` but is not a product requirements document. It is an HTMX implementation plan. Originally filed 2026-04-24. Suggested rename: `docs/plans/htmx-implementation.md`. Kept at root for now to preserve the single existing commit reference; safe to move once the team agrees.
>
> **Last reviewed:** 2026-05-13. Original plan superseded by the as-built `live_search` pattern (see §2). This doc now tracks what was built, what was decided against, and what remains.

---

## 1. Why this exists

The original plan (preserved in §5 for history) proposed converting list pages to HTMX partial swaps via:

- A central `app/services/htmx.py` helper module
- A new `templates/components/htmx_macros.html` with 9 macros
- Dedicated `_*_table.html` / `_*_row.html` partials per entity (8+ entities)
- Per-entity HTMX delete endpoints
- A global `htmx:responseError` handler

Between filing and review, the team shipped equivalent user-visible behaviour (no full reloads on search/filter/paginate) using a different pattern (§2). The structural pieces of the original plan were never built. The relevant question is no longer "execute the plan" but "given what shipped, what's left worth doing?"

---

## 2. As-built pattern (the one we actually use)

### 2.1 List pages

`templates/components/macros.html::live_search` (lines 931+) does all search/filter/pagination via HTMX:

- `hx-get` to the same page URL
- `hx-trigger="input changed delay:300ms, search"` (or `change` for filters)
- `hx-target="#results-container"` + `hx-select="#results-container"` — server renders the *whole page*, HTMX swaps only the matched fragment client-side
- `hx-push-url="true"` keeps the URL in sync so browser back/forward works
- `hx-include="closest form"` carries all sibling filter values
- `hx-indicator="closest [data-live-search]"` drives a small spinner inside the search input

Templates wrap their table + pagination in `<div id="results-container">`. No dedicated partial templates required.

**Trade-off being made:** server renders the full page on every keystroke (wasteful), but template count stays flat (one file per page, not three). The team accepted this for maintenance simplicity. Revisit only if a benchmark proves the full render is materially slow.

### 2.2 Inline action endpoints (workflow tasks, banking rules, AP payments)

Where a small piece of UI updates without a list refresh (e.g. "Mark task complete"), routes check `request.headers.get("HX-Request")` and return a snippet of HTML with an `HX-Trigger` header for toast/event signalling. See:

- `app/web/workflow_tasks.py:203-235` — task complete/snooze
- `app/services/finance/ap/web/payment_web.py:755-772` — payment actions
- `app/services/finance/banking/web_parts/reconciliations.py:627` — reconciliation actions
- `app/services/finance/banking/web_parts/rules.py:850` — bank rule edits

These are ad-hoc — there's no central helper. See §3.1 for whether to centralise.

### 2.3 CSRF on HTMX state-changing requests

`app/main.py:257` wires `csrf_middleware` globally. HTMX forms render `{{ request.state.csrf_form | safe }}` exactly like non-HTMX forms; nothing HTMX-specific is required. For HTMX DELETE buttons that don't submit a form, the CSRF token must be added via `hx-headers='{"X-CSRF-Token": "{{ csrf_token }}"}'` — but no such endpoints exist today (see §3.2).

---

## 3. Remaining work (decided)

### 3.1 Central HTMX helper module — **DO** (small scope)

Six places duplicate `request.headers.get("HX-Request")` and ad-hoc snippet construction. Add a thin helper module to remove the duplication.

**File:** `app/services/htmx.py`

```python
from fastapi import Request
from fastapi.responses import HTMLResponse
import json

def is_htmx_request(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"

def htmx_response(
    content: str = "",
    trigger: dict | None = None,
    push_url: str | None = None,
    redirect: str | None = None,
) -> HTMLResponse:
    headers: dict[str, str] = {}
    if trigger:
        headers["HX-Trigger"] = json.dumps(trigger)
    if push_url:
        headers["HX-Push-Url"] = push_url
    if redirect:
        headers["HX-Redirect"] = redirect
    return HTMLResponse(content=content, headers=headers)

def htmx_toast(message: str, level: str = "success") -> dict:
    return {"showToast": {"message": message, "type": level}}
```

**Acceptance criteria:**
- All six existing `HX-Request` call sites migrated to `is_htmx_request(request)`.
- Migration done in one PR (not staged) to keep the pattern unambiguous.
- `app/services/finance/ap/web/payment_web.py`, `app/services/finance/banking/web_parts/{reconciliations,rules}.py`, `app/web/workflow_tasks.py`, `app/main.py:381` all use the helper.
- No new direct `request.headers.get("HX-Request")` reads survive the PR (enforced by grep in CI or a one-time audit).

**Explicitly out of scope:**
- Dedicated `*_table.html` / `*_row.html` partials per entity (rejected — see §4.1).
- `htmx_macros.html` as a separate macros file (rejected — see §4.2).

### 3.2 HTMX error handler — **DO**

`templates/base.html` listens for `htmx:configRequest`, `htmx:afterSwap`, `htmx:afterRequest` — but not `htmx:responseError`. When an HTMX request 4xx/5xxs today, the user sees no feedback.

Add to `base.html` (near the other listeners around line 672):

```javascript
document.body.addEventListener('htmx:responseError', function (evt) {
    const status = evt.detail.xhr.status;
    if (status === 401) { window.location.href = '/login'; return; }
    const message =
        status === 403 ? 'Permission denied'
      : status === 404 ? 'Not found'
      : status >= 500 ? 'Server error — try again'
      : 'Request failed';
    if (typeof window.showToast === 'function') {
        window.showToast(message, 'error');
    }
});
```

**Acceptance criteria:**
- Triggering a 500 from any HTMX endpoint shows the toast within 1s.
- 401 redirects to `/login` without showing a toast.
- Existing `htmx-loading-states.js` indicators continue to work (no CSS conflict).

### 3.3 Audit `live_search` callers for `#results-container` correctness — **DO**

`live_search` requires the calling template to wrap its results in `<div id="results-container">`. Forgetting this silently breaks HTMX (the swap target doesn't exist). Verify the 11 templates currently using HTMX attributes all have the wrapper.

**Acceptance criteria:**
- Each of the 11 `hx-get`-using templates either (a) wraps its results in `#results-container` or (b) explicitly opts out by setting `target_id="…"`.
- A short note added to `.claude/rules/templates.md` codifying the wrapper requirement (it's already in CLAUDE.md but worth a more findable home).

---

## 4. Remaining work (decided against)

### 4.1 Dedicated `_*_table.html` / `_*_row.html` partials — **NO**

The original plan proposed 8+ pairs of partial templates so the server could return just the table fragment on HTMX requests. The `hx-select="#results-container"` approach gets the same client-side outcome at the cost of one extra server-side full render per HTMX request.

**Reverse if:** a list page's full render exceeds 200ms p50 *and* HTMX latency budget is the bottleneck (measure first — likely the DB query, not the template). Until then, keep the template count flat.

### 4.2 Standalone `htmx_macros.html` file — **NO**

`macros.html::live_search` already covers search, filter, pagination, debounce, push-url, autosuggest, loading indicator. The plan's nine proposed macros either duplicate this or are one-line wrappers around stock HTMX attributes. A separate file would fragment the macro catalogue without adding capability.

**Reverse if:** the `live_search` macro becomes a maintenance burden (>1000 lines, too many flags) — at that point splitting *might* help, but the split should be by *function* (search vs delete vs form-submit), not by tech-tag.

### 4.3 Phase 5 CSS (`.htmx-indicator`, `.htmx-swapping`) — **NO**

`htmx-loading-states.js` is already loaded in `base.html:62` and uses `data-loading-*` attribute conventions. Adding the plan's class-based rules would create two parallel systems for the same job. Stick with `htmx-loading-states.js` conventions; if a list page needs a loading state, use `data-loading-class` rather than `.htmx-indicator`.

---

## 5. Original plan (preserved for context)

The original 6-phase plan with milestones (AR Customers → AP Suppliers → GL Accounts → Inventory → Fixed Assets → People → Banking) is preserved in git history at commit `4436fce0` if needed. Summary of which items shipped via the as-built path:

| Original plan item | Status | Notes |
|---|---|---|
| Phase 1: `app/services/htmx.py` | Not built | Now scheduled in §3.1 |
| Phase 1: `partial_context` in `app/web/deps.py` | Not built, **rejected** | `base_context` already covers the need |
| Phase 1: `templates/components/htmx_macros.html` | Not built, **rejected** | See §4.2 |
| Phase 2: 9 HTMX macros | **Replaced** | `live_search` covers equivalent capability |
| Phase 3: per-entity partial templates | Not built, **rejected** | See §4.1 |
| Phase 4: HTMX-aware web services | **Shipped (different shape)** | Ad-hoc per route; centralisation in §3.1 |
| Phase 4: HTMX delete endpoints | Not built | No demand to date — list pages reload on delete via plain form POST + redirect, which is fine |
| Phase 5: HTMX loading-state CSS | Not built, **rejected** | See §4.3 |
| Phase 6: `htmx:responseError` handler | Not built | Now scheduled in §3.2 |

---

## 6. Decisions log

- **2026-04-24** — Original plan filed (commit `4436fce0`).
- **2026-05-13** — Reviewed. Plan declared superseded by as-built `live_search` pattern. Scoped remaining work to: central HTMX helper module, missing error handler, `#results-container` audit. Three plan phases formally rejected.
