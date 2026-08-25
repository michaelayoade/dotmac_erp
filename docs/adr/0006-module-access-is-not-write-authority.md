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
