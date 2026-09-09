# ERP's async / PID / fork lifecycle contract for the Kernel successor

**Status:** analysis, committed for record — no Kernel repin, no runtime
switch, no writer retirement, no adoption claim
**Scope:** what ERP genuinely *requires* from a shared `DatabaseRuntime`
successor, stated as invariants; current implementation cited as evidence,
not as the thing being requested. Companion to Sub's equivalent report,
which removed three speculative APIs from scope.

## The three verdicts

1. **Async.** NOT REQUIRED of the Kernel API. ERP has real, live async
   database paths (finance/people settings screens, numbering-sequence
   generation), but every one of them is self-contained inside
   `app/db/__init__.py`'s own `get_async_engine()` /
   `get_async_session_local()` / `AsyncSessionLocal` — none of it touches, or
   would need to touch, Kernel plumbing. ERP's requirement is narrower than
   "the Kernel owns async session/transaction semantics": it is "ERP's own
   async paths keep working, unmodified, alongside a Kernel-owned sync
   `DatabaseRuntime`." That is satisfiable today with zero Kernel change —
   the product keeps a second, product-owned async engine/sessionmaker pair
   beside the Kernel's sync one. A permanent async surface on
   `DatabaseRuntime` would be a large, permanent API added for a requirement
   ERP does not actually have.

2. **PID / fork lifecycle.** NOT REQUIRED of the Kernel API — satisfiable by
   the product using the exposed `engine` plus hook points ERP already owns.
   ERP forks in two places (Gunicorn `preload_app` worker spawn, Celery
   prefork), and the invariant in both is the same one Sub already
   established: **a child process must never reuse a connection or pool
   entry created before the fork.** ERP's current mechanism differs from
   Sub's in *shape* (self-healing PID check on every accessor call, versus
   Sub's single `worker_process_init` → `engine.dispose()`), but not in the
   invariant it enforces, and ERP already owns the exact hook points
   (`worker_process_init` in `app/celery_app.py`, `post_worker_init` in
   `gunicorn.conf.py`) where `runtime.engine.dispose()` would go if ERP
   adopted a Kernel-owned sync engine. No fork-safety API needs to exist on
   `DatabaseRuntime` for this.

3. **Cross-tenant / BYPASSRLS access.** NOT NEEDED today. ERP has no runtime
   BYPASSRLS engine, and no per-call privilege switch — and its own accepted
   architecture explicitly rejects a per-call switch as illegitimate,
   independently reaching the same design Michael states for the Kernel: any
   future genuinely fleet-wide need must be served by "an isolated
   process/service with a distinct `NOSUPERUSER BYPASSRLS` credential...and
   no substitution into the ordinary application pool" — i.e. a **separately
   bound engine**, never a flag on a shared session
   (`docs/architecture/rls-bypass-boundary.md`, "Accepted boundary" §3). That
   shape, if it ever materializes, is already expressible through
   `DatabaseRuntime.__init__`'s existing `platform_engine` parameter — no new
   Kernel surface, only a decision to bind it.

## 1. Async

### What was measured

Every DB-async symbol in the tree (MEASURED, `grep -rl` sweep, 2026-09-09):

```
app/db/__init__.py                         # owns get_async_engine / get_async_session_local / AsyncSessionLocal
app/rls.py                                 # async GUC layer: set_current_organization, tenant_context, clear_organization_context, get_current_organization_id
app/services/finance/common/numbering.py   # NumberingService, async throughout
app/services/finance/settings_web.py       # finance settings web-context service
app/services/people/settings_web.py        # HR settings web-context service
app/web/deps.py                            # get_async_db, get_async_db_for_org
app/web/finance/settings.py                # numbering-sequence + settings routes
app/web/people/settings.py                 # HR settings routes
app/web/settings.py                        # cross-module settings landing route
```

`app/db/__init__.py:96-121` (`get_async_engine`) creates a **separate**
`AsyncEngine` from the sync one — it URL-rewrites `postgresql://` /
`postgresql+asyncpg://` to `postgresql+psycopg://` and applies the same
pool/statement-timeout settings independently. `app/db/__init__.py:161-178`
(`get_async_session_local`) is a second, independently PID-tracked factory
paralleling the sync `_get_session_local` at lines 138-149. `app/rls.py`
duplicates the sync tenant-GUC primitives (`set_current_organization_sync`,
`tenant_scope`, etc. — not shown here) for the async session, so tenant
scoping is maintained twice, once per session type, by design.

These are genuine, currently-shipping features — `app/web/finance/settings.py`
and `app/web/people/settings.py` wire real FastAPI routes
(`GET/POST .../settings`, numbering-sequence list/edit/reset) through
`Depends(get_async_db_for_org)` (`app/web/deps.py:1527-1558`). This is not
dead code or an abandoned experiment.

### Why this is narrower than "Kernel needs async"

Nothing in the async path imports from, or would need, `dotmac_kernel`. The
async engine, its sessionmaker, its PID tracking, and its GUC layer are all
private to `app/db/` and `app/rls.py`. If ERP adopted a Kernel-owned
`DatabaseRuntime` for its **sync** path tomorrow, none of the files above
would need to change, because they never call into the sync accessors they'd
be replacing (`get_engine`, `SessionLocal`, `_get_session_local`) — they have
their own async equivalents end to end.

The distinguishing question the brief poses — "does ERP require an async
session factory from the Kernel, or does it require that its own async paths
keep working alongside a Kernel-owned sync runtime" — resolves cleanly to the
second. Retaining a product-owned async runtime during (and likely after) a
staged cutover is a valid, self-contained shape; it does not by itself
justify a permanent async surface on `DatabaseRuntime`.

**Verdict: NOT REQUIRED of the Kernel API.** Product-owned async
engine/sessionmaker pair, unaffected by which sync runtime ERP composes.

## 2. PID / fork lifecycle

### What forks, and where (MEASURED)

| Fork point | Configuration | Evidence |
|---|---|---|
| Gunicorn worker spawn | `preload_app = True`, `workers = GUNICORN_WORKERS or cpu_count()*2+1`, `worker_class = "uvicorn.workers.UvicornWorker"` | `gunicorn.conf.py:34-36,58` |
| Celery prefork | Default Celery worker pool (no `--pool`/`--concurrency` override); entrypoint execs `celery -A app.celery_app worker -l info` | `app/celery_worker_entrypoint.py:12-15` |

`Dockerfile:121` runs `gunicorn -c gunicorn.conf.py app.main:app` in the
production image; `Dockerfile.hardened:123` runs bare `uvicorn` with no
`--workers` flag (single process, no fork) — so the fork-safety requirement
is deployment-profile-specific, not universal across every ERP entrypoint.

### The invariant, and ERP's current mechanism

Invariant (same one Sub's report already established):
**a child process must never execute a query against a connection or pool
entry that was opened before the fork.**

ERP's current mechanism (`app/db/__init__.py:39-71`, `96-121`, `138-149`) is
PID-tracking at every accessor:

```python
def get_engine():
    global _engine, _engine_pid
    pid = _current_pid()
    if _engine is not None and _engine_pid == pid:
        return _engine
    if _engine is not None and _engine_pid != pid:
        _dispose_sync_engine(_engine)   # engine.dispose(), full close
    _engine = create_engine(...)
    _engine_pid = pid
    return _engine
```

The same pattern is repeated independently for `_async_engine` /
`_async_engine_pid` (lines 96-108) and `_session_local` / `_session_local_pid`
(lines 138-149). This is **self-healing on every call**, not a one-time hook:
whichever accessor is called first in a child process detects the PID
mismatch and disposes-then-recreates, with no dependency on a fork signal
actually firing.

By contrast, ERP already owns explicit post-fork hook points that do nothing
DB-related today:

- `app/celery_app.py:91-93,52-67` — `@worker_process_init.connect` →
  `_bootstrap_worker_process_observability` → `bootstrap_celery_observability`
  (logging, monitoring, OTel, audit-listener registration; no DB call).
- `gunicorn.conf.py:63-66` — `post_worker_init(worker)` →
  `bootstrap_runtime_observability(app)` (same shape, no DB call).

### Is ERP's shape a genuinely different requirement?

No. The self-healing PID check is a *more defensive implementation* of the
identical invariant Sub satisfied with a single disposal call in
`worker_process_init` — it additionally tolerates code paths that never wire
an explicit fork hook, and it survives repeated re-forks (Celery
autoscale/respawn, Gunicorn worker recycling via `max_requests`) without
depending on each new child running the hook. But it does not ask for
anything the Kernel needs to expose: ERP already has both fork points
instrumented with an observability hook; adding `runtime.engine.dispose()` to
each transfers exactly the way Sub's did. No `register_at_fork` mechanism, no
`getpid`-aware factory, and no disposal API needs to exist on
`DatabaseRuntime` — the product supplies the hook, using the plain `engine`
property Kernel already exposes.

One caveat worth naming precisely, since it is the actual difference from
Sub's shape: ERP's own `get_engine()`/`_get_session_local()` never leak a
stale engine to a caller **even if nobody wires a fork hook at all**, because
every access re-checks the PID. A Kernel-owned `DatabaseRuntime` has no such
self-check — if ERP adopted it and a future deploy path forked without a
`dispose()` hook (e.g., a bare `uvicorn --workers N` invocation without
Gunicorn's `post_worker_init`, similar in shape to `Dockerfile.hardened`'s
single-process profile today), that new path would need its own disposal
hook. That is a product-side deployment discipline requirement, not a Kernel
API gap — Sub's finding transfers because Sub's *forking* deploy shapes
(Celery, and whichever WSGI/ASGI server Sub uses) already wire the
equivalent hook; a hookless fork path would be a defect in either project's
deployment config, not evidence the Kernel needs a new mechanism.

**Verdict: NOT REQUIRED of the Kernel API.** Satisfiable by the product
calling `runtime.engine.dispose()` from the fork/worker-init hooks ERP
already owns.

## 3. Cross-tenant / BYPASSRLS access

### What was measured

`MIGRATION_DATABASE_URL` — the credential Academy binds a BYPASSRLS engine to
— is used **exclusively** by migration/deploy-time tooling in ERP, never by
application runtime code:

```
alembic/env.py:94-97                           # required for Alembic; "never falls back"
scripts/bootstrap_database_roles.py:106         # role-bootstrap CLI
scripts/verify_runtime_admission.py:393-395     # deploy-time verification, refuses if DATABASE_URL == MIGRATION_DATABASE_URL
app/migration_credential_custody.py:73,479      # names every service permitted to see it
```

Zero occurrences in `app/db/__init__.py`, `app/main.py`, `app/celery_app.py`,
or any `app/api|web|services|tasks` file. `app/runtime_admission.py:620`
states the online runtime credential explicitly excludes it: `"runtime
admission — read-only, runtime credential (no MIGRATION_DATABASE_URL)"`.

ERP's cross-organization application-layer mechanism
(`app/db/session_context.py:112-134,251-284`, `app.bypass_rls` GUC family in
`app/rls.py`) is a **separate, older** question from BYPASSRLS, and ERP's own
accepted architecture (`docs/architecture/rls-bypass-boundary.md`) has
already resolved it against exactly the shape Michael names:

- The `allow_cross_org` / `cross_org_session` mechanism bypasses only the
  SQLAlchemy ORM listener, never PostgreSQL RLS
  (`docs/architecture/rls-bypass-boundary.md` §"Measured state", table row 1;
  confirmed again in `app/db/session_context.py:251-284`'s own docstring:
  "It does not bypass PostgreSQL RLS").
- A separate, now-being-retired mechanism — a runtime-settable
  `app.bypass_rls` PostgreSQL GUC, consulted by `should_bypass_rls()` in 105
  tables' RLS policies — is explicitly rejected as illegitimate: *"The
  user-settable GUC cannot be a legitimate cross-tenant capability. Any
  ordinary PostgreSQL role can set a custom `app.*` parameter, so retaining
  the writer would let `app_user` grant itself the same database reach as an
  approved administrator"* (same file, §"Runtime removal"). The runtime
  writer is being removed from every source family that set it
  (`app/rls.py`, `app/api/deps.py`, `app/db/session_context.py`,
  `app/services/auth_dependencies.py`, careers/onboarding services, seven
  scripts), enforced by a structural guard that scans for both the Python
  helper and raw SQL setters.
- The accepted replacement for any **genuinely** irreducible cross-tenant
  need is stated as a design constraint, not yet built: *"for irreducible
  database-wide operations, use an isolated process/service with a distinct
  `NOSUPERUSER BYPASSRLS` credential, reviewed grants, no object ownership,
  and no substitution into the ordinary application pool"* (same file,
  §"Accepted boundary" item 3). The same document states plainly: *"This PR
  creates no cross-tenant role, pool, service, Bao field or credential. Those
  are added only for residual, measured demand after individual
  disposition—not as a blanket replacement for the GUC."*

This is ERP independently reaching the same conclusion Michael states for the
Kernel classification: BYPASSRLS is a credential/role property expressed as a
**separately bound engine or session factory**, never a per-call flag such as
`session(bypass_rls=True)`. ERP has already rejected the per-call-flag shape
in production and named the distinct-credential shape as its accepted
replacement, before that replacement was ever built.

### Does ERP have Academy's requirement today?

No. Academy has a live `MIGRATION_DATABASE_URL`-bound engine used for runtime
cross-tenant reads. ERP's `MIGRATION_DATABASE_URL` never leaves
migration/deploy tooling, `app_admin` (the BYPASSRLS role) is stated to
"remain migration/offline-only. No application route, worker pool or web
process receives that credential" (`rls-bypass-boundary.md` §"Accepted
boundary"), and the one BYPASSRLS-flavored runtime mechanism ERP ever had
(the `app.bypass_rls` GUC) is being actively removed as illegitimate, not
extended.

**Verdict: NOT NEEDED today.** If a genuinely fleet-wide, no-tenant-to-resolve
requirement materializes later (the `isolated_cross_tenant_service` contract
already named in ERP's own caller-disposition inventory,
`docs/inventories/rls-cross-org-callers.tsv`), the shape ERP has already
committed to — a separately bound engine/service with its own distinct
`NOSUPERUSER BYPASSRLS` credential — is expressible today through
`DatabaseRuntime.__init__`'s existing `platform_engine` parameter
(`packages/dotmac-kernel/src/dotmac_kernel/session_runtime.py:132-171`,
docstring: *"a privilege boundary, not a performance one"*). That is a
binding decision for whenever real demand appears, not a new Kernel API.

## Anything else ERP needs that `DatabaseRuntime` cannot express

### `statement_timeout` / pool tuning — satisfiable by the product's own `Engine`

ERP sets `statement_timeout` via `connect_args={"options": f"-c statement_timeout=..."}`
at `create_engine()` time (`app/db/__init__.py:23-32`, applied at line 68) and
similarly for the async engine (lines 74-83, 109). `DatabaseRuntime.__init__`
(`session_runtime.py:132-158`) takes a raw, already-constructed `Engine` —
the product builds it however it needs (`connect_args`, `isolation_level`,
`execution_options`, pool sizing) and hands it to the constructor. This
requires no Kernel change at all; it is not even "satisfiable via the exposed
`engine` property" post-construction — it is satisfiable at construction,
before `DatabaseRuntime` ever sees the engine.

**Verdict: NOT REQUIRED of the Kernel API.** Already satisfiable via the
constructor's `engine: Engine` parameter.

### Legacy tenant GUC name (`app.current_organization_id`) — already accounted for

`app/db/session_context.py`'s three-layer tenant-scoping model (ORM listener
+ `app.current_organization_id` GUC + shared-module `app.current_tenant` GUC)
is the exact motivating case cited in `DatabaseRuntime`'s own docstring for
`legacy_tenant_settings` (`session_runtime.py:146-149`: *"ERP's
`app.current_organization_id` is the motivating case"*). This is not a gap —
it is a requirement the Kernel successor already named and designed for.

**Verdict: already REQUIRED and already present** — no further action; cited
here only so this contract's coverage is complete.

## What this contract does NOT claim

- No claim that ERP is ready to adopt `DatabaseRuntime`, has adopted it, or
  should adopt it on any schedule. This is a requirements document.
- No claim about Academy's or Sub's runtime beyond what their own committed
  reports state; this document does not re-derive them.
- No claim that ERP's `app.bypass_rls` retirement is complete — the
  `rls-bypass-boundary.md` ledger it is evidenced from states the `app_user`
  cutover remains blocked and several rows remain `undecided`. This
  document only uses that ledger's *accepted target shape* for cross-tenant
  access, not its completion state.

## Net result versus the assumption

ERP asked for **less** than the async/fork/BYPASSRLS surface a naive reading
of its current code would suggest. All three questions in this brief resolve
to "not required of the Kernel API": async stays product-owned, fork safety
is satisfiable with the exposed `engine` plus hooks ERP already has, and
cross-tenant BYPASSRLS access is not a live ERP requirement at all today —
and where ERP's own architecture anticipates one, it has already designed
that requirement's shape as a separately bound engine, matching (and
independently corroborating) the classification this contract was asked to
test against, not asked to prove.
