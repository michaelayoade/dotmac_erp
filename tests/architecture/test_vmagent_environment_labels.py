"""Guard the bind-mounted vmagent configuration's environment label contract."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "vmagent" / "config.yml"
COMPOSE = ROOT / "docker-compose.yml"


def _config():
    return yaml.safe_load(CONFIG.read_text())


def test_environment_label_uses_vmagent_native_placeholder():
    labels = _config()["global"]["external_labels"]
    assert labels["environment"] == "%{DEPLOY_ENV}"
    assert labels["app"] == "dotmac_erp"
    assert not any("${" in str(value) for value in labels.values())


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_native_expansion_contract_produces_the_deployment_environment(environment):
    # Contract projection of the documented %{NAME} syntax, not an integration
    # test of the agent binary. Compose does not expand a bind-mounted file.
    raw_label = _config()["global"]["external_labels"]["environment"]
    expanded = re.sub(r"%\{DEPLOY_ENV\}", environment, raw_label)
    assert expanded == environment


def test_same_job_and_instance_do_not_collide_between_environments():
    config = _config()
    identities = {}
    for environment in ("production", "staging"):
        labels = dict(config["global"]["external_labels"])
        labels["environment"] = labels["environment"].replace(
            "%{DEPLOY_ENV}", environment
        )
        identities[environment] = {
            (
                labels["app"],
                labels["environment"],
                job["job_name"],
                job["relabel_configs"][0]["replacement"],
            )
            for job in config["scrape_configs"]
        }
    assert identities["production"].isdisjoint(identities["staging"])


def test_compose_supplies_environment_to_the_unmodified_bind_mount():
    agent = yaml.safe_load(COMPOSE.read_text())["services"]["vmagent"]
    assert "DEPLOY_ENV" in agent["environment"]
    assert agent["environment"]["DEPLOY_ENV"].startswith("${DEPLOY_ENV")
    assert "./config/vmagent/config.yml:/etc/vmagent/config.yml:ro" in agent["volumes"]
    assert "-promscrape.config=/etc/vmagent/config.yml" in agent["command"]


def test_environment_fix_preserves_scrape_authentication_and_targets():
    jobs = {job["job_name"]: job for job in _config()["scrape_configs"]}
    app = jobs["dotmac-erp-app"]
    assert app["authorization"] == {
        "type": "Bearer",
        "credentials": "%{METRICS_TOKEN}",
    }
    assert app["static_configs"][0]["targets"] == ["app:8002"]
    assert jobs["dotmac-erp-worker"]["static_configs"][0]["targets"] == ["worker:8004"]
