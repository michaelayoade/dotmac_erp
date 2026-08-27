"""ERP's `deploy/product.toml` is a real, conformant `ProductDeploymentSpec.v1`.

`dotmac-deployment-foundation` (ADR-0070 in dotmac_starter_mt) is not yet a
published, exact-pinned distribution this repository's `pyproject.toml`/
`poetry.lock` install — it is still being built in the Starter repository.
This test therefore skips when the package is not importable — but only while
`DOTMAC_DEPLOYMENT_FOUNDATION_REQUIRED` is unset. The conformance job sets it,
so a missing package there is a failure rather than a silent skip.
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

import os

import pytest

# A skip is not a gate — so the skip is CONDITIONAL and the condition is
# explicit. Setting `DOTMAC_DEPLOYMENT_FOUNDATION_REQUIRED=1` turns a missing
# package from a quiet skip into a hard failure, and the conformance job sets
# it. Before the distribution is published the skip is honest: the package
# genuinely is not installable, and failing every build over that would teach
# people to ignore red. After it is published, a silent skip would mean the
# descriptor stopped being checked and nothing said so — which is the shape of
# every defect in this branch's own review.
_REQUIRED = os.environ.get("DOTMAC_DEPLOYMENT_FOUNDATION_REQUIRED") == "1"
try:
    import dotmac_deployment_foundation  # noqa: F401
except ImportError as exc:  # pragma: no cover - exercised by the env var
    if _REQUIRED:
        raise AssertionError(
            "DOTMAC_DEPLOYMENT_FOUNDATION_REQUIRED=1 but the package is not "
            f"importable: {exc}. Either the pin is wrong or the install step "
            "did not run — both are failures, not skips."
        ) from exc
    pytest.skip(
        "dotmac-deployment-foundation is not published yet; set "
        "DOTMAC_DEPLOYMENT_FOUNDATION_REQUIRED=1 to make this a failure",
        allow_module_level=True,
    )

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


# ── the conformance ratchet ─────────────────────────────────────────────────
#
# The descriptor is NOT conformance-clean, and pretending otherwise is exactly
# the failure this branch was reviewed for. The findings below are listed
# EXACTLY — not as a subset — so the list fails in both directions: a new
# finding fails here, and a finding that gets fixed without this list shrinking
# fails here too. That is the two-directional ratchet `AGENTS.md` rule 25
# requires of a temporary deviation.
#
# Every entry must be gone before this adapter merges. They are placeholders
# because no image has been built for this descriptor to pin.
KNOWN_UNRESOLVED = (
    "image.reference is pinned to the placeholder",
    "assembly.manifest_digest is the placeholder",
)


def test_descriptor_parses() -> None:
    spec = _load()
    assert spec.product == "dotmac_erp"
    assert len(spec.roles) >= 1


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
