# Runbook — ERP database backup and restore

Until 2026-08-30 ERP had no restore procedure. It had a backup producer that
ran `pg_dump` on one database, and `scripts/restore_from_backup.py`, which
extracts `COPY` blocks and skips every `CREATE`, `ALTER`, `DROP`, `GRANT` and
`REVOKE`. Roles, `app_admin`, the least-privilege `dotmac_erp_app` login and
every GRANT the RLS policies depend on were captured by no artifact.

A restored cluster with correct rows and no roles cannot serve. That is the
failure mode this runbook exists to prevent, and the verification steps below
assert against it specifically.

Measured evidence:
`docs/inventories/2026-08-30-erp-production-infrastructure-preflight.md` § 6.

## What a backup consists of

Two artifacts per run. **They are useless apart.**

| artifact | contains |
|---|---|
| `dotmac_erp_<ts>.globals.sql.gz` | roles, role memberships, cluster-level GRANTs (`pg_dumpall --globals-only`) |
| `dotmac_erp_<ts>.dump` | the database, `pg_dump` custom format (`-Fc`) |

Custom format is used because it is the only format `pg_restore` can inspect
without executing. `pg_restore --list` walks the archive's table of contents
and fails on a truncated or corrupt file, which a byte count cannot detect.
`scripts/backup_erp_db.sh` runs that check before uploading and refuses a
globals dump containing no `CREATE ROLE`.

## Taking a backup

```
./scripts/backup_erp_db.sh
```

Reads `POSTGRES_*` from `.env`. It **refuses** if a key is duplicated there,
because Compose reads the last occurrence while a naive `sed` reads the first —
a live disagreement in production `.env` today. Deduplicate rather than guess.

Retention keeps the last `KEEP_LAST` **runs** (default 5), removing every file
belonging to an expired timestamp. It counts runs, not files, because each run
now writes two artifacts.

`SKIP_UPLOAD=1` skips rclone (used by the CI rehearsal).

## Restoring

**Order is not optional. Globals first, then the database.** `pg_restore`
emits `ALTER ... OWNER TO` and `GRANT ... TO`; if the role does not exist those
statements fail, and with `--exit-on-error` the restore stops rather than
producing a database whose ownership is quietly wrong.

```
./scripts/restore_erp_db.sh \
    --globals /var/backups/db/dotmac_erp_<ts>.globals.sql.gz \
    --dump    /var/backups/db/dotmac_erp_<ts>.dump \
    --target-container <disposable postgres container> \
    --target-db dotmac_erp_restore \
    --require-roles app_admin,dotmac_erp_app
```

The script:

1. verifies the archive parses **before touching the target**;
2. restores globals into the cluster;
3. drops and recreates the target database;
4. `pg_restore --exit-on-error`;
5. **asserts** each `--require-roles` role exists, that tables were created,
   and reports the Alembic heads found.

It exits non-zero if any required role is missing. A restore that produced rows
but no roles is reported as **incomplete**, not as success.

### It refuses production

`--target-container dotmac_pg_local` or `--target-db dotmac_erp` is refused.
Restoring onto the live cluster is an outage, and a rehearsal script is exactly
where that mistake gets made. A genuine disaster recovery onto the real name
requires setting `ALLOW_PRODUCTION_NAME=1` deliberately.

## Rehearsal status — read this before claiming the backup works

| rehearsal | status |
|---|---|
| **Mechanism**, against a freshly migrated ERP cluster with real bootstrapped roles, restored into a second disposable PostgreSQL container, with a sensitivity proof that a roleless backup is rejected | **runs in CI** on every build, in the `Docker Build & Health Check` job |
| **Production bytes** — restoring an actual production artifact (~8 GB) onto a disposable host | **NOT DONE.** No disposable PostgreSQL target exists with the capacity and the clearance to hold production data. `85.190.246.211` is leased exclusively to Deployment Foundation and must not be used. This is an open blocker. |

The CI rehearsal proves the *mechanism* is correct — that globals are captured,
that the archive parses, that a restore reproduces roles, privileges and data,
and that the verification actually bites. It does **not** prove any particular
production artifact is restorable. Those are different claims and only the
first is currently evidenced.

## The sensitivity proof

CI strips `CREATE ROLE`/`ALTER ROLE` out of the globals artifact and re-runs the
restore, reproducing exactly the defect that was live in production. That run
**must fail**. If it ever succeeds, the role assertion has stopped detecting
anything and the gate is decorative — the step fails loudly in that case rather
than passing quietly.

## Disaster recovery outline

1. Provision a PostgreSQL 16 instance (production uses `postgis/postgis:16-3.4-alpine`;
   PostGIS is required — the schema uses spatial types).
2. Restore globals, then the database, per the command above with
   `ALLOW_PRODUCTION_NAME=1`.
3. Confirm `app_admin` and `dotmac_erp_app` exist and that
   `dotmac_erp_app` is `NOSUPERUSER`/`NOBYPASSRLS`.
4. Confirm the Alembic heads match the seven the deployed image expects.
5. Only then point the application at it.

Step 3 is the one that was impossible before this change.
