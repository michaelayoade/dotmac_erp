"""Security and durability contracts for the standalone Traccar stack."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "deploy" / "traccar" / "compose.yml"
BACKUP_PATH = REPO_ROOT / "scripts" / "backup_traccar_db.sh"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def test_images_are_versioned_and_digest_pinned() -> None:
    services = _compose()["services"]
    assert services["traccar"]["image"].startswith("traccar/traccar:6.15.3@sha256:")
    assert services["traccar-db"]["image"].startswith("postgres:16.15-bookworm@sha256:")
    assert ":latest" not in COMPOSE_PATH.read_text(encoding="utf-8")


def test_only_gps103_is_published_publicly() -> None:
    services = _compose()["services"]
    traccar_ports = services["traccar"]["ports"]
    public_ports = [port for port in traccar_ports if port["host_ip"] == "0.0.0.0"]

    assert public_ports == [
        {
            "name": "gps103",
            "target": 5001,
            "published": "5001",
            "host_ip": "0.0.0.0",
            "protocol": "tcp",
            "app_protocol": "gps103",
        }
    ]
    assert services["traccar-db"].get("ports") is None


def test_http_is_loopback_only_and_database_network_is_internal() -> None:
    config = _compose()
    http = next(
        port
        for port in config["services"]["traccar"]["ports"]
        if port["target"] == 8082
    )
    assert http["host_ip"] == "127.0.0.1"
    assert config["networks"]["traccar_data"]["internal"] is True


def test_database_secret_is_file_backed_and_not_literal() -> None:
    config = _compose()
    db_environment = config["services"]["traccar-db"]["environment"]
    traccar_environment = config["services"]["traccar"]["environment"]

    assert db_environment["POSTGRES_PASSWORD_FILE"].startswith("/run/secrets/")
    assert "POSTGRES_PASSWORD" not in db_environment
    assert "DATABASE_PASSWORD" not in traccar_environment
    assert "file" in config["secrets"]["traccar_db_password"]


def test_services_have_health_checks_restart_and_persistent_storage() -> None:
    services = _compose()["services"]
    for name in ("traccar", "traccar-db"):
        assert services[name]["restart"] == "unless-stopped"
        assert services[name]["healthcheck"]["retries"] > 0
        assert services[name]["volumes"]

    traccar_health = " ".join(services["traccar"]["healthcheck"]["test"])
    assert "/api/health" in traccar_health
    assert "5001" in traccar_health
    assert services["traccar"]["depends_on"]["traccar-db"]["condition"] == (
        "service_healthy"
    )


def test_gps103_is_explicit_and_no_erp_connection_is_configured() -> None:
    config = _compose()
    environment = config["services"]["traccar"]["environment"]
    all_text = COMPOSE_PATH.read_text(encoding="utf-8").lower()

    assert environment["GPS103_PORT"] == "5001"
    assert "dotmac-erp" not in config["services"]
    assert "traccar_base_url" not in all_text


def test_backup_targets_the_dedicated_database_and_hides_role_passwords() -> None:
    script = BACKUP_PATH.read_text(encoding="utf-8")
    assert 'DB_CONTAINER="${DB_CONTAINER:-dotmac_traccar_db}"' in script
    assert "read_container_env POSTGRES_USER" in script
    assert "read_container_env POSTGRES_DB" in script
    assert 'psql -U "${DB_USER}" -d "${DB_NAME}"' in script
    assert 'pg_dumpall -U "${DB_USER}"' in script
    assert 'pg_dump -U "${DB_USER}" -d "${DB_NAME}"' in script
    assert "DB_USER:-postgres" not in script
    assert "--globals-only --no-role-passwords" in script
    assert "pg_restore --list" in script
    assert 'REMOTE_DIR="${REMOTE_DIR:-${REMOTE}/traccar}"' in script


def test_backup_executes_with_the_compose_database_role(tmp_path: Path) -> None:
    database_environment = _compose()["services"]["traccar-db"]["environment"]
    assert database_environment["POSTGRES_USER"] == "traccar"
    assert database_environment["POSTGRES_DB"] == "traccar"

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    command_log = tmp_path / "docker-commands.log"
    fake_docker = bin_dir / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"${DOCKER_COMMAND_LOG}"
if [[ "$1" == "inspect" ]]; then
  printf 'POSTGRES_USER=traccar\nPOSTGRES_DB=traccar\n'
  exit 0
fi
case " $* " in
  *" psql "*) printf '1\n' ;;
  *" pg_dumpall "*) printf 'CREATE ROLE traccar;\nALTER ROLE traccar WITH SUPERUSER;\n' ;;
  *" pg_dump "*) printf 'fake-custom-archive' ;;
  *" pg_restore --list "*) printf '1; 0 0 TABLE public tc_devices traccar\n' ;;
  *) printf 'unexpected docker invocation: %s\n' "$*" >&2; exit 64 ;;
esac
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "DOCKER_COMMAND_LOG": str(command_log),
        "LOCAL_DIR": str(tmp_path / "backups"),
        "SKIP_UPLOAD": "1",
    }
    completed = subprocess.run(  # noqa: S603 - fixed shell and repository path
        ["/usr/bin/bash", str(BACKUP_PATH)],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    invocations = command_log.read_text(encoding="utf-8")
    assert "psql -U traccar -d traccar --no-password" in invocations
    assert "pg_dumpall -U traccar --no-password --globals-only" in invocations
    assert "pg_dump -U traccar -d traccar --no-password -Fc" in invocations
    assert " -U postgres" not in invocations

    artifacts = list((tmp_path / "backups").iterdir())
    assert len(artifacts) == 2
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in artifacts)
