"""Validate effective metrics configuration before touching running services."""

BRANCH = "fix/erp-metrics-destination-preflight"
TITLE = "fix(deploy): refuse placeholder metrics destinations before runtime changes"
TESTS = ["tests/test_metrics_remote_write_preflight.py", "tests/test_deploy_script.py"]


def apply(change, write, rewrite):
    write("scripts/validate_metrics_remote_write.py", '''"""Validate vmagent's effective Compose command without exposing credentials.

Read `docker compose config --format json` from stdin. This deliberately uses
Compose's own interpolation rather than evaluating .env as shell code. Internal
receiver names and private IP addresses are supported. No network request is
made: ingestion must still be verified against the actual receiver after deploy.
"""

from __future__ import annotations

import ipaddress
import json
import re
import shlex
import sys
from urllib.parse import unquote, urlsplit


class MetricsConfigurationError(ValueError):
    """Only credential-free diagnostic messages may cross this boundary."""


def validate_remote_write_url(value: str) -> None:
    if not value or value != value.strip() or any(char.isspace() for char in value):
        raise MetricsConfigurationError("Metrics destination must be a nonempty absolute URL")
    if any(marker in value for marker in ("${", "<", ">", "\\\\")):
        raise MetricsConfigurationError("Metrics destination contains an unresolved placeholder")
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError:
        raise MetricsConfigurationError("Metrics destination has an invalid host or port") from None
    if parsed.scheme not in {"http", "https"} or not hostname or parsed.fragment:
        raise MetricsConfigurationError("Metrics destination requires HTTP(S), a host, and no fragment")
    if port is not None and not 1 <= port <= 65535:
        raise MetricsConfigurationError("Metrics destination port is invalid")
    decoded_host = unquote(hostname)
    if (
        decoded_host == "invalid"
        or decoded_host.endswith(".invalid")
        or "is-unset" in decoded_host
        or "vm_remote_write_url" in decoded_host
        or decoded_host in {"changeme", "replace-me", "example.com", "example.org"}
        or decoded_host.endswith((".example.com", ".example.org"))
    ):
        raise MetricsConfigurationError("Metrics destination is a placeholder, not a configured receiver")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9_.-]*[a-z0-9])?", hostname):
            raise MetricsConfigurationError("Metrics destination hostname is invalid") from None
        if any(not label or len(label) > 63 for label in hostname.split(".")):
            raise MetricsConfigurationError("Metrics destination hostname is invalid")


def validate_compose_metrics(config: object) -> None:
    if not isinstance(config, dict):
        raise MetricsConfigurationError("Expected effective Compose JSON configuration")
    services = config.get("services")
    service = services.get("vmagent") if isinstance(services, dict) else None
    if not isinstance(service, dict):
        raise MetricsConfigurationError("Effective Compose configuration is missing vmagent")
    command = service.get("command", [])
    if isinstance(command, str):
        try:
            command = shlex.split(command)
        except ValueError:
            raise MetricsConfigurationError("vmagent command is malformed") from None
    if not isinstance(command, list) or not all(isinstance(arg, str) for arg in command):
        raise MetricsConfigurationError("vmagent command is malformed")
    urls = []
    for index, argument in enumerate(command):
        name, separator, value = argument.partition("=")
        if name.lstrip("-") != "remoteWrite.url":
            continue
        if not separator:
            if index + 1 == len(command):
                raise MetricsConfigurationError("vmagent remoteWrite.url has no value")
            value = command[index + 1]
        urls.append(value)
    if not urls:
        raise MetricsConfigurationError("vmagent remoteWrite.url is missing")
    for value in urls:
        validate_remote_write_url(value)


def main() -> int:
    try:
        validate_compose_metrics(json.load(sys.stdin))
    except (MetricsConfigurationError, ValueError, TypeError):
        # Do not echo the effective config or destination: both can carry auth.
        print("ERROR: invalid metrics remote-write configuration; set a real HTTP(S) receiver URL and retry deployment.", file=sys.stderr)
        return 2
    print("Metrics remote-write destination configuration is valid (ingestion not yet verified).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''')
    change("scripts/deploy.sh", "# Step 1: pre-migration DB backup (SKIP_BACKUP=1 to skip)", '''# Use effective Compose interpolation, not an ad-hoc .env parser. Validate
# before backup/pull, and again after a pull can change the Compose command.
validate_metrics_configuration() {
    local validation_image="${APP_IMAGE:-}"
    if ! command -v python3 >/dev/null 2>&1; then
        echo "ERROR: python3 is required for metrics configuration preflight." >&2
        return 2
    fi
    if [[ -z "$validation_image" ]]; then
        validation_image="$("$SCRIPT_DIR/resolve_deploy_image.sh" \\
            --compose "$PROJECT_DIR/$RENDERED_COMPOSE")"
    fi
    ( export APP_IMAGE="$validation_image"; docker compose config --format json ) |
        python3 "$SCRIPT_DIR/validate_metrics_remote_write.py"
}
validate_metrics_configuration

# Step 1: pre-migration DB backup (SKIP_BACKUP=1 to skip)''')
    change("scripts/deploy.sh", '''    export APP_IMAGE="$NEW_IMAGE"
    echo "  Pinning image: ${APP_IMAGE}"''', '''    export APP_IMAGE="$NEW_IMAGE"
    validate_metrics_configuration
    echo "  Pinning image: ${APP_IMAGE}"''')
    change("tests/test_deploy_script.py", '''    shutil.copy2(IMAGE_GATE_PATH, scripts_dir / IMAGE_GATE_PATH.name)''', '''    shutil.copy2(IMAGE_GATE_PATH, scripts_dir / IMAGE_GATE_PATH.name)
    shutil.copy2(
        REPO_ROOT / "scripts/validate_metrics_remote_write.py",
        scripts_dir / "validate_metrics_remote_write.py",
    )''')
    change("tests/test_deploy_script.py", '''if args[:2] == ["inspect", "--format"]:''', '''if args[:2] == ["compose", "config"]:
    import json
    print(json.dumps({"services": {"vmagent": {"command": [
        "-remoteWrite.url=" + os.environ.get("DEPLOY_TEST_METRICS_URL", "http://victoriametrics:8428/api/v1/write")
    ]}}}))
    raise SystemExit(0)

if args[:2] == ["inspect", "--format"]:''')
    write("tests/test_metrics_remote_write_preflight.py", '''"""Credential-safe metrics preflight and real deployment-path refusal."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.validate_metrics_remote_write import (
    MetricsConfigurationError,
    validate_compose_metrics,
    validate_remote_write_url,
)
from tests.test_deploy_script import _deployment_harness


@pytest.mark.parametrize("url", [
    "http://victoriametrics:8428/api/v1/write",
    "https://metrics.dotmac.io/api/v1/write",
    "http://10.1.2.3:8428/api/v1/write",
    "http://[::1]:8428/api/v1/write",
    "https://receiver.local/write?tenant=production",
])
def test_real_internal_or_public_receivers_are_allowed(url):
    validate_remote_write_url(url)


@pytest.mark.parametrize("url", [
    "", "http://VM_REMOTE_WRITE_URL-is-unset.invalid/api/v1/write",
    "http://UNSET.INVALID./write", "${VM_REMOTE_WRITE_URL}",
    "https://example.com/write", "ftp://receiver/write",
    "http://receiver:99999/write", "http://receiver/write#fragment",
    "http://bad host/write", "http://receiver/%20 path", "http:///write",
])
def test_invalid_or_placeholder_destinations_are_refused(url):
    with pytest.raises(MetricsConfigurationError):
        validate_remote_write_url(url)


def test_split_command_argument_is_supported():
    validate_compose_metrics({"services": {"vmagent": {
        "command": ["-remoteWrite.url", "http://receiver:8428/api/v1/write"]
    }}})


@pytest.mark.parametrize("config", [{}, {"services": {}}, {"services": {"vmagent": {"command": []}}}])
def test_missing_receiver_configuration_is_refused(config):
    with pytest.raises(MetricsConfigurationError):
        validate_compose_metrics(config)


def test_cli_never_echoes_sensitive_configuration():
    script = Path(__file__).resolve().parents[1] / "scripts/validate_metrics_remote_write.py"
    payload = {"services": {"vmagent": {"command": [
        "-remoteWrite.url=https://private-user:private-password@placeholder.invalid/write"
    ]}}}
    result = subprocess.run([sys.executable, str(script)], input=json.dumps(payload), text=True, capture_output=True)
    assert result.returncode == 2
    assert "private-password" not in result.stdout + result.stderr
    assert "private-user" not in result.stdout + result.stderr


def test_deployment_refuses_placeholder_before_migration_or_runtime_changes(tmp_path):
    script, env, log = _deployment_harness(tmp_path)
    env["DEPLOY_TEST_METRICS_URL"] = "http://VM_REMOTE_WRITE_URL-is-unset.invalid/api/v1/write"
    result = subprocess.run(["bash", str(script)], env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert "invalid metrics" in result.stderr
    calls = log.read_text()
    assert "compose config --format json" in calls
    assert "compose run" not in calls
    assert "compose up" not in calls
    assert "compose pull" not in calls
''')
    write("docs/runbooks/metrics-remote-write-preflight.md", '''# Metrics remote-write preflight

Deployment validates the effective `vmagent` command before backup/pull and
again after the checkout changes. It rejects unset or placeholder destinations
without displaying the URL or any credentials. Internal receiver DNS names and
private addresses are supported; this is configuration validation, not an
external connectivity test.

Set the correct `VM_REMOTE_WRITE_URL` through the deployment's approved
configuration source. Preserve the existing persistent vmagent buffer. Run the
normal deployment procedure only after the preflight succeeds.

After rollout, verify fresh ERP series at the intended receiver and that the
vmagent pending buffer drains. A syntactically valid URL is not proof of remote
acceptance or ingestion. No receiver URL, credentials, buffers or live containers
are changed by this patch.
''')
