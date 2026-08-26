"""ERP's `deploy/product.toml` is a real, conformant `ProductDeploymentSpec.v1`.

`dotmac-deployment-foundation` (ADR-0065 in dotmac_starter_mt) is not yet a
published, exact-pinned distribution this repository's `pyproject.toml`/
`poetry.lock` install — it is still being built in the Starter repository.
This test therefore guards `pytest.importorskip("dotmac_deployment_foundation")`
and SKIPS rather than fails the build when the package is not importable.
Once ERP exact-pins the released distribution (AGENTS.md rule 30's
authoritative-oracle standard — a published version, not merely a file present
on `main`), this import stops skipping and the assertions below start being
enforced for real, with no other change needed here.

`deploy/product.toml` is an ADAPTER, not a cutover: `scripts/deploy.sh`,
`docker-compose.yml` and `Dockerfile` remain the live, unmodified deployment
path (see `deploy/README.md`). This test only proves the descriptor itself is
well-formed and internally consistent — it does not run a deployment.
"""

from __future__ import annotations

from pathlib import Path

import pytest

dotmac_deployment_foundation = pytest.importorskip("dotmac_deployment_foundation")

from dotmac_deployment_foundation.conformance import check_all  # noqa: E402
from dotmac_deployment_foundation.errors import DeploymentFoundationError  # noqa: E402
from dotmac_deployment_foundation.spec import ProductDeploymentSpec  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DESCRIPTOR_PATH = REPO_ROOT / "deploy" / "product.toml"

#: The migration owner material, named explicitly here as a SECOND line of
#: defence beside spec.py's own parse-time refusal (D3: dotmac_erp's
#: .env.example used to default the runtime DSN to the Postgres superuser —
#: this credential split is the fix, and it must stay checkable even if a
#: future spec.py version stops raising on the violation itself).
MIGRATION_OWNER_MATERIAL = "MIGRATION_DATABASE_URL"


def _load() -> ProductDeploymentSpec:
    return ProductDeploymentSpec.load(DESCRIPTOR_PATH)


def test_descriptor_parses() -> None:
    spec = _load()
    assert spec.product == "dotmac_erp"
    assert len(spec.roles) >= 1


def test_conformance_is_clean() -> None:
    spec = _load()
    assert check_all(spec) == []


def test_no_role_holds_the_migration_owner_material() -> None:
    """Explicit, named second line of defence beside spec.py's own refusal.

    spec.py's `_validate_cross_field` already raises `SpecError` at LOAD time
    if any role names `migration.owner_material` — so `_load()` above would
    already have failed before this test body ran if the file regressed. This
    assertion exists so the failure is attributed to THIS specific, named
    property (rather than "the file failed to parse, for some reason") and so
    it keeps checking the property even if spec.py's own rule is ever
    loosened.
    """
    spec = _load()
    assert spec.migration.owner_material == MIGRATION_OWNER_MATERIAL
    for role in spec.roles:
        assert MIGRATION_OWNER_MATERIAL not in role.materials, (
            f"role {role.code!r} holds the migration owner material; a "
            "runtime role holding it can create, alter and drop any table "
            "for the life of the deployment (inventory D3)"
        )


def test_app_liveness_and_readiness_are_different_paths() -> None:
    """THE central fix this descriptor exists to make checkable (D2).

    ERP's real `/health` (app/main.py:914-916) is hardcoded to
    `{"status": "ok"}` and can never fail; the real dependency-aware check is
    `/health/ready` (app/main.py:969+), previously unused by either the
    Compose healthcheck or scripts/deploy.sh's health gate. Liveness and
    readiness must be two different paths, and readiness must specifically be
    `/health/ready` — not merely "different from liveness" — because a
    descriptor that pointed readiness at some OTHER always-200 path would
    satisfy "different paths" while reintroducing the exact defect.
    """
    spec = _load()
    app_role = spec.role("app")
    assert app_role.live is not None
    assert app_role.ready is not None
    assert app_role.live.path != app_role.ready.path
    assert app_role.ready.path == "/health/ready"
    assert app_role.live.path == "/health/live"


def test_image_is_pinned_by_digest_not_a_tag() -> None:
    """A digest, never a tag (D5: docker-compose.yml floats `:latest`)."""
    spec = _load()
    assert "@sha256:" in spec.image
    digest = spec.image_digest
    assert digest.startswith("sha256:")
    hex_part = digest.removeprefix("sha256:")
    assert len(hex_part) == 64
    int(hex_part, 16)  # raises ValueError if it is not actually hex


# ─────────────────────────────────────────────────────────────────────────
# Sensitivity proof (ADR-0018's standard: a guard exemption/detector must be
# demonstrated to fail, not assumed to). Two halves, and both are required:
# the POSITIVE case proves the loader actually refuses a planted violation
# (rather than, say, silently ignoring an unknown sub-table); the NEGATIVE
# CONTROL proves the same loader accepts the real, unmodified descriptor text
# unchanged. Without the negative control, a loader that rejects EVERY
# document (a typo in the TOML parser call, an overly-broad except clause)
# would make the positive case pass for the wrong reason — it would "prove"
# refusal by refusing everything, including a perfectly valid file.
# ─────────────────────────────────────────────────────────────────────────


def _descriptor_text() -> str:
    return DESCRIPTOR_PATH.read_text(encoding="utf-8")


def test_negative_control_the_real_descriptor_text_loads_cleanly() -> None:
    """Must pass, or the positive case below proves nothing."""
    text = _descriptor_text()
    spec = ProductDeploymentSpec.loads(text, source="<negative-control>")
    assert spec.product == "dotmac_erp"


def test_a_role_given_the_owner_material_is_refused() -> None:
    """Positive case: plant the exact D3-shaped violation and prove refusal.

    Appends a deliberately broken role to the real descriptor TEXT (not a
    hand-built minimal fixture) so the sensitivity proof exercises the same
    document the negative control above just proved loads cleanly — the two
    tests differ by exactly one planted defect.
    """
    # Every OTHER required field is filled in with a trivially valid value
    # (a real `[roles.resources]` table included) so the only difference from
    # a valid role is the one planted violation — otherwise this could raise
    # for an unrelated reason (e.g. a missing required `cpus`) and "pass" the
    # assertion without actually proving the credential-separation rule bit.
    text = _descriptor_text()
    broken = text + (
        "\n"
        "[[roles]]\n"
        'code = "broken_probe"\n'
        'command = ["true"]\n'
        "replicas = 0\n"
        'materials = ["MIGRATION_DATABASE_URL"]\n'
        "\n"
        "[roles.resources]\n"
        'cpus = "1.0"\n'
        'memory = "512m"\n'
    )
    with pytest.raises(DeploymentFoundationError) as excinfo:
        ProductDeploymentSpec.loads(broken, source="<sensitivity-proof>")
    assert "MIGRATION_DATABASE_URL" in str(excinfo.value)
