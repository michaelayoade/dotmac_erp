"""The one owner of bank-fee journal creation and posting.

Three reconciliation adapters used to do this themselves — `auto_reconciliation_parts.special`,
`programmatic_parts.special_strategies` and `reconciliation_engine_parts.handlers`.
They all delegate here now, because the defect they shared was not in any one of
them: it was in the order of operations they all repeated.

## The defect this exists to prevent

Every adapter knew the exact source row — it built
``correlation_id = f"bank-fee-{line.line_id}"`` — and then:

1. left ``source_document_id`` unset, spending that identity on a display
   string instead of the typed field that would have made it a key;
2. called ``create_and_approve_journal`` **unconditionally**, so a journal was
   committed before anything checked whether the effect already existed;
3. posted with a line-keyed idempotency key, which the ledger correctly
   answered with ``PostingResult(success=True, idempotent_replay=True)``;
4. **discarded ``idempotent_replay``**, seeing only ``success=True``;
5. left the journal from step 2 sitting in APPROVED forever.

Create-then-check. Each re-run of a reconciliation pass over the same statement
line minted another orphan. In production that produced **12,117 APPROVED
journals for 149 statement lines** — one line carried 85.

`gl.posting_batch.idempotency_key` is globally unique, so *posting* was already
at-most-once. Creation was not, and creation is what ran first.

## What this module does instead

**Check, then create.** Two independent pre-checks, either of which stops the
write:

* a journal already carries this line as typed ``source_document_id``; or
* a posting batch already exists under this line's idempotency key.

The second matters more than it looks. Legacy bank-fee journals carry a NULL
``source_document_id`` — their identity is only in the correlation string — so
without the batch check the first run of this code would look at 149 already-
posted lines, see no typed identity, and create 149 fresh journals.

**Typed identity.** ``source_document_id = line.line_id``. A formatted string is
for humans; it is not a key. ``correlation_id`` is still written, unchanged, so
existing traces keep working.

**A database boundary, not just an application one.** Two concurrent callers can
both pass the pre-checks. What stops the second is the partial unique index
``uq_journal_entry_bank_fee_source`` on
``(organization_id, source_document_id) WHERE source_document_type = 'BANK_FEE'``.
The loser catches `IntegrityError`, re-reads, and reports the winner's journal.
An application-level check alone loses that race, and losing it is how duplicates
are made.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.finance.gl.journal_entry import JournalEntry, JournalType
from app.models.finance.gl.posting_batch import PostingBatch
from app.services.finance.gl.journal import JournalInput, JournalLineInput

logger = logging.getLogger(__name__)

SOURCE_MODULE = "BANKING"
SOURCE_DOCUMENT_TYPE = "BANK_FEE"
IDEMPOTENCY_ACTION = "bank-fee"


class _Poster(Protocol):
    """The caller's period-fallback posting function.

    Each adapter owns its own fiscal-period fallback behaviour, so posting stays
    the caller's to supply. What is NOT the caller's any more is deciding
    whether to create a journal at all.
    """

    def __call__(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class BankFeeOutcome:
    """What happened, stated so a caller cannot mistake one case for another."""

    journal: JournalEntry | None
    created: bool
    #: An effect for this statement line already existed; nothing was written.
    already_present: bool
    #: The create lost a race to a concurrent caller; that caller's journal wins.
    lost_a_race: bool
    message: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def find_existing_fee_journal(
    db: Session, *, organization_id: uuid.UUID, line_id: uuid.UUID
) -> JournalEntry | None:
    """A journal already carrying this statement line as typed identity."""
    return db.scalars(
        select(JournalEntry).where(
            JournalEntry.organization_id == organization_id,
            JournalEntry.source_document_type == SOURCE_DOCUMENT_TYPE,
            JournalEntry.source_document_id == line_id,
        )
    ).first()


def fee_idempotency_key(organization_id: uuid.UUID, line_id: uuid.UUID) -> str:
    # Imported at call time, not at module import. `BasePostingAdapter` is
    # patched at its source module by the banking tests, and a module-level
    # `from ... import` would bind the real class before that patch applies —
    # the adapters this module replaced imported it inside their functions for
    # the same reason.
    from app.services.finance.posting.base import BasePostingAdapter

    return BasePostingAdapter.make_idempotency_key(
        organization_id, SOURCE_MODULE, line_id, action=IDEMPOTENCY_ACTION
    )


def existing_posting_batch(
    db: Session, *, organization_id: uuid.UUID, line_id: uuid.UUID
) -> PostingBatch | None:
    """A ledger batch already posted for this line.

    Covers legacy journals whose `source_document_id` is NULL: their identity
    survives only in the idempotency key, and this is what reads it.
    """
    return db.scalars(
        select(PostingBatch).where(
            PostingBatch.idempotency_key
            == fee_idempotency_key(organization_id, line_id)
        )
    ).first()


def post_bank_fee(
    db: Session,
    *,
    organization_id: uuid.UUID,
    line: Any,
    bank_gl_account_id: uuid.UUID,
    finance_cost_account_id: uuid.UUID,
    posted_by_user_id: uuid.UUID,
    poster: _Poster | None = None,
    description: str | None = None,
) -> BankFeeOutcome:
    """Create and post the fee journal for one statement line, at most once.

    `poster` is the caller's fiscal-period fallback function. Adapters that post
    through `create_approve_and_post_journal` omit it and get that path instead;
    either way the CREATE is guarded, which is the part that was not.

    Returns rather than raises for the ordinary "already there" case, because it
    is not an error — it is the boundary doing its job.
    """
    line_id = line.line_id
    correlation_id = f"bank-fee-{line_id}"

    # ---- Pre-check 1: typed identity already used -------------------------
    existing = find_existing_fee_journal(
        db, organization_id=organization_id, line_id=line_id
    )
    if existing is not None:
        return BankFeeOutcome(
            journal=existing,
            created=False,
            already_present=True,
            lost_a_race=False,
            message=f"Bank fee already recorded for line {line_id} ({existing.journal_number})",
        )

    # ---- Pre-check 2: the ledger already posted this line ------------------
    batch = existing_posting_batch(db, organization_id=organization_id, line_id=line_id)
    if batch is not None:
        return BankFeeOutcome(
            journal=None,
            created=False,
            already_present=True,
            lost_a_race=False,
            message=(
                f"Bank fee already posted for line {line_id} under batch "
                f"{batch.batch_id}; no journal created"
            ),
        )

    amount = abs(Decimal(line.amount))
    label = description or f"Bank charge - {line.description}"
    journal_input = JournalInput(
        journal_type=JournalType.STANDARD,
        entry_date=line.transaction_date,
        posting_date=line.transaction_date,
        description=label,
        reference=line.reference,
        source_module=SOURCE_MODULE,
        source_document_type=SOURCE_DOCUMENT_TYPE,
        # The whole point: the line id in the typed field, not only in a string.
        source_document_id=line_id,
        correlation_id=correlation_id,
        lines=[
            JournalLineInput(
                account_id=finance_cost_account_id,
                debit_amount=amount,
                description=label,
            ),
            JournalLineInput(
                account_id=bank_gl_account_id,
                credit_amount=amount,
                description=label,
            ),
        ],
    )

    from app.services.finance.posting.base import BasePostingAdapter

    idempotency_key = fee_idempotency_key(organization_id, line_id)
    combined_posting = None
    try:
        with db.begin_nested():
            if poster is None:
                # Adapters that post through the combined helper keep doing so;
                # what changes for them is only that the create is now guarded.
                journal, combined_posting = (
                    BasePostingAdapter.create_approve_and_post_journal(
                        db,
                        organization_id,
                        journal_input,
                        posted_by_user_id,
                        posting_date=line.transaction_date,
                        idempotency_key=idempotency_key,
                        source_module=SOURCE_MODULE,
                        correlation_id=correlation_id,
                        success_message="Bank fee posted",
                        creation_error_prefix="Fee journal creation failed",
                        ledger_error_prefix="Fee journal posting failed",
                    )
                )
                create_error = None
            else:
                journal, create_error = BasePostingAdapter.create_and_approve_journal(
                    db,
                    organization_id,
                    journal_input,
                    posted_by_user_id,
                    error_prefix="Fee journal creation failed",
                )
    except IntegrityError:
        # The partial unique index refused a second journal for this line. A
        # concurrent caller won; report its journal rather than ours.
        winner = find_existing_fee_journal(
            db, organization_id=organization_id, line_id=line_id
        )
        logger.info(
            "Bank fee for line %s was created concurrently; deferring to %s",
            line_id,
            getattr(winner, "journal_number", "<unknown>"),
        )
        return BankFeeOutcome(
            journal=winner,
            created=False,
            already_present=True,
            lost_a_race=True,
            message=f"Bank fee created concurrently for line {line_id}",
        )

    if create_error:
        return BankFeeOutcome(
            journal=None,
            created=False,
            already_present=False,
            lost_a_race=False,
            message=create_error.message,
            error=create_error.message,
        )

    if combined_posting is not None:
        posting_result = combined_posting
    else:
        posting_result = poster(  # type: ignore[misc]
            db=db,
            organization_id=organization_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=line.transaction_date,
            idempotency_key=idempotency_key,
            source_module=SOURCE_MODULE,
            correlation_id=correlation_id,
            posted_by_user_id=posted_by_user_id,
            success_message="Bank fee posted",
            error_prefix="Fee journal posting failed",
        )

    if not posting_result.success:
        return BankFeeOutcome(
            journal=journal,
            created=True,
            already_present=False,
            lost_a_race=False,
            message=posting_result.message,
            error=posting_result.message,
        )

    # The pre-checks make this unreachable; it is surfaced rather than dropped
    # because discarding exactly this signal is what produced 12,117 orphans.
    if getattr(posting_result, "idempotent_replay", False):
        logger.warning(
            "Bank fee for line %s posted as an idempotent replay even though no "
            "prior journal or batch was found. Journal %s is APPROVED and "
            "unposted; investigate before re-running.",
            line_id,
            journal.journal_number,
        )
        return BankFeeOutcome(
            journal=journal,
            created=True,
            already_present=True,
            lost_a_race=False,
            message=(
                f"Bank fee for line {line_id} replayed against an existing batch; "
                f"journal {journal.journal_number} left APPROVED"
            ),
            error="idempotent replay after a clean pre-check",
        )

    return BankFeeOutcome(
        journal=journal,
        created=True,
        already_present=False,
        lost_a_race=False,
        message="Bank fee posted",
    )


__all__ = [
    "BankFeeOutcome",
    "SOURCE_DOCUMENT_TYPE",
    "SOURCE_MODULE",
    "existing_posting_batch",
    "fee_idempotency_key",
    "find_existing_fee_journal",
    "post_bank_fee",
]
