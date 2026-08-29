"""The protected-main artifact is one audited, non-root runtime image."""

from __future__ import annotations

import tomllib
from pathlib import Path

from scripts.bootstrap_instance import generate_docker_compose, generate_setup_script


REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
COMPOSE = REPO_ROOT / "docker-compose.yml"
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy.sh"
ENTRYPOINT = REPO_ROOT / "scripts" / "entrypoint-monitoring.sh"
PYPROJECT = REPO_ROOT / "pyproject.toml"
LOCKFILE = REPO_ROOT / "poetry.lock"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
MAKEFILE = REPO_ROOT / "Makefile"
DEPLOY_DOC = REPO_ROOT / "docs" / "deployment.md"

HARDENED_RUN_FLAGS = (
    "--read-only",
    "--user 10001:10001",
    "--tmpfs /tmp:rw,nosuid,nodev,size=256m",
    "--cap-drop ALL",
    "--security-opt no-new-privileges:true",
)


def _docker_job() -> str:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    return workflow.split("  docker-build:\n", 1)[1].split(
        "\n  integration-test:\n", 1
    )[0]


def _named_step(job: str, name: str) -> str:
    marker = f"      - name: {name}"
    step = job.split(marker, 1)[1]
    return step.split("\n      - name:", 1)[0]


def _docker_run(step: str, container_name: str) -> str:
    """Return one docker-run command even when a YAML step starts several."""
    marker = f"--name {container_name}"
    marker_at = step.index(marker)
    start = step.rfind("docker run", 0, marker_at)
    assert start >= 0, f"docker run not found for {container_name}"
    end = step.find("docker run", marker_at + len(marker))
    return step[start:] if end < 0 else step[start:end]


def _hardened_process_runs(job: str) -> dict[str, str]:
    celery = _named_step(
        job, "Start Celery worker and scheduler  # pragma: allowlist secret"
    )
    return {
        "role-bootstrap": _docker_run(
            _named_step(job, "Bootstrap database roles (explicitly privileged)"),
            "ci-role-bootstrap",
        ),
        "migrate": _docker_run(
            _named_step(job, "Run database migrations"), "ci-migrate"
        ),
        "app": _docker_run(
            _named_step(job, "Start application  # pragma: allowlist secret"),
            "ci-app",
        ),
        "worker": _docker_run(celery, "ci-worker"),
        "beat": _docker_run(celery, "ci-beat"),
    }


def _missing_hardened_flags(command: str) -> tuple[str, ...]:
    return tuple(flag for flag in HARDENED_RUN_FLAGS if flag not in command)


def test_runtime_stage_contains_only_named_runtime_surfaces() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    runtime = source.split("FROM python:3.12-slim AS runtime\n", 1)[1]

    assert "ARG POETRY_VERSION=2.4.1" in source
    assert "FROM python:3.12-slim AS dependency-builder" in source
    assert "POETRY_VIRTUALENVS_CREATE=0" in source
    assert "python -m venv /opt/venv" in source
    assert "poetry install --only main --no-root --no-ansi" in source
    assert "COPY --from=dependency-builder /opt/venv /opt/venv" in runtime

    assert "COPY . ." not in source
    assert "poetry install" not in runtime.lower()
    assert "pyproject.toml" not in runtime
    assert "poetry.lock" not in runtime
    assert "pip install" not in runtime
    assert "COPY tests" not in runtime

    for required_copy in (
        "COPY app ./app",
        "COPY alembic ./alembic",
        "COPY alembic.ini ./alembic.ini",
        "COPY gunicorn.conf.py ./gunicorn.conf.py",
        "COPY locales ./locales",
        "COPY templates ./templates",
        "COPY static ./static",
        "COPY scripts/bootstrap_database_roles.py ./scripts/bootstrap_database_roles.py",
        "COPY scripts/verify_runtime_admission.py ./scripts/verify_runtime_admission.py",
    ):
        assert required_copy in runtime

    assert "PATH=/opt/venv/bin:$PATH" in runtime
    assert "PYTHONPYCACHEPREFIX=/tmp/pycache" in runtime
    assert "TMPDIR=/tmp" in runtime
    assert "XDG_CACHE_HOME=/tmp/.cache" in runtime
    assert runtime.count("USER 10001:10001") == 1
    assert "EXPOSE 8002" in runtime
    assert 'CMD ["gunicorn", "-c", "gunicorn.conf.py", "app.main:app"]' in runtime


def test_runtime_compatibility_pin_is_part_of_the_lock_input() -> None:
    pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    lock = tomllib.loads(LOCKFILE.read_text(encoding="utf-8"))

    assert pyproject["tool"]["poetry"]["dependencies"]["pydyf"] == "0.11.0"
    pydyf = [package for package in lock["package"] if package["name"] == "pydyf"]
    assert len(pydyf) == 1
    assert pydyf[0]["version"] == "0.11.0"


def test_operator_and_report_material_never_enters_the_build_context() -> None:
    ignored = {
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert {
        "license/*",
        "reports/",
        "proposals/",
        "asset_import_cleaned.csv",
        "stock_tracked_items.csv",
        "go-live-checklist.html",
        "ui-audit-report.html",
    } <= ignored
    assert "!license/.gitkeep" in ignored


def test_boot_time_installer_is_retired_from_all_compose_roles() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")

    assert not ENTRYPOINT.exists()
    assert "entrypoint-monitoring.sh" not in compose
    assert "entrypoint:" not in compose
    assert 'python -c "import logging_loki"' not in compose
    assert "http://localhost:8002/health/ready" in compose


def test_source_builds_receive_the_private_index_token_as_a_file_secret() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    app_dev = compose.split("  app-dev:\n", 1)[1].split("\n  worker:\n", 1)[0]
    generated = generate_docker_compose("ACME")
    setup = generate_setup_script("ACME")

    assert "build:\n      context: .\n      secrets:\n      - forgejo_token" in app_dev
    assert (
        "file: ${FORGEJO_TOKEN_FILE:-/run/secrets/dotmac/forgejo-read-token}" in compose
    )

    assert generated.count("secrets:\n        - forgejo_token") == 3
    assert "build: ${APP_BUILD_CONTEXT" not in generated
    assert "file: ${FORGEJO_TOKEN_FILE:?" in generated
    assert "FORGEJO_TOKEN_FILE must be an absolute path" in setup
    assert "must identify a non-empty readable regular file" in setup
    assert setup.index("export FORGEJO_TOKEN_FILE") < setup.index(
        "docker compose up -d db redis openbao"
    )
    assert 'cat "$FORGEJO_TOKEN_FILE"' not in setup


def test_container_migration_documentation_uses_the_runtime_executable() -> None:
    deployment = DEPLOY_DOC.read_text(encoding="utf-8")

    assert "docker compose exec app alembic upgrade heads" in deployment
    assert "docker compose exec app poetry run alembic" not in deployment


def test_deploy_containers_do_not_depend_on_builder_poetry() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "poetry run" not in script
    assert "--entrypoint" not in script
    assert "--entrypoint" not in makefile
    assert "python scripts/bootstrap_database_roles.py --verify-only" in script
    assert "python scripts/verify_runtime_admission.py" in script
    assert "alembic upgrade heads" in script
    assert "http://localhost:8003/health/ready" in script


def test_ci_runs_every_product_process_in_the_hardened_envelope() -> None:
    job = _docker_job()
    runs = _hardened_process_runs(job)

    assert set(runs) == {"role-bootstrap", "migrate", "app", "worker", "beat"}
    for process, command in runs.items():
        assert not _missing_hardened_flags(command), process
        assert command.count("dotmac-erp:ci") == 1, process

    assert "python scripts/bootstrap_database_roles.py" in runs["role-bootstrap"]
    assert "alembic upgrade heads" in runs["migrate"]
    app = runs["app"]
    assert "dotmac-erp:ci\n" in app
    assert "gunicorn -c" not in app  # exercise the image's default CMD
    assert "celery -A app.celery_app worker -l info" in runs["worker"]
    assert "celery -A app.celery_app beat -l info" in runs["beat"]

    operational = _named_step(
        job, "Verify Celery worker and scheduler stay operational"
    )
    assert "stat -c %Y /tmp/dotmac-erp-beat-heartbeat" in operational
    assert "age > 120" in operational


def test_hardened_process_detector_rejects_each_planted_missing_flag() -> None:
    runs = _hardened_process_runs(_docker_job())

    for planted_process, command in runs.items():
        for planted_flag in HARDENED_RUN_FLAGS:
            planted = command.replace(planted_flag, "--planted-missing-flag", 1)
            assert planted != command
            candidates = {**runs, planted_process: planted}
            detected = {
                process: _missing_hardened_flags(candidate)
                for process, candidate in candidates.items()
            }
            assert detected[planted_process] == (planted_flag,)
            assert all(
                not missing
                for process, missing in detected.items()
                if process != planted_process
            )


def test_ci_uses_dependency_readiness_not_the_legacy_liveness_alias() -> None:
    job = _docker_job()
    health = _named_step(job, "Wait for health check")

    assert "http://localhost:8002/health/ready" in health
    assert "payload['status']" in health
    assert "http://localhost:8002/health'" not in health


def test_ci_audits_the_exact_tested_image_before_exporting_it() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    job = _docker_job()
    audit = _named_step(job, "Audit tested runtime image")

    python_setup = _named_step(job, "Install the declared audit Python")
    assert "actions/setup-python@v5" in python_setup
    assert "python-version: ${{ env.PYTHON_VERSION }}" in python_setup
    assert workflow.count('FOUNDATION_IMAGE_AUDIT_VERSION: "0.2.0a2"') == 1
    assert "PENDING_CORRECTED_IMAGE_AUDIT_RELEASE" not in workflow
    assert "dotmac-deployment-foundation==${FOUNDATION_IMAGE_AUDIT_VERSION}" in audit
    assert "dotmac-deployment-foundation==0." not in audit
    assert "docker image inspect dotmac-erp:ci" in audit
    assert "docker history --no-trunc" in audit
    assert "docker run --rm --user 0:0 --entrypoint sh dotmac-erp:ci" in audit
    assert "find / -xdev -type f 2>/dev/null" not in audit
    assert "find / -xdev -type f" in audit
    assert "dotmac-deploy image-audit" in audit
    assert job.index("- name: Audit tested runtime image") < job.index(
        "- name: Export the tested image"
    )


def test_ci_refuses_a_malformed_audit_coordinate() -> None:
    audit = _named_step(_docker_job(), "Audit tested runtime image")

    assert 'if [[ ! "${FOUNDATION_IMAGE_AUDIT_VERSION}" =~' in audit
    assert "Foundation image-audit release is unresolved" in audit
    assert "exit 1" in audit
