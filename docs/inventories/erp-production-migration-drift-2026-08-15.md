# ERP production-to-migration catalog drift — 2026-08-15

## Purpose

This is a deployment-drift report. It is not the tenant-disposition baseline
for ERP's code.

The first version of PR #300 incorrectly generated
`tests/integration/tenant_table_inventory.tsv` from production and then asserted
that snapshot against a database built from migrations. Production was behind
the repository, so the test pinned drift and could never pass in CI.

The two source artifacts are now deliberately separate:

- `erp-production-catalog-2026-08-15.tsv` is the read-only production snapshot;
- `tests/integration/tenant_table_inventory.tsv` is generated from a clean
  PostgreSQL database after `alembic upgrade heads`, including composed module
  lineages, and is the enforced design baseline.

## Measured states

| Fact | Production snapshot | Fully migrated design |
|---|---:|---:|
| Alembic state | `20260808_open_setting_domain` | `20260815_academy_course_projection` + `fi_0001_stored_files` |
| Tables | 420 | 418 |
| Direct tenant tables | 311 (`organization_id` only) | 310 (`organization_id` or module `tenant_id`) |
| Database-enforced inherited paths | 6 | 85 |
| Explicit platform catalogs | not classified | 3 |
| Unclassified tables | 103 | 20 |
| RLS enabled | 16 | 158 |
| RLS forced | 87 | 88 |
| Tables with a `should_bypass_rls()` policy | 16 | 103 |
| PostgreSQL foreign keys | 188 across 65 tables | 1,130 across 368 tables |
| Tables with no PostgreSQL foreign key | 355 | 50 |

The 1,279 ORM-declared `ForeignKey` relationships remain useful application
intent, but only the 1,130 constraints installed by migrations are database
evidence. Tenant inheritance is claimable only through that enforced catalog.

## Tables present only after full migration

- `mod_files.platform_stored_files`
- `mod_files.stored_files`
- `public._migration_tz_affected`
- `public.tenant_domains`
- `public.tenants`
- `support.ticket_notification`
- `tax.control_evidence`

`public._migration_tz_affected` is retained by a migration despite its helper
name. The design baseline records it rather than silently treating the name as
proof that the table is temporary; retirement needs its own usage and data
disposition.

## Tables present only in production

None of these nine tables exists in the migration-defined catalog:

- `fa.asset_accum_dep_backfill_backup_20260522`
- `fa.asset_duplicate_cleanup_backup_20260520`
- `fa.asset_duplicate_cleanup_backup_20260520_part1`
- `fa.asset_gl_recon_backup_20260506`
- `fa.asset_serial_placeholder_backup_20260520`
- `public._clean_sweep_2025_audit`
- `public.tmp_ap_inventory_reclass_backup`
- `public.tmp_inv_receipt_1300_fix_backup`
- `public.tmp_lenovo_return_fix_backup`

They are operational production drift, not exceptions in the migrated-schema
gate. Their row counts, restore value, sensitivity and deletion/archival outcome
must be established before the `app_user` cutover.

## Programme corrections

1. PR #300 enforces the fully migrated design and preserves this production
   snapshot only as drift evidence.
2. PR #301's claim that every runtime `app.bypass_rls` writer is a database-layer
   no-op is withdrawn. That was true of the stale production catalog and false
   of ERP's migrated design, where 103 tables have dependent policies.
3. The dotmac-files schema is not present in production yet. Its migration must
   remain fail-closed until the privileged role bootstrap, ownership cutover and
   provider revisions are verified in the deployment sequence.
4. The production runtime connection remains a least-privilege and deployment
   debt: a superuser connection masks RLS behavior and cannot be used as
   isolation evidence.
