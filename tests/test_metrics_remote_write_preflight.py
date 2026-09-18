"""Credential-safe metrics preflight and real deployment-path refusal."""

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


@pytest.mark.parametrize(
    "url",
    [
        "http://victoriametrics:8428/api/v1/write",
        "https://metrics.dotmac.io/api/v1/write",
        "http://10.1.2.3:8428/api/v1/write",
        "http://[::1]:8428/api/v1/write",
        "https://receiver.local/write?tenant=production",
    ],
)
def test_real_internal_or_public_receivers_are_allowed(url):
    validate_remote_write_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://VM_REMOTE_WRITE_URL-is-unset.invalid/api/v1/write",
        "http://UNSET.INVALID./write",
        "${VM_REMOTE_WRITE_URL}",
        "https://example.com/write",
        "ftp://receiver/write",
        "http://receiver:99999/write",
        "http://receiver/write#fragment",
        "http://bad host/write",
        "http://receiver/%20 path",
        "http:///write",
    ],
)
def test_invalid_or_placeholder_destinations_are_refused(url):
    with pytest.raises(MetricsConfigurationError):
        validate_remote_write_url(url)


def test_split_command_argument_is_supported():
    validate_compose_metrics(
        {
            "services": {
                "vmagent": {
                    "command": ["-remoteWrite.url", "http://receiver:8428/api/v1/write"]
                }
            }
        }
    )


@pytest.mark.parametrize(
    "config", [{}, {"services": {}}, {"services": {"vmagent": {"command": []}}}]
)
def test_missing_receiver_configuration_is_refused(config):
    with pytest.raises(MetricsConfigurationError):
        validate_compose_metrics(config)


def test_cli_never_echoes_sensitive_configuration():
    script = (
        Path(__file__).resolve().parents[1] / "scripts/validate_metrics_remote_write.py"
    )
    payload = {
        "services": {
            "vmagent": {
                "command": [
                    "-remoteWrite.url=https://private-user:private-password@placeholder.invalid/write"
                ]
            }
        }
    }
    result = subprocess.run(  # noqa: S603 - repository-owned synthetic test harness
        [sys.executable, str(script)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "private-password" not in result.stdout + result.stderr
    assert "private-user" not in result.stdout + result.stderr


def test_deployment_refuses_placeholder_before_migration_or_runtime_changes(tmp_path):
    script, env, log = _deployment_harness(tmp_path)
    env["DEPLOY_TEST_METRICS_URL"] = (
        "http://VM_REMOTE_WRITE_URL-is-unset.invalid/api/v1/write"
    )
    result = subprocess.run(  # noqa: S603 - repository-owned synthetic test harness
        ["/bin/bash", str(script)], env=env, text=True, capture_output=True
    )
    assert result.returncode != 0
    assert "invalid metrics" in result.stderr
    calls = log.read_text()
    assert "compose config --format json" in calls
    assert "compose run" not in calls
    assert "compose up" not in calls
    assert "compose pull" not in calls
