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


def normalise_name(name: str) -> str:
    """PEP 503 normalisation: runs of `-`, `_`, `.` collapse to one `-`,
    lower-cased. Raises `ValueError` if `name` contains any character
    outside `[A-Za-z0-9._-]` (see this module's docstring, "`normalise_name`
    validates the INPUT charset"), or if the normalised result starts or
    ends with `-` (see "`normalise_name` does not strip — an edge separator
    is REFUSED").
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
    """

    text = url.strip()
    parts = urllib.parse.urlsplit(text)
    if parts.scheme.lower() != "https" or not parts.netloc:
        return text
    path = parts.path.rstrip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    return urllib.parse.urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, parts.query, parts.fragment)
    )
