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
REQUIRED_GATE_SHELL = "/bin/bash -p --noprofile --norc -eo pipefail {0}"
ALLOWED_SECRET_EXPRESSION = "${{ secrets.FORGEJO_READ_TOKEN }}"
CREDENTIAL_REFERENCE = re.compile(
    r"secrets\s*(?:\.\s*FORGEJO_READ_TOKEN(?![A-Za-z0-9_])|\[\s*(?:'FORGEJO_READ_TOKEN'|"
    r'"FORGEJO_READ_TOKEN")\s*\])',
    re.IGNORECASE,
)
# GitHub Actions expression contexts and secret names are both
# case-insensitive on reference (https://docs.github.com/en/actions/reference/
# security/secrets): `${{ Secrets.FORGEJO_READ_TOKEN }}` names the exact same
# secret as `${{ secrets.FORGEJO_READ_TOKEN }}`. Both patterns above therefore
# normalise casing rather than trusting the spelling on the page.
SECRET_CONTEXT_REFERENCE = re.compile(r"\bsecrets\b", re.IGNORECASE)


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
    workflow_defaults = workflow.get("defaults", {})
    workflow_run = (
        workflow_defaults.get("run", {})
        if isinstance(workflow_defaults, Mapping)
        else {}
    )
    workflow_shell = isinstance(workflow_run, Mapping) and "shell" in workflow_run
    workflow_env = workflow.get("env", {})
    workflow_dangerous_env = _has_dangerous_shell_env(workflow_env)
    result = {}
    for name, raw_body in jobs.items():
        body = _mapping(raw_body, f"{name} job")
        job_defaults = body.get("defaults", {})
        job_run = (
            job_defaults.get("run", {}) if isinstance(job_defaults, Mapping) else {}
        )
        job_shell = isinstance(job_run, Mapping) and "shell" in job_run
        job_continue = body.get("continue-on-error")
        job_continue_unsafe = job_continue not in (None, False, "false")
        unsafe_context = (
            workflow_shell or job_shell or workflow_dangerous_env or job_continue_unsafe
        )
        result[name] = (
            {**body, "__unsafe_gate_context": True} if unsafe_context else body
        )
    return result


def _contains_credential(value: object) -> bool:
    if isinstance(value, str):
        return has_credential_reference(value)
    if isinstance(value, Mapping):
        return any(_contains_credential(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_credential(item) for item in value)
    return False


def _has_dangerous_shell_env(value: object) -> bool:
    return isinstance(value, Mapping) and any(
        str(key).upper() in {"BASH_ENV", "SHELLOPTS"} for key in value
    )


def job_level_env_values(body: Mapping[str, object]) -> Mapping[str, object]:
    return _mapping(body.get("env", {}), "job env")


def split_steps(body: Mapping[str, object]) -> list[Mapping[str, object]]:
    steps = body.get("steps", [])
    if not isinstance(steps, list):
        raise AssertionError("job steps must be a YAML sequence")
    job_env_unsafe = _has_dangerous_shell_env(body.get("env", {}))
    job_defaults = body.get("defaults", {})
    run_defaults = (
        job_defaults.get("run", {}) if isinstance(job_defaults, Mapping) else {}
    )
    job_shell_unsafe = isinstance(run_defaults, Mapping) and "shell" in run_defaults
    job_continue = body.get("continue-on-error")
    job_continue_unsafe = job_continue not in (None, False, "false")
    context_unsafe = (
        job_env_unsafe
        or job_shell_unsafe
        or job_continue_unsafe
        or bool(body.get("__unsafe_gate_context"))
    )
    return [
        {
            **_mapping(step, "step"),
            **({"__unsafe_gate_context": True} if context_unsafe else {}),
        }
        for step in steps
    ]


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


def _step_metadata_is_safe(step: Mapping[str, object]) -> bool:
    """Only the default GitHub step execution path may be a gate."""
    if "if" in step or step.get("__unsafe_gate_context"):
        return False
    if "continue-on-error" in step:
        value = step["continue-on-error"]
        if value is not False and value != "false":
            return False
    return step.get("shell") == REQUIRED_GATE_SHELL and not _has_dangerous_shell_env(
        step.get("env", {})
    )


# A single plain diagnostic line: `echo` followed by exactly ONE quoted
# literal argument and nothing else. `$`, backticks and backslashes are
# excluded from INSIDE the quotes -- those are how a "diagnostic string"
# would smuggle expansion, command substitution or an escaped quote that
# extends past where it looks like the string ends. Everything else inside
# the quotes (including `;` and `#`) is DATA, never syntax, because the
# regex treats the quoted span as one opaque unit rather than scanning its
# characters for shell meaning. Nothing is permitted after the closing
# quote except trailing whitespace -- no `;`, `&`, `|`, `>`, `<`.
_ECHO_LITERAL = re.compile(r"""^echo\s+(?:"[^"$`\\]*"|'[^'$`\\]*')\s*$""")

# The one exit shape the grammar accepts: `exit` then a non-zero integer
# (leading digit 1-9, so `exit 0` never matches) and nothing else on the
# line -- no trailing `&` (backgrounding), no trailing `;` (a packed next
# command), no pipe or redirection.
_EXIT_STATEMENT = re.compile(r"^exit\s+[1-9][0-9]*\s*$")


def _parses_as_refusal_body(statements: list[str]) -> bool:
    """Accept ONLY: zero or more `_ECHO_LITERAL` lines, then exactly one
    `_EXIT_STATEMENT` line, with that exit LAST -- no statement of any
    other shape anywhere, and nothing after the exit.
    """
    if not statements:
        return False
    *echoes, last = statements
    return bool(_EXIT_STATEMENT.match(last)) and all(
        _ECHO_LITERAL.match(line) for line in echoes
    )


# The only non-blank shape admitted BEFORE the `if` header is the one
# side-effect-free preamble used by the real workflow. Keep this an exact
# positive allowlist: bare `set` prints shell variables, `-x`/`xtrace` can
# print expanded credential values, and `-n`/`noexec` prevents the refusal
# from running. Whitespace between the three literal tokens is normalized,
# but no other spelling or option is admitted.
_PREAMBLE_SET_LINE = re.compile(r"^set\s+-euo\s+pipefail\s*$")


def _preamble_is_admissible(text: str, var: str) -> bool:
    """The text before the matched `if` header must be side-effect-free:
    only blank lines and the exact `set -euo pipefail` line, per Michael's
    specification, and NO reference to the credential's own shell
    variable anywhere in it, checked unconditionally ahead of the shape
    check -- a credential reference in the preamble disqualifies the step
    regardless of what else that line looks like.
    """
    reference = re.compile(rf"\$\{{?{re.escape(var)}\b")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "":
            continue
        if reference.search(stripped):
            return False
        if not _PREAMBLE_SET_LINE.match(stripped):
            return False
    return True


def is_refusal_gate(step: Mapping[str, object]) -> bool:
    """Recognise ONE narrow, positively-specified shell grammar:

        set -euo pipefail  # optional, side-effect-free preamble only
        if [ -z "$VAR" ]; then
          echo "literal"     # zero or more of these
          ...
          exit N             # exactly one, N != 0, nothing after it
        fi
        echo "..."           # anything at all, freely permitted

    with no `else`/`elif`, no heredoc, no compound statement, no function,
    no loop, no nested condition, no `&`, and no packed `;` commands
    anywhere in the `if` body. A blank line ANYWHERE in the body --
    immediately after `then`, between two `echo` lines, or before the
    final `exit` -- is tolerated: it is whitespace, not a statement, and a
    deliberate grammar decision rather than an oversight, scoped to lines
    that are empty after stripping so it can never absorb a line that
    carries any other content. This is a POSITIVE grammar, not a denylist
    of known-bad shapes: every body statement must match `_ECHO_LITERAL`
    or be the single trailing `_EXIT_STATEMENT`, so an unrecognised
    construct fails by not matching either shape, not by being enumerated
    as forbidden. That is a deliberate response to how this recogniser
    broke before -- `exit 0` before a real `exit 1`, an `exit 1` inside a
    heredoc body (payload text a shell never executes), and a backgrounded
    `exit 1 &` (the parent step continues past it) all read, line by line,
    as "a bare `exit 1` statement is present somewhere in the block" to a
    scanner that only checked for absence of specific bad shapes. A
    positive grammar makes each of those fail on its own terms: `exit 0`
    is not an echo, a heredoc terminator is not `exit N`, and `exit 1 &`
    does not match `_EXIT_STATEMENT` at all. Reachability stops being a
    question the scanner has to keep guessing at.

    THE TEXT BEFORE THE HEADER is exactly as dangerous as the body is
    strict about, and for a distinct reason: a step whose script USES the
    credential and only THEN checks it for emptiness (e.g. a `curl` call
    immediately followed by this exact `if` block, fused into one step)
    would otherwise be credited as a compliant absence gate while the
    credential had already crossed the network. `is_refusal_gate` treats
    a step as ONE ATOMIC unit -- `gate_indices`/`ordering_problems` only
    reason about ordering BETWEEN steps, by index -- so ordering WITHIN a
    single step's own script is exactly what this preamble rule exists to
    enforce; nothing else in this module checks it. The rule: everything
    before the header must be blank lines or the exact `set -euo pipefail`
    option line
    (`_PREAMBLE_SET_LINE`), and no line before the header may reference
    the credential's own shell variable at all, checked unconditionally.
    The asymmetry is deliberate and stays: text AFTER the closing `fi` is
    NOT restricted -- the real gate ends with a diagnostic `echo` that
    runs only once the gate has already passed, and that is exactly what
    a passed gate is allowed to do next.

    This is NOT general shell control-flow analysis, and it refuses every
    shape it does not parse, including legitimate ones -- an honest narrow
    recogniser that is wrong about nothing beats a broad one that is wrong
    about something. It also proves nothing about the surrounding job or
    repository: this is defence in depth over a step's own shell text, not
    a substitute for GitHub-enforced credential custody (a protected
    environment gating the secret itself is still required and is a
    known, separate closure step -- its absence here is not this guard's
    gap to close).
    """
    if not _step_metadata_is_safe(step):
        return False
    var = credential_env_var(step)
    if var is None:
        return False
    env = step.get("env", {})
    if not isinstance(env, Mapping) or env.get(var) != ALLOWED_SECRET_EXPRESSION:
        return False
    variable = (
        rf"(?:\${re.escape(var)}|\$\{{{re.escape(var)}\}}|\$\{{{re.escape(var)}:-\}})"
    )
    header = rf'if \[ -z "{variable}"\s*\];?\s*then'
    body = commands_of(step)
    for match in re.finditer(header, body):
        if not _preamble_is_admissible(body[: match.start()], var):
            continue
        statements: list[str] = []
        closed = False
        for line in body[match.end() :].splitlines():
            stripped = line.strip()
            if stripped == "":
                # Blank -- whitespace, not a statement. Tolerated anywhere
                # in the body (immediately after `then`, between two
                # echoes, or before `exit`/`fi`): a blank line carries no
                # shell meaning at all, so it is neither a disqualifying
                # statement nor a candidate for the final `exit`.
                continue
            if stripped == "fi":
                closed = True
                break
            statements.append(stripped)
        if closed and _parses_as_refusal_body(statements):
            return True
    return False


def gate_indices(steps: list[Mapping[str, object]]) -> list[int]:
    """The indices of steps `is_refusal_gate` recognises. Each step is
    judged as one atomic unit; this function and `ordering_problems`
    below reason only about ordering BETWEEN steps by index -- ordering
    WITHIN a single step's own script is `is_refusal_gate`'s own preamble
    rule, not this function's concern.
    """
    return [index for index, step in enumerate(steps) if is_refusal_gate(step)]


def credential_step_indices(steps: list[Mapping[str, object]]) -> list[int]:
    return [index for index, step in enumerate(steps) if _contains_credential(step)]


def ordering_problems(steps: list[Mapping[str, object]]) -> list[str]:
    """Name every step that references the credential at an index before
    the earliest recognised gate. A step is treated as ATOMIC here: the
    filter is `index < gate`, so the gate step's own index is excluded by
    construction and a step can never be named as a violation of itself.
    That is safe only because `is_refusal_gate` independently refuses to
    credit a step whose own script uses the credential before its `if`
    check -- if it did not, a step could use-then-check the credential
    fused into one script and be credited as its own compliant gate,
    invisible to this index-based check. Ordering WITHIN a step is
    `is_refusal_gate`'s job; this function only orders BETWEEN steps.
    """
    gates = gate_indices(steps)
    if not gates:
        return ["no step in this job tests the credential for emptiness and refuses"]
    gate = min(gates)
    problems = []
    for index, step in enumerate(steps):
        if index >= gate:
            continue
        if _contains_credential(step):
            problems.append(
                f"{step_name(step)!r} references the credential before the refusal gate"
            )
    return problems


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


def test_case_insensitive_reference_to_the_allowed_secret_is_recognised() -> None:
    """GitHub resolves the `secrets` context and a secret's name
    case-insensitively on reference, so `${{ Secrets.FORGEJO_READ_TOKEN }}`
    and `${{ SECRETS.forgejo_read_token }}` name the exact same secret as
    the canonical spelling. Design break condition: without `re.IGNORECASE`
    on `CREDENTIAL_REFERENCE`, a differently-cased reference to the
    ALLOWED secret would not be recognised as that reference at all --
    which would make `credential_env_var`/`_contains_credential` blind to
    a real credential use, not merely picky about spelling.
    """
    assert has_credential_reference("${{ Secrets.FORGEJO_READ_TOKEN }}")
    assert has_credential_reference("${{ SECRETS.forgejo_read_token }}")


def test_mixed_case_suffixed_secret_still_fails_closed() -> None:
    """Layers the case-insensitivity gap on top of the identifier-boundary
    bypass: `${{ Secrets.FORGEJO_READ_TOKEN_V2 }}` differs from the
    allowed secret both in casing and in the trailing `_V2`. Design break
    condition: this is caught by the SAME right-hand identifier boundary
    that closes the plain-lowercase suffix bypass, now applied
    case-insensitively; without `re.IGNORECASE` on BOTH
    `CREDENTIAL_REFERENCE` and `SECRET_CONTEXT_REFERENCE`, a case-sensitive
    backstop cannot see a differently-cased `Secrets` context reference
    either, so this exact combination would bypass both checks at once.
    """
    assert not has_credential_reference("${{ Secrets.FORGEJO_READ_TOKEN_V2 }}")
    text = """jobs:
  acquire:
    env:
      TOKEN: ${{ Secrets.FORGEJO_READ_TOKEN_V2 }}
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
        "shell": REQUIRED_GATE_SHELL,
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
        "shell": REQUIRED_GATE_SHELL,
        "run": shell_body,
    }
    assert not is_refusal_gate(step)


def test_outer_else_inverts_the_refusal_and_is_rejected() -> None:
    """`exit 1` sits in the outer `if`'s ELSE branch, not its `then`
    branch. By DESIGN that fires the instant the credential IS present
    and does nothing when it is absent -- the exact inversion of a
    credential-absence refusal, not a variant of one. Design break
    condition: the grammar's statement list for this body is `["echo
    present", "else", "exit 1"]`; `"else"` matches neither `_ECHO_LITERAL`
    nor `_EXIT_STATEMENT`, so the whole body fails to parse as a refusal
    regardless of which branch actually holds the exit -- the grammar
    rejects it for containing an unrecognised statement, not because it
    specifically knows what `else` means.
    """
    step = {
        "env": {"ALIAS": "${{ secrets.FORGEJO_READ_TOKEN }}"},
        "shell": REQUIRED_GATE_SHELL,
        "run": 'if [ -z "$ALIAS" ]; then\n  echo present\nelse\n  exit 1\nfi',
    }
    assert not is_refusal_gate(step)


def test_outer_elif_inverts_the_refusal_and_is_rejected() -> None:
    """Same inversion, the `elif` syntactic path rather than `else`: the
    `exit 1` sits in a second, alternative branch of the outer
    conditional. Design break condition: the `elif [ -n "$ALIAS" ]; then`
    line matches neither `_ECHO_LITERAL` nor `_EXIT_STATEMENT`, so it
    disqualifies the body exactly like `else` does above -- a gate with
    any alternative branch at all is refused, independent of which branch
    happens to hold the exit. This is a distinct syntactic path from
    `else` (a different token, a different line shape) and is
    deliberately proven separately rather than folded into one
    parametrized case with it.
    """
    step = {
        "env": {"ALIAS": "${{ secrets.FORGEJO_READ_TOKEN }}"},
        "shell": REQUIRED_GATE_SHELL,
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
        "shell": REQUIRED_GATE_SHELL,
        "run": 'if [ -z "$ALIAS" ]; then\n  exit 1\nfi',
    }
    assert is_refusal_gate(step)


def test_nonempty_parameter_expansion_is_not_an_empty_check() -> None:
    """A non-empty `:-present` fallback makes the condition false when the
    credential is missing, so it cannot serve as a refusal gate.
    """
    step = {
        "env": {"ALIAS": "${{ secrets.FORGEJO_READ_TOKEN }}"},
        "shell": REQUIRED_GATE_SHELL,
        "run": 'if [ -z "${ALIAS:-present}" ]; then\n  exit 1\nfi',
    }
    assert not is_refusal_gate(step)


def test_unbraced_fallback_suffix_is_not_an_empty_check() -> None:
    """The `:-` fallback is valid only inside the braced parameter form."""
    step = {
        "env": {"ALIAS": "${{ secrets.FORGEJO_READ_TOKEN }}"},
        "shell": REQUIRED_GATE_SHELL,
        "run": 'if [ -z "$ALIAS:-" ]; then\n  exit 1\nfi',
    }
    assert not is_refusal_gate(step)


def test_exit_zero_before_exit_one_is_rejected() -> None:
    """`exit 0` is an ordinary shell statement that, run first, exits the
    step SUCCESSFULLY before the `exit 1` after it is ever reached -- no
    shell trickery is involved, just two ordinary statements in sequence.
    Design break condition: the grammar's statement list is `["exit 0",
    "exit 1"]`; `"exit 0"` matches neither `_ECHO_LITERAL` (it is not an
    `echo`) nor is it the trailing statement, so its mere presence before
    the real exit disqualifies the whole body regardless of what the
    final line says.
    """
    step = {
        "env": {"ALIAS": "${{ secrets.FORGEJO_READ_TOKEN }}"},
        "shell": REQUIRED_GATE_SHELL,
        "run": 'if [ -z "$ALIAS" ]; then\n  exit 0\n  exit 1\nfi',
    }
    assert not is_refusal_gate(step)


def test_heredoc_payload_exit_is_rejected() -> None:
    """`exit 1` here is HEREDOC PAYLOAD: text piped to `cat` as input and
    printed, never executed as a command. Design break condition: the
    grammar's designated final statement is whatever line sits
    immediately before the closing `fi` -- for a real heredoc that is
    always the terminator line (`EOF`), not the payload text inside it.
    `"EOF"` does not match `_EXIT_STATEMENT`, so the body is rejected
    without the recogniser needing to understand heredoc syntax at all;
    it never has to decide that the middle line is "just data".
    """
    step = {
        "env": {"ALIAS": "${{ secrets.FORGEJO_READ_TOKEN }}"},
        "shell": REQUIRED_GATE_SHELL,
        "run": "if [ -z \"$ALIAS\" ]; then\n  cat <<'EOF'\nexit 1\nEOF\nfi",
    }
    assert not is_refusal_gate(step)


def test_backgrounded_exit_is_rejected() -> None:
    """`exit 1 &` forks the exit into a background subshell; the parent
    step continues running past the `if` block regardless of what that
    subshell later returns. Design break condition: `_EXIT_STATEMENT` is
    anchored immediately after the exit code with only trailing
    whitespace permitted before end of line -- a trailing `&` means the
    line never matches `_EXIT_STATEMENT` at all, so it can never be
    accepted as the grammar's final statement.
    """
    step = {
        "env": {"ALIAS": "${{ secrets.FORGEJO_READ_TOKEN }}"},
        "shell": REQUIRED_GATE_SHELL,
        "run": 'if [ -z "$ALIAS" ]; then\n  exit 1 &\nfi',
    }
    assert not is_refusal_gate(step)


def test_statement_after_the_exit_is_rejected() -> None:
    """Michael's grammar requires the final exit to have NO statements
    after it. Design break condition: `_parses_as_refusal_body` only ever
    checks the LAST statement against `_EXIT_STATEMENT`; a further
    diagnostic line after a genuine `exit 1` makes that trailing line, not
    the real exit, the one checked -- so it fails the exit grammar.
    Loosening this "last statement only" rule to search for an exit
    ANYWHERE in the body is precisely what would let a statement placed
    after the exit -- reachable or not -- go unnoticed.
    """
    step = {
        "env": {"ALIAS": "${{ secrets.FORGEJO_READ_TOKEN }}"},
        "shell": REQUIRED_GATE_SHELL,
        "run": 'if [ -z "$ALIAS" ]; then\n  exit 1\n  echo "still here"\nfi',
    }
    assert not is_refusal_gate(step)


def test_echo_literal_excludes_expansion_and_substitution() -> None:
    """Design break condition: `_ECHO_LITERAL` excludes `$`, backticks and
    backslashes from INSIDE the quoted argument. Loosening that exclusion
    is exactly what would let a `$(...)`- or backtick-substitution-bearing
    `echo` argument pass as a "plain diagnostic literal", when it is
    really an executable substitution wearing a diagnostic's clothes.
    """
    assert not _ECHO_LITERAL.match('echo "$(rm -rf /)"')
    assert not _ECHO_LITERAL.match('echo "`whoami`"')


def test_echo_literal_excludes_packed_commands() -> None:
    """Design break condition: `_ECHO_LITERAL` anchors immediately after
    the closing quote, permitting only trailing whitespace before end of
    line. An unquoted `;` after the quote packs a second command onto the
    same physical line; allowing anything after the quote is exactly what
    would let an attacker smuggle an unchecked second statement past a
    scanner that only inspects the echoed literal itself.
    """
    assert not _ECHO_LITERAL.match('echo "safe"; rm -rf /')


def test_quoted_semicolon_and_hash_are_data_not_syntax() -> None:
    """The real gate's second diagnostic line is exactly
    `echo "::error::Its owner is OpenBao secret/dotmac/forgejo/read-token#value;"`
    -- a `;` and a `#` sit INSIDE the double-quoted literal, as DATA, not
    a command separator or a comment starter. Design break condition: a
    naive unquoted-`;`-split or comment-strip check would see the `;` as a
    packed second command or the `#` as a truncation point and reject this
    exact real-workflow line; `_ECHO_LITERAL` instead matches the whole
    quoted span as one opaque literal and only excludes `$`, backticks and
    backslashes from inside it -- `;` and `#` are simply not in that
    exclusion set.
    """
    line = (
        'echo "::error::Its owner is OpenBao secret/dotmac/forgejo/read-token#value;"'
    )
    assert _ECHO_LITERAL.match(line)


def test_the_real_gates_exact_shape_is_accepted() -> None:
    """The exact shape of the real workflow's gate, reproduced as a
    synthetic step so this test does not depend on reading the file from
    disk: three plain diagnostic `echo "..."` lines (the second one
    carrying a literal `;` and `#` inside its quotes, per the test above)
    followed by exactly one final `exit 1`, with nothing else. This is the
    positive control every negative plant in this module depends on --
    without it, a grammar that always returns False would also pass every
    negative test above for the wrong reason.
    """
    step = {
        "env": {"FORGEJO_CREDENTIAL": "${{ secrets.FORGEJO_READ_TOKEN }}"},
        "shell": REQUIRED_GATE_SHELL,
        "run": (
            'if [ -z "${FORGEJO_CREDENTIAL:-}" ]; then\n'
            '  echo "::error::FORGEJO_READ_TOKEN is unset or empty in this repository."\n'
            '  echo "::error::Its owner is OpenBao secret/dotmac/forgejo/'
            'read-token#value;"\n'
            '  echo "::error::this workflow consumes the projection, it does not'
            ' fetch it."\n'
            "  exit 1\n"
            "fi"
        ),
    }
    assert is_refusal_gate(step)


def test_blank_lines_between_statements_are_tolerated() -> None:
    """A deliberate grammar decision, not an oversight: a blank line
    carries no shell meaning at all, so an author leaving one between two
    diagnostics, or between the diagnostics and the final exit, must not
    disqualify an otherwise-conforming gate. Design break condition:
    blank-line tolerance is scoped to lines that are empty AFTER
    stripping -- it can never absorb a line carrying real content, so
    widening this check beyond truly-empty lines is what would risk
    swallowing something that should have disqualified the body.
    """
    step = {
        "env": {"ALIAS": "${{ secrets.FORGEJO_READ_TOKEN }}"},
        "shell": REQUIRED_GATE_SHELL,
        "run": (
            'if [ -z "$ALIAS" ]; then\n'
            '  echo "first"\n'
            "\n"
            '  echo "second"\n'
            "\n"
            "  exit 1\n"
            "fi"
        ),
    }
    assert is_refusal_gate(step)


def test_fused_use_then_check_step_is_not_credited_as_a_gate() -> None:
    """A single step that USES the credential (the `curl` line) and only
    THEN checks it for emptiness is not a compliant absence gate: the
    credential has already crossed the network by the time the check
    runs. Design break condition: `${FORGEJO_CREDENTIAL}` appears in the
    PREAMBLE -- the text before the matched `if` header -- and
    `_preamble_is_admissible` refuses any credential reference there
    unconditionally, before the preamble's shape is even considered;
    removing that check (or narrowing it to only look at the body after
    the header, as the recogniser used to) is exactly what let a fused
    use-then-check step be credited as its own gate.
    """
    step = {
        "env": {"FORGEJO_CREDENTIAL": "${{ secrets.FORGEJO_READ_TOKEN }}"},
        "shell": REQUIRED_GATE_SHELL,
        "run": (
            'curl -H "Authorization: Bearer ${FORGEJO_CREDENTIAL}" '
            "https://registry/ -o /tmp/out\n"
            'if [ -z "${FORGEJO_CREDENTIAL}" ]; then\n'
            '  echo "::error::missing"\n'
            "  exit 1\n"
            "fi"
        ),
    }
    assert not is_refusal_gate(step)


def test_fused_use_then_check_step_is_named_by_ordering_problems() -> None:
    """The other half of the same defect: even with `is_refusal_gate`
    correctly refusing the fused step above, `ordering_problems` still
    needs to actually flag it rather than silently passing a job that
    holds no recognised gate at all. Design break condition: with the
    fused step no longer credited as a gate, it is an ordinary
    credentialed step sitting before the real gate at a later index, so
    `ordering_problems`'s `index < gate` filter -- which used to exclude
    a gate step from being named as a violation of ITSELF -- now applies
    to it like any other credentialed step and names it directly.
    """
    fused = {
        "name": "Download and check",
        "env": {"FORGEJO_CREDENTIAL": "${{ secrets.FORGEJO_READ_TOKEN }}"},
        "shell": REQUIRED_GATE_SHELL,
        "run": (
            'curl -H "Authorization: Bearer ${FORGEJO_CREDENTIAL}" '
            "https://registry/ -o /tmp/out\n"
            'if [ -z "${FORGEJO_CREDENTIAL}" ]; then\n'
            '  echo "::error::missing"\n'
            "  exit 1\n"
            "fi"
        ),
    }
    real_gate = {
        "name": "There is a credential to resolve with",
        "env": {"FORGEJO_CREDENTIAL": "${{ secrets.FORGEJO_READ_TOKEN }}"},
        "shell": REQUIRED_GATE_SHELL,
        "run": (
            'if [ -z "${FORGEJO_CREDENTIAL:-}" ]; then\n'
            '  echo "::error::missing"\n'
            "  exit 1\n"
            "fi"
        ),
    }
    assert is_refusal_gate(real_gate)
    assert any(
        "Download and check" in item for item in ordering_problems([fused, real_gate])
    )


def test_preamble_set_option_line_is_admitted() -> None:
    """The positive control for the preamble rule itself: `set -euo
    pipefail` immediately before the `if` header is exactly the real
    gate's own shape, and must still be admitted -- it is side-effect-free
    and references no credential. Design break condition: if
    `_PREAMBLE_SET_LINE` stopped matching this exact line, or the
    preamble check were tightened to reject any preamble at all, the real
    workflow's own gate would break; the rule must accommodate this shape
    on its own terms, not by being loosened to fit it after the fact.
    """
    step = {
        "env": {"ALIAS": "${{ secrets.FORGEJO_READ_TOKEN }}"},
        "shell": REQUIRED_GATE_SHELL,
        "run": 'set -euo pipefail\nif [ -z "$ALIAS" ]; then\n  exit 1\nfi',
    }
    assert is_refusal_gate(step)


@pytest.mark.parametrize(
    "preamble",
    [
        pytest.param("set", id="bare-set"),
        pytest.param("set -x", id="short-xtrace"),
        pytest.param("set -o xtrace", id="long-xtrace"),
        pytest.param("set -n", id="short-noexec"),
        pytest.param("set -o noexec", id="long-noexec"),
    ],
)
def test_preamble_refuses_variable_display_tracing_and_noexec(
    preamble: str,
) -> None:
    """Only the real workflow's errexit/pipefail preamble is admissible.

    Bare `set` displays shell variables, tracing can print expanded
    credentials, and noexec prevents the refusal from executing. Design
    break condition: removing the exact positive allowlist and restoring a
    broad token-shape grammar makes each of these plants pass as a gate.
    """
    step = {
        "env": {"ALIAS": "${{ secrets.FORGEJO_READ_TOKEN }}"},
        "shell": REQUIRED_GATE_SHELL,
        "run": f'{preamble}\nif [ -z "$ALIAS" ]; then\n  exit 1\nfi',
    }
    assert not is_refusal_gate(step)


def test_real_workflow_gate_with_exact_preamble_is_admitted() -> None:
    """The actual acquire workflow remains the positive control for the
    narrowed preamble rule, including its exact `set -euo pipefail` line.
    """
    gate = next(
        step
        for step in _acquire_steps()
        if step_name(step) == "There is a credential to resolve with"
    )
    assert "set -euo pipefail" in commands_of(gate)
    assert is_refusal_gate(gate)


@pytest.mark.parametrize(
    "metadata",
    [
        pytest.param({"if": "always()"}, id="step-if"),
        pytest.param({"continue-on-error": True}, id="continue-on-error"),
        pytest.param({"continue-on-error": "true"}, id="string-continue-on-error"),
        pytest.param({"shell": "bash"}, id="custom-shell"),
        pytest.param({"env": {"BASH_ENV": "setup.sh"}}, id="bash-env"),
        pytest.param({"env": {"SHELLOPTS": "xtrace"}}, id="shellopts"),
    ],
)
def test_gate_refuses_untrusted_step_metadata(metadata: Mapping[str, object]) -> None:
    metadata_env = _mapping(metadata.get("env", {}), "metadata env")
    baseline = {
        "env": {"ALIAS": "${{ secrets.FORGEJO_READ_TOKEN }}"},
        "shell": REQUIRED_GATE_SHELL,
        "run": 'if [ -z "$ALIAS" ]; then\n  exit 1\nfi',
    }
    assert is_refusal_gate(baseline)
    step = {
        **baseline,
        "env": {"ALIAS": "${{ secrets.FORGEJO_READ_TOKEN }}", **metadata_env},
        **{key: value for key, value in metadata.items() if key != "env"},
    }
    assert credential_env_var(step) == "ALIAS"
    assert not is_refusal_gate(step)


def test_gate_requires_exact_secret_expression() -> None:
    baseline = {
        "env": {"ALIAS": ALLOWED_SECRET_EXPRESSION},
        "shell": REQUIRED_GATE_SHELL,
        "run": 'if [ -z "$ALIAS" ]; then\n  exit 1\nfi',
    }
    assert is_refusal_gate(baseline)
    for expression in (
        "${{ Secrets.FORGEJO_READ_TOKEN }}",
        "${{ secrets.FORGEJO_READ_TOKEN || 'present' }}",
    ):
        mutated = {**baseline, "env": {"ALIAS": expression}}
        assert credential_env_var(mutated) == "ALIAS"
        assert not is_refusal_gate(mutated)


def test_gate_refuses_inherited_shell_and_environment_defaults() -> None:
    text = WORKFLOW.read_text()
    anchor = "jobs:\n"
    assert text.count(anchor) == 1
    text = text.replace(anchor, "defaults:\n  run:\n    shell: bash\njobs:\n", 1)
    assert text != WORKFLOW.read_text()
    jobs = parse_jobs(text)
    assert gate_refusal_problems(split_steps(jobs["acquire"]))

    text = WORKFLOW.read_text()
    anchor = "  acquire:\n"
    assert text.count(anchor) == 1
    text = text.replace(
        anchor, "  acquire:\n    defaults:\n      run:\n        shell: bash\n", 1
    )
    assert text != WORKFLOW.read_text()
    jobs = parse_jobs(text)
    assert gate_refusal_problems(split_steps(jobs["acquire"]))

    text = WORKFLOW.read_text()
    anchor = "env:\n  # The loopback port the acquired bundle is served on during resolution.\n"
    assert text.count(anchor) == 1
    text = text.replace(anchor, anchor + "  BASH_ENV: setup.sh\n", 1)
    assert text != WORKFLOW.read_text()
    jobs = parse_jobs(text)
    assert gate_refusal_problems(split_steps(jobs["acquire"]))

    text = WORKFLOW.read_text()
    anchor = "  acquire:\n    runs-on: ubuntu-latest\n"
    assert text.count(anchor) == 1
    text = text.replace(anchor, anchor + "    continue-on-error: true\n", 1)
    assert text != WORKFLOW.read_text()
    jobs = parse_jobs(text)
    assert jobs["acquire"].get("continue-on-error") is True
    assert gate_refusal_problems(split_steps(jobs["acquire"]))
