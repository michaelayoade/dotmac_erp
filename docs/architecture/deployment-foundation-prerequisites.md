# Deployment foundation prerequisites

Status: **release identity implemented; deployment adapter and host cutover are
not part of this slice**.

ERP now has two immutable release facts that a later deployment descriptor can
join without a digest cycle:

1. `deploy/product-manifest.json` is the canonical, checked representation of
   `ERP_PRODUCT_ASSEMBLY` under the product identity `dotmac-erp`. The
   historical source repository and Python distribution remain `dotmac_erp`;
   that source coordinate is not a product identity. The manifest binds every
   currently composed module's code,
   exact distribution version, declared persistence planes, any explicit ERP
   plane selection, and the resulting effective planes.
2. A successful `main` image publication uploads `image-release.json`, binding
   `GITHUB_SHA`, the registry-returned `sha256` digest and the exact
   `ghcr.io/...@sha256:...` reference. It contains no tag and no credential.

## Ownership boundary

`app/product_assembly.py` is release metadata only. It does not call
`create_app`, mount a module, open a session, or move any domain authority;
`app.main` remains ERP's runtime owner. The module tuple is cross-checked
against `COMPOSED_MODULE_LINEAGES`, while distribution coordinates are checked
against exact Poetry pins.

The generic kernel `ProductManifestSnapshot` is intentionally not used here.
That document owns a capability projection and omits installable module
identity. Reconstructing packages or planes from it would make the deployment
record look typed while dropping the facts a rollback actually needs.

For atomic modules, `effective_planes` is the manifest's declared plane set and
`explicit_planes` is null. For selectable modules, the assembly's
`ModulePlaneSelection` is required and becomes the effective set. Numbering
therefore records declared `[platform, tenant]`, explicit `[tenant]`, and
effective `[tenant]`; no missing selection is inferred from installed tables.

## Why `deploy/` is outside the image

The Docker build context excludes the whole `deploy/` control-plane directory.
If the product manifest or a descriptor containing its digest were copied into
the image, inserting the final image digest into that descriptor would change
the image and therefore change the digest again. Exclusion makes image bytes a
function of application source and dependencies only. The post-push release
artifact then binds that immutable image to the same Git commit whose canonical
product manifest is checked in CI.

The exclusion also means none of these files is an in-container runtime input.
A later deployment-foundation adoption must download verified release evidence
and explicitly assemble a deployment plan; it may not read an accidental copy
from `/app/deploy`.

## CI contract

`publish-image` waits for every gate in the CI workflow, including security,
pre-commit and the independent private-key scan. The Docker job builds one
image, applies its OCI metadata before testing, runs migrations and the health
probe against it, and exports that exact image only for a protected-main push.
The publication job downloads and loads the tested image, applies registry tags
without changing its bytes, and pushes it. The existing production deployer
continues to consume the immutable seven-character revision tag, while release
evidence resolves the same image through the unambiguous full-revision tag.
Both tags point at the exact tested bytes. The publication job contains no image
build step. GitHub cannot
express a cross-workflow `needs`, so CI re-runs the exact pinned Governance
action as a release gate while retaining the separate Engineering standards
workflow and its stable branch-protection check context. Only the registry
lookup's returned digest is admitted to `image-release.json`. The artifact is
uploaded for review and promotion; its existence does not authorize a
deployment.

Regenerate or verify the committed manifest with the pinned environment:

```bash
poetry run python -m scripts.product_manifest generate \
  --path deploy/product-manifest.json
poetry run python -m scripts.product_manifest check --path deploy/product-manifest.json
```

This slice creates no deployment descriptor, changes no live deploy script,
names no host, moves no data, and performs no production action.
