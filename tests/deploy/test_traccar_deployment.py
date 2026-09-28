"""Security and durability contracts for the standalone Traccar stack."""

from __future__ import annotations

from pathlib import Path

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
    assert 'DB_NAME="${DB_NAME:-traccar}"' in script
    assert "--globals-only --no-role-passwords" in script
    assert "pg_restore --list" in script
    assert 'REMOTE_DIR="${REMOTE_DIR:-${REMOTE}/traccar}"' in script
