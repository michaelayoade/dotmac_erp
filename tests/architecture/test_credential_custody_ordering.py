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
    r"secrets\s*(?:\.\s*FORGEJO_READ_TOKEN(?![A-Za-z0-9_])|\[\s*(?:'FORGEJO_READ_TOKEN'|"
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


_NESTED_CONTROL_START = re.compile(
    r"^(?:if|for|while|until|case|select)\b"
    r"|^(?:function\s+)?[\w.-]+\s*\(\)\s*\{?\s*$"
)


def is_refusal_gate(step: Mapping[str, object]) -> bool:
    """Recognise ONE narrow shell shape: a simple outer
    ``if [ -z "$VAR" ]; then ... fi`` whose body is a direct, unconditional
    ``exit`` and plain statements only, with NO alternative branch on that
    same outer conditional.

    This is NOT general shell control-flow analysis, and does not attempt to
    determine whether an ``exit`` is actually reachable. It recognises one
    known-good shape and fails closed -- returns False, i.e. "not a proven
    refusal gate" -- on every shape it does not understand, including:

    * a nested `if`, `for`, `while`, `until`, `case`, `select`, or a
      function-definition body anywhere inside the outer block, even where
      that nested construct happens to be provably unreachable (e.g. `while
      false; do exit 1; done`);
    * an `else` or `elif` on the OUTER conditional itself -- an alternative
      branch means the exit is conditioned on something other than the
      credential's absence, and `exit 1` sitting in an `else` fires when the
      credential IS present and does nothing when it is absent: the exact
      inversion of a credential-absence refusal, not a variant of one.

    An honest narrow recogniser that refuses unfamiliar or ambiguous shapes
    is preferred over a broad one that is wrong about them.
    """
    var = credential_env_var(step)
    if var is None:
        return False
    pattern = rf'if \[ -z "\$\{{?{re.escape(var)}(?::-[^}}]*)?\}}?"\s*\];?\s*then'
    body = commands_of(step)
    for match in re.finditer(pattern, body):
        depth = 1
        nested_control = False
        outer_alternative = False
        clause_lines: list[str] = []
        for line in body[match.end() :].splitlines():
            stripped = line.strip()
            if _NESTED_CONTROL_START.match(stripped):
                nested_control = True
                if re.match(r"if\b", stripped):
                    depth += 1
            elif depth == 1 and (
                stripped == "else"
                or stripped.startswith("else;")
                or re.match(r"elif\b", stripped)
            ):
                # An `else`/`elif` at the SAME depth as the gate's own `if`
                # is an alternative branch on the gate itself. One that
                # belongs to a nested `if` instead sits at depth >= 2 (the
                # nested `if` above already incremented depth) and is
                # already disqualified via `nested_control`, so it never
                # reaches this branch.
                outer_alternative = True
            elif stripped == "fi" or stripped.startswith("fi;"):
                depth -= 1
                if depth == 0:
                    break
            clause_lines.append(line)
        clause = "\n".join(clause_lines)
        if (
            depth == 0
            and not nested_control
            and not outer_alternative
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


@pytest.mark.parametrize("suffix", ["_V2", "_STAGING"])
def test_similarly_named_secret_is_not_the_one_allowed_reference(suffix: str) -> None:
    """`secrets.FORGEJO_READ_TOKEN_V2` and `secrets.FORGEJO_READ_TOKEN_STAGING`
    name a DIFFERENT secret than the one this workflow is allowed to hold.
    Design break condition: the dot branch's right-hand identifier boundary
    (`(?![A-Za-z0-9_])`) is what refuses these -- delete that lookahead and
    the pattern matches the `FORGEJO_READ_TOKEN` prefix of the longer name,
    silently treating an unrelated secret as the allowed one. The bracket
    branch never needed this fix: `]` is already a boundary the identifier
    cannot extend past.
    """
    name = f"FORGEJO_READ_TOKEN{suffix}"
    assert not has_credential_reference(f"${{{{ secrets.{name} }}}}")
    text = f"""jobs:
  acquire:
    env:
      TOKEN: ${{{{ secrets.{name} }}}}
    steps: []
"""
    with pytest.raises(AssertionError, match=r"only the literal"):
        parse_jobs(text)


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


@pytest.mark.parametrize(
    "shell_body",
    [
        pytest.param(
            'if [ -z "$ALIAS" ]; then\n  while false; do\n    exit 1\n  done\nfi',
            id="while-loop-body-never-entered",
        ),
        pytest.param(
            'if [ -z "$ALIAS" ]; then\n  for x in; do\n    exit 1\n  done\nfi',
            id="for-loop-over-an-empty-list",
        ),
        pytest.param(
            'if [ -z "$ALIAS" ]; then\n  until true; do\n    exit 1\n  done\nfi',
            id="until-loop-body-never-entered",
        ),
        pytest.param(
            'if [ -z "$ALIAS" ]; then\n'
            "  case $ALIAS in\n"
            "    never)\n"
            "      exit 1\n"
            "      ;;\n"
            "  esac\n"
            "fi",
            id="case-with-no-matching-branch",
        ),
        pytest.param(
            'if [ -z "$ALIAS" ]; then\n  unreachable() {\n    exit 1\n  }\nfi',
            id="function-defined-but-never-called",
        ),
    ],
)
def test_nested_shell_control_flow_is_not_a_proven_refusal_gate(
    shell_body: str,
) -> None:
    """Each shape lexically contains `exit 1` inside the outer empty-check,
    but by DESIGN the shell never reaches that `exit`: a `while false`/
    `until true` loop body never runs, a `for` over an empty list iterates
    zero times, the `case` has no branch matching `$ALIAS`, and the
    function is merely DEFINED, not called. `is_refusal_gate` must fail
    closed on every one of them -- returning False -- because it recognises
    exactly one narrow known-good shape (a plain outer empty-check with an
    unconditional exit and no nested construct) and these are not that
    shape; it does not evaluate whether the exit is reachable, and must not
    be read as having done so.

    This exercises the SHELL shape the detector parses, not the detector's
    own Python scanning loop -- perturbing the Python loop's bookkeeping
    is a different property from supplying an unreachable shell construct,
    and this plant is the latter.
    """
    step = {
        "env": {"ALIAS": "${{ secrets.FORGEJO_READ_TOKEN }}"},
        "run": shell_body,
    }
    assert not is_refusal_gate(step)


def test_outer_else_inverts_the_refusal_and_is_rejected() -> None:
    """`exit 1` sits in the outer `if`'s ELSE branch, not its `then`
    branch. By DESIGN that fires the instant the credential IS present
    and does nothing when it is absent -- the exact inversion of a
    credential-absence refusal, not a variant of one. Design break
    condition: `outer_alternative` is set the moment an `else` is seen at
    the SAME depth as the gate's own `if`; remove that check and the
    `exit 1`'s mere lexical presence inside the outer `if...fi` block is
    again enough to pass, regardless of which branch it is actually in.
    """
    step = {
        "env": {"ALIAS": "${{ secrets.FORGEJO_READ_TOKEN }}"},
        "run": 'if [ -z "$ALIAS" ]; then\n  echo present\nelse\n  exit 1\nfi',
    }
    assert not is_refusal_gate(step)


def test_outer_elif_inverts_the_refusal_and_is_rejected() -> None:
    """Same inversion, the `elif` syntactic path rather than `else`: the
    `exit 1` sits in a second, alternative branch of the outer
    conditional. Design break condition: an `elif` at the SAME depth as
    the gate's own `if` sets `outer_alternative` exactly like `else`
    does -- a gate with any alternative branch at all is refused,
    independent of which branch happens to hold the exit. This is a
    distinct syntactic path from `else` (a different token, a different
    regex branch) and is deliberately proven separately rather than
    folded into one parametrized case with it.
    """
    step = {
        "env": {"ALIAS": "${{ secrets.FORGEJO_READ_TOKEN }}"},
        "run": (
            'if [ -z "$ALIAS" ]; then\n'
            "  echo present\n"
            'elif [ -n "$ALIAS" ]; then\n'
            "  exit 1\n"
            "fi"
        ),
    }
    assert not is_refusal_gate(step)


def test_direct_exit_with_no_alternative_branch_is_the_positive_control() -> None:
    """The positive control the two inversion tests above depend on: a
    plain outer empty-check with ONE direct, unconditional exit and no
    alternative branch is exactly the narrow shape the recogniser exists
    to accept. Without this control, a recogniser that always returns
    False would also pass the two tests above for the wrong reason -- a
    guard that refuses everything is as useless as one that accepts
    everything.
    """
    step = {
        "env": {"ALIAS": "${{ secrets.FORGEJO_READ_TOKEN }}"},
        "run": 'if [ -z "$ALIAS" ]; then\n  exit 1\nfi',
    }
    assert is_refusal_gate(step)
