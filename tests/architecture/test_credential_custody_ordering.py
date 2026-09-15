"""Check `erp-lock.yml` credential custody from resolved YAML.

PyYAML is an explicit development dependency.  `safe_load` resolves aliases,
so anchors cannot disguise a job-level credential. Its one GitHub-specific
YAML 1.1 mismatch -- coercing the top-level `on` key to ``True`` -- is repaired
explicitly before the string-keyed workflow is inspected. Raw text rejects
every secret-context expression except the one exact secret this workflow is
allowed to hold.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "erp-lock.yml"
CREDENTIAL_REFERENCE = re.compile(
    r"secrets\s*(?:\.\s*FORGEJO_READ_TOKEN|\[\s*(?:'FORGEJO_READ_TOKEN'|"
    r'"FORGEJO_READ_TOKEN")\s*\])'
)
SECRET_CONTEXT_REFERENCE = re.compile(r"\bsecrets\b")


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise AssertionError(f"{context} must be a string-keyed YAML mapping")
    return value


def has_credential_reference(text: str) -> bool:
    return CREDENTIAL_REFERENCE.search(text) is not None


def _has_unsupported_secret_reference(text: str) -> bool:
    return (
        SECRET_CONTEXT_REFERENCE.search(CREDENTIAL_REFERENCE.sub("", text)) is not None
    )


def _load_workflow(text: str) -> Mapping[str, object]:
    loaded = yaml.safe_load(text)
    if isinstance(loaded, Mapping) and True in loaded:
        if "on" in loaded:
            raise AssertionError("workflow declares both `on` and YAML boolean `true`")
        loaded = dict(loaded)
        loaded["on"] = loaded.pop(True)
    return _mapping(loaded, "workflow")


def parse_jobs(text: str) -> dict[str, Mapping[str, object]]:
    """Load jobs after aliases resolve; other secret syntax fails closed."""

    if _has_unsupported_secret_reference(text):
        raise AssertionError(
            "only the literal FORGEJO_READ_TOKEN secret reference is supported"
        )
    workflow = _load_workflow(text)
    jobs = _mapping(workflow.get("jobs"), "workflow jobs")
    return {name: _mapping(body, f"{name} job") for name, body in jobs.items()}


def _contains_credential(value: object) -> bool:
    if isinstance(value, str):
        return has_credential_reference(value)
    if isinstance(value, Mapping):
        return any(_contains_credential(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_credential(item) for item in value)
    return False


def job_level_env_values(body: Mapping[str, object]) -> Mapping[str, object]:
    return _mapping(body.get("env", {}), "job env")


def split_steps(body: Mapping[str, object]) -> list[Mapping[str, object]]:
    steps = body.get("steps", [])
    if not isinstance(steps, list):
        raise AssertionError("job steps must be a YAML sequence")
    return [_mapping(step, "step") for step in steps]


def job_level_credential_violations(
    jobs: Mapping[str, Mapping[str, object]],
) -> list[str]:
    return [
        name
        for name, body in jobs.items()
        if _contains_credential(job_level_env_values(body))
    ]


def jobs_referencing_credential(jobs: Mapping[str, Mapping[str, object]]) -> set[str]:
    return {name for name, body in jobs.items() if _contains_credential(body)}


def credential_references_outside_steps(text: str) -> list[str]:
    """Name resolved workflow/job credential uses that are not in a step."""

    if _has_unsupported_secret_reference(text):
        return ["workflow uses an unsupported secret-context reference"]
    workflow = _load_workflow(text)
    jobs = parse_jobs(text)
    problems = []
    if _contains_credential(
        {key: value for key, value in workflow.items() if key != "jobs"}
    ):
        problems.append("workflow references the credential outside any job")
    for name, body in jobs.items():
        if _contains_credential(
            {key: value for key, value in body.items() if key != "steps"}
        ):
            problems.append(f"{name} job references the credential outside a step")
    return problems


def credential_env_var(step: Mapping[str, object]) -> str | None:
    for name, value in _mapping(step.get("env", {}), "step env").items():
        if isinstance(value, str) and has_credential_reference(value):
            return name
    return None


def commands_of(step: Mapping[str, object]) -> str:
    return "\n".join(
        line
        for line in str(step.get("run", "")).splitlines()
        if not line.strip().startswith("#")
    )


def step_name(step: Mapping[str, object]) -> str:
    return str(step.get("name", "<unnamed step>"))


def is_refusal_gate(step: Mapping[str, object]) -> bool:
    var = credential_env_var(step)
    if var is None:
        return False
    pattern = rf'if \[ -z "\$\{{?{re.escape(var)}(?::-[^}}]*)?\}}?"\s*\];?\s*then'
    body = commands_of(step)
    for match in re.finditer(pattern, body):
        depth = 1
        nested_control = False
        clause_lines: list[str] = []
        for line in body[match.end() :].splitlines():
            stripped = line.strip()
            if re.match(r"if\b", stripped):
                nested_control = True
                depth += 1
            elif stripped == "fi" or stripped.startswith("fi;"):
                depth -= 1
                if depth == 0:
                    break
            clause_lines.append(line)
        clause = "\n".join(clause_lines)
        if (
            depth == 0
            and not nested_control
            and re.search(r"^\s*exit\s+[1-9]\b", clause, re.M)
        ):
            return True
    return False


def gate_indices(steps: list[Mapping[str, object]]) -> list[int]:
    return [index for index, step in enumerate(steps) if is_refusal_gate(step)]


def credential_step_indices(steps: list[Mapping[str, object]]) -> list[int]:
    return [index for index, step in enumerate(steps) if _contains_credential(step)]


def ordering_problems(steps: list[Mapping[str, object]]) -> list[str]:
    gates = gate_indices(steps)
    if not gates:
        return ["no step in this job tests the credential for emptiness and refuses"]
    gate = min(gates)
    return [
        f"{step_name(step)!r} references the credential before the refusal gate"
        for index, step in enumerate(steps)
        if index < gate and _contains_credential(step)
    ]


def gate_refusal_problems(steps: list[Mapping[str, object]]) -> list[str]:
    return [] if gate_indices(steps) else ["no credential step refuses emptiness"]


def _acquire_steps() -> list[Mapping[str, object]]:
    return split_steps(parse_jobs(WORKFLOW.read_text())["acquire"])


def _post_steps_env(env_key: str) -> str:
    text = WORKFLOW.read_text()
    result = text.replace(
        "  resolve:\n",
        f"    {env_key}:\n"
        "      LEAKED_TOKEN: ${{ secrets.FORGEJO_READ_TOKEN }}\n"
        "  resolve:\n",
    )
    assert result != text
    return result


def test_real_workflow_keeps_credential_inside_acquire_steps() -> None:
    text = WORKFLOW.read_text()
    jobs = parse_jobs(text)
    assert set(jobs) == {"acquire", "resolve", "attest"}
    assert all(split_steps(body) for body in jobs.values())
    assert job_level_credential_violations(jobs) == []
    assert jobs_referencing_credential(jobs) == {"acquire"}
    assert credential_references_outside_steps(text) == []


@pytest.mark.parametrize("env_key", ["env", "'env'", '"env"'])
def test_plain_or_quoted_post_steps_env_is_named(env_key: str) -> None:
    text = _post_steps_env(env_key)
    assert job_level_credential_violations(parse_jobs(text)) == ["acquire"]
    assert any(
        "acquire job" in item for item in credential_references_outside_steps(text)
    )


def test_acquire_anchor_reused_as_resolve_job_env_is_named() -> None:
    text = """jobs:
  acquire:
    env: &credential
      TOKEN: ${{ secrets.FORGEJO_READ_TOKEN }}
    steps: []
  resolve:
    env: *credential
    steps: []
  attest:
    steps: []
"""
    jobs = parse_jobs(text)
    assert job_level_credential_violations(jobs) == ["acquire", "resolve"]
    assert any(
        "resolve job" in item for item in credential_references_outside_steps(text)
    )


def test_dynamic_secret_indexing_fails_closed() -> None:
    text = """jobs:
  acquire:
    env:
      TOKEN: ${{ secrets[github.event.inputs.secret_name] }}
    steps: []
"""
    with pytest.raises(AssertionError, match=r"only the literal"):
        parse_jobs(text)
    assert credential_references_outside_steps(text) == [
        "workflow uses an unsupported secret-context reference"
    ]


@pytest.mark.parametrize(
    "expression",
    ["${{ toJSON(secrets) }}", "${{ secrets.SOME_OTHER_SECRET }}"],
)
def test_any_other_secret_context_reference_fails_closed(expression: str) -> None:
    text = f"""jobs:
  acquire:
    env:
      TOKEN: {expression}
    steps: []
"""
    with pytest.raises(AssertionError, match=r"only the literal"):
        parse_jobs(text)


def test_literal_bracket_reference_outside_acquire_is_named() -> None:
    text = WORKFLOW.read_text().replace(
        "  resolve:\n",
        "  resolve:\n    env:\n      SNEAKY: ${{ secrets['FORGEJO_READ_TOKEN'] }}\n",
    )
    assert "resolve" in jobs_referencing_credential(parse_jobs(text))
    assert any(
        "resolve job" in item for item in credential_references_outside_steps(text)
    )


def test_gate_is_behavioural_and_precedes_credentialed_steps() -> None:
    steps = _acquire_steps()
    gates = gate_indices(steps)
    assert len(gates) == 1
    assert "There is a credential to resolve with" in step_name(steps[gates[0]])
    assert ordering_problems(steps) == []
    assert gate_refusal_problems(steps) == []


def test_credentialed_step_before_gate_is_named() -> None:
    steps = _acquire_steps()
    gate = gate_indices(steps)[0]
    victim = next(
        index for index in credential_step_indices(steps) if index == gate + 1
    )
    mutated = [*steps]
    mutated[gate], mutated[victim] = mutated[victim], mutated[gate]
    assert any(
        "Download the closed bundle" in item for item in ordering_problems(mutated)
    )


def test_unexpected_alias_and_defanged_gate_fail() -> None:
    steps = _acquire_steps()
    gate = gate_indices(steps)[0]
    synthetic = {
        "name": "Synthetic preflight with an unexpected alias",
        "env": {"UNEXPECTED_ALIAS": "${{ secrets.FORGEJO_READ_TOKEN }}"},
        "run": "echo preflight",
    }
    assert credential_env_var(synthetic) == "UNEXPECTED_ALIAS"
    assert any(
        "Synthetic preflight" in item
        for item in ordering_problems([*steps[:gate], synthetic, *steps[gate:]])
    )
    defanged = dict(steps[gate])
    defanged["run"] = commands_of(steps[gate]).replace("exit 1", "echo no-refusal")
    assert gate_refusal_problems([*steps[:gate], defanged, *steps[gate + 1 :]])
    nested = {
        "env": {"ALIAS": "${{ secrets.FORGEJO_READ_TOKEN }}"},
        "run": 'if [ -z "$ALIAS" ]; then\n  if false; then\n    exit 1\n  fi\nfi',
    }
    assert not is_refusal_gate(nested)
