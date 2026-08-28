# ERP deployment descriptor — released adapter, not cutover

`deploy/product.toml` is ERP's `ProductDeploymentSpec.v1`, implemented by the
published `dotmac-deployment-foundation==0.2.0a1` facility (Starter ADR-0070).
The package is exact-pinned as a dev dependency from Dotmac's private Forgejo
index. Its annotated release tag peels to Starter commit
`ac21c9ae382ac866ec8f2ab21e5970e1ac8cc844`; ERP's reusable conformance workflow
is pinned to that same immutable source revision.

The descriptor and `app/product_assembly.py` share the product identity
`dotmac-erp`. The canonical `deploy/product-manifest.json` binds the exact
composed module versions and persistence-plane selections, and the descriptor
pins the digest of those bytes. The published CLI deterministically renders:

- `deploy/rendered/docker-compose.yml`;
- `deploy/rendered/nginx/erp.dotmac.io.conf`;
- `deploy/rendered/otel-collector.yaml`;
- `deploy/rendered/alerts.rules.yml`.

## What CI proves

The normal CI workflow checks the canonical product manifest, builds the image
once, runs migrations and health checks against that image, and only on a
successful protected-main push transfers those same bytes to the publication
job. The publisher records a non-secret `image-release.json` containing the
source SHA and registry digest; it has no rebuild step.

`.github/workflows/deployment-conformance.yml` installs the exact facility
release with the repository's read-only `FORGEJO_READ_TOKEN`. It validates the
descriptor, runs the conformance kit, checks rendered bytes, and asks a real
Docker Compose engine to parse the rendered project. The architecture tests
also refuse a wrong package pin, mutable workflow coordinate, product identity
drift, product-manifest drift, Alembic-head drift, migration-owner material in
a runtime role, or an unrecorded conformance finding.

Run the same adapter checks with:

```bash
poetry run dotmac-deploy -f deploy/product.toml validate
poetry run dotmac-deploy -f deploy/product.toml render --check -o deploy/rendered
poetry run dotmac-deploy -f deploy/product.toml plan
poetry run dotmac-deploy -f deploy/product.toml preflight
poetry run dotmac-deploy -f deploy/product.toml backup
poetry run pytest tests/architecture/test_deployment_descriptor.py -q
```

Regenerate only with the exact pinned release:

```bash
poetry run dotmac-deploy -f deploy/product.toml render \
  --thresholds deploy/alerts/thresholds.json \
  -o deploy/rendered
```

Never hand-edit a rendered file.

## Current boundary

This slice adopts and continuously checks the shared contract; it does not
change a production host. ERP's existing `scripts/deploy.sh`, root
`docker-compose.yml`, `Dockerfile` and backup scripts remain the live path.
The product-manifest digest is real. The image reference remains an explicit
all-zero sentinel until protected-main CI publishes the tested image and
records its registry digest. The PR gate therefore sets
`require-real-digests: false`; any candidate or production cutover must
substitute the real image digest and turn that refusal on.

The descriptor makes several legacy defects executable rather than silently
accepted:

- app liveness is `/health/live`; dependency-aware readiness is
  `/health/ready`, never legacy always-200 `/health`;
- no runtime role may receive `MIGRATION_DATABASE_URL`;
- images are digest-shaped, never tags;
- worker liveness uses a Celery ping and Beat freshness uses a declared tick
  command instead of invented HTTP probes;
- migration ordering, dependency waits, resource limits, immutable static
  delivery, verified backup policy and telemetry identity are rendered from
  one descriptor;
- unbacked alert definitions are omitted rather than emitted as rules that no
  producer can satisfy.

## Gates before production execution

1. Use protected-main's `image-release.json` to replace the image sentinel.
2. Run the hardened image audit in strict mode against that exact image.
3. Census the production Docker/Compose versions and prove the rendered project
   on that supported engine.
4. Connect and prove the metrics/OTLP evaluator, alert routing, firing and
   recovery chain.
5. Prove migration-owner separation, backup restore, readiness, worker and
   scheduler health, warm ingress handoff, rollback and drift refusal on an ERP
   candidate.
6. Compare the shared executor with the current production path, switch only
   after parity, then retire the displaced local engine.

Until those gates pass, operators continue to use `scripts/deploy.sh`; the
committed descriptor and rendered assets are conformance evidence, not a second
production command path.
