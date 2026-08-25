"""The one place that decides whether a bulk backlog post may run.

Three live paths can post the APPROVED journal backlog in bulk —
`app.tasks.gl_posting.post_approved_journal_backlog`,
`app.tasks.gl_posting.post_stranded_source_journals`, and
`scripts/post_stranded_bank_fees.py`. They had one implementation of the rule
between them and a copy in the script, which is how the two drift apart.

## Why a gate exists at all

`dry_run=True` is a default, and a default is not a safeguard: `dry_run=False`
is one keyword argument away. Two measured facts make that dangerous right now:

* **`post_approved_journals` does not filter by source.** Its query selects every
  APPROVED journal for the organization, so the blast radius is the whole
  backlog — 14,263 journals, ₦76,495,739.50 — of which the Gate G detector finds
  **zero** that should be posted.
* **`post_stranded_source_journals` has already caused duplicate postings.** It
  keys idempotency on `backfill-stranded-bank-fees-<journal_number>`, a
  per-journal namespace that bypasses the ledger's per-statement-line boundary.
  429 duplicate bank-fee postings (₦7,764.68) came from exactly that path.

## What this flag is, and is not

`ALLOW_BULK_JOURNAL_BACKLOG_POSTING` is a **kill switch**. It is emphatically
NOT Finance authorization, and nothing here should be read as conferring any:

* it says an operator deliberately re-enabled a disabled mechanism;
* it says nothing about whether any particular journal SHOULD post;
* per-journal authorization is a Finance disposition recorded outside this
  codebase entirely (ERP PR #335, appendices A and B).

Setting it does not make a bulk post correct. It makes it possible.

Retire the gate when every journal in the backlog has an approved disposition —
not when the backlog is smaller, and not when a run is convenient.
"""

from __future__ import annotations

import os

#: Environment flag re-enabling live bulk backlog posting. A kill switch.
BULK_POSTING_ENV_FLAG = "ALLOW_BULK_JOURNAL_BACKLOG_POSTING"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


class BulkPostingDisabled(RuntimeError):
    """A live bulk backlog post was attempted while the kill switch is off."""


def bulk_posting_enabled(env: dict[str, str] | None = None) -> bool:
    """Whether live bulk posting has been deliberately re-enabled."""
    source = os.environ if env is None else env
    return source.get(BULK_POSTING_ENV_FLAG, "").strip().lower() in _TRUTHY


def require_bulk_posting_allowed(
    caller: str, dry_run: bool, *, env: dict[str, str] | None = None
) -> None:
    """Refuse a live bulk post unless the kill switch is off.

    Raises rather than silently downgrading to a dry run. An operator who asked
    for a live run and got a quiet no-op would believe the work was done — the
    same class of mistake as a caller that cannot tell "I posted" from "someone
    already had".
    """
    if dry_run:
        return
    if bulk_posting_enabled(env):
        return
    raise BulkPostingDisabled(
        f"{caller} refused: live bulk backlog posting is disabled. Set "
        f"{BULK_POSTING_ENV_FLAG}=true to re-enable the mechanism. That flag is "
        f"a kill switch, NOT Finance authorization — per-journal disposition is "
        f"recorded outside this codebase (ERP PR #335). The backlog currently "
        f"contains no journal that should be posted, and one of these paths has "
        f"already produced duplicate postings."
    )


__all__ = [
    "BULK_POSTING_ENV_FLAG",
    "BulkPostingDisabled",
    "bulk_posting_enabled",
    "require_bulk_posting_allowed",
]
