"""Every suppression in `.secrets.baseline` names why it is there.

The baseline held **121 findings across 77 files, none carrying a reason**.
That is not a list of accepted risks; it is a record that somebody pressed
"ignore" 121 times. It had already swallowed three of the four credentials
scrubbed on 2026-08-11 — each was correctly flagged `Secret Keyword`, and each
was written into the baseline instead of fixed.

## What the retirement found

The 121 were not 121 judgements:

* **82 were for files the hook already excluded** (`tests/`, `alembic/`,
  `scripts/`, `.env.example`). Never scanned, suppressing nothing — pure
  residue from a baseline generated before the exclude list existed.
* **20 more were Python**, now covered by
  `test_no_committed_credentials.py`, which walks the AST of every tracked
  `.py` file INCLUDING `scripts/` — the directory this hook skips and where all
  four real credentials actually lived.
* **19 remain**, and they are enumerated below with reasons.

The Python entries are worth naming, because they are what an entropy
heuristic produces: 22 were `Artifactory Credentials` matching `AP`-prefixed
identifiers (`APAgingService`, `APBatchStatus`, `APAgingBucketRead` — accounts
payable), and 32 were `Hex High Entropy String` matching Alembic revision IDs
(`revision = "9b2a7c1d4c9a"`), which are REQUIRED to be random hex and gain one
more with every migration. A detector whose false-positive rate grows with the
codebase produces a suppression list that grows with it too.

## What is covered, and what is deliberately not

After this change, tracked Python is checked by the AST guard over `app/`,
`scripts/` and `alembic/` — 2,018 files — and every other file type by this
hook. Between them that is everything EXCEPT `tests/` (674 Python files),
which neither covers.

That gap is stated rather than closed, and the measurement is why: extending
the AST guard to `tests/` fires on **71 sites**, almost all of them fake
`token=` fixtures in API tests. Covering it would mean a 71-entry allowlist —
recreating, in a new file, exactly the unexplained suppression list this change
exists to delete.

Per ADR-0018 the honest form is that `tests/` is UNMONITORED, not exempt. A
credential in a test fixture is a real if lower-severity leak, and nothing here
would catch it. Closing it needs a detector that can tell a fixture from a
credential, which is a different piece of work.

## The rule

A baseline entry is an exemption, and ADR-0018 says an exemption states an
enforceable premise. `REASONS` below is that statement. The list may shrink
freely; growing it means adding the reason at the same time, which is the whole
point — the cost of suppressing a finding should be having to say why.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, cast

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
BASELINE = REPO_ROOT / ".secrets.baseline"

# Why each file's findings are not secrets. Keyed by path, because the
# findings within a file are all the same kind of thing.
REASONS = {
    ".dotmac/standards-profile.json": (
        "Public integrity identifiers: the pinned governance commit (hard "
        "rule 15) and schema-9 conservation fingerprints. Both are hex by "
        "definition; neither authenticates an actor or grants access."
    ),
    ".github/workflows/ci.yml": (
        "`secrets: |` is a YAML KEY, not a value, and the DATABASE_URL is "
        "`postgres:postgres` against the ephemeral service container that "
        "exists only for the length of the job."
    ),
    ".github/workflows/release-hardened.yml": (
        "`secrets: |` — the same YAML key. The values it forwards live in "
        "GitHub's secret store and never appear here."
    ),
    "README.md": (
        "Documentation of the DATABASE_URL FORMAT, with the local dev "
        "credentials as the example. Nothing here reaches a real host."
    ),
    "deploy/rendered/otel-collector.yaml": (
        "The renderer projects that same public Git commit AND image digest "
        "into telemetry resource attributes. Rendered bytes are checked against "
        "the released facility and the deployment descriptor test proves the "
        "fields stay identical to the descriptor they came from."
    ),
    "docs/kernel-runtime-composition.json": (
        "`Hex High Entropy String` on `starter_catalogue_revision`: a "
        "40-character git commit SHA naming the dotmac_starter_mt "
        "protected-main revision this record's dimensional-composition.v2 "
        "catalogue and manifests were derived against. It is REQUIRED to "
        "be 40 hex characters by its own contract "
        "(tests/architecture/composition_schema.py's derive_distribution_"
        "universe/manifest reading, and this file's own "
        "test_envelope_starter_catalogue_revision_is_the_full_protected_"
        "main_sha), which is exactly why the entropy heuristic fires. It "
        "authenticates nothing: a git commit SHA is published in every "
        "clone of dotmac_starter_mt, names a point in history rather than "
        "granting access, and this specific value is independently pinned "
        "and checked against by that same test."
    ),
    "docs/paystack_chargebacks_investigation.md": (
        "Paystack transaction references from a written-up investigation. "
        "They identify transactions, not an actor — a reference authorises "
        "nothing on its own, and the doc is the record of what was examined."
    ),
    "locales/en.json": (
        "UI strings: 'Forgot password?', 'Reset password', 'Confirm "
        "password'. The detector matches the WORD. There is no value here to "
        "leak, and the alternative is not translating the login screen."
    ),
    "templates/people/hr/geofence_editor.html": (
        "Subresource Integrity hashes for the Leaflet CDN assets. An SRI "
        "attribute IS a base64 hash — flagging it as a secret inverts its "
        "purpose, which is to make the asset tamper-evident in public."
    ),
}


def _baseline() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(BASELINE.read_text(encoding="utf-8")))


def _files() -> set[str]:
    return set(_baseline()["results"])


def test_every_suppressed_file_states_a_reason() -> None:
    unexplained = sorted(_files() - set(REASONS))
    assert unexplained == [], (
        "These files have suppressed findings and no stated reason. A baseline "
        "entry without one is indistinguishable from a real secret nobody "
        "looked at — which is how three of the four scrubbed credentials got "
        "here (ADR-0018):\n  " + "\n  ".join(unexplained)
    )


def test_no_reason_outlives_its_finding() -> None:
    """A reason for a file that no longer has findings is stale documentation,
    and stale documentation is how a list stops being read."""
    stale = sorted(set(REASONS) - _files())
    assert stale == [], (
        "These no longer have suppressed findings — delete their entries:\n  "
        + "\n  ".join(stale)
    )


def test_python_is_not_suppressed_here() -> None:
    """Python belongs to `test_no_committed_credentials.py`, which has no
    baseline. An entry reappearing here means the AST guard was routed around
    rather than satisfied — and it covers `scripts/`, which this hook does not.
    """
    python = sorted(f for f in _files() if f.endswith(".py"))
    assert python == [], (
        "Python findings must be fixed, not suppressed — "
        "tests/architecture/test_no_committed_credentials.py owns them:\n  "
        + "\n  ".join(python)
    )


def test_the_suppression_count_only_shrinks() -> None:
    """An exact pin, not merely a ceiling.

    The prior floor was 18. Schema-9 conservation added seven public integrity
    fingerprints to the already-listed profile; ERP's required public source
    revision adds one descriptor finding and one deterministic telemetry
    projection, reaching 27.

    28 admitted exactly ONE more, named rather than absorbed: the published
    OCI image digest, projected into the rendered collector at
    `deploy/rendered/otel-collector.yaml`. It appeared when the descriptor
    stopped carrying an all-zero sentinel and began binding the digest
    protected-main CI resolved for the image it built and tested. A digest is
    the NAME of publicly published bytes and authorises nothing; it is long hex,
    which is the whole reason the entropy heuristic fires on it. It is here
    precisely BECAUSE the descriptor refuses a mutable tag, so suppressing it is
    the cost of the stronger reference, not a concession.

    29 is `docs/kernel-runtime-composition.json`'s single finding:
    `Hex High Entropy String` on `starter_catalogue_revision`, a required
    40-character git commit SHA (see this repository's `REASONS` entry for
    that path for the full statement). It replaces an earlier, INCORRECT
    account in this docstring naming a "schema-9 governance fingerprint
    shared by the two Paystack relay retry tests" as the 29th slot — no such
    entry exists in the current baseline, and a docstring whose prose no
    longer accounts for the file's actual contents is exactly the kind of
    unexamined narrative this guard exists to prevent elsewhere.

    Every entry above the reviewed 29 would be unexplained, and the reason
    check above only fires per FILE — a new finding in an already-listed
    file would otherwise slip in silently, which is exactly the shape this
    one had. The bound below is `==`, not `<=`: per ADR-0018 a ratchet is
    two-directional, and an UNEXPLAINED drop below 29 (a finding quietly
    disappearing without its `REASONS` entry being removed by
    `test_no_reason_outlives_its_finding`, or a finding merging into another
    file's count) is exactly as worth surfacing as a rise -- either way,
    someone must look at this docstring and correct it in the same change.
    """
    total = sum(len(v) for v in _baseline()["results"].values())
    assert total == 29, (
        f"{total} suppressed findings, expected exactly 29. A rise means fix "
        "the finding or explain and pin the new total here; a drop means "
        "correct this docstring's accounting and lower the pin in the same "
        "change -- never leave a stale number unexamined either direction."
    )


def test_every_reason_is_a_real_one() -> None:
    thin = sorted(k for k, v in REASONS.items() if len(v.strip()) < 40)
    assert thin == [], f"entries with no substantive reason: {thin}"
