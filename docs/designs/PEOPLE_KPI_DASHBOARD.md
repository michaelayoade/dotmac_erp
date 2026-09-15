# People / Performance / KPI Dashboard and Employee Insights

## Independent delivery

This is a self-contained feature based on main `b449c4d82fdb6c19d2c9e26eab8ef85ba50528ed`.
It incorporates the reviewed foundation from PR #580, including that PR's CI
repairs, but has no commit-parent or merge dependency on #580. This PR supersedes
the earlier dashboard proposal; do not merge both overlapping implementations.
No merge or production deployment is performed as part of feature authoring.

## User surface

A single **KPI Dashboard** card lives beside **Goals & KPIs** under
**People > Performance**, at `/people/perf/kpi-dashboard`.

Goals & KPIs remains the authority for employee KPI definitions, targets, actuals,
evidence, department-template generation and existing support-metric syncs. There
is no second KPI database, scoring authority, separate app or external BI service.
The dashboard is read-only for business records. Its only writes are personal
saved display settings under the existing namespaced Person metadata key.

The dashboard provides:

- One, multiple or all authorized departments, literal employee-name/code and
  KPI-name search, recorded status selection, current week/month/quarter, previous
  month and custom inclusive periods of at most 366 days.
- Six optional/reorderable cards and up to ten named personal saved views.
  Existing v1 settings load with default insight options; relative periods stay
  relative. New settings also retain chart visibility, date scope and review mode.
- Recorded-status stacked department charts and measurement-coverage doughnuts.
  Data is aggregated before pagination. The chart shows the first 20 departments
  alphabetically when necessary and says so; the department table contains all.
  Overdue is never stacked with status, because those sets overlap. Tooltips show
  counts and percentages; accessible text and tables are always present.
- An active-employee review matrix: full name/code, department, matching KPI count,
  recorded achievements, measured below-target results after the deadline,
  overdue open KPIs, missing measurements and unscorable-measurement warnings.
- Separate 25-row pagination for employees and underlying KPI records. KPI rows
  show targets, actuals, units, direction, calculated target attainment and recorded
  status side by side. Search can narrow the view to a named employee or code.

Chart.js is already vendored by ERP. The page-specific initializer destroys old
instances before HTMX removes canvases, rebuilds after swaps/theme changes, and
is safe against duplicate initialization. It makes no network requests.

## Meaning and limits

**Due** selects KPI period-end dates. **Active** selects periods overlapping the
reporting range. **All overdue** ignores reporting dates and selects open KPIs
whose deadlines have passed, using the organization's business date. The default
remains Due. Status and KPI-name filters still apply to the selected records.

Employee review mode filters only the employee table, not the KPI totals/charts.
Assignment coverage ignores status and KPI-name filters, but retains department,
employee and date scope. An achieved-only filter must not make an employee's
other assigned KPIs disappear from assignment coverage. In All overdue, having no
matching records means no overdue open KPIs, and does not itself require review.

“Needs attention” is a request to review evidence: below target after deadline,
overdue open, missing measurement on a started KPI, unscorable measurement, or
no KPI records in the date scope. It is **not** a finding that an employee is
failing, and triggers no appraisal, disciplinary or PIP action. A KPI below its
final target before period end is “In progress”; no invented linear pacing curve
or final failure is applied. Existing private status bands remain 100/80; recorded
status and end-of-period target assessment deliberately answer different questions.

Current employee department assignments attribute the results. Past-period views
show today's records, NOT historical as-of snapshots. No trend history, composite
employee ranking or cross-role leaderboard is fabricated. The existing OHCSF
appraisal scoring engine, appraisal completion gates and final decisions remain
separate. There is no new automation for Sales/Marketing/NOC/CRM metrics.

Coverage is non-null actual count / selected KPI count; a genuine zero is present
measurement data. Record-change timestamps do not claim that an actual is fresh.
A database failure does not turn into a zero-valued successful dashboard.

## Measurement corrections

`perf.kpi.lower_is_better` is a new nullable Boolean with an additive migration.
NULL means legacy behavior: recognize supported lower-is-better ticket tags, and
otherwise retain the old higher-is-better default. Nothing guesses the direction
of other legacy KPIs. Managers should review them using the new explicit direction
selector on the existing KPI form; the API also accepts the field. Newly generated
employee KPIs inherit the template's direction. Changing a template does not rewrite
already generated employee definitions.

`kpi_measurement.py` defines shared target attainment for the model, manual progress,
system sync, and KPI-fed scorecard-item updates. The dashboard uses the same pure
function for row details and matching direction/comparison rules for SQL counts.

- Zero actuals are scored explicitly; they cannot retain a stale achieved status.
- Lower-is-better uses target/actual, including explicit zero-tolerance handling.
- Missing, non-finite, negative ratio inputs and zero higher-is-better targets are
  unscorable. A missing observation is never treated as a measured zero.
- Ratios are capped at 999.99 to fit the existing Numeric(5,2) field. Rounding just
  below 100 cannot turn an unmet target into an achieved target.
- Target/direction/actual changes recalculate cached achievement through the same
  path. A draft without an actual stays a draft. Completed, missed, deferred and
  cancelled lifecycle statuses are not reopened by score refreshes.
- Empty support resolution-rate/duration cohorts produce None and clear stale
  achievement/scorecard item scores. Count metrics can legitimately measure zero.
  Invalid negative resolution durations are excluded from duration measurement.
- Support synchronization preserves existing metric/perspective notes instead of
  replacing the whole notes field.
- The resolution-rate default no longer claims to be SLA compliance. Explicit
  template regeneration can rename that known legacy default, with an alias check
  to avoid generating duplicate employee KPIs. Existing employee KPI labels and
  targets are not bulk-renamed or backfilled. True SLA timeliness is not implemented.

There is no automatic rescore job/backfill. Old recorded statuses can disagree
with newly calculated attainment until reviewed or updated through the normal
writer/sync. Both are visible. Existing ticket calculations still read ERP's
`support.ticket`; their completeness against Sub/CRM must be validated separately.

## Security boundary

All routes retain existing People access and PRIVATE/HYBRID mode guards.
Within that boundary, admin/hr_manager/hr_director see their current organization;
other People-authorized users must be active employees heading the selected active
departments. Descendant departments are not silently granted. Every join to KPI,
Employee, Person and Department is tenant-constrained. Employee roster queries
reuse the same authorization. Saved department selections are reauthorized on
load/save. Responses remain `Cache-Control: private, no-store`.

Personal views are limited to the authenticated person's organization/profile,
row locked on changes, and preserve unrelated metadata. All POST forms retain
CSRF tokens. Leave-write-restricted accounts cannot save/delete views. Manager
links to pre-existing authoring pages remain limited to organization-wide users.

**Inherited limitation:** other People/Goals routes have broader module guards.
Do not grant broad `hr:access` solely for dashboard-only managers. This feature
introduces no new role grants and does not claim a full legacy People RBAC repair.

## Deployment / verification

Apply `20260915_kpi_direction` through the normal `alembic upgrade heads` pipeline
before serving the updated model. It adds a nullable column without rewriting KPI
values. Build CSS using the locked npm toolchain and regenerate the served-static
fingerprint; both compiled assets and descriptor must agree with the tested image.
No new runtime package or production credential is required.

Run the normal required CI unchanged, plus the focused KPI measurement, insight,
seeded-record, configuration, service and web tests. Run chart lifecycle tests with
`node --test tests/js/test_kpi_dashboard_charts.cjs`. In staging verify real
PostgreSQL/RLS, composed dotmac-ui rendering, forged department requests, saved-view
reauthorization, CSRF, permissions, mobile/dark-mode/keyboard use and query plans.
Reconcile a representative employee in Goals & KPIs against the same dashboard
scope; include unfinished periods, no records, zero results, empty support cohorts,
legacy lower-direction tags, template regeneration and more than one page.

Offline authoring checks run with the pinned Ruff wheel and isolated tests because
terminal GitHub/Forgejo networking is unavailable. Any SQL column-substitute or
extracted-method harness is LOCAL ONLY and is not claimed as full ERP/PostgreSQL
verification. Hosted CI and browser staging results must be recorded separately.

Rollback the code first; the unused nullable column can remain safely. Downgrading
that migration removes explicitly chosen directions, so export those settings
before an intentional schema downgrade. Do not erase all Person metadata. A roll
back to the earlier #580 code can reject new saved-view option fields; strip only
new display options in a reviewed recovery procedure, not unrelated preferences.
