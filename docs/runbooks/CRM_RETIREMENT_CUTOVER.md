# CRM runtime-retirement cutover

This retirement is a one-switch, destructive application and database change.
It removes the CRM routes, tasks, settings, service keys, live mappings, and
provider-specific columns. Historical records are sealed in
`archive.retired_crm_records`; external ticket projections are sealed in
`archive.retired_external_ticket`. The migrations then drop the live CRM
mapping surface and have no automatic downgrade.

This pull request does not deploy production.

## Deployment rule

Do not use the normal `scripts/deploy.sh` rolling deployment for this change.
A rolling deployment can run old application, worker, or beat processes against
the retired schema and can accept CRM traffic during the authority switch.

A clean ERP installation is the preferred production path. Admit only the
approved opening state, reconciled masters, explicitly selected live items,
and continuity identities through their typed import owners. Keep the legacy
ERP read-only as historical authority; do not copy its CRM runtime into the
clean instance.

## Clean-install path

1. Complete and approve the clean-instance accounting and operational admission
   plans.
2. Build the new database from migration heads and verify the live catalog,
   RLS inventory, and archive protections.
3. Import only approved admission classes through typed, idempotent adapters.
4. Reconcile source counts, fingerprints, balances, open-item identities, and
   operational references before enabling ingress.
5. Start only the new app, worker, and beat image, then run the verification
   checklist below.

## Legacy-database path

Use this path only with a separately approved production change record and a
named target host.

1. Take and verify a recoverable database backup. Record immutable backup
   coordinates and the restore proof without recording credentials.
2. Enter a maintenance window and stop external ingress.
3. Stop every old app, worker, and beat process. Prove none remains connected
   or consuming queues before migrating.
4. Run the CRM retirement migration once with the approved migration role.
5. Verify the archive counts and fingerprints against the pre-migration
   source counts, and verify that the live CRM mapping relation and retired
   provider-specific columns are absent.
6. Start only the new image's app, worker, and beat processes.
7. Complete the verification checklist before reopening ingress.

Do not run an automatic database downgrade. If a gate fails after migration,
keep ingress closed, stop the new processes, and choose either a reviewed
forward repair or an explicit restore from the verified backup. Restoring the
backup also restores the old schema and therefore requires the matching old
application processes; it is a full change decision, not a deploy-script
fallback.

## Verification checklist

- Application health and migration heads are exact for the approved image.
- No `/crm`, `/sync/crm`, or admin CRM route is present in the live OpenAPI and
  route catalog.
- No CRM Celery task is registered or scheduled, and no old worker or beat
  process is running.
- No CRM setting, environment reader, dependency-health row, API-key scope,
  provider client, service key, or active integration binding remains.
- Both `archive.retired_crm_records` and
  `archive.retired_external_ticket` have RLS enabled and forced, their policies
  exist, and online application roles have no privileges on them. The separate
  platform archive `archive.retired_crm_scheduled_tasks` has no tenant column
  and no RLS; `PUBLIC`, `app_user`, and `platform_api` have no schema, table,
  sequence, or column privileges on it.
- Canonical `/sync/sub/*` operations authenticate only with explicit `sub:*`
  scopes and use provider-neutral source identities.
- Finance and operational smoke checks pass, with no unreviewed data rewrite or
  CRM archive replay.

Reopen ingress only after every item is evidenced and the change owner accepts
the result.
