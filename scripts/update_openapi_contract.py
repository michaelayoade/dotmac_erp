#!/usr/bin/env python3
"""Regenerate tests/architecture/openapi_contract_surface.json.

Run after an intentional /api/v1 contract change, then review the manifest
diff in your commit — the diff IS the contract-change review artifact.

Usage: python scripts/update_openapi_contract.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _set_test_env() -> None:
    """Pin the module-flag defaults the test environment sees
    (ENABLED_MODULES unset -> all modules; DOTMAC_DEV_MODE unset -> dev-mode
    licensing -> every module licensed) and keep any real database out of
    reach. The JWT/TOTP values below mirror tests/conftest.py."""
    os.environ["JWT_SECRET"] = "test-secret"
    os.environ["JWT_ALGORITHM"] = "HS256"
    os.environ["TOTP_ENCRYPTION_KEY"] = "QLUJktsTSfZEbST4R-37XmQ0tCkiVCBXZN2Zt053w8g="
    os.environ["TOTP_ISSUER"] = "StarterTemplate"
    os.environ.setdefault("PYTEST_CURRENT_TEST", "1")
    os.environ.pop("ENABLED_MODULES", None)
    os.environ.pop("DOTMAC_DEV_MODE", None)
    # Dead-port DATABASE_URL: anything that reads it fails fast (logged,
    # non-fatal) instead of touching a real database.
    os.environ["DATABASE_URL"] = (
        "postgresql+psycopg://postgres:postgres@127.0.0.1:9/dotmac_erp_test"
        "?connect_timeout=1"
    )


def main() -> None:
    _set_test_env()
    # The contract test imports app.main under tests/conftest.py, whose
    # import-time side effects (app.db/app.rls test doubles, patched
    # SQLAlchemy types, env) measurably shift some schema fingerprints.
    # Import it here too so the manifest is generated under EXACTLY the
    # import environment the test computes the surface in.
    import tests.conftest  # noqa: F401

    from tests.architecture import openapi_contract_lib as lib

    surface = lib.compute_surface(lib.build_full_app())
    lib.write_manifest(surface)
    print(
        f"wrote {lib.MANIFEST_PATH.relative_to(REPO_ROOT)}: "
        f"{len(surface['routes'])} routes, {len(surface['schemas'])} schemas "
        f"(surface pinned under test-env flag defaults: all modules enabled)"
    )


if __name__ == "__main__":
    main()
