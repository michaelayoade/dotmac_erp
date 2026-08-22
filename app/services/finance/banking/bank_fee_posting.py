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
from enum import Enum
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.finance.gl.journal_entry import (
    JournalEntry,
    JournalStatus,
    JournalType,
)
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.models.finance.gl.posted_ledger_line import PostedLedgerLine
from app.models.finance.gl.posting_batch import BatchStatus, PostingBatch
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


class BankFeeState(str, Enum):
    """Every distinct thing that can be true of a statement line's fee.

    A single `already_present` boolean was not enough, and the gap was not
    cosmetic: a failed post leaves the journal in DRAFT (see
    `BasePostingAdapter._revert_unposted_journal`) while it still carries the
    typed source id, so "a row exists" would make every later invocation report
    success and skip the line **forever**. Status has to be part of the answer.
    """

    #: Created and posted by this call.
    CREATED = "CREATED"
    #: A POSTED, non-reversed journal already carries this line. Nothing to
    #: create — but the caller should still repair a missing statement match.
    ALREADY_POSTED = "ALREADY_POSTED"
    #: A previous post failed and left the journal in DRAFT. Re-posted here
    #: rather than creating a second journal.
    REPOSTED_DRAFT = "REPOSTED_DRAFT"
    #: APPROVED with no ledger batch — the stranded-orphan shape. NOT posted
    #: automatically: that is a Finance disposition, not an adapter's call.
    APPROVED_ORPHAN = "APPROVED_ORPHAN"
    #: The journal was voided or reversed. The effect is not live, and the
    #: unique index still holds the identity, so this needs a human.
    NOT_LIVE = "NOT_LIVE"
    #: A ledger batch exists under this line's key but no journal carries the
    #: typed id — legacy rows written before the identity was typed.
    LEGACY_BATCH_ONLY = "LEGACY_BATCH_ONLY"
    #: A concurrent caller won the create; that caller's journal is canonical.
    LOST_RACE = "LOST_RACE"
    #: The ledger replayed against an existing batch, so nothing was posted and
    #: the journal this call was working on is still unposted.
    REPLAY_LEFT_UNPOSTED = "REPLAY_LEFT_UNPOSTED"
    #: A DRAFT journal exists but its content no longer matches the statement
    #: line. NOT re-posted — posting stale content under a live identity is
    #: worse than posting nothing.
    CONTENT_MISMATCH = "CONTENT_MISMATCH"
    #: More than one primary journal carries this line. Only possible before
    #: the unique index is deployed, and it must never be resolved by guessing.
    AMBIGUOUS = "AMBIGUOUS"
    #: Creation or posting failed.
    FAILED = "FAILED"


@dataclass(frozen=True)
class BankFeeOutcome:
    """What happened, stated so a caller cannot mistake one case for another."""

    state: BankFeeState
    #: The CANONICAL journal for this line where one exists — returned even when
    #: nothing was created, because the caller may still need to repair a
    #: statement-line match that a crash left behind.
    journal: JournalEntry | None
    message: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def created(self) -> bool:
        return self.state is BankFeeState.CREATED

    @property
    def already_present(self) -> bool:
        """An effect for this line is already in the ledger.

        Deliberately NOT true for APPROVED_ORPHAN or NOT_LIVE: in those cases
        something exists but the ledger effect does not, and treating them as
        "present" is what makes a defect invisible.
        """
        return self.state in {
            BankFeeState.ALREADY_POSTED,
            BankFeeState.LEGACY_BATCH_ONLY,
            BankFeeState.LOST_RACE,
        }

    @property
    def needs_attention(self) -> bool:
        return self.state in {
            BankFeeState.APPROVED_ORPHAN,
            BankFeeState.REPLAY_LEFT_UNPOSTED,
            BankFeeState.NOT_LIVE,
            BankFeeState.CONTENT_MISMATCH,
            BankFeeState.AMBIGUOUS,
        }

    @property
    def posted_journal(self) -> JournalEntry | None:
        """The journal whose effect is live, for statement-match repair."""
        if self.state in {
            BankFeeState.CREATED,
            BankFeeState.ALREADY_POSTED,
            BankFeeState.REPOSTED_DRAFT,
            BankFeeState.LOST_RACE,
        }:
            return self.journal
        return None


IDENTITY_INDEX_NAME = "uq_journal_entry_bank_fee_source"


def _is_fee_identity_violation(exc: IntegrityError) -> bool:
    """Whether THIS integrity error is the source-identity index refusing a duplicate.

    Checked by constraint name, with the message as a fallback for drivers that
    do not populate `diag`. Anything else — a foreign key, a check, a different
    unique index — must propagate: a caller that reports every `IntegrityError`
    as "someone else won" converts real write failures into success.
    """
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    constraint = getattr(diag, "constraint_name", None)
    if constraint:
        return bool(constraint == IDENTITY_INDEX_NAME)
    return IDENTITY_INDEX_NAME in str(exc)


def find_fee_journals(
    db: Session, *, organization_id: uuid.UUID, line_id: uuid.UUID
) -> list[JournalEntry]:
    """Every PRIMARY journal carrying this statement line as typed identity.

    **Reversals are excluded, and that exclusion is required for correctness,
    not tidiness.** The unique index deliberately exempts `is_reversal = true`,
    because an ERP reversal PRESERVES its original's source identity — so once a
    bank fee has been reversed, two or more journals carry this `line_id`. A
    lookup that did not filter, and took `.first()` off an unordered query,
    could return the REVERSAL and then read its status as though it were the
    original's. The reversal is POSTED, so the answer would be "already posted"
    at exactly the moment no live effect exists.

    Returns a list rather than one row so the caller can SEE multiplicity. The
    index permits at most one primary per line, but only once it is deployed;
    before that a race could leave two, and silently picking one would hide it.
    Ordered by creation so any report is stable.
    """
    return list(
        db.scalars(
            select(JournalEntry)
            .where(
                JournalEntry.organization_id == organization_id,
                JournalEntry.source_document_type == SOURCE_DOCUMENT_TYPE,
                JournalEntry.source_document_id == line_id,
                JournalEntry.is_reversal.is_(False),
            )
            .order_by(JournalEntry.created_at, JournalEntry.journal_number)
        ).all()
    )


def find_existing_fee_journal(
    db: Session, *, organization_id: uuid.UUID, line_id: uuid.UUID
) -> JournalEntry | None:
    """The single primary journal for this line, or None."""
    journals = find_fee_journals(db, organization_id=organization_id, line_id=line_id)
    return journals[0] if journals else None


def is_effect_live(db: Session, journal: JournalEntry) -> bool:
    """Whether this journal's effect is in the ledger RIGHT NOW.

    Three conditions, and none of them is implied by the others:

    * it is POSTED — not DRAFT, APPROVED, VOID or REVERSED;
    * it has not since been reversed (`reversal_journal_id`), because a reversed
      journal keeps its POSTED status while its effect is cancelled;
    * it actually has rows in `gl.posted_ledger_line`.
    """
    if journal.status != JournalStatus.POSTED:
        return False
    if journal.reversal_journal_id is not None:
        return False
    return bool(
        db.scalar(
            select(func.count(PostedLedgerLine.ledger_line_id)).where(
                PostedLedgerLine.journal_entry_id == journal.journal_entry_id
            )
        )
    )


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
    """A batch for this line whose effect is STILL LIVE, or None.

    Covers legacy journals whose `source_document_id` is NULL: their identity
    survives only in the idempotency key, and this is what reads it.

    Two filters, and the second is the one that is easy to miss — see below.
    """
    batch = db.scalars(
        select(PostingBatch).where(
            PostingBatch.idempotency_key
            == fee_idempotency_key(organization_id, line_id),
            # A batch ROW is not a posted effect. PENDING, PROCESSING, FAILED
            # and PARTIALLY_POSTED batches all exist without one, and treating
            # any of them as "already posted" would strand the fee permanently:
            # nothing would ever create the journal, and nothing would say why.
            PostingBatch.status == BatchStatus.POSTED,
        )
    ).first()
    if batch is None:
        return None

    # NOR is a POSTED batch a live effect. **Reversing a journal leaves its
    # original batch POSTED** — the batch records that a posting happened, not
    # that its effect still stands. So follow the batch to the ledger rows it
    # wrote, and those rows to their journals, and require at least one journal
    # whose effect is live.
    journal_ids = set(
        db.scalars(
            select(PostedLedgerLine.journal_entry_id).where(
                PostedLedgerLine.posting_batch_id == batch.batch_id
            )
        ).all()
    )
    if not journal_ids:
        return None
    for journal_id in journal_ids:
        journal = db.get(JournalEntry, journal_id)
        if journal is not None and is_effect_live(db, journal):
            return batch
    return None


def _existing_journal_outcome(
    db: Session,
    existing: JournalEntry,
    *,
    organization_id: uuid.UUID,
    line: Any,
    line_id: uuid.UUID,
    bank_gl_account_id: uuid.UUID,
    finance_cost_account_id: uuid.UUID,
    posted_by_user_id: uuid.UUID,
    poster: _Poster | None,
) -> BankFeeOutcome:
    """Decide what an EXISTING journal for this line means.

    "A row exists" is not an answer. The five statuses mean five different
    things, and collapsing them is what turns a transient posting failure into a
    line that is skipped forever:

    * **POSTED** — the effect is live. Nothing to create. The journal is still
      returned, because a crash between posting and matching leaves the
      statement line unmatched and the caller has to be able to repair that.
    * **DRAFT** — a previous post failed and `_revert_unposted_journal` put it
      back. RE-POST this journal. Creating a second one is forbidden by the
      index, and skipping it strands the fee permanently.
    * **SUBMITTED / APPROVED** — created but never posted: the stranded-orphan
      shape that produced 12,117 rows. NOT posted automatically — whether a
      backlog journal should post is a Finance disposition, not an adapter's.
      Reported so it is visible rather than silently counted as done.
    * **VOID / REVERSED** — the effect is not live, and the unique index still
      holds this identity, so no replacement can be created here. A human has to
      decide; saying so is the only honest outcome.
    """
    status = existing.status
    number = existing.journal_number

    if status == JournalStatus.POSTED:
        if not is_effect_live(db, existing):
            # POSTED is not the same as effective. A reversed journal keeps its
            # POSTED status while its effect is cancelled, and a journal with no
            # `posted_ledger_line` rows never had one.
            return BankFeeOutcome(
                state=BankFeeState.NOT_LIVE,
                journal=existing,
                message=(
                    f"Bank-fee journal {number} for line {line_id} is POSTED but "
                    f"its effect is not live (reversed, or no ledger rows). A "
                    f"replacement cannot be created automatically while it holds "
                    f"the identity."
                ),
            )
        return BankFeeOutcome(
            state=BankFeeState.ALREADY_POSTED,
            journal=existing,
            message=f"Bank fee already posted for line {line_id} ({number})",
        )

    if status == JournalStatus.DRAFT:
        mismatch = _content_mismatch(
            db,
            existing,
            line=line,
            debit_account_id=finance_cost_account_id,
            credit_account_id=bank_gl_account_id,
        )
        if mismatch is not None:
            # Re-posting is a recovery, not a rewrite. If the DRAFT no longer
            # says what the statement line says, posting it would put stale
            # content into the ledger under the line's identity — and the
            # identity is what makes it un-correctable later.
            return BankFeeOutcome(
                state=BankFeeState.CONTENT_MISMATCH,
                journal=existing,
                message=(
                    f"DRAFT bank-fee journal {number} no longer matches line "
                    f"{line_id}: {mismatch}. Not re-posted."
                ),
                error=mismatch,
            )
        posting_result = _post_existing(
            db,
            existing,
            organization_id=organization_id,
            line=line,
            line_id=line_id,
            posted_by_user_id=posted_by_user_id,
            poster=poster,
        )
        if not posting_result.success:
            return BankFeeOutcome(
                state=BankFeeState.FAILED,
                journal=existing,
                message=(
                    f"Re-post of DRAFT bank-fee journal {number} failed: "
                    f"{posting_result.message}"
                ),
                error=posting_result.message,
            )
        if getattr(posting_result, "idempotent_replay", False):
            # `success=True` with a replay means the ledger found an existing
            # batch and posted NOTHING. This journal is still unposted, and
            # calling that a successful re-post is the same false success the
            # whole module exists to remove — here it would also strand the
            # DRAFT forever, because the next run would find it and replay again.
            return BankFeeOutcome(
                state=BankFeeState.REPLAY_LEFT_UNPOSTED,
                journal=existing,
                message=(
                    f"Re-post of bank-fee journal {number} replayed against an "
                    f"existing batch; nothing was posted and the journal is "
                    f"still {existing.status.value}."
                ),
                error="idempotent replay left the journal unposted",
            )
        return BankFeeOutcome(
            state=BankFeeState.REPOSTED_DRAFT,
            journal=existing,
            message=f"Re-posted previously failed bank-fee journal {number}",
        )

    if status in {JournalStatus.SUBMITTED, JournalStatus.APPROVED}:
        return BankFeeOutcome(
            state=BankFeeState.APPROVED_ORPHAN,
            journal=existing,
            message=(
                f"Bank-fee journal {number} for line {line_id} is {status.value} "
                f"and unposted. Not posted automatically — its disposition is a "
                f"Finance decision."
            ),
        )

    return BankFeeOutcome(
        state=BankFeeState.NOT_LIVE,
        journal=existing,
        message=(
            f"Bank-fee journal {number} for line {line_id} is {status.value}, so "
            f"no effect is live; the unique identity is still held by that "
            f"journal, so a replacement cannot be created automatically."
        ),
    )


#: Journals permitted to differ from the expected chart of accounts, each named
#: with the reason. An audited exception list, NOT a blanket omission.
#:
#: `JE202603-0227` and `JE202603-0230` (₦25.00 and ₦10.00, 2026-01-15) credit the
#: legacy `Paystack OPEX - DT` code where their statement line's bank account is
#: Paystack OPEX — a chart-of-accounts duplication, verified in ERP PR #335
#: appendix B5. They are exempt from the ACCOUNT comparison only; amounts, line
#: count and direction are still checked, and every other journal is compared in
#: full.
#:
#: Removing a row from this list is the goal. Adding one requires evidence that
#: the difference is a chart remap and not a wrong posting.
LEGACY_ACCOUNT_EXEMPTIONS: frozenset[str] = frozenset(
    {"JE202603-0227", "JE202603-0230"}
)


def _content_mismatch(
    db: Session,
    journal: JournalEntry,
    *,
    line: Any,
    debit_account_id: uuid.UUID,
    credit_account_id: uuid.UUID,
) -> str | None:
    """Describe how a DRAFT journal differs from its statement line, or None.

    Compares SHAPE and ACCOUNTS, not just totals. An earlier version compared
    only aggregate debit and credit, which a journal with the right money on the
    WRONG accounts passes — and re-posting that writes a wrong posting into the
    ledger under the line's identity, where it is hard to correct afterwards.

    A bank fee is two lines: the fee debited to the finance-cost account, the
    same amount credited to the statement line's bank account. Anything else is
    not this fee.
    """
    expected = quantize_amount(abs(Decimal(line.amount)))
    rows = list(
        db.scalars(
            select(JournalEntryLine).where(
                JournalEntryLine.journal_entry_id == journal.journal_entry_id
            )
        ).all()
    )
    if not rows:
        return "the journal has no lines"
    if len(rows) != 2:
        return f"the journal has {len(rows)} lines; a bank fee has 2"

    debit = quantize_amount(
        sum((r.debit_amount_functional or Decimal("0") for r in rows), Decimal("0"))
    )
    credit = quantize_amount(
        sum((r.credit_amount_functional or Decimal("0") for r in rows), Decimal("0"))
    )
    if debit != expected or credit != expected:
        return (
            f"journal totals are debit {debit} / credit {credit}, "
            f"the line implies {expected}"
        )

    if journal.journal_number in LEGACY_ACCOUNT_EXEMPTIONS:
        # Amounts, shape and direction were all checked above; only the account
        # comparison is waived, for the reason recorded beside the list.
        return None

    by_account = {
        r.account_id: (
            quantize_amount(r.debit_amount_functional or Decimal("0")),
            quantize_amount(r.credit_amount_functional or Decimal("0")),
        )
        for r in rows
    }
    if by_account.get(debit_account_id, (Decimal("0"), Decimal("0")))[0] != expected:
        return (
            f"the fee is not debited to the expected finance-cost account "
            f"{debit_account_id}"
        )
    if by_account.get(credit_account_id, (Decimal("0"), Decimal("0")))[1] != expected:
        return (
            f"the fee is not credited to the statement line's bank account "
            f"{credit_account_id}"
        )
    return None


def quantize_amount(amount: Decimal) -> Decimal:
    """Round to the scale the ledger stores, matching PostgreSQL's rounding."""
    from app.services.finance.posting.residue import quantize

    return quantize(amount)


def _post_existing(
    db: Session,
    journal: JournalEntry,
    *,
    organization_id: uuid.UUID,
    line: Any,
    line_id: uuid.UUID,
    posted_by_user_id: uuid.UUID,
    poster: _Poster | None,
) -> Any:
    """Drive an existing DRAFT journal back through submit → approve → post."""
    from app.services.finance.posting.base import BasePostingAdapter

    # The SAME submit/approve path the create route uses, including its
    # segregation-of-duties bypass for system actors. Calling
    # `JournalService.approve_journal` directly here raised an uncaught
    # HTTPException the first time SoD fired — which for an automated actor
    # that both creates and approves is every time.
    BasePostingAdapter.submit_and_approve_as_system(
        db, organization_id, journal, posted_by_user_id
    )

    idempotency_key = fee_idempotency_key(organization_id, line_id)
    correlation_id = f"bank-fee-{line_id}"
    if poster is not None:
        return poster(
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
    return BasePostingAdapter.post_to_ledger(
        db,
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
    primaries = find_fee_journals(db, organization_id=organization_id, line_id=line_id)
    if len(primaries) > 1:
        numbers = ", ".join(j.journal_number for j in primaries)
        return BankFeeOutcome(
            state=BankFeeState.AMBIGUOUS,
            journal=None,
            message=(
                f"{len(primaries)} primary bank-fee journals carry line {line_id} "
                f"({numbers}). The unique index permits one; this predates it or "
                f"it is missing. Resolve deliberately — do not guess."
            ),
            error="multiple primary journals for one statement line",
        )
    if primaries:
        existing = primaries[0]
        return _existing_journal_outcome(
            db,
            existing,
            organization_id=organization_id,
            line=line,
            line_id=line_id,
            bank_gl_account_id=bank_gl_account_id,
            finance_cost_account_id=finance_cost_account_id,
            posted_by_user_id=posted_by_user_id,
            poster=poster,
        )

    # ---- Pre-check 2: the ledger already posted this line ------------------
    batch = existing_posting_batch(db, organization_id=organization_id, line_id=line_id)
    if batch is not None:
        return BankFeeOutcome(
            state=BankFeeState.LEGACY_BATCH_ONLY,
            journal=None,
            message=(
                f"Bank fee already posted for line {line_id} under batch "
                f"{batch.batch_id}; no journal carries the typed identity "
                f"(legacy row). Nothing created."
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
    except IntegrityError as exc:
        # ONLY the source-identity index means "someone else won this line". Any
        # other constraint failure — a bad account foreign key, a duplicate
        # journal number — is a real error, and reporting it as a lost race
        # would turn a broken write into a success.
        if not _is_fee_identity_violation(exc):
            raise

        # And winning the race is not the same as having a live effect. The
        # winner is classified exactly as any pre-existing journal would be:
        # POSTED-and-live, DRAFT to be re-posted, an unposted orphan, or
        # reversed. Assuming LOST_RACE means "fine, someone else did it" is the
        # false-success this handler used to produce.
        winners = find_fee_journals(
            db, organization_id=organization_id, line_id=line_id
        )
        if not winners:
            message = (
                f"The source-identity index refused a bank-fee journal for line "
                f"{line_id}, but no journal for that line is visible. The write "
                f"was refused and nothing explains it."
            )
            logger.error("%s", message)
            return BankFeeOutcome(
                state=BankFeeState.FAILED,
                journal=None,
                message=message,
                error="identity violation with no visible winner",
            )
        if len(winners) > 1:
            numbers = ", ".join(j.journal_number for j in winners)
            return BankFeeOutcome(
                state=BankFeeState.AMBIGUOUS,
                journal=None,
                message=(
                    f"{len(winners)} primary bank-fee journals carry line "
                    f"{line_id} ({numbers}) after a collision. Resolve "
                    f"deliberately — do not guess."
                ),
                error="multiple primary journals for one statement line",
            )

        winner = winners[0]
        logger.info(
            "Bank fee for line %s was created concurrently; deferring to %s",
            line_id,
            winner.journal_number,
        )
        outcome = _existing_journal_outcome(
            db,
            winner,
            organization_id=organization_id,
            line=line,
            line_id=line_id,
            bank_gl_account_id=bank_gl_account_id,
            finance_cost_account_id=finance_cost_account_id,
            posted_by_user_id=posted_by_user_id,
            poster=poster,
        )
        if outcome.state is BankFeeState.ALREADY_POSTED:
            # The one case that really is a clean lost race.
            return BankFeeOutcome(
                state=BankFeeState.LOST_RACE,
                journal=outcome.journal,
                message=(
                    f"Bank fee created concurrently for line {line_id}; "
                    f"{winner.journal_number} holds the live effect"
                ),
            )
        return outcome

    if journal is None:
        # Both helpers are typed as possibly returning no journal. If that
        # happens there is nothing to post and nothing to report, and going on
        # would dereference None at exactly the point this module exists to make
        # reliable.
        message = create_error.message if create_error else "no journal was created"
        return BankFeeOutcome(
            state=BankFeeState.FAILED, journal=None, message=message, error=message
        )

    if create_error:
        return BankFeeOutcome(
            state=BankFeeState.FAILED,
            journal=None,
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
        # `_revert_unposted_journal` has just put this journal back to DRAFT
        # while it still carries the typed source id. The next invocation must
        # RE-POST it, not skip it and not create a second one — which is what
        # `_existing_journal_outcome` does for a DRAFT.
        return BankFeeOutcome(
            state=BankFeeState.FAILED,
            journal=journal,
            message=posting_result.message,
            error=posting_result.message,
        )

    # The pre-checks make this unreachable; it is surfaced rather than dropped
    # because discarding exactly this signal is what produced 12,117 orphans.
    if posting_result.idempotent_replay:
        logger.warning(
            "Bank fee for line %s posted as an idempotent replay even though no "
            "prior journal or batch was found. Journal %s is APPROVED and "
            "unposted; investigate before re-running.",
            line_id,
            journal.journal_number,
        )
        return BankFeeOutcome(
            state=BankFeeState.REPLAY_LEFT_UNPOSTED,
            journal=journal,
            message=(
                f"Bank fee for line {line_id} replayed against an existing batch; "
                f"journal {journal.journal_number} is {journal.status.value} and "
                f"unposted"
            ),
            error="idempotent replay after a clean pre-check",
        )

    return BankFeeOutcome(
        state=BankFeeState.CREATED, journal=journal, message="Bank fee posted"
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
