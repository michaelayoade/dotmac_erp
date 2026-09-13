"""The one owner of package-identity normalisation in this repository's
private-index tooling: PEP 503 package NAMES, and off-index repository
URLS.

`scripts/dependency_bundle.py` and `scripts/erp_lock.py` both need to answer
"is this the same package?" for a name that may be spelled with `-`, `_`, or
`.` and mixed case — PEP 503 normalisation exists for exactly that reason.
Before this module existed, each script carried its OWN copy, and they had
already DIVERGED: `dependency_bundle`'s stripped a leading or trailing
separator after collapsing runs; `erp_lock`'s did not. A private package
name that starts or ends with a separator character would therefore
normalise to two different identities depending on which script inspected
it — exactly the kind of silent disagreement a plan digest and a manifest
guard must never have between them, since it can hide an identity mismatch
that looks resolved on one side and unresolved on the other.

The same class of bug existed one level up, for REPOSITORY identity: both
scripts independently decide whether a manifest's off-index `git` URL is
"the same repository" as a policy-pinned one. `erp_lock.off_index_pin_problems`
normalised both sides through a narrow, spelling-aware comparison;
`dependency_bundle` compared the two URL strings RAW, with no normalisation
at all. One manifest spelling (a trailing slash, a missing `.git`, a
different case on the scheme/host) could therefore pass one gate and fail
the other. This was harder to catch than the name-normaliser divergence
because it is a SEMANTIC divergence — one side accumulates a problem list,
the other raises — not a textual one, so a body-similarity detector scores
the two functions well below its threshold. It is exactly the kind of thing
that needs one shared owner rather than a list entry.

This is now the ONLY place this logic lives. Both scripts import it; neither
defines its own. It is deliberately tiny and dependency-free — nothing here
should ever need to change independently of the PEP 503 spec (for names) or
the URL spellings this repository actually has to recognise (for
repository URLs).

## `normalise_name` does not strip — an edge separator is REFUSED

An earlier version of this function stripped a leading/trailing `-` after
collapsing separator runs, to fix the divergence above (one copy stripped,
the other did not). That was itself wrong: PEP 503 normalisation does not
strip edge separators, and no VALID Python distribution name can start or
end with one (the name grammar requires the first and last character to be
alphanumeric). Stripping therefore manufactured a false equivalence between
an INVALID name (`-dotmac-kernel`) and a valid one (`dotmac-kernel`) —
worse than either of the two original diverging behaviours, because both
callers agreed on an incorrect identity instead of disagreeing on the
correct one. `normalise_name` now raises `ValueError` for a name whose
normalised form starts or ends with `-`, and neither caller may treat that
as "these are the same package" — each maps it to its own refusal type
instead (see `dependency_bundle._normalise_name_for_manifest`; `erp_lock`'s
real call sites only ever see names already shaped like valid identifiers,
so the raise is not expected to fire there, and this module stays
dependency-free by raising a plain `ValueError` rather than importing
either caller's exception type).

## `normalise_name` validates the INPUT charset, not just the output shape

Only refusing a leading/trailing separator in the OUTPUT was not enough: a
distribution name may contain only ASCII letters, digits, `.`, `_`, and
`-`. Without checking the INPUT for that charset, a caller-controlled
string containing a path separator (`/tmp/bundle-escape`) or any other
character outside that set passed straight through unchanged (a forward or
backward slash is not touched by the `[-_.]+` collapse), came back equal
to itself,
and was accepted anywhere a caller compared its input to its own
normalised output as proof of "this key is already a valid PEP 503 name"
(`dependency_bundle.build_local_index` did exactly that). A package-name
key that is actually a filesystem path escapes a staging directory the
moment it is joined with `Path.__truediv__`, because an absolute
right-hand operand REPLACES the left side entirely. `normalise_name` now
refuses any input containing a character outside `[A-Za-z0-9._-]` before
doing anything else with it.

## Two forms, for two genuinely different contracts — not one function with a caveat

Adding that input-charset check to the ONE shared `normalise_name`
introduced a real regression: `erp_lock.off_index_lock_problems` calls the
shared normaliser on a package NAME taken from a candidate-supplied
`poetry.lock`, inside the credentialed `erp-lock.yml` resolve workflow, to
answer "is this the same package as the one I have pinned?". Before this
module existed, that comparison could never raise — the old, local
`_normalised` was pure regex substitution with no validation. Routing it
through the new, validating `normalise_name` meant an unusual candidate
lock-package name could raise an uncaught `ValueError` and crash a live
credentialed job that used to merely compare and move on. That is a
regression THIS branch introduced, not a pre-existing gap, and it must not
ship.

The fix is not a caller-side wrapper around one raising function — it is
recognising that the two call sites were never answering the same
question:

* **Identity comparison** ("is this candidate-supplied name the SAME
  package as this other name?") must be TOTAL: it must never raise,
  because its input is untrusted and its job is to report a mismatch, not
  to become one. A name it cannot cleanly interpret is a name that does
  not match — and that answer falls out for free from NOT stripping or
  validating, because an edge-separator or invalid-character name then
  normalises to something that still carries the offending character and
  therefore does not spuriously compare equal to a clean name either. This
  is `normalise_name_for_identity`, below — behaviourally IDENTICAL to
  `erp_lock`'s own pre-existing, pre-this-branch `_normalised`
  (`re.sub(r"[-_.]+", "-", name).lower()`, no validation at all). `erp_lock`
  consumes ONLY this form, so every one of its own call sites behaves
  exactly as it did before this branch existed.
* **Filesystem-key validation** ("is this string safe to use as a
  PEP-503-normalised directory/file-name component?") must REFUSE anything
  that is not a valid, already-normalised distribution name — because that
  is what stops a string like `/tmp/bundle-escape` from being accepted as
  "already normalised" and then escaping a staging directory when joined
  with `Path.__truediv__` (an absolute right-hand operand replaces the
  left side entirely). This remains `normalise_name`, below — the
  validating form, consumed by `dependency_bundle.py`'s manifest/lock
  parsing (which must refuse a malformed name in ADVERSARIAL, but not
  identity-comparison, input) and by its local-index staging path (which
  must refuse a malformed name used as a FILESYSTEM KEY).
"""

from __future__ import annotations

import re
import urllib.parse

_NAME_RUNS = re.compile(r"[-_.]+")

#: The only characters a distribution name may ever contain (PEP 503 /
#: packaging's name grammar). Checked on the INPUT, before normalisation —
#: this is what makes a path separator, a null byte, or any other
#: unexpected character a refusal rather than a value that normalises to
#: itself and is silently trusted as "already valid".
_VALID_NAME_CHARACTERS = re.compile(r"\A[A-Za-z0-9._-]+\Z")


def normalise_name_for_identity(name: str) -> str:
    """PEP 503 separator-collapse and lower-casing — and NOTHING else.
    NEVER RAISES, for any input.

    Use this ONLY to answer "is this the same package as this other name?"
    against input that may be untrusted or malformed (e.g. a
    candidate-supplied `poetry.lock` package name) — see this module's
    docstring, "Two forms, for two genuinely different contracts", for why
    a total, never-raising comparison is the correct contract there, and
    why NOT stripping an edge separator is what keeps a mis-shaped name
    from spuriously comparing equal to a clean one even without raising.

    Do NOT use this to derive a filesystem path segment, a cache key, or
    any other context where an actually-invalid distribution name must be
    refused outright rather than merely fail to match — use `normalise_name`
    for that.
    """

    return _NAME_RUNS.sub("-", name).lower()


def normalise_name(name: str) -> str:
    """PEP 503 normalisation: runs of `-`, `_`, `.` collapse to one `-`,
    lower-cased. Raises `ValueError` if `name` contains any character
    outside `[A-Za-z0-9._-]` (see this module's docstring, "`normalise_name`
    validates the INPUT charset"), or if the normalised result starts or
    ends with `-` (see "`normalise_name` does not strip — an edge separator
    is REFUSED").

    Use this where an invalid distribution name must be REFUSED outright —
    manifest/lock parsing, or deriving a filesystem-key component. Use
    `normalise_name_for_identity` instead where the input may be untrusted
    and the caller's job is to report a mismatch, never to raise — see this
    module's docstring, "Two forms, for two genuinely different contracts".
    """

    if not name or not _VALID_NAME_CHARACTERS.match(name):
        raise ValueError(
            f"{name!r} is not a valid distribution name; only ASCII "
            "letters, digits, '.', '_', and '-' are permitted"
        )
    normalised = _NAME_RUNS.sub("-", name).lower()
    if normalised.startswith("-") or normalised.endswith("-"):
        raise ValueError(
            f"{name!r} normalises to {normalised!r}, which starts or ends "
            "with a separator; PEP 503 normalisation does not strip this, "
            "and no valid distribution name can start or end with one"
        )
    return normalised


def normalise_repository_url(url: str) -> str:
    """Enough normalisation to compare two spellings of one repository.

    Deliberately narrow: case-folded scheme and host, a stripped trailing
    slash and a single optional `.git` suffix. It does NOT try to equate ssh
    and https forms or resolve redirects -- a spelling this does not
    recognise is refused rather than guessed at, because a guess here
    decides where the resolver reaches. This is erp_lock's original,
    documented behaviour; it wins over any looser or stricter alternative,
    because "refuse an unrecognised spelling" is the property that was
    reasoned about.

    A query string or fragment is PRESERVED in the normalised output, never
    discarded: `repo.git` and `repo.git?x=1` must not compare equal, because
    the query string is part of what actually reaches the resolver even
    though it plays no role in this function's own equality test.

    NEVER RAISES: a malformed authority (e.g. unbalanced IPv6 brackets)
    makes `urllib.parse.urlsplit` itself raise `ValueError` — that is
    treated exactly like any other spelling this function does not
    recognise, and the input is returned unchanged rather than the
    exception propagating to a caller that has no reason to expect this
    pure comparison helper can raise.
    """

    text = url.strip()
    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError:
        return text
    if parts.scheme.lower() != "https" or not parts.netloc:
        return text
    path = parts.path.rstrip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    return urllib.parse.urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, parts.query, parts.fragment)
    )
