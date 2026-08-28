"""E2 — the pinned dotmac-kernel release is pure-contract compatible.

Companion to ``test_kernel_import_boundary.py`` (E1). That guard bounds WHICH
kernel modules ``app/`` may import; this file proves the release-consumption
lane itself (docs/PLATFORM_ADOPTION_LEDGER.md, plan slice E2):

1. **Dependency-update gate** — ``pyproject.toml`` pins ``dotmac-kernel`` to
   one exact version from the named private index. Any range (``^ ~ >= *``),
   a missing source, or a credential in the URL fails here, so an unreviewed
   version drift cannot land silently.
2. **Wheel canaries** — ``import dotmac_kernel`` succeeds in a subprocess
   WITHOUT ``DATABASE_URL`` (the kernel top level is DB-free by design), the
   installed distribution is exactly the pinned version and ships
   ``py.typed``, and every consume-pure module — including the full
   ``dotmac_kernel.testing`` subtree, DB-free again since 0.1.0a8 fixed the
   a7 eager-engine defect — imports cleanly in the same no-DB subprocess.
3. **Contract behavior** — Money/Currency exact arithmetic (Decimal in, float
   rejected, cross-currency rejected), the composition contract types
   construct, ``verify_licence`` is importable, and ``FakeLicenceSigner``
   constructs (cryptography is present via ERP's OWN pin, not a kernel
   extra — see test_fake_licence_signer_works_via_erps_own_cryptography).
4. **App-unchanged canary** — booting the unchanged ERP app pulls in no
   ``dotmac_kernel`` module, mounts no kernel route, and adds no kernel
   middleware. Route/schema drift itself is pinned separately by
   ``test_openapi_contract_surface.py`` (not duplicated here).

Every canary RUNS — there is deliberately no skip machinery in this file.
The 0.1.0a7 attempt could not install the wheel (kernel floors excluded
ERP's fastapi 0.111.0 / pydantic 2.7.4 / python >=3.11) and had to guard the
wheel canaries behind skips; 0.1.0a8 widened the floors (see the kernel
CHANGELOG), so a missing or wrong-version kernel is now a hard failure here,
never a skip.
"""

from __future__ import annotations

import importlib.metadata
import importlib.resources
import os
import re
import subprocess
import sys
import tomllib
from decimal import Decimal
from pathlib import Path

import pytest

KERNEL_DIST = "dotmac-kernel"
KERNEL_PACKAGE = "dotmac_kernel"
KERNEL_PIN = "0.1.0a98"
FORGEJO_SOURCE = "forgejo"
FORGEJO_URL = "https://registry.dotmac.io/api/packages/dotmac/pypi/simple/"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = PROJECT_ROOT / "pyproject.toml"

# The E1 consume-pure surface, spelled as concrete import targets (the public
# submodules of each allowlisted module included). Kept in sync with the E1
# guard's ALLOWED_KERNEL_MODULES by test_consume_pure_list_matches_e1_allowlist.
#
# The ``dotmac_kernel.testing`` subtree is part of this list since 0.1.0a8:
# the a7 wheel's ``testing/__init__`` eagerly imported ``harness`` -> ``deps``
# -> ``db`` and built the kernel SQLAlchemy engine at import time, so the
# whole subtree needed a DATABASE_URL. a8 moved the deps import inside
# ``assembly_test_client`` (the only helper that builds a real app), making
# the subtree importable DB-free — asserted by
# test_every_consume_pure_module_imports_without_db below.
CONSUME_PURE_IMPORTS: tuple[str, ...] = (
    "dotmac_kernel.assembly",
    "dotmac_kernel.capabilities",
    "dotmac_kernel.features",
    "dotmac_kernel.licensing",
    "dotmac_kernel.money",
    "dotmac_kernel.planes",
    "dotmac_kernel.prerequisites",
    "dotmac_kernel.profiles",
    "dotmac_kernel.providers",
    "dotmac_kernel.providers.provisioning",
    "dotmac_kernel.testing",
    "dotmac_kernel.testing.fakes",
    "dotmac_kernel.testing.licensing",
    "dotmac_kernel.testing.provisioning",
)


def _no_db_env() -> dict[str, str]:
    """A minimal subprocess environment with NO database configuration.

    Built from scratch (not by deleting keys) so the canary can never
    accidentally inherit DATABASE_URL/PLATFORM_DATABASE_URL — or anything
    else — from the parent test process.
    """
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "LANG": "C.UTF-8",
    }
    assert "DATABASE_URL" not in env and "PLATFORM_DATABASE_URL" not in env
    return env


def _run_no_db_python(code: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """Run ``code`` under the current interpreter with the stripped env.

    cwd is a temp dir so no repo-local package can shadow the installed wheel.
    """
    return subprocess.run(  # noqa: S603 — fixed interpreter, test-owned code
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=_no_db_env(),
        cwd=tmp_path,
        timeout=120,
    )


# ---------------------------------------------------------------------------
# 1. Dependency-update gate
# ---------------------------------------------------------------------------


def _pyproject_data() -> dict[str, object]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    assert data["tool"]["poetry"]["name"] == "dotmac_erp"
    return data


def _kernel_dependency() -> dict[str, object]:
    data = _pyproject_data()
    dep = data["tool"]["poetry"]["dependencies"].get(KERNEL_DIST)
    assert dep is not None, f"pyproject.toml no longer declares {KERNEL_DIST}"
    assert isinstance(dep, dict), (
        f"{KERNEL_DIST} must be an inline table with an explicit source, "
        f"got bare constraint {dep!r}"
    )
    return dep


def test_kernel_is_pinned_to_one_exact_version() -> None:
    """The pin is ``==``-exact: a single PEP 440 version, no range operators."""
    version = str(_kernel_dependency()["version"])
    bare = version.removeprefix("==").strip()
    assert not re.search(r"[\^~*<>|,!]|>=|<=", version), (
        f"{KERNEL_DIST} version {version!r} is a range — the kernel must be "
        "pinned exactly (reviewed upgrades only)"
    )
    assert re.fullmatch(r"\d+(\.\d+)*((a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?", bare), (
        f"{KERNEL_DIST} version {version!r} is not a single exact PEP 440 version"
    )
    assert bare == KERNEL_PIN, (
        f"{KERNEL_DIST} pin changed to {bare!r} without updating this gate "
        f"(expected {KERNEL_PIN}). Reviewed upgrades update both together."
    )


def test_kernel_resolves_from_the_named_private_index() -> None:
    dep = _kernel_dependency()
    assert dep.get("source") == FORGEJO_SOURCE, (
        f"{KERNEL_DIST} must name source {FORGEJO_SOURCE!r} so it can never "
        f"resolve from public PyPI; got {dep.get('source')!r}"
    )

    data = _pyproject_data()
    sources = {s["name"]: s for s in data["tool"]["poetry"].get("source", [])}
    assert FORGEJO_SOURCE in sources, "the forgejo poetry source is missing"
    source = sources[FORGEJO_SOURCE]
    assert source["url"] == FORGEJO_URL
    assert source.get("priority") == "explicit", (
        "the private index must stay priority=explicit so it is only ever "
        "consulted for dependencies that name it"
    )
    # No registry secret may enter Git: the URL carries no userinfo/token.
    assert "@" not in source["url"] and "token" not in source["url"].lower()


def test_consume_pure_list_matches_e1_allowlist() -> None:
    """This file's import list and the E1 guard's allowlist cannot drift."""
    from tests.architecture.test_kernel_import_boundary import (
        ALLOWED_KERNEL_MODULES,
    )

    top_level = {target.split(".")[1] for target in CONSUME_PURE_IMPORTS}
    assert top_level == set(ALLOWED_KERNEL_MODULES), (
        "this file's import list and the E1 ALLOWED_KERNEL_MODULES allowlist "
        f"disagree: only-here={sorted(top_level - set(ALLOWED_KERNEL_MODULES))}, "
        f"only-in-guard={sorted(set(ALLOWED_KERNEL_MODULES) - top_level)}"
    )


# ---------------------------------------------------------------------------
# 2. Wheel canaries
# ---------------------------------------------------------------------------


def test_import_dotmac_kernel_needs_no_database_url(tmp_path: Path) -> None:
    """The canary: ``import dotmac_kernel`` in a no-DB subprocess exits 0.

    Runs under a from-scratch environment (no DATABASE_URL or any other
    inherited variable), so it cannot pass by leaking the parent's env.
    """
    result = _run_no_db_python(
        "import dotmac_kernel; print(dotmac_kernel.__version__)", tmp_path
    )
    assert result.returncode == 0, (
        "`import dotmac_kernel` must be DB-free by design but failed without "
        f"DATABASE_URL:\n{result.stderr}"
    )
    assert result.stdout.strip() == KERNEL_PIN


def test_installed_distribution_is_the_pin_and_ships_py_typed() -> None:
    assert importlib.metadata.version(KERNEL_DIST) == KERNEL_PIN
    files = importlib.metadata.files(KERNEL_DIST) or []
    assert any(f.name == "py.typed" for f in files), (
        f"the {KERNEL_DIST} wheel must ship its PEP 561 py.typed marker"
    )
    marker = importlib.resources.files(KERNEL_PACKAGE).joinpath("py.typed")
    assert marker.is_file(), "py.typed is not importable package data"


def test_every_consume_pure_module_imports_without_db(tmp_path: Path) -> None:
    """Each E1-allowlisted module imports cleanly in the same no-DB env.

    Includes the whole ``dotmac_kernel.testing`` subtree: this is the INVERSE
    of the a7 defect pin (test_testing_kit_db_bound_defect_is_still_present in
    the blocked E2 attempt). a8 fixed the eager engine import, so a DB-bound
    testing kit is a regression and fails here.
    """
    code = "\n".join(
        [
            "import importlib",
            f"for name in {CONSUME_PURE_IMPORTS!r}:",
            "    importlib.import_module(name)",
            "print('ok')",
        ]
    )
    result = _run_no_db_python(code, tmp_path)
    assert result.returncode == 0, (
        "a consume-pure kernel module failed to import without DATABASE_URL "
        "(it is not DB-free and must be reclassified — for the testing "
        f"subtree this would re-open the fixed a7 defect):\n{result.stderr}"
    )
    assert result.stdout.strip() == "ok"


def test_consume_pure_imports_are_all_kernel_supported_modules(
    tmp_path: Path,
) -> None:
    """Everything we import is in the kernel's own SUPPORTED_MODULES manifest.

    INVERSE of the a7 defect pin: 0.1.0a7 omitted ``dotmac_kernel.profiles``
    from SUPPORTED_MODULES even though its five names are re-exported at the
    kernel top level; 0.1.0a8 added it (see the kernel CHANGELOG). Every
    consume-pure import — profiles included, no known-omissions carve-out —
    must now be manifest-supported.
    """
    code = "\n".join(
        [
            "import dotmac_kernel",
            f"missing = [m for m in {CONSUME_PURE_IMPORTS!r}",
            "           if m not in dotmac_kernel.SUPPORTED_MODULES]",
            "assert not missing, f'not kernel-supported: {missing}'",
            "assert 'dotmac_kernel.profiles' in dotmac_kernel.SUPPORTED_MODULES, (",
            "    'the a7 profiles omission is back')",
            "print('ok')",
        ]
    )
    result = _run_no_db_python(code, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


# ---------------------------------------------------------------------------
# 3. Contract behavior (in-process; consume-pure modules only)
# ---------------------------------------------------------------------------


def test_money_is_exact_and_rejects_float() -> None:
    from dotmac_kernel.money import (
        Currency,
        CurrencyMismatchError,
        Money,
        MoneyError,
        currency,
    )

    ngn = currency("NGN")
    assert isinstance(ngn, Currency) and ngn.minor_units == 2

    # Exact Decimal construction, quantized to the currency's minor units.
    amount = Money.of(Decimal("1234.567"), ngn)
    assert amount.amount == Decimal("1234.57")

    # Exact arithmetic — no binary-float drift (0.1 + 0.2 == 0.30 exactly).
    total = Money.of("0.1", ngn) + Money.of("0.2", ngn)
    assert total == Money.of(Decimal("0.30"), ngn)
    assert total.amount == Decimal("0.30")

    # Floats are refused at the boundary, never silently coerced.
    with pytest.raises(MoneyError):
        Money.of(1234.56, ngn)  # type: ignore[arg-type]
    with pytest.raises(MoneyError):
        Money.of("10.00", ngn).multiply(1.5)  # type: ignore[arg-type]

    # Mixing currencies is an error, not a conversion.
    with pytest.raises(CurrencyMismatchError):
        Money.of("1.00", ngn) + Money.of("1.00", currency("USD"))


def test_composition_contract_types_are_constructible() -> None:
    from dotmac_kernel.assembly import ProductAssemblySpec
    from dotmac_kernel.capabilities import CapabilityCatalogue
    from dotmac_kernel.features import FeatureManifest
    from dotmac_kernel.profiles import DeploymentProfileSpec

    manifest = FeatureManifest(name="erp_probe", capabilities=("erp_probe.use",))
    assert manifest.routers == () and manifest.web_routers == ()

    catalogue = CapabilityCatalogue.from_manifests([manifest])
    assert catalogue.is_declared("erp_probe.use")
    assert catalogue.owner("erp_probe.use") == "erp_probe"

    spec = ProductAssemblySpec(name="dotmac-erp-probe", modules=(manifest,))
    assert spec.name == "dotmac-erp-probe"

    profile = DeploymentProfileSpec(
        code="erp-probe",
        version="1",
        required_modules=frozenset({"erp_probe"}),
        commercial_provider="none",
        provisioning_provider="none",
        identity_provider="local",
        telemetry_provider="none",
        update_provider="none",
        ingress_provider="none",
        dns_verification_provider="none",
        tls_provider="none",
        default_locale="en-NG",
        supported_locales=frozenset({"en-NG"}),
        allowed_currencies=frozenset({"NGN"}),
        legal_authority="NG",
        data_residency="NG",
    )
    # provider_selections keys are the bare axis names ("identity", not
    # "identity_provider") — verified against the released wheel.
    selections = profile.provider_selections()
    assert selections["identity"] == "local"
    assert len(selections) == 8


def test_licence_verifier_is_importable() -> None:
    from dotmac_kernel.licensing import LicenceError, verify_licence

    assert callable(verify_licence)
    assert issubclass(LicenceError, Exception)


def test_fake_licence_signer_works_via_erps_own_cryptography(
    tmp_path: Path,
) -> None:
    """``FakeLicenceSigner`` constructs and signs — because ERP ships crypto.

    E2 installs ``dotmac-kernel`` with NO extras: a8's extras split means
    ``[testing]`` would add only httpx and ``[licensing]`` only cryptography,
    and both are already ERP MAIN dependencies (httpx 0.27.0,
    cryptography>=44.0.1 > the kernel's >=42 floor). So the honest assertion
    is that signer instantiation SUCCEEDS here — not that it raises
    ``VerificationUnavailableError`` — and this test first proves the
    provenance (ERP's own pyproject pin) so the claim cannot silently rot if
    ERP ever drops its cryptography dependency.
    """
    deps = _pyproject_data()["tool"]["poetry"]["dependencies"]
    assert "cryptography" in deps, (
        "ERP no longer declares cryptography as its own dependency — "
        "FakeLicenceSigner then needs the kernel's [licensing] extra; "
        "re-decide the E2 extras choice before changing this test"
    )

    # In the same no-DB subprocess environment as the import canaries:
    # construct the signer (generates an ephemeral Ed25519 key — the lazy
    # cryptography import inside __init__ must succeed) and sign an envelope.
    code = "\n".join(
        [
            "from dotmac_kernel.testing.licensing import FakeLicenceSigner",
            "signer = FakeLicenceSigner()",
            "envelope = signer.envelope()",
            "assert envelope['signatures'], 'unsigned envelope'",
            "assert signer.key_id in signer.keyring().key_ids, 'empty keyring'",
            "print('ok')",
        ]
    )
    result = _run_no_db_python(code, tmp_path)
    assert result.returncode == 0, (
        "FakeLicenceSigner must construct in this environment (cryptography "
        f"is installed via ERP's own pin):\n{result.stderr}"
    )
    assert result.stdout.strip() == "ok"


# ---------------------------------------------------------------------------
# 4. App bootstrap canary
# ---------------------------------------------------------------------------


@pytest.mark.timeout(300)
def test_app_import_loads_only_pure_contract_kernel_modules(tmp_path: Path) -> None:
    """Importing ``app.main`` loads no kernel module beyond the reviewed closure.

    E2 pinned this at "zero kernel modules". E4 is the first slice where app/
    legitimately imports the kernel (``dotmac_kernel.money`` in the Money/FX
    boundary adapter), and importing any ``dotmac_kernel.<module>`` also runs
    the kernel package ``__init__`` (its DB-free top-level re-export surface).
    The kernel top-level re-export closure already imports ``models`` even when
    the app asks only for ``money``. E8 slice 4's exact ``Tenant`` adoption is
    therefore enforced at source-symbol precision by
    ``test_kernel_import_boundary.py``; this subprocess canary still proves
    app bootstrap adds no new DB/session/messaging/deps module beyond that
    reviewed closure.

    The matching pytest-timeout marker is intentional: the suite-wide
    60-second default must not kill the parent before this canary's explicit
    300-second subprocess contract can report its own result.

    Runs in a fresh subprocess (mirroring the import bootstrap of
    ``scripts/update_openapi_contract.py``: same env pins, ``tests.conftest``
    first for its app.db/app.rls test doubles) because the in-process test
    session legitimately imports the kernel in the contract-behavior canaries
    above — an in-process ``sys.modules`` scan could only see that pollution,
    not what ``app.main`` itself loads.
    """
    code = "\n".join(
        [
            "import os, sys",
            f"sys.path.insert(0, {str(PROJECT_ROOT)!r})",
            "os.environ['JWT_SECRET'] = 'test-secret'",
            "os.environ['JWT_ALGORITHM'] = 'HS256'",
            "os.environ['TOTP_ENCRYPTION_KEY'] = "
            "'QLUJktsTSfZEbST4R-37XmQ0tCkiVCBXZN2Zt053w8g='",
            "os.environ['TOTP_ISSUER'] = 'StarterTemplate'",
            "os.environ.setdefault('PYTEST_CURRENT_TEST', '1')",
            # Dead-port DATABASE_URL: fails fast if anything touches a DB.
            "os.environ['DATABASE_URL'] = ("
            "'postgresql+psycopg://postgres:postgres@127.0.0.1:9/"
            "dotmac_erp_test?connect_timeout=1')",
            # Snapshot the pure-contract closure: the E4 boundary adapter's
            # import (money) plus the package __init__ it necessarily runs.
            "import dotmac_kernel.money  # noqa: F401",
            "allowed = {n for n in sys.modules",
            f"           if n == {KERNEL_PACKAGE!r}",
            f"           or n.startswith({KERNEL_PACKAGE + '.'!r})}}",
            "assert not any(n.split('.')[1:2] == ['db'] for n in allowed), (",
            "    'the kernel package __init__ itself became DB-bound: '",
            "    + repr(sorted(allowed)))",
            "import tests.conftest  # noqa: F401 — app.db/app.rls doubles",
            "import app.main  # noqa: F401",
            "leaked = sorted(n for n in sys.modules",
            f"                if (n == {KERNEL_PACKAGE!r}",
            f"                    or n.startswith({KERNEL_PACKAGE + '.'!r}))",
            "                and n not in allowed)",
            "assert leaked == [], (",
            "    f'app.main loaded kernel modules beyond the reviewed '",
            "    f'closure: {leaked}')",
            "print('ok')",
        ]
    )
    result = subprocess.run(  # noqa: S603 — fixed interpreter, test-owned code
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**_no_db_env(), "PYTHONDONTWRITEBYTECODE": "1"},
        cwd=PROJECT_ROOT,
        timeout=300,
    )
    assert result.returncode == 0, (
        "importing app.main must load no dotmac_kernel module beyond the "
        "reviewed closure; exact persisted symbols are guarded statically; "
        f"kernel db/messaging/session surfaces stay unimported:\n{result.stderr}"
    )
    assert result.stdout.strip() == "ok"


def test_app_boot_mounts_no_kernel_route_or_middleware() -> None:
    """The mounted app serves no kernel route and runs no kernel middleware.

    The /api/v1 route+schema surface itself is pinned byte-for-byte by
    test_openapi_contract_surface.py; this canary adds the kernel-specific
    assertions: no mounted route (API or web) is served by kernel code or
    sits under the kernel's platform-auth prefix, and the middleware stack
    contains no kernel middleware. (The importing-app.main-loads-no-kernel
    leak check is the subprocess canary above.)
    """
    from tests.architecture.openapi_contract_lib import build_full_app

    app = build_full_app()

    kernel_routes = sorted(
        f"{getattr(route, 'path', route)}"
        for route in app.routes
        if getattr(getattr(route, "endpoint", None), "__module__", "").startswith(
            KERNEL_PACKAGE
        )
    )
    assert kernel_routes == [], f"kernel-served routes mounted: {kernel_routes}"

    platform_auth_routes = sorted(
        route.path
        for route in app.routes
        if getattr(route, "path", "").startswith("/platform/auth")
    )
    assert platform_auth_routes == [], (
        "the kernel platform-auth surface must never mount in ERP: "
        f"{platform_auth_routes}"
    )

    kernel_middleware = [
        m
        for m in app.user_middleware
        if getattr(m.cls, "__module__", "").startswith(KERNEL_PACKAGE)
    ]
    assert kernel_middleware == [], (
        f"kernel middleware entered the app stack: {kernel_middleware}"
    )
