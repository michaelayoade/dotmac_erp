"""ERP's `deploy/product.toml` is a real, conformant `ProductDeploymentSpec.v1`.

The published `dotmac-deployment-foundation==0.2.0a1` distribution is an exact
dev dependency. A missing package is therefore an installation failure, never
a reason to skip this architecture gate.

`deploy/product.toml` is an ADAPTER, not a cutover: `scripts/deploy.sh`,
`docker-compose.yml` and `Dockerfile` remain the live, unmodified deployment
path (see `deploy/README.md`). This test only proves the descriptor itself is
well-formed and internally consistent — it does not run a deployment.
"""

from __future__ import annotations

from pathlib import Path
import tomllib

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from dotmac_deployment_foundation import __version__ as foundation_version
from dotmac_deployment_foundation.conformance import check_all
from dotmac_deployment_foundation.errors import DeploymentFoundationError
from dotmac_deployment_foundation.spec import ProductDeploymentSpec
from scripts.product_manifest import product_manifest_digest

REPO_ROOT = Path(__file__).resolve().parents[2]
DESCRIPTOR_PATH = REPO_ROOT / "deploy" / "product.toml"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "deployment-conformance.yml"
DEPLOYMENT_README_PATH = REPO_ROOT / "deploy" / "README.md"
FOUNDATION_VERSION = "0.2.0a1"
FOUNDATION_RELEASE_SHA = "ac21c9ae382ac866ec8f2ab21e5970e1ac8cc844"
FOUNDATION_WORKFLOW_SHA = "6a8fdb03d4e7594d3c943b338de0872a6f8c2457"

#: The migration owner material, named explicitly here as a SECOND line of
#: defence beside spec.py's own parse-time refusal (D3: dotmac_erp's
#: .env.example used to default the runtime DSN to the Postgres superuser —
#: this credential split is the fix, and it must stay checkable even if a
#: future spec.py version stops raising on the violation itself).
MIGRATION_OWNER_MATERIAL = "MIGRATION_DATABASE_URL"


def _load() -> ProductDeploymentSpec:
    return ProductDeploymentSpec.load(DESCRIPTOR_PATH)


# ── the conformance ratchet ─────────────────────────────────────────────────
#
# The descriptor is NOT conformance-clean, and pretending otherwise is exactly
# the failure this branch was reviewed for. The findings below are listed
# EXACTLY — not as a subset — so the list fails in both directions: a new
# finding fails here, and a finding that gets fixed without this list shrinking
# fails here too. That is the two-directional ratchet `AGENTS.md` rule 25
# requires of a temporary deviation.
#
# Protected-main CI must publish the tested image before the remaining sentinel
# can be replaced with a registry digest. The assembly manifest is already real.
KNOWN_UNRESOLVED = ("image.reference is pinned to the placeholder",)


def test_published_foundation_is_exact_pinned_at_both_install_seams() -> None:
    pyproject = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    dependency = pyproject["tool"]["poetry"]["group"]["dev"]["dependencies"][
        "dotmac-deployment-foundation"
    ]
    assert dependency == {"version": FOUNDATION_VERSION, "source": "forgejo"}
    assert foundation_version == FOUNDATION_VERSION

    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    immutable_call = (
        "michaelayoade/dotmac_starter_mt/"
        ".github/workflows/deployment-conformance.yml@"
        f"{FOUNDATION_WORKFLOW_SHA}"
    )
    assert immutable_call in workflow
    assert f'foundation-version: "{FOUNDATION_VERSION}"' in workflow
    assert "FORGEJO_READ_TOKEN: ${{ secrets.FORGEJO_READ_TOKEN }}" in workflow
    assert FOUNDATION_RELEASE_SHA in DEPLOYMENT_README_PATH.read_text(encoding="utf-8")


def test_descriptor_parses() -> None:
    spec = _load()
    assert spec.product == "dotmac-erp"
    assert len(spec.roles) >= 1


def test_descriptor_binds_the_canonical_product_manifest() -> None:
    spec = _load()
    assert spec.manifest_path == "deploy/product-manifest.json"
    assert spec.manifest_digest == product_manifest_digest()


def test_descriptor_heads_match_the_composed_alembic_graph() -> None:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    actual = set(ScriptDirectory.from_config(config).get_heads())
    declared = set(_load().migration.expected_heads)
    assert declared == actual


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
    assert spec.product == "dotmac-erp"


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


def test_conformance_findings_are_exactly_the_known_unresolved_ones() -> None:
    """Not `== []`, and not a subset — an exact match in both directions.

    A subset assertion passes when a finding is fixed and the list is not
    updated, so the list stops describing anything. An exact match makes the
    ratchet visible: fixing a finding is a diff that removes a line here.
    """
    from dotmac_deployment_foundation.spec import ProductDeploymentSpec

    findings = check_all(ProductDeploymentSpec.load(DESCRIPTOR_PATH))
    matched = [
        finding
        for finding in findings
        if any(known in finding for known in KNOWN_UNRESOLVED)
    ]
    unexpected = [finding for finding in findings if finding not in matched]
    assert unexpected == [], f"new conformance finding(s): {unexpected}"
    assert len(matched) == len(KNOWN_UNRESOLVED), (
        f"{len(KNOWN_UNRESOLVED)} known finding(s) declared but {len(matched)} "
        "observed — a fixed finding must be removed from KNOWN_UNRESOLVED in "
        "the same change that fixes it"
    )
