# Multi-Tenant Session Listener — Design Spec

**Audit reference**: `docs/2026_correctness_audit_findings.md`, P1 #5 — Multi-org permission bleed at the model layer.

**Date**: 2026-05-10
**Author**: Audit + AI-assisted brainstorm
**Status**: Approved (architecture only); ready for implementation plan

---

## Problem

The audit found 86 of 338 `db.get(OrgScopedModel, ...)` calls in `app/services` lacked a same-statement organization check. Sample inspection showed roughly half were transitively safe; the rest trusted the caller to have validated org membership upstream.

The codebase has grown since the audit. Current state on `origin/main` (2026-05-10):

| Metric | Audit (≈3 weeks ago) | Current |
|---|---|---|
| `db.get(OrgScopedModel, ...)` calls in services | 338 | **1065** |
| Without subsequent `organization_id` check | 86 (~half safe) | **403** |
| `select(Employee/Designation)` calls | 135 | 238 |
| Files declaring `organization_id` ad-hoc | n/a | **217** |

No active cross-tenant leak detected. But the pattern means any single upstream auth bug becomes a *cross-tenant* leak rather than a same-tenant logic error — a severity multiplier on every other bug.

The audit's recommendation was a `get_or_404_for_org()` helper plus a lint rule, rolled out incrementally. With 403 sites now, that sweep is multi-sprint. We need a structural fix.

## Goal

A SQLAlchemy session-level listener that auto-injects `WHERE organization_id = ?` for any ORM query (`select()` and `Session.get()`) targeting an org-scoped model, gated on the session having a primed `organization_id`. Code that legitimately spans tenants opts out via an explicit context manager.

The 1065 existing call sites become safe with zero per-site edits. New code stays safe by default.

## Decisions

### D1 — Failure mode: strict raise

When the listener encounters an org-scoped query but `session.info['organization_id']` is not set and `allow_cross_org` is not active, raise `MissingOrgContextError`. No silent permissive fallback.

**Rationale**: Highest correctness guarantee. A missing org context is a bug (forgotten priming), not a runtime condition. The audit's premium tier promise is "no silent-wrong paths" — permissive mode would trade an unscoped-query bug for a different unscoped-query bug.

The blast radius (every untested code path could now error in production) is mitigated by D5 (phased rollout).

### D2 — Cross-org opt-out: context manager

```python
with allow_cross_org(session):
    db.execute(select(Organization))
```

Scope is explicit, easy to grep for, easy to review. The flag is restored in a `finally` block so an exception inside the block doesn't leak the bypass to subsequent queries.

Alternatives rejected: per-statement execution options (spreads opt-outs into individual statement construction; hard to grep), session-info flag set/unset manually (prone to leak on early returns), function decorators (hides cross-org-ness inside the function for callers).

### D3 — Org-scope detection: column heuristic with explicit deny-list

The listener treats a model as org-scoped iff `organization_id in cls.__table__.columns` AND `cls not in CROSS_ORG_DENY_LIST`. The deny-list is a frozenset in `app/db/multi_tenant.py`.

**Initial deny-list** (subject to verification during implementation):
- `Organization` — its PK is the org_id
- `Currency`, `Country`, `TaxJurisdiction` — genuinely-shared reference data
- Possibly `Person`/`User` — pending verification (multi-org login support)

**Deny-list semantic**: a deny-listed model is NOT filtered. The listener treats it as if `allow_cross_org` were active for that query. This is correct for genuinely-shared models that have no single-tenant owner. It is the WRONG choice for a per-tenant model — those must NOT be on the deny-list, they must rely on either the listener's automatic filter (default) or explicit `allow_cross_org` for cross-org operations. R1 below disambiguates the `Person` case.

**Rationale**: Zero refactor. 217 ad-hoc model declarations don't need changes. Adding new models follows existing conventions; the listener Just Works. False-positive risk is bounded by the deny-list, which is small and reviewable.

Alternatives rejected: requiring `OrganizationMixin` (217-file no-op refactor), explicit registry (high maintenance burden, easy to forget when adding a model), marker base class (same migration cost as the mixin).

### D4 — Session priming: `get_db` for HTTP, factory for non-HTTP

For HTTP routes: `get_db()` reads `auth.organization_id` from `WebAuthContext` (already a parameter via `Depends(require_auth)`) and calls `prime_session(db, auth.organization_id)` before yielding the session.

For Celery tasks and CLI scripts: a new factory `session_for_org(org_id)` wraps `SessionLocal()` and primes it. Tasks that span multiple orgs (e.g., daily reminder batch) iterate orgs and open one primed session per org.

For genuinely cross-org Celery work (rare): use `SessionLocal()` directly + `with allow_cross_org(db):`.

**Rationale**: Each entry point primes once at the boundary. Service code never deals with priming. Forgetting to prime an entry point is a bug that the listener catches loudly (per D1).

### D5 — Rollout phasing

Phase 1 (this PR): Build listener disabled by default. `prime_session`, `allow_cross_org`, `session_for_org`, `get_or_404_for_org` available. `get_db` automatically primes. Comprehensive listener-level unit tests. Env flag `ENFORCE_ORG_FILTER` defaults to `false` — no behavior change in any environment.

Phase 2 (next PR): Enable in conftest.py for the test suite. Fix every test that breaks. Each failure is a real unscoped-query finding. Outcome: tests prove org scoping at the query layer.

Phase 3 (next PR): Set `ENFORCE_ORG_FILTER=true` in staging env. Triage Celery tasks and CLI scripts. Fix or wrap each one with `session_for_org` / `allow_cross_org`.

Phase 4 (final PR): Set the flag in prod. Done.

Each phase is reviewable, reversible, and produces a list of findings the next phase consumes.

## Architecture

A single `do_orm_execute` event listener registered globally on the `Session` class, gated by an env flag.

```
HTTP request
  → FastAPI dispatch
  → Depends(require_auth) — produces auth: WebAuthContext
  → Depends(get_db) — opens Session, calls prime_session(db, auth.organization_id)
                       which sets session.info['organization_id'] = org_id
  → route handler
  → service.do_thing(db, ...)
  → db.scalars(select(Invoice).where(Invoice.status == OPEN))
        ↓
  do_orm_execute fires:
    target = Invoice (org-scoped, not on deny-list)
    org_id = session.info['organization_id']  ← set by get_db
    inject: WHERE organization_id = :org_id via with_loader_criteria
        ↓
  SQL: SELECT ... FROM ar.invoice WHERE status = 'OPEN' AND organization_id = ?
```

Celery tasks open `session_for_org(org_id)` (which delegates to `SessionLocal()` + `prime_session`) and the same listener flow applies.

Cross-org admin code uses `with allow_cross_org(db):`. Inside the block, `session.info['allow_cross_org']` is True; the listener short-circuits before checking org_id.

`session.get(Model, pk)` flows through `do_orm_execute` in SQLAlchemy 1.4+ — the listener applies `with_loader_criteria` which composes with the PK lookup. A get for an entity in a different org returns None.

## Components

| Path | Purpose | Status |
|---|---|---|
| `app/db/multi_tenant.py` | `MissingOrgContextError`, `CROSS_ORG_DENY_LIST`, `is_org_scoped(model)` | NEW |
| `app/db/org_listener.py` | `do_orm_execute` handler + `register_org_listener()` enable/disable | NEW |
| `app/db/session_context.py` | `prime_session(session, org_id)`, `allow_cross_org(session)`, `session_for_org(org_id)` | NEW |
| `app/services/common/multi_tenant.py` | `get_or_404_for_org(db, Model, pk)` helper | NEW |
| `app/web/deps.py` | `get_db` calls `prime_session` automatically | MODIFY |
| `app/db/__init__.py` | Wire `register_org_listener()` based on env flag at startup | MODIFY |
| `app/config.py` | Add `enforce_org_filter: bool = False` setting | MODIFY |
| `tests/db/test_org_listener.py` | Listener behaviour tests | NEW |
| `tests/conftest.py` | Phase-2 hook to enable in tests (deferred to Phase 2 PR) | MODIFY (Phase 2) |

## Public contract

```python
# app/db/session_context.py

def prime_session(session: Session, organization_id: UUID) -> None:
    """Set the org context for the session. Called once at the entry-point boundary
    (HTTP request via get_db, Celery task via session_for_org)."""

@contextmanager
def allow_cross_org(session: Session) -> Iterator[None]:
    """Temporarily disable org filtering for genuinely cross-org queries.
    Restores prior state in finally."""

@contextmanager
def session_for_org(organization_id: UUID) -> Iterator[Session]:
    """Factory for non-HTTP entry points (Celery, CLI). Opens SessionLocal,
    primes it with the given org_id, yields, closes."""

# app/db/multi_tenant.py

class MissingOrgContextError(RuntimeError):
    """Raised when an ORM query targets an org-scoped model without a primed
    org_id and without allow_cross_org. The exception message names the model
    and the SQL fragment for debuggability."""

# app/services/common/multi_tenant.py

def get_or_404_for_org(db: Session, model: type[T], pk: UUID) -> T:
    """db.get + raise NotFoundError if missing. The listener handles
    org-scoping; this helper just adds the 404. Sugar; using db.get directly
    + manual None check is equivalent."""
```

## Error handling

| Trigger | Exception | When | What user sees |
|---|---|---|---|
| Listener fires on org-scoped query, no primed org_id, no `allow_cross_org` | `MissingOrgContextError` | Bug — code forgot to prime | 500 + stack trace; message names model and query |
| `get_or_404_for_org` returns None | `NotFoundError(f"{Model.__name__} not found")` | Routine | 404; same message regardless of cross-tenant probing (no info leak) |
| `with allow_cross_org` exits via exception | exception propagates; flag restored in `finally` | Routine | Normal exception handling; flag does NOT leak |

`MissingOrgContextError` subclasses `RuntimeError`, not `HTTPException`. Services stay decoupled from FastAPI; the route layer translates to 500 via FastAPI's default handler.

## Testing strategy

**Unit tests** (`tests/db/test_org_listener.py`) — listener contract in isolation:

- `test_select_org_scoped_with_org_set_filters_by_org` — SQL contains `organization_id = :org_id`.
- `test_select_org_scoped_without_org_raises` — `MissingOrgContextError`.
- `test_select_non_org_scoped_unaffected` — deny-listed models execute without injection.
- `test_get_org_scoped_returns_none_if_other_org` — `db.get(Invoice, pk_from_org_a)` returns None when session primed for org_b.
- `test_get_org_scoped_returns_model_if_same_org` — happy path.
- `test_allow_cross_org_bypasses_enforcement` — inside the context manager, no filter, no raise.
- `test_allow_cross_org_restores_state_after_exit` — flag restored even on exception.
- `test_allow_cross_org_nested_preserves_outer_state` — nested context managers don't clobber.
- `test_listener_disabled_when_env_flag_off` — Phase 1 default; no behavior change.

**Integration smoke**: run existing FX revaluation suite with `ENFORCE_ORG_FILTER=true` after Phase 2 conftest hook lands. Failures = real unscoped-query bugs, becoming Phase 2 worklist.

**Negative test for deny-list**: confirm `Currency`, `Organization`, etc. are NOT filtered. New ad-hoc reference tables not on the deny-list will fail loud the first time they're queried — acceptable; loud failure beats silent leakage.

## Out of scope (Phase 1)

- **UPDATE/DELETE filtering**: `do_orm_execute` fires for UPDATE/DELETE too, but `with_loader_criteria` is select-only. Update/delete needs `event.listen(Mapper, 'before_update'/'before_delete', ...)` or statement-rewrite logic. Deferred to Phase 2 (likely a separate spec).
- **Bulk operations** (`session.execute(insert(...).values([...]))`): don't reliably fire `do_orm_execute`. Phase 1 leaves them unscoped — manual `organization_id=...` in the values dict is still required.
- **Cross-org admin UI**: existing super-admin tooling (if any) needs `allow_cross_org` annotations; that's Phase 3 work.
- **Lint rule blocking raw `SessionLocal()`**: nice-to-have for catching unprimed sessions at code review; not built in Phase 1.
- **Migration of `organization_id` ad-hoc declarations to `OrganizationMixin`**: 217-file no-op, postponed indefinitely. The heuristic-based listener doesn't require it.

## Open questions

- **R1**: Is `Person` per-org or multi-org? If a single Person row can belong to multiple orgs (e.g., contractors invited to multiple tenants), it goes on the deny-list. If per-org, it's filterable. Verify by inspecting `app/models/people/person.py` and the auth flow.
- **R2**: Are there existing `event.listens_for(Session, ...)` listeners in the codebase that might conflict (e.g., audit logging)? Check `app/services/audit_listener.py` for ordering / interaction.
- **R3**: Does `with_loader_criteria` compose correctly with `selectinload` and `joinedload` on org-scoped relationships? SQLAlchemy docs say yes for `with_loader_criteria(include_aliases=True)` — verify with a test.
- **R4**: What's the right home for the env flag — `app/config.py` Pydantic settings (typed, in the existing config) or directly via `os.environ.get` (simpler but inconsistent)? Default to Pydantic for consistency.

These are verification steps for the implementation phase, not architectural unknowns.
