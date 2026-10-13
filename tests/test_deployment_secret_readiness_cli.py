"""Isolated, stdlib-only candidate CLI execution and no-database boundary."""

import os
import subprocess
import sys

import pytest

from tests.test_deployment_secret_readiness import ROOT, VALID_KEY, _assert_wire


@pytest.mark.parametrize("missing", [False, True])
def test_real_cli_no_db_no_startup_and_closed_receipt(missing):
    # Guard imports in a fresh process, rather than trusting the unit-test
    # process whose global conftest has already imported the application.
    source = """
import importlib.abc, runpy, sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ("app.db", "app.main", "app.startup", "app.config") or fullname.startswith(("app.models", "app.licensing", "app.authz")):
            raise RuntimeError("forbidden application import")
sys.meta_path.insert(0, Guard())
runpy.run_module("scripts.verify_deployment_secrets", run_name="__main__")
"""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(ROOT),
        "DATABASE_URL": "unusable-database-canary",
        "JWT_SECRET": VALID_KEY,
        "TOTP_ENCRYPTION_KEY": VALID_KEY,
    }
    if missing:
        del env["JWT_SECRET"]
    proc = subprocess.run(  # noqa: S603 - fixed interpreter and authored source
        [sys.executable, "-c", source],
        cwd=ROOT,
        env=env,  # noqa: S603 - fixed interpreter and authored source
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert proc.stderr == ""
    assert proc.returncode == (1 if missing else 0)
    assert _assert_wire(proc.stdout)["status"] == ("refused" if missing else "ready")
