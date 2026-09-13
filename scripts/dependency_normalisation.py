"""The one owner of PEP 503 package-name normalisation in this repository's
private-index tooling.

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

This is now the ONLY place this logic lives. Both scripts import it; neither
defines its own. It is deliberately tiny and dependency-free — nothing here
should ever need to change independently of the PEP 503 spec itself.
"""

from __future__ import annotations

import re

_NAME_RUNS = re.compile(r"[-_.]+")


def normalise_name(name: str) -> str:
    """PEP 503 normalisation, with a leading or trailing separator stripped.

    Runs of `-`, `_`, `.` collapse to one `-`, the result is lower-cased, and
    a leading or trailing `-` is stripped. Stripping is not optional: without
    it, whether `-dotmac-thing` and `dotmac-thing` (or `dotmac-thing-` and
    `dotmac-thing`) compare equal would depend on which of the two formerly-
    duplicated copies did the comparing.
    """

    return _NAME_RUNS.sub("-", name).strip("-").lower()
