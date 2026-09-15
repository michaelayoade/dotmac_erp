# People / Performance / KPI Dashboard

## Scope and audit baseline

Focused source audit and first implementation slice based on ERP commit
`b449c4d82fdb6c19d2c9e26eab8ef85ba50528ed` (15 September 2026).
This is not a full security or performance audit of every ERP module.

Repository access used the authorized GitHub connector. Terminal Git/archive
retrieval was unavailable because the working environment could not resolve
GitHub. Existing files were read at the pinned commit; the two modified original
files were reconstructed locally and verified against their Git blob hashes.
No production database or customer records were accessed.

### Existing foundations verified

| Source | Finding / reuse decision |
| --- | --- |
| `app/web/people/perf.py` | Performance landing page and Goals & KPIs already exist. Keep KPI creation, targets, updates and existing support-metric syncs there. |
| `app/models/people/perf/kpi.py` | Employee-owned KPIs already have periods, targets, actuals, units and recorded status. Do not create a second authority. The current model does not define general aggregation/direction semantics. |
| `app/models/people/hr/department.py` | Canonical departments include `head_id`, active state, hierarchy and cost-centre linkage. Use exact headed departments; do not silently grant descendants. |
| `app/models/people/hr/employee.py` | `person_id` resolves the actor, `department_id` attributes KPI owners. Explicit organization predicates apply to every join. |
| `app/models/person.py` | Existing mutable JSON metadata can hold bounded personal display preferences. Only the namespaced preference entry changes. |
| `app/web/deps.py` | Reuse People access, PRIVATE/HYBRID performance policy, authenticated organization and leave-write restrictions. Add department authorization to this surface. |
| `app/models/analytics/org_metric_snapshot.py` | A generic metric snapshot store already exists; do not add a duplicate warehouse. |
| `app/services/analytics/dashboard_metrics.py` | Cross-module organization metrics already exist. They cannot safely become department metrics just by adding a dropdown; attribution, permissions and per-metric freshness need further work. |
| `templates/people/base_people.html`, `components/macros.html` | Reuse the People shell and existing statistics, status and empty-state components. No separate frontend or BI runtime. |

## Delivered surface

Navigation: **People > Performance > KPI Dashboard** on the Performance landing
page. Canonical URL: `/people/perf/kpi-dashboard`.

The page supports:

- One, multiple or all authorized departments; current week/month/quarter,
  previous month or a custom inclusive date range of at most 366 days.
- Literal KPI-name search and explicit recorded-status selection. Draft,
  deferred and cancelled records are excluded initially but can be selected.
- Six optional, reorderable summary cards: tracked KPIs, distinct owners,
  recorded achieved, at risk/missed, overdue open and actuals-recorded coverage.
- Department comparison and a stable, paginated 25-record target/actual table.
- Up to ten named personal saved views; create, rename/update, load and delete.
  Saved periods remain relative unless custom dates are selected.

Saved configuration is not executable: only approved periods, statuses, widget
keys, bounded search strings and authorized department UUIDs are accepted.
There is no arbitrary SQL/Python expression editor.

## Meaning of the numbers

This is a dashboard **of existing employee KPI records**, not a new automatic
finance, inventory, NOC, advertising or CRM data integration.

The date filter selects records whose **KPI period end** is within the chosen
range. Values are the records' current values, not values as they stood at that
past date. A quarterly KPI is not prorated across months. Department attribution
uses the owner's **current** department, not historical department membership.

`Recorded achieved` counts only status `ACHIEVED`; `COMPLETED` is not assumed to
mean the target was achieved. `At risk or missed` counts the corresponding
recorded statuses. `Overdue open` means a past end date and status PENDING,
ACTIVE, ON_TRACK or AT_RISK. Today's date uses the organization timezone;
missing timezone falls back explicitly to UTC, invalid timezone fails visibly.

Coverage is `count(non-null actuals) / count(selected KPI records) * 100`.
A recorded zero is present data. An empty cohort has no coverage percentage.
There is no sum of mixed currencies/units, average of departmental percentages,
inferred lower-is-better scoring or composite employee ranking.

The page states when it was read; individual record-change dates are shown.
A recent read is not a claim that every actual is fresh. Database failures are
not converted into zero-valued success responses. No historical trend is
fabricated from current data.

## Access and persistence

All routes retain People access and PRIVATE/HYBRID performance-mode guards.
GOVERNMENT_PMS-only deployments do not expose this private KPI surface.

Within that gate, normalized `admin`, `hr_manager` and `hr_director` roles can
read the current organization. Other People-authorized users must resolve to
an ACTIVE employee and can read only active departments where `head_id` is
that employee. A general manager/payroll role alone grants no company scope.
Unassigned owners appear only in unrestricted organization views.

Scope is resolved server-side for every request and saved department selections
are reauthorized on load/save. KPI, employee and department queries are tenant
constrained; all summaries, comparisons and detail rows derive from the same
scoped query. Responses use `Cache-Control: private, no-store`.

Views belong to the authenticated person and organization, even for admins.
The storage key is `Person.metadata_["people_kpi_dashboard_v1"]`, containing
version 1 and at most ten validated view entries. Writes lock and reload that
person row, preserve unrelated metadata, flush in the service, and commit only
in the route. Existing CSRF middleware and POST form tokens remain mandatory.
Leave-write-restricted accounts cannot save/delete views.

**Pre-existing authorization boundary:** legacy People/Goals routes use broader
People-level guards. This change does not claim to secure every other HR route.
Do not grant broad `hr:access` just to onboard a dashboard-only manager. A future
narrow-permission rollout must review module entry, existing authoring/detail
routes and department-scoped permissions together. No new roles or grants are
seeded by this feature. Organization viewers receive links to existing KPI
management; department viewers see the underlying records without those links.

## Deployment and acceptance

No new database table, migration, dependency, background job or external
credential is required for this slice. It performs no automatic KPI backfill and
changes no existing targets, actuals, lifecycle state or appraisal calculations.
Deploy through the existing ERP build/release process, including the normal
frontend asset build. No merge or deployment was performed during authoring.

Before releasing the draft:

1. Run the repository's locked Poetry/Ruff/type checks and full relevant tests.
   New tests are `tests/unit/test_kpi_dashboard_contract.py`,
   `tests/unit/test_kpi_dashboard_service.py` and
   `tests/architecture/test_kpi_dashboard_surface.py`.
   Apply the repository's minor-version release workflow and version metadata
   using its approved bump script; no version files were altered in this draft.
2. In staging with real PostgreSQL and the application's composed template
   loader, check authorized HR/admin access and department-head restriction,
   foreign department/organization attempts, unmapped/inactive actor denial,
   and PRIVATE/HYBRID versus GOVERNMENT_PMS routing.
3. Verify missing/invalid CSRF rejection for both write routes, simultaneous
   personal-view saves, preservation of other metadata, stale/revoked saved
   department access, and leave-write restriction.
4. Exercise real empty/populated KPI data, non-null zero, mixed units, inactive
   departments, unassigned owners, pagination, filters, HTMX and ordinary form
   navigation. Check desktop/mobile, dark mode and keyboard accessibility.
5. Reconcile summary counts to the same filtered KPI records and measure the
   aggregate/group/page query plans against representative organization data.
   KPI count is bounded by date selection; detail rows are paginated, but the
   aggregate still scans the eligible cohort. Do not assume performance at scale
   without that check.

Local authoring verification: 53 pure contract tests and 4 static surface tests
passed; 12 service/query tests passed in an offline harness with minimal
SQLAlchemy column-contract substitutes. The latter does **not** verify the
actual ERP model registry or PostgreSQL/RLS behavior. Python compilation and
Jinja syntax parsing passed. The full locked ERP environment, Ruff 0.15.0,
type checks, composed-template rendering and browser/production database tests
were unavailable locally and remain release gates, not claimed successes.

Rollback: reverting the code removes the page and router. Existing KPI data
is unchanged. Unused namespaced personal preferences may remain harmlessly;
never erase all Person metadata as a cleanup operation.

## Next slice, not part of this implementation

Extend the existing MetricStore with reviewed KPI/provider definitions,
source-specific permissions, department/cost-centre attribution and explicit
missing/stale states. Version targets and formulas before adding historical
comparisons. Support department-owned/shared dashboards with separately
reviewed permissions instead of broadening personal preferences into business
records. Automate Finance/Engineering/Support/Marketing metrics only when each
authoritative source, period basis and eligible cohort has been agreed and
reconciled. This slice does not deliver those integrations or a general BI
report builder.
