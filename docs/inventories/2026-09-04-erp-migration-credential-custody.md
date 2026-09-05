# ERP migration credential — custody inventory

- **Date:** 2026-09-04
- **Subject:** the `app_admin` DSN that `deploy/product.toml` declares as
  `[migration].owner_material = "MIGRATION_DATABASE_URL"`
- **Status:** unratified. This is an inventory and a proposal, authored by its
  own author, and a Dotmac record is not self-certified. The decision it serves
  is `docs/adr/0011-the-app-admin-migration-credential-has-one-custody-pointer.md`
  (`Proposed`).
- **Reads performed:** repository files only. No database, no host, no SSH, no
  OpenBao. **No secret value was read, and none appears here.**

## 0. Why this document exists

Two facts, both verified in this checkout, combine into a deployment block:

1. `deploy/product.toml:386` declares `owner_material = "MIGRATION_DATABASE_URL"`,
   and `deploy/product.toml:70` deliberately EXCLUDES that name from
   `[runtime_materials]`. `app_admin` is the migration executor and no runtime
   role holds its material.
2. `scripts/deploy.sh:127-131` exits `2` when `MIGRATION_DATABASE_URL` is unset,
   with the message *"Alembic never uses DATABASE_URL"*. There is no fallback.

So every deployment candidate — a privilege manifest, a bug fix, a typo — runs
through one credential. The latest production observation is that this
credential **fails to authenticate from the one-shot migration container**. The
executor is therefore unavailable, and the blast radius is *all deployments*,
not one change.

What the repository never wrote down is where that credential lives, who may
change it, or how a change is proven. Those are the gaps below.

## 1. What is verified as already true

| # | claim | evidence in this checkout |
|---|---|---|
| V1 | `app_admin` is the migration executor | `app/migration_database_roles.py:MIGRATION_EXECUTOR = "app_admin"` |
| V2 | `app_admin` is `BYPASSRLS`, `NOSUPERUSER` | `app/migration_database_roles.py:ROLE_CONTRACT["app_admin"] = (True, False)` — `(rolbypassrls, rolsuper)` |
| V3 | The descriptor names the owner material | `deploy/product.toml:386` |
| V4 | No runtime role may hold it | `deploy/product.toml:70` comment; foundation `spec.py` `_validate_cross_field` refuses at load; `tests/architecture/test_deployment_descriptor.py::test_no_role_holds_the_migration_owner_material` |
| V5 | The deploy path requires it and refuses fallback | `scripts/deploy.sh:127-131` |
| V6 | Runtime services receive only `DATABASE_URL` | `deploy/rendered/docker-compose.yml` — `MIGRATION_DATABASE_URL` appears at line 63 under `services.migrate` **and nowhere else in that file** |
| V7 | The one-shot containers get it explicitly | `scripts/deploy.sh:299, 485, 537` — `docker compose run --rm -e MIGRATION_DATABASE_URL app …` |
| V8 | ERP already has a custody-pointer precedent | `docs/runbooks/database-restore.md:93` names `secret/dotmac/postgres/erp-shared-primary/postgres` as the approved source for the cluster superuser |
| V9 | The `openbao://` reference grammar exists and resolves | `app/services/secrets.py:is_openbao_ref`; `README.md:300-303`; `openbao://<mount>/data/<path>#<field>` — a placeholder GRAMMAR, not material: `<mount>`, `<path>` and `<field>` are literal angle-bracket placeholders and no credential, path or value is named. detect-secrets reads the URI shape as a secret.  # pragma: allowlist secret |

## 2. The six gaps this inventory closes

| # | gap | closed by |
|---|---|---|
| G1 | No canonical OpenBao path and field | § 3 below |
| G2 | "Approved secret source" is prose, not a resolvable pointer | § 3 — `CANONICAL_POINTER.reference()` resolves |
| G3 | No accepted authorization identifies who may reconcile or rotate | § 4 |
| G4 | No authorization binds the operation to environment, database, role and pointer | `ReconciliationAuthorization` — § 5 |
| G5 | No proof from the actual one-shot migration container | `VerificationOutcome` — § 6 |
| G6 | No reconciliation receipt recording which version was installed and verified | `ReconciliationReceipt` — § 7 |

## 3. G1/G2 — the canonical pointer (POINTER ONLY)

```
mount : secret
path  : dotmac/postgres/erp-shared-primary/app_admin
field : MIGRATION_DATABASE_URL
```

Resolvable reference, in the grammar `app/services/secrets.py` already parses:

```
openbao://secret/data/dotmac/postgres/erp-shared-primary/app_admin#MIGRATION_DATABASE_URL
```

Rotation staging path, same mount and field:

```
path  : dotmac/postgres/erp-shared-primary/app_admin_candidate
```

Machine-readable owner: `app/migration_credential_custody.py`
(`CANONICAL_POINTER`, `CANDIDATE_POINTER`). This document and that module state
the same three coordinates; the module is what a reconciler would import.

**Why this shape.** It is a sibling of the pointer ERP already wrote down (V8),
under the same `<mount>/dotmac/postgres/<cluster>/<role>` grammar, on the same
cluster. Inventing a second grammar for the second role on the same cluster is
how a store becomes unnavigable. The field is named for the environment variable
the material is installed as, so a reader of the store can tell what a value is
*for* without dereferencing it — and `deploy/product.toml`'s `owner_material` is
the same string, so the descriptor and the store agree by construction.

**What this section is not.** It is not a claim that anything exists at that
path today. Nothing here contacted OpenBao. Creating the path, and placing
working material at it, is the first act of the reconciliation this document
authorizes — and it is human-gated.

## 4. G3 — who may do what

Copied from the ruling this slice was given, so the record is self-contained.

| concern | owner |
|---|---|
| **Custody** | OpenBao, under Michael's existing sole named administrative authority |
| **Decision / request** | ERP Deployment/Operations |
| **Authorization** | **Michael approves the exact production reconciliation** — per run, not a standing grant. This holds until a separately approved, scoped service principal exists; that principal does not exist today and this document does not create one. |
| **Execution** | A **one-shot privileged credential reconciler**. Explicitly **not** ERP runtime (app/worker/beat), and explicitly **not** `scripts/deploy.sh` — the deploy path is a *consumer* of this credential and must never be able to change it. |
| **Verification** | The **real one-shot migration container**, using the canonical pointer |
| **Evidence** | Secret version, pointer, database identity and result. **Never material.** |

### Forbidden as the application credential — each named

None of the following may ever be `MIGRATION_DATABASE_URL`, and none may ever be
what a runtime service connects as. Named individually because a category
("don't use privileged credentials") is not checkable and has already failed:

1. **The OpenBao root token.** It is the custody authority itself. Using it as
   an application credential collapses the store's root of trust into the thing
   the store is protecting, and it cannot be scoped, rotated per-consumer, or
   revoked without breaking custody.
2. **`dotmac_erp_app`.** The legacy least-privilege login. It is being retired
   into `app_user` by ERP Deployment/Operations' own separate migration
   (`docs/adr/0010-…`, branch `docs/adr-erp-runtime-identity-ownership`). A
   credential mid-retirement is not a credential to build a new dependency on.
3. **`app_user`.** The runtime identity. It is `NOBYPASSRLS`/`NOSUPERUSER` by
   contract (`ROLE_CONTRACT`) precisely so tenant isolation binds it, and it
   cannot execute the DDL migrations require. `scripts/deploy.sh:559-566` makes
   this explicit: the runtime admission step deliberately withholds
   `-e MIGRATION_DATABASE_URL` so it verifies the credential the app actually
   serves on, not the one that bypasses RLS.
4. **An `.env`-stored password.** `.env` is read by `env_file:` for app, worker
   and beat (§ 8). A credential placed there is handed to every runtime process
   whether or not anyone intended it — and `.env.example` already asks, in
   prose, that this one be left blank. See § 8: the census says it is not.
5. **A manually pasted DSN.** It has no version, no custody, no expiry and no
   receipt. Nothing can later say which value was installed, so nothing can
   later reconcile or revoke it, and a rotation cannot know what it replaced.
6. **Migration-role material in app, worker or Beat.** `app_admin` is
   `BYPASSRLS`; a runtime process holding it can read and write every tenant's
   rows and can create, alter or drop any table for the life of the deployment.
   `deploy/product.toml:70` names this as inventory finding D3.

## 5. G4 — the typed authorization

`ReconciliationAuthorization` (`app/migration_credential_custody.py`). Every
field is a *binding*; an authorization missing any of them is reusable against a
target it was never granted for.

| field | type | why it is a binding |
|---|---|---|
| `environment` | `str` | An approval for staging must not execute against production |
| `database` | `DatabaseIdentity` (host, port, dbname) | `bootstrap_database_roles.py --verify-only` asserts `current_user` and role posture but **never which database it reached**. A run pointed at the wrong database passes every check that script makes. This field is what closes that. |
| `role` | `str` | Refused at construction unless it equals `MIGRATION_EXECUTOR` |
| `pointer` | `CustodyPointer` | Binds to path AND field. A right path with a wrong field installs the wrong value. |
| `expected_current_version` | `int` | The approval refers to the state of custody **at the moment of approval**. If custody moved since, the approval no longer describes what it approved, and the run refuses instead of overwriting a concurrent rotation. |
| `operation` | `Operation` | `ROTATE` / `REINSTALL` / `VERIFY`. One authorization permits one operation; a `VERIFY` approval can never install. |
| `authorization_reference` | `str` | Where Michael's approval is recorded. Refused if empty — an unreferenced approval is not one. |
| `expires_at` | `datetime` (tz-aware) | An approval that never expires is a standing grant, which is exactly what the ruling withholds until a scoped service principal exists. |

`authorization_refusals(authorization, observed, now)` compares the
authorization against what the reconciler **actually observed** and returns
**every** mismatch, not the first. An empty tuple is the only state that permits
a change. This is step 4 of the slice: refuse if target database, role, pointer,
version or authorization differs.

## 6. G5 — proof from the actual migration container

Steps 5 and 6 are one artifact, `VerificationOutcome`, and it is populated only
from a run inside the real one-shot container — the same invocation shape
`scripts/deploy.sh` uses at lines 485 and 537, against the same image digest.

Commands (step 5), both from the same one-shot context:

```
docker compose run --rm -e MIGRATION_DATABASE_URL app \
    python scripts/bootstrap_database_roles.py --verify-only
docker compose run --rm -e MIGRATION_DATABASE_URL app \
    alembic current
```

Assertions (step 6):

| assertion | source | already covered? |
|---|---|---|
| `current_user = app_admin` | `SELECT current_user` | **Yes** — `migration_executor_violations` |
| `NOSUPERUSER` | `pg_roles.rolsuper` | **Yes** — `ROLE_CONTRACT` |
| `BYPASSRLS` | `pg_roles.rolbypassrls` | **Yes** — `ROLE_CONTRACT` |
| **correct database** | `current_database()` compared to the authorized `dbname`, and host/port from the connection | **NO.** `MIGRATION_OWNERSHIP_SQL` calls `current_database()` only to check *ownership*; nothing compares the database's **identity** to what was authorized. This is a real gap in the existing preflight, found while writing this document. |

`VerificationOutcome` additionally records the **image digest** the checks ran
in, and refuses a digest that is not `sha256:`-prefixed. A verification that
cannot say which bytes produced it is a claim about an unidentified program.

## 7. G6 — the reconciliation receipt

`ReconciliationReceipt`. Exact contents:

| field | example shape |
|---|---|
| `receipt_id` | opaque identifier |
| `recorded_at` | tz-aware UTC |
| `executed_by` | the operator identity that ran the reconciler |
| `authorization_reference` | the approval this run executed |
| `environment` | `production` |
| `database` | host, port, dbname — **no user, no password** |
| `role` | `app_admin` |
| `pointer` / `candidate_pointer` | mount, path, field |
| `predecessor_version` | KV version that was current before |
| `installed_version` | KV version staged and installed on the role |
| `promoted_version` | KV version written to the canonical path, or `None` |
| `predecessor_retired` | bool |
| `operation` | `rotate` / `reinstall` / `verify` |
| `verification` | the `VerificationOutcome` above |
| `outcome` | one of four, § 9 |

### What a receipt must NEVER contain

- The DSN, in any form — including one with the password elided, because the
  elision is done by hand and the hand is what fails.
- Any password, token or key.
- **A hash or digest of the material.** A digest is an offline guessing oracle:
  a receipt carrying one has not avoided disclosing the credential, it has
  disclosed a slower version of it. `_looks_like_material` treats it as material.
- A connection URL of any kind carrying userinfo.
- Anything read out of the store other than the *version number*.

Enforced at construction: `ReconciliationReceipt.__post_init__` scans every one
of its own string fields and raises `CustodyError` on a material-shaped value.
`receipt_disclosure_refusals()` re-applies the same check to a serialized
receipt, for the moment it becomes JSON or a ticket comment and stops being a
typed object.

The type also encodes the ordering: `predecessor_retired=True` is only
describable when `outcome is VERIFIED_AND_PROMOTED` and `promoted_version` is
set. A receipt describing a run that retired the predecessor before verifying
cannot be constructed.

## 8. G7 — step 8's negative proof, and the live defect it uncovers

**The property:** app, worker and Beat cannot see `MIGRATION_DATABASE_URL`.

It is established in three layers, and only the first exists today.

### Layer 1 — the descriptor. **Enforced today.**

`deploy/product.toml` excludes the name from `[runtime_materials]` and declares
it as `[migration].owner_material`. The foundation's `spec.py`
`_validate_cross_field` refuses **at load time** any role naming the owner
material, and `tests/architecture/test_deployment_descriptor.py::test_no_role_holds_the_migration_owner_material`
asserts the property by name so a failure is attributed to *this* property.

Its sensitivity proof already exists and is not being invented here:
`test_a_role_given_the_owner_material_is_refused` appends a role carrying
`materials = ["MIGRATION_DATABASE_URL"]` **to the real descriptor text** and
asserts the raised error names the variable. Same document, one planted defect.

### Layer 2 — the rendered artifact. **Not enforced. Specified here.**

The descriptor is intent; `deploy/rendered/docker-compose.yml` is what
`docker compose` reads. `render --check` is a byte comparison of the rendered
file against the descriptor, which proves rendering fidelity — not that the
artifact keeps the variable to one service.

Proposed gate: parse the rendered file, build
`{service: set(environment names)}`, and pass it to
`environment_leak_refusals()`, which returns a finding **naming each offending
service**. Covers `environment`, `env_file`, `command`, `entrypoint` and any
`x-` extension carrying the name.

**Sensitivity proof, in the same shape layer 1 uses.** Take the real rendered
text, insert `MIGRATION_DATABASE_URL: "${MIGRATION_DATABASE_URL}"` into
`services.app.environment` in a copy, run the same checker, and assert the
finding **names `app`**. Naming the service is the load-bearing half: a checker
that merely returned "non-empty" could be firing for an unrelated reason and
would pass its own proof while being blind to the real thing.

### Layer 3 — the running host. **UNMONITORED, not exempt — and currently violated.**

This is where the property is actually false, and no repository-local check can
see it. ERP's own checked-in evidence, composed:

- `docker-compose.yml` — the **legacy** compose that the entrypoint census
  records as what actually runs in production — gives `env_file: - .env` to
  `app` (lines 61-62), `worker` (150-151) and `beat` (180-181).
- `docs/inventories/2026-08-30-erp-deployment-entrypoint-census.md`, row **E2**,
  records `MIGRATION_DATABASE_URL` as present **in that very `/root/dotmac/.env`**,
  describing it as "the `app_admin` DSN".
- `.env.example:13-17` ships the key with a comment instructing that it be
  "left blank in the app env file so runtime processes never hold migration
  authority". That instruction is **prose in a template**. Nothing reads it,
  nothing checks it, and the census records the host's `.env` holding the DSN
  regardless. This is the cleanest illustration in the repository of the
  difference this document is about: a stated intention is not a monitored one.

`env_file` loads every key in the file into the container. Composed, these say
**app, worker and Beat see `MIGRATION_DATABASE_URL` in production today** — a
`BYPASSRLS` DDL credential in three long-running network-facing processes. Step
8 is not a formality; it names a live defect.

**Why no repository-local check can establish layer 3.** The value arrives from
a host file this repository cannot read, into containers this repository cannot
inspect. Per the external-oracle rule, a claim about a running host needs an
oracle carrying immutable coordinates. The oracle here is an operator-run
inspection recording **names only**:

```
docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' \
    dotmac_erp_app dotmac_erp_worker dotmac_erp_beat | cut -d= -f1
```

`cut -d= -f1` is not cosmetic — it is the whole difference between an
inspection and a disclosure. The recorded evidence is the container IDs, the
image digest inspected, the timestamp, the operator, and the variable **names**
found. Until such a record exists, **layer 3 is unmonitored**. It is not exempt:
an exemption states an enforceable premise, and there is no premise available
here — the repository has affirmative evidence the property is violated.

**The remediation this implies** (named, not performed, and out of this slice's
scope): remove `MIGRATION_DATABASE_URL` from `/root/dotmac/.env`, remove the key
from `.env.example`, and pass it only as `-e MIGRATION_DATABASE_URL` on the
one-shot containers — which is already exactly what `scripts/deploy.sh` does at
lines 299, 485 and 537. **The variable never needed to be in `.env` at all.**

## 9. Enforceable today vs. stated review discipline

Per starter ADR-0018. Nothing proposed in this document is built, so nothing
proposed in it is enforced.

| region | status |
|---|---|
| No runtime **role in the descriptor** holds the owner material | **Enforced** — load-time refusal + named test + existing sensitivity proof |
| `deploy.sh` refuses to run without `MIGRATION_DATABASE_URL`, no fallback | **Enforced** — `scripts/deploy.sh:127-131` |
| `app_admin` posture and `current_user` at migration time | **Enforced** — `bootstrap_database_roles.py --verify-only` |
| No credential literal in tracked Python | **Enforced** — `tests/architecture/test_no_committed_credentials.py`, with its own sensitivity proof |
| The canonical pointer is the one used by anything | **Unmonitored.** No gate binds `CUSTODY_*` to `deploy/product.toml`, to `deploy.sh`, or to any consumer. |
| **Which database** the migration container reached | **Unmonitored.** `--verify-only` never compares database identity to what was authorized (§ 6). |
| The rendered Compose artifact keeps the owner material to `migrate` | **Unmonitored.** Layer 2 is specified, not built. |
| The running app/worker/beat cannot see the owner material | **Unmonitored, and evidenced as violated** (§ 8, layer 3). |
| An authorization exists, is bound, and has not expired | **Unmonitored.** The type exists; no reconciler consumes it, because no reconciler exists. |
| A receipt is written for every reconciliation | **Unmonitored.** Same reason. |
| A receipt carries no material | **Partly structural.** `__post_init__` refuses at construction — but nothing today constructs one, and there is no test proving the refusal fires. Treat as unmonitored until a sensitivity proof exists. |

## 10. What this document does not close

- **The production observation itself.** That `app_admin` authentication fails
  from the migration container is reported, not reproduced here — no database
  was contacted. Whether the material is wrong, absent, or the role's password
  was never set is unresolved and is the reconciler's first finding.
- **Whether anything exists at the declared path.** OpenBao was not contacted.
- **The scoped service principal.** Until one is approved, every production
  reconciliation needs Michael's per-run approval. This is a deliberate
  bottleneck, and it is a real one.
- **`docs/adr/0010`'s runtime-identity migration** (`dotmac_erp_app` →
  `app_user`) is ERP Deployment/Operations' separate work on
  `docs/adr-erp-runtime-identity-ownership`. This slice does not touch it, and
  the two must not be sequenced into each other: rotating the migration
  credential and changing the runtime identity in one movement would leave no
  way to attribute a failure to either.
