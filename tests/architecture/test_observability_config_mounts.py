"""Release-shipped agent config must be bind-mounted as a directory.

A single-file bind mount pins the inode that existed when the container was
created. `git pull` replaces a changed tracked file with a NEW inode, so the
running container keeps reading the old content; a SIGHUP re-reads the same
stale inode, and only recreating the container picks up the change. vmagent
kept a two-week-old config (a literal `${DEPLOY_ENV}` environment label) on
production and staging that way, merging both deployments' metrics into the
same series. A directory mount resolves file names at open time, so the
container sees the pulled file.

See docs/runbooks/VMAGENT_ENVIRONMENT_LABELS.md.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = sorted(ROOT.glob("docker-compose*.yml"))
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy.sh"

#: service -> (checkout directory, container directory, config file name,
#: command flag naming the config file). The file list is exhaustive on
#: purpose: every file in the directory becomes visible to the container, so a
#: new file there must be a deliberate, reviewed addition.
AGENTS = {
    "vmagent": (
        "./config/vmagent",
        "/etc/vmagent",
        "config.yml",
        "-promscrape.config=",
    ),
    "promtail": (
        "./config/promtail",
        "/etc/promtail",
        "promtail-config.yml",
        "-config.file=",
    ),
}
EXPECTED_DIRECTORY_CONTENTS = {
    "vmagent": {"config.yml"},
    "promtail": {"promtail-config.yml"},
}


def _services(path: Path) -> dict:
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("services", {})


def _bind_mounts(service: dict) -> list[tuple[str, str]]:
    """Return (source, target) for every bind mount, short or long syntax."""
    mounts = []
    for volume in service.get("volumes", []) or []:
        if isinstance(volume, str):
            parts = volume.split(":")
            if len(parts) >= 2 and parts[0].startswith((".", "/", "~")):
                mounts.append((parts[0], parts[1]))
        elif isinstance(volume, dict) and volume.get("type") == "bind":
            mounts.append((volume["source"], volume["target"]))
    return mounts


def _command(service: dict) -> list[str]:
    command = service.get("command", [])
    return command.split() if isinstance(command, str) else list(command)


def test_compose_files_are_discovered():
    assert ROOT / "docker-compose.yml" in COMPOSE_FILES


@pytest.mark.parametrize("compose", COMPOSE_FILES, ids=lambda p: p.name)
def test_no_checkout_file_is_bind_mounted_as_a_single_file(compose: Path):
    """Every bind mount sourced from ./config must be a directory."""
    offenders = []
    for name, service in _services(compose).items():
        for source, target in _bind_mounts(service):
            if not source.startswith("./config"):
                continue
            if not (ROOT / source).is_dir():
                offenders.append(f"{name}: {source}:{target}")
    assert offenders == [], (
        "single-file bind mounts go stale after `git pull` replaces the inode; "
        f"mount the containing directory instead: {offenders}"
    )


@pytest.mark.parametrize("agent", sorted(AGENTS))
def test_agent_config_is_mounted_as_a_directory(agent: str):
    source, target, filename, flag = AGENTS[agent]
    service = _services(ROOT / "docker-compose.yml")[agent]
    mounts = _bind_mounts(service)

    assert (source, target) in mounts, mounts
    assert (ROOT / source).is_dir()
    # No other mount reaches into the config directory as a single file.
    assert not [
        m
        for m in mounts
        if m[0].startswith(source + "/") or m[1].startswith(target + "/")
    ], mounts
    # The agent reads its config through the directory mount.
    assert f"{flag}{target}/{filename}" in _command(service)
    assert (ROOT / source / filename).is_file()


@pytest.mark.parametrize("agent", sorted(AGENTS))
def test_mounted_config_directory_exposes_only_the_agent_config(agent: str):
    source = AGENTS[agent][0]
    # .DS_Store is Finder noise on developer checkouts, never deployed content.
    contents = {p.name for p in (ROOT / source).iterdir()} - {".DS_Store"}
    assert contents == EXPECTED_DIRECTORY_CONTENTS[agent], (
        f"{source} is mounted into the {agent} container in full; anything "
        "added here (especially a secret) becomes readable by it"
    )


def test_deploy_restarts_agents_whose_config_changed():
    """`up -d` does not recreate a container for a config-only change."""
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    declared = re.search(r"OBSERVABILITY_CONFIG_AGENTS=\(([^)]*)\)", script)
    assert declared, "deploy.sh no longer declares OBSERVABILITY_CONFIG_AGENTS"
    entries = set(re.findall(r'"([^"]+)"', declared.group(1)))
    assert entries == {
        f"{agent}:{source.removeprefix('./')}"
        for agent, (source, *_rest) in AGENTS.items()
    }
    assert 'docker compose restart "$service"' in script
    assert (
        'restart_agents_whose_config_changed "$PREV_SHA" "$(git rev-parse HEAD)"'
        in script
    )
    assert 'restart_agents_whose_config_changed "$failed_sha" "$PREV_SHA"' in script
