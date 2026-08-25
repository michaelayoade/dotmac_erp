# 0006 — A module-visibility scope is never write authority

## Status

Accepted

## Date

2026-08-25

## Context

`app/web/fixed_assets.py` had exactly one authorization dependency across all
49 of its routes: `require_fixed_assets_access`. Twenty-three of those routes
mutate — they create and dispose assets, run depreciation, post depreciation
journals into the general ledger, approve asset-to-GL reconciliation packages
and draft the correction journals for them, and bulk-delete assets. None of
them carried an in-body permission check.

The guard admitted three scopes:

```python
if not auth.has_any_permission(["fa:access", "fa:*", "fixed_assets:*"]):
```

The escalation was double.

**One.** `fa:access` is a navigation scope. Every read-only asset role in
`scripts/seed_rbac.py` holds it — that is its whole purpose, because
`WebAuthContext.accessible_modules` used it to decide whether Fixed Assets
appears in the sidebar. Holding "I can see this module" therefore satisfied
"I may post a journal in this module".

**Two.** `WebAuthContext.has_permission` matched wildcards in both
directions. As well as the correct rule — a HELD `X:*` scope grants the `X`
subtree — it also had:

```python
if requested.endswith(":*"):
    requested_root = requested[:-2]
    if scope == requested_root or scope.startswith(f"{requested_root}:"):
        return True
```

which made a REQUESTED `X:*` satisfiable by any held scope beneath `X`. That
inverts the meaning of the symbol: a guard asking for "all of Fixed Assets"
was in fact asking for "anything at all in Fixed Assets", and
`has_permission("fa:*")` returned `True` for a holder of `fa:assets:read`
alone. Every `X:*` guard written anywhere in the web layer would have had the
same defect; `require_fixed_assets_access` was the only site that had one yet.

The concrete result: `auditor`, `finance_viewer`, `asset_viewer` and
`inventory_manager` — each seeded with only `fa:access`, `fa:assets:read`,
`fa:depreciation:read`, `fa:categories:read` — could POST every mutating
Fixed Assets route in the portal, including
`/fixed-assets/depreciation/runs/{run_id}/post`, which posts to the ledger.
`asset_custodian`, which deliberately holds neither `fa:assets:dispose` nor
`fa:depreciation:run`, could perform both. Nineteen granular `fa:` permissions
already existed and the JSON API in `app/api/fixed_assets/` already enforced
them route by route, so the whole escalation was confined to the web adapter:
the same business acts were correctly authorized over HTTP+JSON and
unauthorized over HTTP+HTML.

`app/web/finance/ap.py` shows the target shape — 77 granular
`require_web_permission("ap:...")` guards, no module-access gate, and
`tests/finance/test_ap_route_permissions.py` holding it there.

## Decision

**A module-visibility scope is never write authority. This is fleet-wide.**

1. A `<module>:access` scope answers one question — may this person SEE this
   module — and may guard read/navigation routes only. `require_*_access`
   dependencies are visibility guards; a route that changes state must not
   have one as its only guard.

2. Every mutating route names the act it authorizes, with a granular
   `<module>:<resource>:<action>` permission. Where the same act is already
   reachable through another adapter, both adapters use the **same permission
   string** — one vocabulary, many transports. The 23 mutating Fixed Assets
   web routes now carry `require_web_permission(...)` and reuse the API's
   existing names (`fa:assets:create`, `fa:assets:dispose`,
   `fa:depreciation:run`, `fa:depreciation:post`,
   `fa:assets:import:preview`, `fa:assets:import:execute`, …).

3. **A wildcard is HELD, never REQUESTED.** `has_permission` keeps only the
   held-side rule: a held `X:*` scope grants everything under `X`. A requested
   `X:*` is matched as the literal string it is. A guard that means "the whole
   subtree" must say which action it wants, or ask for several with
   `require_any_web_permission`.

4. Module visibility is derived from the `<module>:access` scope alone.
   `accessible_modules` no longer treats any `fa:`-prefixed scope as Fixed
   Assets visibility; it matches `fa:access`, exactly like every other module
   in the same function.

5. Narrowing authority never widens it elsewhere. Permissions this change
   newly names are granted only to roles that already performed the act, and
   an existing omission is preserved rather than quietly filled in.

### Vocabulary added

Nine permissions the web routes now name and `seed_rbac` did not define:
`fa:assets:delete`, `fa:assets:import:{read,preview,execute}` (the API already
enforced the import trio against a catalogue that never listed them),
`fa:counts:{create,check}`, and
`fa:reconciliation:{create,approve,journal}`.

`fa:counts:create` covers the count-plan lifecycle (create, start, complete);
`fa:counts:check` covers recording line checks, which is what a custodian
physically does. `fa:reconciliation:approve` covers approve and reject
together, following `banking:reconciliation:approve`.

### The impairment gap, resolved deliberately

`fa:impairment:create` has existed since the catalogue was seeded and is held
by `finance_director` and `asset_manager`. `finance_manager` holds
`fa:revaluation:create` and `fa:revaluation:post` but has never held either
impairment permission — while today reaching `POST /assets/{id}/impair`
through `fa:access`.

**Decision: guard the route with `fa:impairment:create` and do not grant it to
`finance_manager`.** IAS 36 impairment is a judgement about recoverable
amount, and the grant table already places it with the finance director and
the asset manager. `finance_manager` loses web impairment as a consequence of
this fix. That is the correct direction for a change that exists to remove
authority nobody granted: widening a grant inside a security narrowing would
hide a policy decision inside a bug fix. If the business wants
`finance_manager` to impair, that is a one-line grant in its own reviewed
change, with the same visibility as any other authority increase.

## Consequences

- Four read-only roles (`auditor`, `finance_viewer`, `asset_viewer`,
  `inventory_manager`) lose write access to 23 Fixed Assets routes they were
  never granted. They keep every GET route.
- `asset_custodian` loses dispose and depreciation-run, which it was never
  granted, and gains `fa:counts:check`, which matches what the role does.
- `finance_manager` loses web impairment (see above). `finance_director` and
  `asset_manager` keep it.
- `finance_director`, `finance_manager` and `asset_manager` gain the newly
  named permissions for acts they could already perform, so no role loses a
  capability it was actually granted.
- The wildcard change has a blast radius of exactly one call site: a
  repository grep for a requested `X:*` string finds only the
  `require_fixed_assets_access` admit list this change rewrites. The other
  `:*` occurrences are Redis key patterns (`settings:*`, `ff:*`) and a literal
  containment test on a HELD scope (`"audit:*" in scopes` in
  `app/services/auth_dependencies.py`), none of which route through
  `has_permission`. Every `X:*` guard written from now on means what it says.
- Every seeded role that holds any `fa:` permission also holds `fa:access`, so
  narrowing `accessible_modules` removes the module from nobody's sidebar.
- `app/web/finance/**` carries the same defect in twelve modules and 172
  mutating routes. It is NOT fixed here: it is frozen by a two-directional
  ratchet in `tests/architecture/test_module_access_is_not_write_authority.py`
  with a named, per-file allowlist, so the count can only fall and only by
  lowering the allowlist in the same reviewed change. Those entries are
  **grandfathered, not reviewed-and-correct**.
- The permission catalogue grows by nine rows, which every deployment picks up
  on the next `seed_rbac` run. Roles are granted by key, so an operator who
  has customised grants keeps them.

## Alternatives rejected

**Keep the broad scope and add amount thresholds in the services.** Push the
decision down: let anyone with `fa:access` reach the route, and have
`FixedAssetService` refuse a disposal or a depreciation posting above some
value. Rejected for four reasons. It answers the wrong question — a threshold
asks "how much", authorization asks "who", and no threshold makes an auditor
the right person to post a depreciation journal at any amount. It cannot
express the acts that have no amount at all: approving a reconciliation
package, toggling a category, starting a count plan, executing an import. It
puts the same rule in two places and guarantees they drift, which is precisely
how the web adapter and the JSON API came to disagree about the identical
business act. And it moves an authorization decision out of the one layer that
is uniformly testable — a route's declared dependency is inspectable by an AST
walk, whereas a threshold buried in a service is reachable only by execution.
Authorization belongs at the adapter boundary, expressed as the act being
performed; amount thresholds are an approval-workflow concern and remain one.

**Fix only `require_fixed_assets_access` and leave the wildcard matcher
alone.** Rejected: the matcher is the reusable half of the defect. Removing
the broad admit list without it would leave the next `X:*` guard anyone writes
silently meaning `X:anything`.

**Introduce a `require_fixed_assets_write` module-level guard.** Rejected: it
repeats the original mistake one notch finer. A single write scope cannot
separate creating an asset from disposing one or from posting its
depreciation to the ledger, which is exactly the separation of duties the
existing 19-permission catalogue and the JSON API already express.

**Give the web adapter its own permission names.** Rejected: two vocabularies
for one act is how the API and the portal diverged in the first place. The
web routes reuse the API's strings verbatim.

## Amendment — 2026-08-25: the detector covers all of `app/web/**`

The decision above is unchanged. What changes is the reach of the guard that
enforces it.

### What was wrong

`tests/architecture/test_module_access_is_not_write_authority.py` read
`app/web/fixed_assets.py` and `app/web/finance/**` and nothing else — 21 of
the web adapter's 81 modules. The premise this ADR states is fleet-wide, so
the other 60 modules were not exempt from it; they were unmonitored, which is
the failure this fleet's guard-exemption rule (Governance ADR-0018) names
explicitly. The exact defect decided against here could have been added to
`app/web/people/**`, `app/web/inventory.py`, `app/web/projects.py` or
`app/web/support.py` and no test would have said a word.

The detector now walks every `app/web/**/*.py`.

### What the widened scan measured

**661 mutating web routes are authorized by module visibility alone**, of
which **195 are being decomposed in flight** (172 finance in `FINANCE_BACKLOG`,
23 Fixed Assets already fixed by this ADR). The remaining **489 across 41
modules** are newly recorded in `WEB_BACKLOG`, on the same two-directional
ratchet: the count may not rise, and it may not fall without lowering the
table in the same reviewed change.

Grouped by what the routes can cause:

| Module group | Routes | What a holder of module visibility alone can do |
|---|---|---|
| People / payroll | 35 | run payroll, post payslips, adjust earnings and deductions |
| People / performance (`perf.py`, `pms.py`) | 104 | appraisals, PIPs, appeals, contract amendments, institutional scoring |
| People / training | 50 | courses, assessments, grading, certificates |
| People / recruitment | 23 | requisitions, offers, hiring decisions |
| People / HR records | 144 | establishment, positions, org tree, lifecycle, discipline, leave, attendance, scheduling, self-service |
| Inventory | 31 | stock movements, adjustments, counts, transfers |
| Projects | 39 | project lifecycle, tasks, budgets, billing inputs |
| Support | 23 | ticket lifecycle |
| Public sector | 7 | appropriations, commitments, funds, virements — statutory budget instruments |
| Procurement | 6 | requisition and vendor actions |
| Admin sync + portals + misc | 27 | integration credentials, API keys, help content, notifications, profile |

A prioritized decomposition program should take them in the order money,
stock, establishment/headcount and statutory filings move: public sector (7,
smallest and most statutory), payroll (35), inventory (31), procurement (6),
then projects (39), then the People performance/HR/training mass (321), then
support and the remainder.

### The severe subset: 13 mutating routes with no authorization at all

Recorded separately in `UNAUTHENTICATED_WEB_MUTATIONS`, because a coarse guard
and no guard are not the same finding.

- **`app/web/admin_crm_sync.py` — 5 routes, live.** Mounted at
  `/admin/sync/crm` whenever the `crm` module is enabled. Every route depends
  on `optional_web_auth`, which returns a guest context instead of raising,
  and the router carries no `dependencies=` list. Unlike `app/web/admin.py`
  beside it, nothing supplies `require_admin_access`. The five routes save CRM
  integration configuration, **generate an API key**, **revoke an API key**,
  trigger an inventory push and run a health check. This is not grandfathered
  debt of the kind the tables above describe; it is a live authorization gap
  and needs its own fix.
- **`app/web/admin_dotmac_sub_sync.py` — 3 routes, currently unmounted.** The
  same shape (`/admin/sync/dotmac-sub`, `optional_web_auth`, no router-level
  guard) for saving dotmac_sub integration config, testing the connection and
  triggering a sync. No module imports this router today, so the routes are
  unreachable — until somebody mounts it, at which point they are the case
  above.
- **`app/web/careers.py` — 4 routes — and `app/web/onboarding_portal.py` — 1
  route.** Deliberately public, with a stated and enforceable premise: job
  application submission and application-status requests are anonymous intake
  behind a captcha and a rate limit; offer accept/decline and onboarding task
  completion authorize by possession of an unguessable per-record token
  verified in the handler (`get_offer_by_token`, `_require_valid_token`) —
  bearer-of-secret authority rather than session authority. Listed so the
  count is honest, not because they are wrong.

### A second finding: guards naming permissions that cannot be granted

Widening job (2) of the test — "every guarded permission is real" — surfaced
seven permission strings guarding nine sites in `app/web/inventory.py`'s
material-request routes that `scripts/seed_rbac.py` does not define:
`inv:material_requests:{create,submit,approve,delete}` and an `inventory:`
spelling of three of them. No role can hold them, and `has_permission`
short-circuits on `is_admin`, so those routes are admin-only by accident
rather than by decision. This is the inverse failure — a granular guard that
denies everyone — and it is recorded in `WEB_UNSEEDABLE_PERMISSIONS`.
Resolving it means seeding the permissions and deciding which roles hold them,
which is an authority grant and belongs in its own reviewed change.

### Classification: guards are classified by body, never by name

Three corrections came out of reading every `require_*` body rather than
trusting its name.

- `require_discipline_cases_{read,create,update}` and
  `require_discipline_workflow_manage` read as capability checks and are not:
  each opens `if auth.is_admin or auth.has_module_access("people")` and only
  then considers its granular permission, so any holder of `hr:access` passes
  without it. They are visibility guards and are classified as such.
  `require_self_service_discipline_manager` admits `discipline:access`, which
  is that module's visibility scope, and is classified the same way.
- `require_private_performance_mode` and `require_government_pms_mode` are
  router-level dependencies on `people/perf.py` and `people/pms.py` that chain
  `require_hr_access` and compare the organization's `PerformanceMode`. A
  deployment mode says which features are switched on, not who may use them —
  it cannot distinguish two people in the same organization — so it adds no
  authority.
- Conversely, `app/web/fleet.py`'s `require_fleet_<resource>_manage` family
  (14 mutating routes) and `people/weekly_meeting_reports.py`'s
  `require_report_{write,submit,reopen}` **are** genuine: each ANDs a real
  `<module>:<resource>:<action>` permission onto module access. They are not
  in any backlog. `people/self_service.py`'s
  `require_self_service_{profile_update,documents_upload,leave_approver,expense_approver}`
  are genuine too, which is why that file records 17 rather than 27.

### A per-route guard is not the whole guard

The detector now also reads `APIRouter(dependencies=[...])`. `app/web/admin.py`
declares `optional_web_auth` on all 35 of its mutating handlers and is
nonetheless admin-only, because its router is constructed with
`dependencies=[Depends(require_admin_access)]`; `admin_sla_policies.py` is the
same for five more. Counting argument defaults alone would have reported those
40 routes as unauthorized, and a table of 40 false positives is how a ratchet
gets deleted. The same mechanism is what identified `admin_crm_sync.py` as
genuinely unguarded — it has the routes and not the router dependency.

### Status of the backlog

**Grandfathered, not reviewed-and-correct.** No individual route in
`WEB_BACKLOG` has been assessed and found acceptable. Nothing in this
amendment changes any route's authorization: it changes only what is measured.
Decomposing these routes into granular `<module>:<resource>:<action>`
permissions is a separate, prioritized program, and the two admin sync routers
above are a defect to fix rather than debt to schedule.
