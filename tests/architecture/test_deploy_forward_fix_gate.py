"""The reviewed forward-fix path never restarts old code after migration starts.

Reuse the deploy script's controlled fake-command harness so these assertions
exercise Bash control flow, including its ERR trap and explicit failure paths.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.test_deploy_script import _deployment_harness

EXPECTED_SHA = "abcdef0123456789abcdef0123456789abcdef01"
FORWARD_FIX_ARGS = (
    "--forward-fix-after-migration",
    f"--expected-checkout-sha={EXPECTED_SHA}",
)


def _run(
    deploy_script: Path, env: dict[str, str], *arguments: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [str(deploy_script), *arguments],
        cwd=deploy_script.parent.parent,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _invocations(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _assert_no_old_restart(result: subprocess.CompletedProcess[str], log: Path) -> None:
    assert "Rolling back code" not in result.stdout
    assert not any(
        "|compose up -d app worker beat" in line for line in _invocations(log)
    )


@pytest.mark.parametrize(
    "arguments",
    [
        ("--forward-fix-after-migration",),
        ("--forward-fix-after-migration", "--expected-checkout-sha=not-a-sha"),
        ("--expected-checkout-sha=" + EXPECTED_SHA,),
        (*FORWARD_FIX_ARGS, "--quick"),
        (*FORWARD_FIX_ARGS, "--people-employment-type-activation"),
        (*FORWARD_FIX_ARGS, "sha256:" + "ab" * 32),
    ],
)
def test_invalid_forward_fix_selector_refuses_before_docker(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    deploy_script, env, log = _deployment_harness(tmp_path)

    result = _run(deploy_script, env, *arguments)

    assert result.returncode == 2
    assert "ERROR:" in result.stderr
    assert not _invocations(log)


def test_unexpected_pulled_checkout_refuses_before_drain_or_migration(
    tmp_path: Path,
) -> None:
    deploy_script, env, log = _deployment_harness(tmp_path)
    other_sha = "f" * 40

    result = _run(
        deploy_script,
        env,
        "--forward-fix-after-migration",
        f"--expected-checkout-sha={other_sha}",
    )

    assert result.returncode == 2
    assert "forward-fix checkout" in result.stderr
    commands = _invocations(log)
    assert not any("|compose stop " in line for line in commands)
    assert not any("alembic upgrade heads" in line for line in commands)


def test_forward_fix_proves_old_runtimes_absent_before_migration(
    tmp_path: Path,
) -> None:
    deploy_script, env, log = _deployment_harness(tmp_path)

    result = _run(deploy_script, env, *FORWARD_FIX_ARGS)

    assert result.returncode == 0, result.stderr
    commands = _invocations(log)
    drained = next(
        i for i, line in enumerate(commands) if "|compose stop app worker beat" in line
    )
    migrated = next(
        i for i, line in enumerate(commands) if "alembic upgrade heads" in line
    )
    assert drained < migrated
    for service in ("app", "app-dev", "worker", "beat"):
        assert any(
            drained < i < migrated
            and f"label=com.docker.compose.service={service}" in line
            for i, line in enumerate(commands)
        ), service
    assert "-e PEOPLE_EMPLOYMENT_TYPE_ACTIVATION=1" not in commands[migrated]


def test_forward_fix_refuses_a_remaining_old_one_off_before_migration(
    tmp_path: Path,
) -> None:
    deploy_script, env, log = _deployment_harness(tmp_path)
    env["DEPLOY_TEST_RUNNING_ONE_OFF"] = "1"

    result = _run(deploy_script, env, *FORWARD_FIX_ARGS)

    assert result.returncode == 1
    assert "legacy-capable Compose containers remain" in result.stderr
    assert not any("alembic upgrade heads" in line for line in _invocations(log))


@pytest.mark.parametrize(
    "failure_flag",
    [
        "DEPLOY_TEST_FAIL_MIGRATION",
        "DEPLOY_TEST_FAIL_ADMISSION",
        "DEPLOY_TEST_FAIL_HEALTH",
    ],
)
def test_forward_fix_never_rolls_back_after_migration_attempt(
    tmp_path: Path, failure_flag: str
) -> None:
    deploy_script, env, log = _deployment_harness(tmp_path)
    env[failure_flag] = "1"

    result = _run(deploy_script, env, *FORWARD_FIX_ARGS)

    assert result.returncode == 1
    assert "FORWARD-FIX-ONLY" in result.stderr
    assert any("alembic upgrade heads" in line for line in _invocations(log))
    _assert_no_old_restart(result, log)


@pytest.mark.parametrize("failed_runtime", ["worker", "beat"])
def test_forward_fix_worker_or_beat_failure_does_not_restart_old_runtime(
    tmp_path: Path, failed_runtime: str
) -> None:
    deploy_script, env, log = _deployment_harness(tmp_path)
    env["DEPLOY_TEST_FAIL_RUNTIME_ADMISSION"] = failed_runtime

    result = _run(deploy_script, env, *FORWARD_FIX_ARGS)

    assert result.returncode == 1
    assert "FORWARD-FIX-ONLY" in result.stderr
    _assert_no_old_restart(result, log)
