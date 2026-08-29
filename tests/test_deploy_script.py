from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

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
    rendered = " ".join(args).replace("\\n", "\\\\n")
    log.write(f"{project}|{rendered}\\n")

if args[:2] == ["inspect", "--format"]:
    if ".State.Running" in args[2]:
        print("true")
    else:
        print("ghcr.io/michaelayoade/dotmac_erp:sha-old")

if args and args[0] == "ps":
    service = ""
    for argument in args:
        prefix = "label=com.docker.compose.service="
        if argument.startswith(prefix):
            service = argument.removeprefix(prefix)
    if os.environ.get("DEPLOY_TEST_RUNNING_APP_DEV") == "1" and service == "app-dev":
        print("dotmac_erp_app_dev")
    if os.environ.get("DEPLOY_TEST_RUNNING_ONE_OFF") == "1" and service == "app":
        print("dotmac-run-app-one-off")

# The deploy makes three `compose run` calls: the executor preflight, the
# migration, and the runtime admission check. Fail only the operation named by
# each test flag.
if os.environ.get("DEPLOY_TEST_FAIL_MIGRATION") == "1":
    if args[:2] == ["compose", "run"] and "alembic" in args and "upgrade" in args:
        raise SystemExit(1)

if os.environ.get("DEPLOY_TEST_FAIL_ROLE_PREFLIGHT") == "1":
    if args[:2] == ["compose", "run"] and "--verify-only" in args:
        raise SystemExit(1)

if os.environ.get("DEPLOY_TEST_FAIL_ADMISSION") == "1":
    if args[:2] == ["compose", "run"] and any(
        "verify_runtime_admission.py" in arg for arg in args
    ):
        raise SystemExit(1)

if any("probe_people_employment_type_activation.py" in argument for argument in args):
    state = os.environ.get("DEPLOY_TEST_ACTIVATION_PROBE", "pre-activation")
    if state == "activated":
        raise SystemExit(0)
    if state == "pre-activation":
        raise SystemExit(3)
    raise SystemExit(4)

admission_failure = os.environ.get("DEPLOY_TEST_FAIL_RUNTIME_ADMISSION")
if args[:2] == ["compose", "exec"]:
    if admission_failure == "worker" and any(
        "inspect ping" in argument for argument in args
    ):
        raise SystemExit(1)
    if admission_failure == "beat" and any(
        "dotmac-erp-beat-heartbeat" in argument for argument in args
    ):
        raise SystemExit(1)
""",
    )
    _write_executable(
        fake_bin / "curl",
        """#!/usr/bin/env python3
import os
if os.environ.get("DEPLOY_TEST_FAIL_HEALTH") == "1":
    raise SystemExit(1)
raise SystemExit(0)
""",
    )
    for script_name in ("sync-static.sh", "prune_docker_images.sh"):
        _write_executable(
            scripts_dir / script_name,
            """#!/usr/bin/env bash
set -euo pipefail
if [[ "${DEPLOY_TEST_FAIL_STATIC:-0}" == "1" && "$(basename "$0")" == "sync-static.sh" ]]; then
    exit 1
fi
""",
        )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["DEPLOY_TEST_LOG"] = str(invocation_log)
    env["SKIP_BACKUP"] = "1"
    env["HEALTH_TIMEOUT"] = "1"
    env["RUNTIME_ADMISSION_TIMEOUT"] = "1"
    env["MIGRATION_DATABASE_URL"] = (
        "postgresql+psycopg://app_admin@database.test/dotmac_erp"
    )
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
    one_off = [line for line in invocations if "|compose run " in line]
    runtime = [line for line in invocations if "|compose up " in line]
    assert one_off
    # The runtime admission check is the ONE one-off that must NOT carry the
    # migration credential: it verifies the connection the application serves
    # on, and re-verifying `app_admin` there would prove nothing. Every other
    # one-off is a migration-executor operation and still carries it.
    admission = [line for line in one_off if "verify_runtime_admission.py" in line]
    executor = [line for line in one_off if "verify_runtime_admission.py" not in line]
    assert len(admission) == 1, admission
    assert "-e MIGRATION_DATABASE_URL" not in admission[0]
    assert executor
    assert all("-e MIGRATION_DATABASE_URL" in line for line in executor)
    assert all("MIGRATION_DATABASE_URL" not in line for line in runtime)
    assert all("PEOPLE_EMPLOYMENT_TYPE_ACTIVATION" not in line for line in one_off)
    assert "compose: dotmac" in result.stdout


def test_deploy_script_refuses_a_missing_migration_credential(
    tmp_path: Path,
) -> None:
    deploy_script, env, invocation_log = _deployment_harness(tmp_path)
    env.pop("MIGRATION_DATABASE_URL")

    result = subprocess.run(  # noqa: S603
        [str(deploy_script)],
        cwd=deploy_script.parent.parent,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "MIGRATION_DATABASE_URL is required" in result.stderr
    assert not invocation_log.exists()


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


def test_the_executor_preflight_runs_before_migrations(tmp_path: Path) -> None:
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
    preflights = [
        index
        for index, line in enumerate(invocations)
        if "|compose run " in line and "--verify-only" in line
    ]
    migrations = [
        index
        for index, line in enumerate(invocations)
        if "|compose run " in line and "alembic" in line and "upgrade" in line
    ]
    assert preflights, "the migration-executor preflight was not recorded"
    assert migrations, "the Alembic migration invocation was not recorded"
    assert preflights[0] < migrations[0]


def test_a_failed_executor_preflight_stops_before_migrating(
    tmp_path: Path,
) -> None:
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
    assert not any("alembic" in line and "upgrade" in line for line in invocations)
    assert "Rolling back code" not in result.stdout


def test_the_runtime_admission_runs_after_migrations_and_before_the_app(
    tmp_path: Path,
) -> None:
    """The seam this step must occupy, asserted as an ORDER, not a presence.

    After the DDL, because it inspects grants and row-level security those
    migrations have just (re)created. Before `up -d app`, because refusing a
    runtime identity is only useful while the previous container is still
    serving requests. A step that merely EXISTS somewhere in the script would
    satisfy neither requirement.
    """
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
    migrations = [
        index
        for index, line in enumerate(invocations)
        if "|compose run " in line and "alembic" in line and "upgrade" in line
    ]
    admissions = [
        index
        for index, line in enumerate(invocations)
        if "|compose run " in line and "verify_runtime_admission.py" in line
    ]
    recreations = [
        index
        for index, line in enumerate(invocations)
        if line.endswith("|compose up -d app")
    ]
    assert migrations, "the Alembic migration invocation was not recorded"
    assert admissions, "the runtime admission check was not recorded"
    assert recreations, "the app container was never recreated"
    assert migrations[0] < admissions[0] < recreations[0]


def test_a_failed_runtime_admission_stops_before_recreating_the_app(
    tmp_path: Path,
) -> None:
    """A refused runtime identity must not reach the containers.

    The rollback that follows reverts code and image only — the migrations from
    step 3b stay applied — so the assertion that matters is the negative one:
    `up -d app` never ran on the new image.
    """
    deploy_script, env, invocation_log = _deployment_harness(tmp_path)
    env["DEPLOY_TEST_FAIL_ADMISSION"] = "1"

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
    assert "verify_runtime_admission.py" not in result.stderr
    assert "not admissible" in result.stderr
    # The operator is told, in the failure itself, that the DDL is still there.
    assert "NOT rolled" in result.stderr

    invocations = invocation_log.read_text(encoding="utf-8").splitlines()
    assert not any(line.endswith("|compose up -d app") for line in invocations), (
        "the app container was recreated despite an inadmissible runtime connection"
    )


def test_employment_type_activation_drains_old_writers_before_migrating(
    tmp_path: Path,
) -> None:
    deploy_script, env, invocation_log = _deployment_harness(tmp_path)

    result = subprocess.run(  # noqa: S603
        [str(deploy_script), "--people-employment-type-activation"],
        cwd=deploy_script.parent.parent,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    invocations = invocation_log.read_text(encoding="utf-8").splitlines()
    drained = next(
        index
        for index, line in enumerate(invocations)
        if "|compose stop app worker beat" in line
    )
    migrated = next(
        index
        for index, line in enumerate(invocations)
        if "|compose run " in line and "alembic" in line and "upgrade" in line
    )
    started = next(
        index for index, line in enumerate(invocations) if "|compose up -d app" in line
    )
    assert drained < migrated < started
    migration = invocations[migrated]
    assert "-e MIGRATION_DATABASE_URL" in migration
    assert "-e PEOPLE_EMPLOYMENT_TYPE_ACTIVATION=1" in migration


def test_activation_migration_failure_can_restore_the_old_image(
    tmp_path: Path,
) -> None:
    deploy_script, env, invocation_log = _deployment_harness(tmp_path)
    env["DEPLOY_TEST_FAIL_MIGRATION"] = "1"

    result = subprocess.run(  # noqa: S603
        [str(deploy_script), "--people-employment-type-activation"],
        cwd=deploy_script.parent.parent,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Rolling back code" in result.stdout
    assert "FORWARD-FIX-ONLY" not in result.stderr
    invocations = invocation_log.read_text(encoding="utf-8").splitlines()
    assert any("|compose stop app worker beat" in line for line in invocations)
    assert any("|compose up -d app worker beat" in line for line in invocations)
    assert any(
        "probe_people_employment_type_activation.py" in line for line in invocations
    )


@pytest.mark.parametrize("probe_state", ["activated", "ambiguous"])
def test_ambiguous_or_committed_migration_failure_never_restarts_legacy_writers(
    tmp_path: Path,
    probe_state: str,
) -> None:
    deploy_script, env, invocation_log = _deployment_harness(tmp_path)
    env["DEPLOY_TEST_FAIL_MIGRATION"] = "1"
    env["DEPLOY_TEST_ACTIVATION_PROBE"] = probe_state

    result = subprocess.run(  # noqa: S603
        [str(deploy_script), "--people-employment-type-activation"],
        cwd=deploy_script.parent.parent,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Rolling back code" not in result.stdout
    assert "FORWARD-FIX-ONLY" in result.stderr
    invocations = invocation_log.read_text(encoding="utf-8").splitlines()
    assert any(
        "probe_people_employment_type_activation.py" in line for line in invocations
    )
    assert not any("|compose up -d app worker beat" in line for line in invocations)


def test_activation_health_failure_never_restores_legacy_writers(
    tmp_path: Path,
) -> None:
    deploy_script, env, invocation_log = _deployment_harness(tmp_path)
    env["DEPLOY_TEST_FAIL_HEALTH"] = "1"

    result = subprocess.run(  # noqa: S603
        [str(deploy_script), "--people-employment-type-activation"],
        cwd=deploy_script.parent.parent,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Rolling back code" not in result.stdout
    assert "FORWARD-FIX-ONLY" in result.stderr
    assert "previous image remains stopped" in result.stderr
    invocations = invocation_log.read_text(encoding="utf-8").splitlines()
    assert any("|compose stop app worker beat" in line for line in invocations)
    assert not any("|compose up -d app worker beat" in line for line in invocations)


def test_activation_post_health_failure_remains_forward_fix_only(
    tmp_path: Path,
) -> None:
    deploy_script, env, invocation_log = _deployment_harness(tmp_path)
    env["DEPLOY_TEST_FAIL_STATIC"] = "1"

    result = subprocess.run(  # noqa: S603
        [str(deploy_script), "--people-employment-type-activation"],
        cwd=deploy_script.parent.parent,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Rolling back code" not in result.stdout
    assert "FORWARD-FIX-ONLY" in result.stderr
    invocations = invocation_log.read_text(encoding="utf-8").splitlines()
    assert any("|compose stop app worker beat" in line for line in invocations)
    assert not any("|compose up -d app worker beat" in line for line in invocations)


def test_activation_refuses_a_running_app_dev_before_mutation(tmp_path: Path) -> None:
    deploy_script, env, invocation_log = _deployment_harness(tmp_path)
    env["DEPLOY_TEST_RUNNING_APP_DEV"] = "1"

    result = subprocess.run(  # noqa: S603
        [str(deploy_script), "--people-employment-type-activation"],
        cwd=deploy_script.parent.parent,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "refuses while app-dev is running" in result.stderr
    invocations = invocation_log.read_text(encoding="utf-8").splitlines()
    assert not any("|compose stop" in line for line in invocations)
    assert not any("alembic" in line and "upgrade" in line for line in invocations)


def test_activation_refuses_a_running_one_off_after_the_drain(tmp_path: Path) -> None:
    deploy_script, env, invocation_log = _deployment_harness(tmp_path)
    env["DEPLOY_TEST_RUNNING_ONE_OFF"] = "1"

    result = subprocess.run(  # noqa: S603
        [str(deploy_script), "--people-employment-type-activation"],
        cwd=deploy_script.parent.parent,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "legacy-capable Compose containers remain" in result.stderr
    invocations = invocation_log.read_text(encoding="utf-8").splitlines()
    assert any("|compose stop app worker beat" in line for line in invocations)
    assert not any("alembic" in line and "upgrade" in line for line in invocations)
    assert any("|compose up -d app worker beat" in line for line in invocations)


@pytest.mark.parametrize("failed_runtime", ["worker", "beat"])
def test_activation_runtime_admission_failure_remains_forward_fix_only(
    tmp_path: Path,
    failed_runtime: str,
) -> None:
    deploy_script, env, invocation_log = _deployment_harness(tmp_path)
    env["DEPLOY_TEST_FAIL_RUNTIME_ADMISSION"] = failed_runtime

    result = subprocess.run(  # noqa: S603
        [str(deploy_script), "--people-employment-type-activation"],
        cwd=deploy_script.parent.parent,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "FORWARD-FIX-ONLY" in result.stderr
    assert "Rolling back code" not in result.stdout
    invocations = invocation_log.read_text(encoding="utf-8").splitlines()
    assert any("|compose up -d worker beat" in line for line in invocations)
    assert any(
        "inspect ping" in line and "--destination" in line for line in invocations
    )
    assert not any("|compose up -d app worker beat" in line for line in invocations)


def test_activation_refuses_quick_mode_before_any_docker_action(
    tmp_path: Path,
) -> None:
    deploy_script, env, invocation_log = _deployment_harness(tmp_path)

    result = subprocess.run(  # noqa: S603
        [
            str(deploy_script),
            "--quick",
            "--people-employment-type-activation",
        ],
        cwd=deploy_script.parent.parent,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "cannot use --quick" in result.stderr
    assert not invocation_log.exists()
