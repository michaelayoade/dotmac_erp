# ERP deployment descriptor — adapter, not cutover

`deploy/product.toml` is ERP's `ProductDeploymentSpec.v1` — the typed
deployment descriptor defined by `dotmac-deployment-foundation`
(`dotmac_starter_mt:packages/dotmac-deployment-foundation`, ADR-0070
"Deployment is a stateless versioned foundation, not a module"). ERP is the
first full adopter because the source inventory
(`dotmac_starter_mt:docs/inventories/deployment-foundation-sources.md` § 5-6)
found it carries the most defects of the four Dotmac repositories audited.

## What this is

A single, typed statement of ERP's process roles, migration contract,
runtime materials, ingress, backup, telemetry and domain alerts — the input a
future renderer turns into a Compose file, an Nginx site and a deployment
plan, with no second, hand-maintained copy to drift from the descriptor.

## What this is NOT (yet)

- **Not rendered.** `dotmac-deployment-foundation` ships a `spec` module (the
  typed descriptor and its parser) and a `conformance` module (checks a
  product runs in its own CI) in this generation. It does not yet ship a
  Compose/Nginx renderer or a deployment engine. Nothing in this repository
  consumes `deploy/product.toml` at build or deploy time.
- **Not the live deployment path.** `scripts/deploy.sh`, `docker-compose.yml`,
  `Dockerfile`, `scripts/backup_erp_db.sh` and
  `scripts/bootstrap_database_roles.py` are all UNCHANGED by this adapter and
  remain exactly what runs in production. This adapter does not touch any of
  them.
- **Not exact-pinned.** `pyproject.toml`/`poetry.lock` do not depend on
  `dotmac-deployment-foundation` — it has not been published as a distribution
  yet. `tests/architecture/test_deployment_descriptor.py` guards on
  `pytest.importorskip("dotmac_deployment_foundation")` and skips rather than
  fails the build until that pin exists (per AGENTS.md rule 30: a
  release/adoption claim needs an authoritative external oracle, not a file
  present on `main`).

Retirement of the scripts above happens only after PROVEN parity — a separate,
later change, gated on the renderer existing and `render --check` passing in
ERP's own CI, per ADR-0070's "Consequences" section. This change adds the
descriptor alongside the existing path; it deletes nothing.

## Defects this descriptor's existence makes checkable

Referencing `dotmac_starter_mt:docs/inventories/deployment-foundation-sources.md`
§ 6's defect table (D1-D18):

| Defect | Status here |
|---|---|
| D2 — `/health` used as both the healthcheck and the deploy gate, and can never fail | **Fixed in the descriptor.** `[roles.health.live]` (`/health/live`) and `[roles.health.ready]` (`/health/ready`) are declared separately; `spec.py` refuses a role that points both at the same path. Not yet fixed in the RUNNING system — `docker-compose.yml`'s healthcheck and `scripts/deploy.sh`'s `HEALTH_URL` still hit plain `/health` until a renderer and a cutover exist. |
| D3 — a runtime role could hold the migration owner (superuser-shaped) credential | **Structurally prevented in the descriptor.** A role naming `MIGRATION_DATABASE_URL` is a parse-time refusal (`spec.py`'s `_validate_cross_field`), checked a second time by this repo's own architecture test. |
| D5 — floating `:latest` tag | **Structurally prevented.** `[image].reference` must be `...@sha256:<64 hex>`; a tag does not parse. |
| D1 — production bind-mounts of `static/`/`templates/` | **Declared as a target, not yet true.** `[ingress.static]` declares `static = "image"`; `docker-compose.yml` still bind-mounts today. |
| D4 — dev-unsafe `CSP_ALLOW_UNSAFE`/`OPENBAO_ALLOW_INSECURE` literals baked into the production Compose block | **Not addressed by this descriptor.** These are plain booleans, not secret-shaped materials, and `ProductDeploymentSpec.v1` has no general "never bake this literal" field outside `runtime_materials.names`. See the report for this being flagged as a possible spec.py gap rather than something fixed here. |
| D6 — root containers, boot-time `pip install` in `scripts/entrypoint-monitoring.sh` | **Not fixed; flagged loudly in `product.toml`'s comments.** The descriptor keeps the foundation's hardened security defaults (`read_only_root = true`, non-root UID) rather than quietly declaring a permissive exception to match today's actual container. |
| D7 — fixed `container_name` prevents blue/green | Not addressed — no renderer exists yet to omit it. |
| D8 — backup verification is a size check only (or, for ERP, no check at all) | **Declared as a target.** `[[backup.datasets]]` requires `verify = ["schema", "row_counts", "migration_heads"]`; `scripts/backup_erp_db.sh` performs neither today. |
| D18 — image retention is the one fail-open step | Unchanged; noted in a comment, not claimed as fixed by a field that doesn't exist for it. |

Two additional, ERP-specific findings surfaced while writing this descriptor
(not in the original inventory, both flagged in `product.toml`'s comments and
in the task report rather than fixed here):

- `worker`/`beat` run no HTTP server at all, yet `spec.py`'s cross-field rule
  requires every non-zero-replica background role to declare
  `[roles.health.live]` (a path+port, i.e. an HTTP probe). The declared ports
  (9100/9101) are placeholders nothing currently listens on.
- `scripts/entrypoint-monitoring.sh`'s boot-time `pip install` is incompatible
  with the hardened `read_only_root = true` default this descriptor keeps.

## How to render (once the renderer exists)

```
dotmac-deploy render deploy/product.toml
dotmac-deploy render --check deploy/product.toml   # CI: rendered assets == committed assets
```

Neither command exists in this repository or in `dotmac-deployment-foundation`
yet. Today, the only thing to run against this file is the parser and the
conformance kit:

```
PYTHONPATH=<path-to>/dotmac-deployment-foundation/src python3 -c "
from dotmac_deployment_foundation.spec import ProductDeploymentSpec as S
from dotmac_deployment_foundation.conformance import check_all
s = S.load('deploy/product.toml')
print('OK', s.startup_order)
print(check_all(s) or 'conformance clean')
"
```

## `scripts/deploy.sh` is still the live path

Until a renderer exists, this descriptor is validated but unrendered and
unadopted. Every real ERP deployment goes through `scripts/deploy.sh` exactly
as before. Do not point any host, CI job, or operator runbook at
`deploy/product.toml` as an executable artifact — it is not one yet.
