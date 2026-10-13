"""Execute the existing deploy owner against recording commands, never Docker."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from tests.test_deploy_script import (
    HARNESS_DIGEST,
    IMAGE_REPOSITORY,
    REPO_ROOT,
    _deployment_harness,
)


def _run(
    script: Path, env: dict[str, str], *args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [str(script), *args], env=env, check=False, capture_output=True, text=True
    )


def test_candidate_checks_use_every_mandatory_role_before_ddl_and_replacement(
    tmp_path: Path,
) -> None:
    script, env, log = _deployment_harness(tmp_path)
    env["SKIP_BACKUP"] = "0"
    # Already-running Beat does not evade the mandatory role check.
    env["DEPLOY_TEST_RUNNING_SERVICES"] = "app worker beat"
    result = _run(script, env)
    assert result.returncode == 0, result.stderr
    commands = log.read_text().splitlines()
    backup = commands.index("dotmac|backup")
    pull = commands.index("dotmac|git pull")
    migrated = next(
        i for i, line in enumerate(commands) if "alembic upgrade heads" in line
    )
    admitted = next(
        i for i, line in enumerate(commands) if "verify_runtime_admission.py" in line
    )
    started = next(i for i, line in enumerate(commands) if "|compose up " in line)
    probes = [
        (i, line)
        for i, line in enumerate(commands)
        if "scripts.verify_deployment_secrets" in line
    ]
    assert len(probes) == 6
    for index, service in enumerate(("app", "worker", "beat")):
        before, after = probes[index], probes[index + 3]
        assert backup < pull < before[0] < migrated < admitted < after[0] < started
        expected = (
            "|compose run --rm --no-deps -T --entrypoint python "
            f"{service} -m scripts.verify_deployment_secrets"
        )
        assert before[1].endswith(expected)
        assert after[1].endswith(expected)
        binding = f"secret-binding|{service}|{IMAGE_REPOSITORY}@{HARNESS_DIGEST}"
        assert commands.count(binding) == 2
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    for service in ("app", "worker", "beat"):
        role = compose["services"][service]
        assert not role.get("profiles"), (
            "update the existing role owner if topology changes"
        )
        assert role["env_file"] == [".env"]
        assert role["environment"]["MIGRATION_DATABASE_URL"] == ""
        assert "APP_IMAGE" in role["image"]


@pytest.mark.parametrize("phase", ["migration", "replacement"])
@pytest.mark.parametrize("service", ["app", "worker", "beat"])
@pytest.mark.parametrize("status", [1, 127])
def test_candidate_refusal_is_redacted_and_prevents_startup(
    tmp_path: Path, phase: str, service: str, status: int
) -> None:
    script, env, log = _deployment_harness(tmp_path)
    env["DEPLOY_TEST_SECRET_FAILURE"] = f"{phase}:{service}"
    env["DEPLOY_TEST_SECRET_EXIT"] = str(status)
    result = _run(script, env)
    assert result.returncode == 1
    assert f"candidate deployment secret check refused for {service}." in result.stderr
    assert "PRIVATE_PROBE" not in result.stdout + result.stderr
    commands = log.read_text().splitlines()
    assert not any("|compose up " in line for line in commands)
    assert not any("|compose exec " in line for line in commands)
    assert any("alembic upgrade heads" in line for line in commands) == (
        phase == "replacement"
    )
    assert "Rolling back code" not in result.stdout


@pytest.mark.parametrize("mode", ["forward-fix", "activation"])
def test_postmigration_refusal_preserves_forward_only_standing(
    tmp_path: Path, mode: str
) -> None:
    script, env, log = _deployment_harness(tmp_path)
    env["DEPLOY_TEST_SECRET_FAILURE"] = "replacement:beat"
    args = (
        (
            "--forward-fix-after-migration",
            "--expected-checkout-sha=" + "abcdef0123456789abcdef0123456789abcdef01",
        )
        if mode == "forward-fix"
        else ("--people-employment-type-activation",)
    )
    result = _run(script, env, *args)
    assert result.returncode == 1
    assert "FORWARD-FIX-ONLY" in result.stderr
    assert "Migrations remain applied" in result.stderr
    assert "Rolling back code" not in result.stdout
    assert not any("|compose up " in line for line in log.read_text().splitlines())
