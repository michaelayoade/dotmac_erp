# Workforce provisioning and KPI reconciliation

## Scope

PR #576 was reconciled with the KPI enhancements merged through PR #582.
The reconciliation commit is `4d4aa3b31f9f9808375c238ec756a3623ca0df93`,
with parents `ce70521075143452f797c795696f323413aa8264` (workforce) and
`ce616818f8f96c50a31929f64d5c55df99e4d18a` (main).

The original workforce and KPI application changes are retained. No CI job,
row-level security policy, runtime database grant, or provisioning stage was
removed to make the checks pass.

## Repairs

### Migration graph

`20260915_merge_workforce_kpi` joins `20260915_workforce_monitor` and
`20260915_kpi_direction`. Both existing migrations remain unchanged and both
must execute. The host lineage remains single-headed; the independently
composed module heads remain separate. The product descriptor and host-head
regression test name the new merge revision.

### Static assets

The combined source was rebuilt with `npm ci` and the locked CSS toolchain.
The descriptor now records 71 files and the static-tree digest:

```text
sha256:e462e28182181f93c339234c261bcb9c5095e87ed988f596857d9b3d445e3734
```

The integrity checks remain enabled. Regenerate the descriptor from verified
build output whenever the static tree changes; do not replace the check with
a bypass.

### PostgreSQL test fixtures

The original failed integration run contained nine `cannot adapt type 'dict'`
failures in `test_selfcare_mapping_ownership.py`. A previous repair changed
only a copied table's DDL, leaving the actual Employee mapper with the
SQLite-patched PostgreSQL variant.

The fixture now restores native UUID/JSONB types recursively, including
`JSON().with_variant(...)`, while retaining the original type objects for
teardown. Regression coverage verifies the ORM binding and a nested
workforce-state round-trip against PostgreSQL. The fixture supplies a scoped
staff-sync settings object rather than mutating the frozen global settings.

The MFA permission-denied messages in the original log are expected negative
security probes, not the failing integration assertions. They do not justify
additional runtime database grants.

### Tenant query binding

Running the ownership tests in isolation exposed a pre-existing organization
filter caching defect. A callable default argument retained the first tenant
when SQLAlchemy reused the criterion. The listener now binds the current
organization directly through the target model's column, avoiding shared
lambda binding/type state. Existing bypass controls and missing-context
rejection remain unchanged.

Actual-row regression tests alternate tenant sessions for entity, column,
alias and primary-key queries and verify rejection of an unprimed session.
They detect the original implementation and pass with the repair.

## Validation evidence

The isolated GitHub validation run `35016386100` used Python 3.12, Poetry
2.4.1, PostgreSQL 16, Node 20 and the repository's locked dependencies.

- Focused workforce, KPI, migration, static and organization-filter tests:
  148 passed, with one pre-existing skipped seeded-invoice integration test.
- PostgreSQL ownership tests: 10 passed, no failures or skips.
- KPI JavaScript chart tests: 2 passed.
- Repository lint and formatting checks passed.
- The locked stylesheet rebuild and recorded static-tree digest matched.

The validation artifact contains `focused.xml`, `postgresql.xml`, `commit.txt`
and `final.patch`. This focused validation is not a claim that the subsequent
full PR CI run has completed.

## Deployment verification

Deploy only after the normal PR checks and review requirements are satisfied.
Apply the composed migrations before workers execute the new workforce task
code. Verify a controlled employee through Mailcow, activation email,
Nextcloud, Selfcare and Talk, including retry/resend behavior. Verify the KPI
dashboard and its charts with the intended measurement direction. The
reconciliation itself neither merges the PR nor changes a running deployment.
