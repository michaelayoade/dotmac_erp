"""No long-running service can be handed the BYPASSRLS migration credential.

## What this gate reads, and why it is not the descriptor

`tests/architecture/test_deployment_descriptor.py::
test_no_role_holds_the_migration_owner_material` already checks that no role in
`deploy/product.toml` names `MIGRATION_DATABASE_URL`. It reads the DESCRIPTOR
and nothing else — `spec.roles`, parsed from one TOML file. That is a real
property and it is not this one.

The descriptor is not what runs. Two independent facts in this repository say
so:

* `deploy/README.md` § "What is still NOT the live path" states that
  `scripts/deploy.sh`, the root `docker-compose.yml`, the `Dockerfile` and the
  backup scripts remain the executing deployment, and that only the image
  identity has moved onto the rendered project.
* The renderer and the descriptor already disagree in a checkable way:
  `deploy/product.toml` declares `[ingress] trusted_proxies = ["172.16.0.0/12",
  "127.0.0.1"]`, and `TRUSTED_PROXY_IPS` appears nowhere under
  `deploy/rendered/`. A gate that reads only the source would report on a value
  the rendered artifact does not carry.

So this gate reads BYTES THAT ARE DEPLOYED OR WILL BE: the root
`docker-compose.yml` (today's production topology) and
`deploy/rendered/docker-compose.yml` (the rendered project, and the file
`scripts/deploy.sh` already consumes for the image reference). It does not open
`deploy/product.toml` at all — asserted below, so this gate cannot be passing
because the descriptor happens to be clean.

## The property

`MIGRATION_DATABASE_URL` connects as `app_admin`, which is `BYPASSRLS` by
contract (`app/migration_database_roles.py::ROLE_CONTRACT`) and owns every
non-extension object in the database. A long-running, network-facing process
that holds it can read and write every organization's rows and issue arbitrary
DDL for the life of the deployment.

A service is EXPOSED when either is true:

1. it names the variable with a non-empty value, or
2. it declares any `env_file:` and does not explicitly neutralise the variable.

Rule 2 is the half that a prose rule cannot do. The production entrypoint census
records the credential inside the very `.env` that `app`, `worker` and `beat`
read, while `.env.example` merely ASKS for it to be left blank. This gate does
not need to know what a host's `.env` contains: any `env_file` is treated as
capable of carrying the value, and only an explicit empty `environment:` entry
— which Compose applies over `env_file` — clears the service.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

OWNER_MATERIAL = "MIGRATION_DATABASE_URL"

#: The live production topology. `scripts/deploy.sh` runs `docker compose` in
#: the checkout root, so this file IS the deployed project today.
LIVE_COMPOSE = REPO_ROOT / "docker-compose.yml"

#: The rendered project. Not yet the runtime topology (deploy/README.md), but
#: already consumed by the deploy path for the image reference, and the file a
#: cutover would promote wholesale. Gated now so a cutover cannot reintroduce
#: what this change removes.
RENDERED_COMPOSE = REPO_ROOT / "deploy" / "rendered" / "docker-compose.yml"

#: Services permitted to hold the material, per artifact. A one-shot executor
#: that exits is not a network-facing process; a `restart: unless-stopped`
#: service is.
#:
#: The live artifact's set is EMPTY and that is the point. Its one-shot
#: migration executor is not a service at all — `scripts/deploy.sh` runs
#: `docker compose run --rm -e MIGRATION_DATABASE_URL app ...`, a disposable
#: container off the `app` service definition, and the credential arrives on the
#: `--env` flag rather than from the file. So the `app` SERVICE must be clean
#: even though the `app` ONE-SHOT is the executor.
DECLARED_EXECUTORS: dict[Path, frozenset[str]] = {
    LIVE_COMPOSE: frozenset(),
    RENDERED_COMPOSE: frozenset({"migrate"}),
}

DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy.sh"


# ---------------------------------------------------------------------------
# Reading a Compose service's environment, in every shape Compose accepts
# ---------------------------------------------------------------------------


def _environment(service: Any) -> dict[str, str | None]:
    """Normalise `environment:` to name -> value.

    Compose accepts a mapping (`KEY: value`) and a sequence (`- KEY=value`, or
    a bare `- KEY` meaning "inherit from the host shell"). The bare form is a
    LEAK in this context, not a neutralisation, so it must survive
    normalisation as `None` rather than collapse into the empty string.
    """
    raw = service.get("environment")
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return {str(k): (None if v is None else str(v)) for k, v in raw.items()}
    entries: dict[str, str | None] = {}
    for item in raw:
        text = str(item)
        name, separator, value = text.partition("=")
        entries[name] = value if separator else None
    return entries


def _env_files(service: Any) -> list[str]:
    """Every `env_file:` entry, in every shape Compose accepts."""
    raw = service.get("env_file")
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    paths: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            paths.append(str(item.get("path", item)))
        else:
            paths.append(str(item))
    return paths


def exposed_services(compose_text: str, executors: frozenset[str]) -> tuple[str, ...]:
    """Every service that can receive the migration credential, with the reason.

    Returns reasons rather than a bare set: an operator reading a failure needs
    to know whether the value was written in, or merely reachable through an
    env file, because the two have different repairs.
    """
    document = yaml.safe_load(compose_text) or {}
    services = document.get("services") or {}
    findings: list[str] = []
    for name in sorted(services):
        if name in executors:
            continue
        service = services[name] or {}
        environment = _environment(service)
        declared = OWNER_MATERIAL in environment
        value = environment.get(OWNER_MATERIAL)

        if declared and value is None:
            findings.append(
                f"{name}: declares a bare `{OWNER_MATERIAL}` entry, which "
                "inherits the value from the host shell"
            )
            continue
        if declared and value != "":
            findings.append(
                f"{name}: sets {OWNER_MATERIAL} to a non-empty value ({value!r})"
            )
            continue
        if declared:
            continue

        files = _env_files(service)
        if files:
            findings.append(
                f"{name}: reads env_file {files!r} and does not neutralise "
                f"{OWNER_MATERIAL}; the production entrypoint census records "
                "the app_admin DSN inside that file, so this service holds a "
                "BYPASSRLS DDL credential"
            )
    return tuple(findings)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The property, on the deployed bytes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", sorted(DECLARED_EXECUTORS, key=str))
def test_no_long_running_service_can_receive_the_migration_credential(
    path: Path,
) -> None:
    findings = exposed_services(_read(path), DECLARED_EXECUTORS[path])
    assert findings == (), (
        f"{path.relative_to(REPO_ROOT)} exposes the app_admin migration "
        "credential to a long-running service:\n  " + "\n  ".join(findings)
    )


def test_the_live_topology_is_the_one_the_deploy_script_runs() -> None:
    """Non-vacuity: this gate is pointed at the file production actually uses.

    A gate over an unused artifact passes for the wrong reason. `deploy.sh` runs
    bare `docker compose` from the checkout root, which resolves to
    `docker-compose.yml` there, and `deploy/README.md` states in prose that the
    root compose remains the executing deployment.
    """
    deploy = _read(DEPLOY_SCRIPT)
    assert "docker compose run" in deploy
    assert "docker compose up -d" in deploy
    assert "-f deploy/rendered/docker-compose.yml" not in deploy, (
        "deploy.sh now names the rendered project; DECLARED_EXECUTORS' "
        "assumption about which file is live has changed"
    )
    readme = _read(REPO_ROOT / "deploy" / "README.md")
    assert "remain the executing deployment" in readme


def test_the_one_shot_executor_still_receives_it_on_the_flag() -> None:
    """The other half: removing it from the service must not remove it entirely.

    `app`'s service definition now carries an empty `MIGRATION_DATABASE_URL`, so
    the migration one-shots depend on `docker compose run --env` overriding the
    service definition. If someone deletes those flags to 'simplify', the
    credential reaches nothing and every deploy stops at preflight. Pin both
    halves so neither can be removed alone.
    """
    deploy = _read(DEPLOY_SCRIPT)
    makefile = _read(REPO_ROOT / "Makefile")
    assert f"-e {OWNER_MATERIAL}" in deploy
    assert f"docker compose run --rm -e {OWNER_MATERIAL} app" in makefile

    document = yaml.safe_load(_read(LIVE_COMPOSE))
    app = document["services"]["app"]
    assert _environment(app)[OWNER_MATERIAL] == "", (
        "the empty override is what the flag has to beat; without it this "
        "pairing means nothing"
    )


def test_the_rendered_executor_actually_receives_the_material() -> None:
    """A rendered project whose migrate step holds nothing migrates nothing.

    Checking only the absence half would pass over an artifact that had lost the
    credential everywhere, including where it is required.
    """
    document = yaml.safe_load(_read(RENDERED_COMPOSE))
    migrate = document["services"]["migrate"]
    value = _environment(migrate)[OWNER_MATERIAL]
    assert value is not None and OWNER_MATERIAL in value, value
    assert _env_files(migrate) == [], (
        "the one-shot executor reads an env file; it should receive exactly the "
        "materials the descriptor names and nothing else"
    )


# ---------------------------------------------------------------------------
# Sensitivity proofs (ADR-0018). A check that has only ever run over a clean
# tree proves nothing about itself.
# ---------------------------------------------------------------------------


def test_a_removed_neutralisation_is_named() -> None:
    """Planted defect: revert `app` to what production runs today."""
    tampered = _read(LIVE_COMPOSE).replace(f"      {OWNER_MATERIAL}: ''\n", "", 1)
    assert tampered != _read(LIVE_COMPOSE), "the tamper target moved; update this"
    findings = exposed_services(tampered, DECLARED_EXECUTORS[LIVE_COMPOSE])
    assert any(finding.startswith("app:") for finding in findings), findings


def test_a_written_in_credential_is_named() -> None:
    """Planted defect: hand `worker` the real value explicitly."""
    tampered = _read(LIVE_COMPOSE).replace(
        f"      {OWNER_MATERIAL}: ''\n",
        f"      {OWNER_MATERIAL}: ${{{OWNER_MATERIAL}}}\n",
    )
    findings = exposed_services(tampered, DECLARED_EXECUTORS[LIVE_COMPOSE])
    assert any(finding.startswith("worker:") for finding in findings), findings
    assert any("non-empty value" in finding for finding in findings), findings


def test_a_bare_passthrough_entry_is_named() -> None:
    """Planted defect: the shape that LOOKS like a declaration and is a leak.

    `environment: [- MIGRATION_DATABASE_URL]` inherits the operator's shell
    value. A gate that normalised a bare entry to the empty string would read
    this as neutralised.
    """
    document = yaml.safe_load(_read(LIVE_COMPOSE))
    document["services"]["beat"]["environment"] = [OWNER_MATERIAL]
    findings = exposed_services(
        yaml.safe_dump(document), DECLARED_EXECUTORS[LIVE_COMPOSE]
    )
    assert any("bare" in finding for finding in findings), findings


def test_the_gate_reads_the_rendered_bytes_not_only_the_descriptor() -> None:
    """THE measurement Michael asked for, taken rather than asserted.

    Plant the leak ONLY in the rendered artifact — give its `app` service an
    `env_file` — and confirm this gate names it. The descriptor is untouched by
    the plant and stays clean, which is exactly why a descriptor-only gate
    cannot see this.
    """
    original = _read(RENDERED_COMPOSE)
    document = yaml.safe_load(original)
    document["services"]["app"]["env_file"] = [".env"]
    tampered = yaml.safe_dump(document)

    findings = exposed_services(tampered, DECLARED_EXECUTORS[RENDERED_COMPOSE])
    assert any(finding.startswith("app:") for finding in findings), findings
    assert any("env_file" in finding for finding in findings), findings

    descriptor = _read(REPO_ROOT / "deploy" / "product.toml")
    assert f'owner_material = "{OWNER_MATERIAL}"' in descriptor
    assert "env_file" not in descriptor, (
        "the descriptor has grown an env_file concept; the plant above is no "
        "longer invisible to a descriptor-only reader and this proof is stale"
    )


def test_the_gate_never_opens_the_descriptor() -> None:
    """Its corpus is rendered/live bytes only, so a clean descriptor cannot
    make it pass."""
    corpus = {path.relative_to(REPO_ROOT).as_posix() for path in DECLARED_EXECUTORS}
    assert corpus == {"docker-compose.yml", "deploy/rendered/docker-compose.yml"}
    assert "deploy/product.toml" not in corpus


def test_a_neutralised_service_and_an_unrelated_variable_are_not_named() -> None:
    """Near-miss control: the gate must be quiet about correct shapes.

    Two near misses in one, because a detector that fires on everything is as
    useless as one that fires on nothing: a service that DOES carry the empty
    override alongside an env_file, and a service that names a different,
    innocuous variable.
    """
    document = yaml.safe_load(_read(RENDERED_COMPOSE))
    document["services"]["app"]["env_file"] = [".env"]
    document["services"]["app"]["environment"][OWNER_MATERIAL] = ""
    document["services"]["beat"]["environment"]["MIGRATION_EXPECTED_DATABASE"] = (
        "dotmac_erp"
    )
    findings = exposed_services(
        yaml.safe_dump(document), DECLARED_EXECUTORS[RENDERED_COMPOSE]
    )
    assert findings == (), findings
