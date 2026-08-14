from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts/deploy.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _deployment_harness(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    project_dir = tmp_path / "release-worktree"
    scripts_dir = project_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    deploy_script = scripts_dir / "deploy.sh"
    shutil.copy2(SCRIPT_PATH, deploy_script)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    invocation_log = tmp_path / "docker-invocations.log"

    _write_executable(
        fake_bin / "git",
        """#!/usr/bin/env python3
import sys

args = sys.argv[1:]
if args == ["rev-parse", "HEAD"]:
    print("abcdef0123456789abcdef0123456789abcdef01")
elif args == ["rev-parse", "--short=7", "HEAD"]:
    print("abcdef0")
""",
    )
    _write_executable(
        fake_bin / "docker",
        """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

args = sys.argv[1:]
log_path = Path(os.environ["DEPLOY_TEST_LOG"])
with log_path.open("a", encoding="utf-8") as log:
    project = os.environ.get("COMPOSE_PROJECT_NAME", "")
    log.write(f"{project}|{' '.join(args)}\\n")

if args[:2] == ["inspect", "--format"]:
    print("ghcr.io/michaelayoade/dotmac_erp:sha-old")

# The deploy now makes TWO `compose run` calls: a database-role preflight and
# then the migration. Failing every `compose run` would abort at the preflight,
# so `DEPLOY_TEST_FAIL_MIGRATION` would silently stop testing the migration
# rollback it is named for. Match on `alembic` to fail only the migration.
if os.environ.get("DEPLOY_TEST_FAIL_MIGRATION") == "1":
    if args[:2] == ["compose", "run"] and "alembic" in args:
        raise SystemExit(1)

# The role preflight runs a heredoc through `python -`; the fake has no
# database, so it succeeds unless a test asks for the opposite.
if os.environ.get("DEPLOY_TEST_FAIL_ROLE_PREFLIGHT") == "1":
    if args[:2] == ["compose", "run"] and "alembic" not in args:
        raise SystemExit(1)
""",
    )
    _write_executable(
        fake_bin / "curl",
        """#!/usr/bin/env python3
raise SystemExit(0)
""",
    )
    for script_name in ("sync-static.sh", "prune_docker_images.sh"):
        _write_executable(
            scripts_dir / script_name,
            """#!/usr/bin/env bash
set -euo pipefail
""",
        )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["DEPLOY_TEST_LOG"] = str(invocation_log)
    env["SKIP_BACKUP"] = "1"
    env["HEALTH_TIMEOUT"] = "1"
    env.pop("COMPOSE_PROJECT_NAME", None)
    return deploy_script, env, invocation_log


def test_deploy_script_exports_stable_compose_project_name(tmp_path: Path) -> None:
    deploy_script, env, invocation_log = _deployment_harness(tmp_path)

    result = subprocess.run(  # noqa: S603
        [str(deploy_script)],
        cwd=deploy_script.parent.parent,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    invocations = invocation_log.read_text(encoding="utf-8").splitlines()
    assert any("|compose run " in line for line in invocations)
    assert any("|compose up -d app" in line for line in invocations)
    assert any("|compose up -d worker beat" in line for line in invocations)
    assert all(line.startswith("dotmac|") for line in invocations)
    assert "compose: dotmac" in result.stdout


def test_deploy_script_rejects_conflicting_compose_project_name(
    tmp_path: Path,
) -> None:
    deploy_script, env, invocation_log = _deployment_harness(tmp_path)
    env["COMPOSE_PROJECT_NAME"] = "production-release-worktree"

    result = subprocess.run(  # noqa: S603
        [str(deploy_script)],
        cwd=deploy_script.parent.parent,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "COMPOSE_PROJECT_NAME must be 'dotmac'" in result.stderr
    assert not invocation_log.exists()


def test_deploy_script_rollback_keeps_stable_compose_project_name(
    tmp_path: Path,
) -> None:
    deploy_script, env, invocation_log = _deployment_harness(tmp_path)
    env["DEPLOY_TEST_FAIL_MIGRATION"] = "1"

    result = subprocess.run(  # noqa: S603
        [str(deploy_script)],
        cwd=deploy_script.parent.parent,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    invocations = invocation_log.read_text(encoding="utf-8").splitlines()
    assert any("|compose run " in line for line in invocations)
    assert any("|compose up -d app worker beat" in line for line in invocations)
    assert all(line.startswith("dotmac|") for line in invocations)
    assert "Rolling back code" in result.stdout


def test_the_role_preflight_runs_before_migrations(tmp_path: Path) -> None:
    """Order is the whole point.

    `20260814_database_roles` fails closed when the three database roles are
    absent. Discovering that mid-chain means a half-applied upgrade and a
    rollback; discovering it in the preflight costs nothing.
    """
    deploy_script, env, invocation_log = _deployment_harness(tmp_path)

    subprocess.run(  # noqa: S603
        [str(deploy_script)],
        cwd=deploy_script.parent.parent,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    invocations = invocation_log.read_text(encoding="utf-8").splitlines()
    runs = [i for i, line in enumerate(invocations) if "|compose run " in line]
    migrations = [
        i
        for i, line in enumerate(invocations)
        if "|compose run " in line and "alembic" in line
    ]
    assert runs, "no `compose run` invocations recorded at all"
    assert migrations, "the migration invocation was not recorded"
    assert runs[0] < migrations[0], (
        "the database-role preflight must precede `alembic upgrade`; "
        f"invocations were {invocations}"
    )


def test_a_failed_role_preflight_stops_before_migrating(tmp_path: Path) -> None:
    """And it does NOT roll back, because nothing has changed yet.

    A preflight that triggered the rollback trap would restore a previous image
    over a deployment that was never modified — noise that trains an operator to
    ignore rollback messages.
    """
    deploy_script, env, invocation_log = _deployment_harness(tmp_path)
    env["DEPLOY_TEST_FAIL_ROLE_PREFLIGHT"] = "1"

    result = subprocess.run(  # noqa: S603
        [str(deploy_script)],
        cwd=deploy_script.parent.parent,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "DEPLOY STOPPED" in result.stderr
    assert "bootstrap_database_roles.py" in result.stderr
    invocations = invocation_log.read_text(encoding="utf-8").splitlines()
    assert not any("alembic" in line for line in invocations), (
        "migrations must not run once the role preflight has failed"
    )
    assert "Rolling back code" not in result.stdout
