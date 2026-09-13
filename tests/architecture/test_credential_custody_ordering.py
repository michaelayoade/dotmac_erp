"""`.github/workflows/erp-lock.yml`'s credential custody is CHECKED, not
merely true.

`secrets.FORGEJO_READ_TOKEN` is held only inside the `acquire` job. That
safety rests today on four properties that are EMERGENT -- true of the file
as written, asserted nowhere -- so a later edit could remove any one of them
silently:

1. the credential is bound at STEP level only, never at job level (a
   job-level `env:` would put it in every step's environment, including
   steps that have no business holding it);
2. only `acquire` ever references it -- `resolve` and `attest` do not;
3. the one step that refuses an EMPTY credential precedes every OTHER step
   that references the credential -- located by its BEHAVIOUR (it tests the
   variable for emptiness and exits non-zero), never by its step name, since
   a name is cosmetic and can be renamed or moved independently of the
   behaviour it currently sits next to;
4. that gate genuinely refuses -- a step that only MENTIONS the variable is
   not a gate.

Each check is proven against a MUTATED copy of the real workflow that
breaks exactly the property it names, because a check that only ever ran
against the clean file would pass for any number of wrong reasons.

PyYAML is not a declared dependency of this repository (absent from
`pyproject.toml`; it appears in `poetry.lock` only as a transitive
dependency of unrelated packages), so this module follows the same
hand-rolled indentation walk already established next to it in
`test_erp_lock_workflow.py`'s `_jobs()`/`_needs()`, rather than adding a
YAML dependency for one file.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "erp-lock.yml"

CREDENTIAL_SECRET = "secrets.FORGEJO_READ_TOKEN"

# ── parsing ──────────────────────────────────────────────────────────────

_JOB_HEADER = re.compile(r"^  ([A-Za-z][\w-]*):\s*$")


def parse_jobs(text: str) -> dict[str, list[str]]:
    """job name -> every line belonging to that job's own body, from the
    `jobs:` block to the next job header or end of file."""

    lines = text.splitlines()
    start = lines.index("jobs:")
    jobs: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines[start + 1 :]:
        header = _JOB_HEADER.match(line)
        if header:
            current = header.group(1)
            jobs[current] = []
            continue
        if current is None:
            continue
        jobs[current].append(line)
    return jobs


def job_level_env_lines(body: list[str]) -> list[str]:
    """The job's OWN `env:` mapping, declared directly under the job at
    4-space indent -- distinct from a step's `env:`, which sits nested
    under a step at 8-space indent and is never returned here."""

    lines: list[str] = []
    in_env = False
    for line in body:
        if re.match(r"^    env:\s*$", line):
            in_env = True
            lines.append(line)
            continue
        if in_env:
            if line.strip() == "" or line.startswith("      "):
                lines.append(line)
                continue
            in_env = False
    return lines


def split_steps(body: list[str]) -> list[str]:
    """Every step in a job's body as its own text block -- steps are the
    `      - ...` list items under `    steps:`. Mirrors the convention
    already established by `test_erp_lock_workflow.py`'s `_jobs()`."""

    steps: list[list[str]] = []
    in_steps = False
    for line in body:
        if line == "    steps:":
            in_steps = True
            continue
        if not in_steps:
            continue
        if line.startswith("      - "):
            steps.append([line])
        elif steps:
            steps[-1].append(line)
    return ["\n".join(step) for step in steps]


def commands_of(step: str) -> str:
    """A step's script with comment-only lines stripped -- a comment can
    say anything without making a behavioural check pass or fail for the
    wrong reason."""

    return "\n".join(
        line for line in step.splitlines() if not line.strip().startswith("#")
    )


def step_name(step: str) -> str:
    match = re.search(r"^      - name: (.+)$", step, re.M)
    return match.group(1).strip() if match else "<unnamed step>"


# ── assertion 1: no job-level credential environment exists ────────────────


def job_level_credential_violations(jobs: dict[str, list[str]]) -> list[str]:
    """Job names whose OWN `env:` mapping (not any step's) binds the
    credential secret. Empty when the property holds."""

    return [
        name
        for name, body in jobs.items()
        if any(CREDENTIAL_SECRET in line for line in job_level_env_lines(body))
    ]


# ── assertion 2: every reference is inside `acquire` ────────────────────────


def jobs_referencing_credential(jobs: dict[str, list[str]]) -> set[str]:
    return {
        name
        for name, body in jobs.items()
        if any(CREDENTIAL_SECRET in line for line in body)
    }


# ── locating the refusal gate by BEHAVIOUR, never by step name ─────────────


def credential_env_var(step: str) -> str | None:
    """The variable name a step binds `secrets.FORGEJO_READ_TOKEN` to in
    its OWN `env:`, or `None` if this step does not hold the credential."""

    match = re.search(r"(\w+):\s*\$\{\{\s*secrets\.FORGEJO_READ_TOKEN\s*\}\}", step)
    return match.group(1) if match else None


def is_refusal_gate(step: str) -> bool:
    """True only if this step tests its OWN credential-bound variable for
    emptiness and exits NON-ZERO inside that same conditional. A step that
    merely mentions the variable -- or tests it without ever refusing --
    does not qualify. This is a behavioural test: it never looks at the
    step's `name:`."""

    var = credential_env_var(step)
    if var is None:
        return False
    body = commands_of(step)
    pattern = rf'if \[ -z "\$\{{?{re.escape(var)}(?::-[^}}]*)?\}}?"\s*\];?\s*then'
    for match in re.finditer(pattern, body):
        remainder = body[match.end() :]
        close = remainder.find("fi")
        clause = remainder[: close if close != -1 else None]
        if re.search(r"\bexit\s+[1-9]", clause):
            return True
    return False


def gate_indices(steps: list[str]) -> list[int]:
    return [i for i, step in enumerate(steps) if is_refusal_gate(step)]


def credential_step_indices(steps: list[str]) -> list[int]:
    return [i for i, step in enumerate(steps) if CREDENTIAL_SECRET in step]


# ── assertion 3: the gate precedes every other credential-using step ───────


def ordering_problems(steps: list[str]) -> list[str]:
    """Empty if the earliest refusal gate precedes every OTHER
    credential-referencing step in `steps`; otherwise one message per step
    left unprotected, naming that step."""

    gates = gate_indices(steps)
    credentialed = credential_step_indices(steps)
    if not gates:
        return ["no step in this job tests the credential for emptiness and refuses"]
    if not credentialed:
        return ["no step in this job references the credential at all"]
    gate = min(gates)
    problems = []
    for i in credentialed:
        if i < gate:
            problems.append(
                f"{step_name(steps[i])!r} references the credential at "
                f"position {i}, before the refusal gate at position {gate} "
                "-- it is not covered by the empty-credential refusal"
            )
    return problems


# ── assertion 4: the gate actually refuses ──────────────────────────────────


def gate_refusal_problems(steps: list[str]) -> list[str]:
    if not gate_indices(steps):
        return [
            "no step tests the credential for emptiness and exits non-zero "
            "-- a step that only mentions the variable is not a gate"
        ]
    return []


# ═══════════════════════════════════════════════════════════════════════════
# non-vacuity: the real workflow, as it stands today
# ═══════════════════════════════════════════════════════════════════════════


def test_the_workflow_parses_into_the_three_expected_jobs() -> None:
    jobs = parse_jobs(WORKFLOW.read_text())
    assert set(jobs) == {"acquire", "resolve", "attest"}, sorted(jobs)
    for name, body in jobs.items():
        assert split_steps(body), name


def test_no_job_level_credential_environment_exists() -> None:
    jobs = parse_jobs(WORKFLOW.read_text())
    assert job_level_credential_violations(jobs) == []


def test_every_reference_to_the_credential_is_inside_acquire() -> None:
    jobs = parse_jobs(WORKFLOW.read_text())
    assert jobs_referencing_credential(jobs) == {"acquire"}


def test_the_gate_is_located_by_behaviour_and_is_the_expected_step() -> None:
    """Confirms the behavioural locator finds exactly one gate in the real
    file, and (only as a cross-check, not as the locating mechanism) that
    it is the step the brief names -- proving the behavioural search is not
    accidentally finding nothing or something else."""

    steps = split_steps(parse_jobs(WORKFLOW.read_text())["acquire"])
    gates = gate_indices(steps)
    assert len(gates) == 1, gates
    assert "There is a credential to resolve with" in steps[gates[0]]


def test_a_credentialed_step_that_never_refuses_is_not_mistaken_for_the_gate() -> None:
    """NON-VACUITY the other way: `acquire` has other steps that reference
    the credential without an emptiness test (the download and the
    bundle/attestation scan steps) -- none of them must be found as a
    gate."""

    steps = split_steps(parse_jobs(WORKFLOW.read_text())["acquire"])
    non_gate_credentialed = [
        i for i in credential_step_indices(steps) if not is_refusal_gate(steps[i])
    ]
    assert len(non_gate_credentialed) >= 2, non_gate_credentialed


def test_the_gate_precedes_every_other_credential_using_step() -> None:
    steps = split_steps(parse_jobs(WORKFLOW.read_text())["acquire"])
    assert ordering_problems(steps) == []


def test_the_gate_actually_refuses() -> None:
    steps = split_steps(parse_jobs(WORKFLOW.read_text())["acquire"])
    assert gate_refusal_problems(steps) == []


# ═══════════════════════════════════════════════════════════════════════════
# SENSITIVITY: each assertion proven against a planted defect it must name
# ═══════════════════════════════════════════════════════════════════════════


def test_a_credentialed_step_moved_above_the_gate_is_named() -> None:
    """PLANT for assertion 3. Swap the gate with the credentialed step that
    immediately follows it in the real file ("Download the closed bundle
    from the private index"), so that step now runs BEFORE the refusal --
    exactly the drift the brief warns about."""

    steps = split_steps(parse_jobs(WORKFLOW.read_text())["acquire"])
    gate = gate_indices(steps)[0]
    credentialed = credential_step_indices(steps)
    victim = next(i for i in credentialed if i == gate + 1)
    mutated = list(steps)
    mutated[gate], mutated[victim] = mutated[victim], mutated[gate]

    problems = ordering_problems(mutated)
    assert problems, "moving a credentialed step above the gate was not caught"
    assert any("Download the closed bundle" in p for p in problems), problems


def test_the_credential_lifted_to_job_level_env_is_named() -> None:
    """PLANT for assertion 1. Insert a job-level `env:` binding the
    credential into `acquire`, directly under the job and before its
    `steps:` key -- exactly the shape that would leak the credential into
    every step's environment."""

    jobs = parse_jobs(WORKFLOW.read_text())
    body = list(jobs["acquire"])
    steps_index = body.index("    steps:")
    mutated_body = (
        body[:steps_index]
        + ["    env:", "      FORGEJO_CREDENTIAL: ${{ secrets.FORGEJO_READ_TOKEN }}"]
        + body[steps_index:]
    )
    mutated_jobs = dict(jobs)
    mutated_jobs["acquire"] = mutated_body

    violations = job_level_credential_violations(mutated_jobs)
    assert violations == ["acquire"], violations


def test_a_clean_job_level_env_with_no_credential_is_not_flagged() -> None:
    """NEAR-MISS for assertion 1. A job-level `env:` that binds something
    OTHER than the credential (like the workflow's own `MIRROR_PORT`
    pattern) must not be flagged -- the rule is about the credential
    specifically, not about job-level `env:` existing at all."""

    jobs = parse_jobs(WORKFLOW.read_text())
    body = list(jobs["acquire"])
    steps_index = body.index("    steps:")
    mutated_body = (
        body[:steps_index]
        + ["    env:", "      SOME_OTHER_VALUE: 'harmless'"]
        + body[steps_index:]
    )
    mutated_jobs = dict(jobs)
    mutated_jobs["acquire"] = mutated_body

    assert job_level_credential_violations(mutated_jobs) == []


@pytest.mark.parametrize("job_name", ["resolve", "attest"])
def test_a_credential_reference_added_outside_acquire_is_named(job_name: str) -> None:
    """PLANT for assertion 2. Add one line referencing the credential into
    `resolve` or `attest`'s body -- neither job may ever reference it."""

    jobs = parse_jobs(WORKFLOW.read_text())
    mutated_jobs = dict(jobs)
    mutated_jobs[job_name] = [
        *jobs[job_name],
        "      SNEAKY: ${{ secrets.FORGEJO_READ_TOKEN }}",
    ]

    referencing = jobs_referencing_credential(mutated_jobs)
    assert referencing == {"acquire", job_name}, referencing


def test_removing_the_gates_refusal_body_is_named() -> None:
    """PLANT for assertion 4. Keep the gate step's name and its `env:`
    binding exactly as they are, but strip the part of its script that
    actually refuses (`exit 1`) -- a step that still MENTIONS the variable
    but no longer refuses on emptiness must stop being recognised as a
    gate."""

    steps = split_steps(parse_jobs(WORKFLOW.read_text())["acquire"])
    gate = gate_indices(steps)[0]
    assert is_refusal_gate(steps[gate])  # sanity: it is the gate before mutation

    defanged = steps[gate].replace("exit 1", "echo 'no longer refuses'")
    assert not is_refusal_gate(defanged), "the plant left the gate intact"

    mutated = list(steps)
    mutated[gate] = defanged
    problems = gate_refusal_problems(mutated)
    assert problems, "a defanged gate was not caught"
    assert "no step tests the credential for emptiness" in problems[0]


def test_a_step_that_only_mentions_the_variable_is_not_treated_as_a_gate() -> None:
    """NEAR-MISS for assertion 4, constructed directly: a synthetic step
    that references the credential and even contains an unrelated `exit 1`
    elsewhere in its script, but never tests the variable for emptiness,
    must not be recognised as a gate."""

    fake_step = (
        "      - name: Looks similar but refuses nothing\n"
        "        env:\n"
        "          FORGEJO_CREDENTIAL: ${{ secrets.FORGEJO_READ_TOKEN }}\n"
        "        run: |\n"
        '          echo "the credential is $FORGEJO_CREDENTIAL"\n'
        "          if [ \"$SOME_OTHER_CHECK\" = 'bad' ]; then\n"
        "            exit 1\n"
        "          fi\n"
    )
    assert not is_refusal_gate(fake_step)
