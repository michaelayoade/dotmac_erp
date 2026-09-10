"""Fail if a strict-JSON evidence document is not valid, strict JSON.

`docs/kernel-runtime-composition.json` and `docs/kernel-runtime-readiness.json`
are read by another repository's gate (dotmac_starter_mt) as DATA -- never
imported, never executed as Python -- and must stay parseable by `json.load`
with zero tolerance: no comments, no trailing commas, no relaxed JSON5/JSONC
shape. This check exists because that exact defect is reachable: `ruff
format <path.json>`, invoked directly with the file named on the command
line, rewrites strict JSON into a trailing-comma shape `json.load` rejects
(reproduced directly against this repository's own composition document
while diagnosing PR #530's Pre-commit failure; the failure that PR actually
hit was unrelated -- see the `.pre-commit-config.yaml` comment above this
hook's entry for the full trace -- but the defect itself is real and this
guard is what stands between it and a merged, silently-corrupted record).

Wired in TWO places, deliberately not redundant with each other -- do not
delete either as a duplicate of the other:

1. The `strict-json-evidence-documents` local hook in
   `.pre-commit-config.yaml`, placed AFTER the ruff hooks in that file so it
   catches a formatter that mangled a document during THAT pre-commit run,
   in the same job, on the same runner, immediately after the step that
   could have mangled it -- a real causal guarantee, not an accident of
   which files an exclusion currently reaches.
2. A "Strict-JSON evidence documents parse after formatting" step in CI's
   `lint` job (`.github/workflows/ci.yml`), immediately after the "Ruff
   format check" step, for the identical same-job-same-runner reason. A
   validator wired only as a separate architecture-test job (parallel to
   `lint` on its own runner, checking out the same commit independently)
   would prove nothing about ordering relative to `lint`'s formatter step --
   that is exactly the gap this second wiring closes.

`tests/architecture/test_strict_json_evidence_documents.py` covers a THIRD,
different failure mode: a document that arrived already broken by some other
route entirely (a hand edit, a different tool, a future script) -- not
formatter causality. Keep all three.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

#: Paths relative to the repository root. Add a new strict-JSON evidence
#: document here (and to the `files:` regex in .pre-commit-config.yaml's
#: `strict-json-evidence-documents` hook, and to the `exclude:` entries on
#: the two ruff hooks above it) the day one is introduced -- never inferred
#: from a glob, since a glob would also catch documents that are allowed to
#: be reformatted.
STRICT_JSON_EVIDENCE_RELATIVE_PATHS: tuple[Path, ...] = (
    Path("docs/kernel-runtime-composition.json"),
    Path("docs/kernel-runtime-readiness.json"),
)


def check_document(path: Path) -> str | None:
    """Return a failure message naming `path`, or `None` if it parses as
    strict JSON. Never raises -- every failure mode (missing file, invalid
    JSON) is reported as text so the caller can collect every failure
    before exiting, rather than stopping at the first one."""
    if not path.is_file():
        return f"{path}: missing"
    try:
        json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return f"{path}: invalid JSON -- {exc}"
    return None


def main(repo_root: Path | None = None) -> int:
    root = repo_root or Path(__file__).resolve().parents[1]
    failures = [
        message
        for relative_path in STRICT_JSON_EVIDENCE_RELATIVE_PATHS
        if (message := check_document(root / relative_path)) is not None
    ]
    for failure in failures:
        print(f"ERROR: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
