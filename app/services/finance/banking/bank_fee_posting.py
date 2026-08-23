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
``(organization_id, source_document_id) WHERE source_module = 'BANKING' AND
source_document_type = 'BANK_FEE'``.
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

from app.models.finance.gl.fiscal_period import FiscalPeriod
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
    #: A journal or its posted ledger effect does not exactly match the source
    #: line. A live identity is not accepted as proof of economic equivalence.
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
        """Whether the outcome proves a live, matching bank-fee effect."""
        return self.state in {
            BankFeeState.CREATED,
            BankFeeState.ALREADY_POSTED,
            BankFeeState.REPOSTED_DRAFT,
            BankFeeState.LEGACY_BATCH_ONLY,
            BankFeeState.LOST_RACE,
        }

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
            BankFeeState.LEGACY_BATCH_ONLY,
            BankFeeState.LOST_RACE,
        }:
            return self.journal
        return None


IDENTITY_INDEX_NAME = "uq_journal_entry_bank_fee_source"


@dataclass(frozen=True)
class ExistingPostingEffect:
    """A legacy idempotency batch and the live journals whose rows it wrote."""

    batch: PostingBatch
    journals: tuple[JournalEntry, ...]


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
                JournalEntry.source_module == SOURCE_MODULE,
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
                PostedLedgerLine.organization_id == journal.organization_id,
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


def find_existing_posting_effect(
    db: Session, *, organization_id: uuid.UUID, line_id: uuid.UUID
) -> ExistingPostingEffect | None:
    """The legacy batch and every live journal effect it actually wrote.

    Covers legacy journals whose `source_document_id` is NULL: their identity
    survives only in the idempotency key. Returning the journal is essential:
    the adapters need it to repair a statement match after a crash, and the
    owner needs its exact content to decide whether the batch is THIS fee.
    """
    batch = db.scalars(
        select(PostingBatch).where(
            PostingBatch.organization_id == organization_id,
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
                PostedLedgerLine.organization_id == organization_id,
                PostedLedgerLine.posting_batch_id == batch.batch_id
            )
        ).all()
    )
    if not journal_ids:
        return None
    journals: list[JournalEntry] = []
    for journal_id in sorted(journal_ids, key=str):
        journal = db.get(JournalEntry, journal_id)
        if (
            journal is not None
            and journal.organization_id == organization_id
            and is_effect_live(db, journal)
        ):
            journals.append(journal)
    if not journals:
        return None
    return ExistingPostingEffect(batch=batch, journals=tuple(journals))


def existing_posting_batch(
    db: Session, *, organization_id: uuid.UUID, line_id: uuid.UUID
) -> PostingBatch | None:
    """Backward-compatible batch lookup for callers that only need liveness."""
    effect = find_existing_posting_effect(
        db, organization_id=organization_id, line_id=line_id
    )
    return effect.batch if effect is not None else None


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
        mismatch = _effect_mismatch(
            db,
            existing,
            line=line,
            debit_account_id=finance_cost_account_id,
            credit_account_id=bank_gl_account_id,
        )
        if mismatch is not None:
            return BankFeeOutcome(
                state=BankFeeState.CONTENT_MISMATCH,
                journal=existing,
                message=(
                    f"POSTED bank-fee journal {number} does not prove the effect "
                    f"for line {line_id}: {mismatch}."
                ),
                error=mismatch,
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
        if not is_effect_live(db, existing):
            return BankFeeOutcome(
                state=BankFeeState.FAILED,
                journal=existing,
                message=(
                    f"Re-post of bank-fee journal {number} reported success, "
                    f"but no live ledger effect exists."
                ),
                error="posting reported success without a live ledger effect",
            )
        mismatch = _effect_mismatch(
            db,
            existing,
            line=line,
            debit_account_id=finance_cost_account_id,
            credit_account_id=bank_gl_account_id,
        )
        if mismatch is not None:
            return BankFeeOutcome(
                state=BankFeeState.CONTENT_MISMATCH,
                journal=existing,
                message=(
                    f"Re-posted bank-fee journal {number} does not prove the "
                    f"effect for line {line_id}: {mismatch}."
                ),
                error=mismatch,
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


def _content_mismatch(
    db: Session,
    journal: JournalEntry,
    *,
    line: Any,
    debit_account_id: uuid.UUID,
    credit_account_id: uuid.UUID,
) -> str | None:
    """Describe how a journal differs from the bank-fee source, or None.

    This is shared by DRAFT recovery and live-effect classification. Journal
    identity, status and a couple of balanced totals are not proof of the
    source's economic effect: date, period, currency, lines, dimensions and
    accounts all have to describe this statement fee.

    A bank fee is two lines: the fee debited to the finance-cost account, the
    same amount credited to the statement line's bank account. Anything else is
    not this fee.
    """
    expected = quantize_amount(abs(Decimal(line.amount)))
    expected_date = line.transaction_date
    if journal.is_reversal:
        return "journal is a reversal, not the primary bank-fee effect"
    if journal.journal_type is not JournalType.STANDARD:
        return f"journal type is {journal.journal_type.value}, expected STANDARD"
    if journal.entry_date != expected_date or journal.posting_date != expected_date:
        return (
            f"journal dates are entry {journal.entry_date} / posting "
            f"{journal.posting_date}, the line date is {expected_date}"
        )
    period_mismatch = _period_mismatch(
        db,
        organization_id=journal.organization_id,
        fiscal_period_id=journal.fiscal_period_id,
        posting_date=journal.posting_date,
        label="journal",
    )
    if period_mismatch is not None:
        return period_mismatch
    if journal.source_module != SOURCE_MODULE:
        return f"source module is {journal.source_module!r}, expected {SOURCE_MODULE}"
    if journal.source_document_type != SOURCE_DOCUMENT_TYPE:
        return (
            f"source document type is {journal.source_document_type!r}, "
            f"expected {SOURCE_DOCUMENT_TYPE}"
        )
    if journal.source_document_id not in {None, line.line_id}:
        return (
            f"source document id is {journal.source_document_id}, "
            f"expected {line.line_id} or legacy NULL"
        )
    expected_correlation = f"bank-fee-{line.line_id}"
    if journal.correlation_id != expected_correlation:
        return (
            f"correlation id is {journal.correlation_id!r}, "
            f"expected {expected_correlation!r}"
        )

    rate = Decimal(journal.exchange_rate)
    if rate <= 0:
        return f"journal exchange rate is {rate}, expected a positive rate"
    functional_expected = quantize_amount(expected * rate)
    header_amounts = (
        quantize_amount(journal.total_debit),
        quantize_amount(journal.total_credit),
        quantize_amount(journal.total_debit_functional),
        quantize_amount(journal.total_credit_functional),
    )
    expected_header_amounts = (
        expected,
        expected,
        functional_expected,
        functional_expected,
    )
    if header_amounts != expected_header_amounts:
        return (
            f"journal header totals are {header_amounts}, expected "
            f"{expected_header_amounts}"
        )

    from app.models.finance.banking.bank_account import BankAccount

    bank_currencies = set(
        db.scalars(
            select(BankAccount.currency_code).where(
                BankAccount.organization_id == journal.organization_id,
                BankAccount.gl_account_id == credit_account_id,
            )
        ).all()
    )
    if len(bank_currencies) > 1:
        return (
            f"bank GL account {credit_account_id} is linked to conflicting "
            f"currencies {sorted(bank_currencies)}"
        )
    if bank_currencies and journal.currency_code not in bank_currencies:
        expected_currency = next(iter(bank_currencies))
        return (
            f"journal currency is {journal.currency_code}, the bank account "
            f"currency is {expected_currency}"
        )

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

    expected_lines = {
        1: (
            debit_account_id,
            expected,
            Decimal("0"),
            functional_expected,
            Decimal("0"),
        ),
        2: (
            credit_account_id,
            Decimal("0"),
            expected,
            Decimal("0"),
            functional_expected,
        ),
    }
    by_number = {row.line_number: row for row in rows}
    if set(by_number) != set(expected_lines):
        return f"journal line numbers are {sorted(by_number)}, expected [1, 2]"
    dimension_names = (
        "business_unit_id",
        "cost_center_id",
        "project_id",
        "segment_id",
    )
    for line_number, expected_line in expected_lines.items():
        row = by_number[line_number]
        actual_line = (
            row.account_id,
            quantize_amount(row.debit_amount),
            quantize_amount(row.credit_amount),
            quantize_amount(row.debit_amount_functional),
            quantize_amount(row.credit_amount_functional),
        )
        if actual_line != expected_line:
            role = (
                "finance-cost account"
                if line_number == 1
                else "statement line's bank account"
            )
            return (
                f"the {role} line is {actual_line}, expected {expected_line}"
            )
        if row.currency_code != journal.currency_code:
            return (
                f"journal line {line_number} currency is {row.currency_code!r}, "
                f"expected {journal.currency_code!r}"
            )
        if row.exchange_rate is None or Decimal(row.exchange_rate) != rate:
            return (
                f"journal line {line_number} exchange rate is "
                f"{row.exchange_rate}, expected {rate}"
            )
        populated_dimensions = [
            name for name in dimension_names if getattr(row, name) is not None
        ]
        if populated_dimensions:
            return (
                f"journal line {line_number} has unexpected dimensions "
                f"{populated_dimensions}"
            )
    return None


def _period_mismatch(
    db: Session,
    *,
    organization_id: uuid.UUID,
    fiscal_period_id: uuid.UUID,
    posting_date: Any,
    label: str,
) -> str | None:
    period = db.get(FiscalPeriod, fiscal_period_id)
    if period is None:
        return f"{label} fiscal period {fiscal_period_id} does not exist"
    if period.organization_id != organization_id:
        return f"{label} fiscal period belongs to another organization"
    if not period.start_date <= posting_date <= period.end_date:
        return (
            f"{label} posting date {posting_date} is outside fiscal period "
            f"{period.start_date}..{period.end_date}"
        )
    return None


def _effect_mismatch(
    db: Session,
    journal: JournalEntry,
    *,
    line: Any,
    debit_account_id: uuid.UUID,
    credit_account_id: uuid.UUID,
    batch: PostingBatch | None = None,
) -> str | None:
    """Describe any difference between the source, journal and posted effect."""
    mismatch = _content_mismatch(
        db,
        journal,
        line=line,
        debit_account_id=debit_account_id,
        credit_account_id=credit_account_id,
    )
    if mismatch is not None:
        return mismatch

    journal_rows = list(
        db.scalars(
            select(JournalEntryLine).where(
                JournalEntryLine.journal_entry_id == journal.journal_entry_id
            )
        ).all()
    )
    ledger_rows = list(
        db.scalars(
            select(PostedLedgerLine).where(
                PostedLedgerLine.organization_id == journal.organization_id,
                PostedLedgerLine.journal_entry_id == journal.journal_entry_id,
            )
        ).all()
    )
    if len(ledger_rows) != len(journal_rows):
        return (
            f"posted effect has {len(ledger_rows)} ledger lines, journal has "
            f"{len(journal_rows)}"
        )
    if journal.posting_batch_id is None:
        return "posted journal has no posting batch id"
    if batch is None:
        batch = db.get(PostingBatch, journal.posting_batch_id)
    if batch is None:
        return f"posting batch {journal.posting_batch_id} does not exist"
    if batch.batch_id != journal.posting_batch_id:
        return (
            f"effect batch {batch.batch_id} differs from journal batch "
            f"{journal.posting_batch_id}"
        )
    if batch.organization_id != journal.organization_id:
        return "posting batch belongs to another organization"
    if batch.status != BatchStatus.POSTED:
        return f"posting batch status is {batch.status.value}, expected POSTED"
    if batch.source_module != SOURCE_MODULE:
        return f"posting batch source is {batch.source_module!r}, expected BANKING"
    expected_key = fee_idempotency_key(journal.organization_id, line.line_id)
    if batch.idempotency_key != expected_key:
        return (
            f"posting batch key is {batch.idempotency_key!r}, expected "
            f"{expected_key!r}"
        )
    if batch.correlation_id != journal.correlation_id:
        return (
            f"posting batch correlation is {batch.correlation_id!r}, expected "
            f"{journal.correlation_id!r}"
        )
    if batch.total_entries != len(journal_rows) or batch.posted_entries != len(
        ledger_rows
    ):
        return (
            f"posting batch counts are total={batch.total_entries}, "
            f"posted={batch.posted_entries}; expected {len(journal_rows)}"
        )

    by_line_id = {row.line_id: row for row in journal_rows}
    if {row.journal_line_id for row in ledger_rows} != set(by_line_id):
        return "posted ledger line ids do not exactly match the journal lines"
    posting_dates = {row.posting_date for row in ledger_rows}
    period_ids = {row.fiscal_period_id for row in ledger_rows}
    batch_ids = {row.posting_batch_id for row in ledger_rows}
    if len(posting_dates) != 1 or len(period_ids) != 1 or batch_ids != {batch.batch_id}:
        return "posted ledger rows disagree on posting date, period, or batch"
    ledger_posting_date = next(iter(posting_dates))
    ledger_period_id = next(iter(period_ids))
    if batch.fiscal_period_id != ledger_period_id:
        return (
            f"batch fiscal period {batch.fiscal_period_id} differs from ledger "
            f"period {ledger_period_id}"
        )
    period_mismatch = _period_mismatch(
        db,
        organization_id=journal.organization_id,
        fiscal_period_id=ledger_period_id,
        posting_date=ledger_posting_date,
        label="ledger",
    )
    if period_mismatch is not None:
        return period_mismatch

    dimension_names = (
        "business_unit_id",
        "cost_center_id",
        "project_id",
        "segment_id",
    )
    for ledger_row in ledger_rows:
        journal_row = by_line_id[ledger_row.journal_line_id]
        if ledger_row.account_id != journal_row.account_id:
            return f"ledger line {ledger_row.ledger_line_id} changed account"
        if ledger_row.entry_date != journal.entry_date:
            return f"ledger line {ledger_row.ledger_line_id} changed entry date"
        if (
            quantize_amount(ledger_row.debit_amount)
            != quantize_amount(journal_row.debit_amount_functional)
            or quantize_amount(ledger_row.credit_amount)
            != quantize_amount(journal_row.credit_amount_functional)
        ):
            return f"ledger line {ledger_row.ledger_line_id} changed functional amount"
        if (
            ledger_row.original_currency_code != journal_row.currency_code
            or quantize_amount(ledger_row.original_debit_amount or Decimal("0"))
            != quantize_amount(journal_row.debit_amount)
            or quantize_amount(ledger_row.original_credit_amount or Decimal("0"))
            != quantize_amount(journal_row.credit_amount)
            or ledger_row.exchange_rate is None
            or journal_row.exchange_rate is None
            or Decimal(ledger_row.exchange_rate) != Decimal(journal_row.exchange_rate)
        ):
            return f"ledger line {ledger_row.ledger_line_id} changed original currency"
        if any(
            getattr(ledger_row, name) != getattr(journal_row, name)
            for name in dimension_names
        ):
            return f"ledger line {ledger_row.ledger_line_id} changed dimensions"
        if (
            ledger_row.source_module != SOURCE_MODULE
            or ledger_row.source_document_type != journal.source_document_type
            or ledger_row.source_document_id != journal.source_document_id
            or ledger_row.correlation_id != journal.correlation_id
        ):
            return f"ledger line {ledger_row.ledger_line_id} changed source identity"
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
    effect = find_existing_posting_effect(
        db, organization_id=organization_id, line_id=line_id
    )
    if effect is not None:
        if len(effect.journals) != 1:
            numbers = ", ".join(j.journal_number for j in effect.journals)
            return BankFeeOutcome(
                state=BankFeeState.AMBIGUOUS,
                journal=None,
                message=(
                    f"Legacy bank-fee batch {effect.batch.batch_id} for line "
                    f"{line_id} leads to {len(effect.journals)} live journals "
                    f"({numbers}); one canonical effect is required."
                ),
                error="legacy batch has multiple live journals",
            )
        legacy_journal = effect.journals[0]
        mismatch = _effect_mismatch(
            db,
            legacy_journal,
            line=line,
            debit_account_id=finance_cost_account_id,
            credit_account_id=bank_gl_account_id,
            batch=effect.batch,
        )
        if mismatch is not None:
            return BankFeeOutcome(
                state=BankFeeState.CONTENT_MISMATCH,
                journal=legacy_journal,
                message=(
                    f"Legacy bank-fee batch {effect.batch.batch_id} does not "
                    f"prove the effect for line {line_id}: {mismatch}."
                ),
                error=mismatch,
            )
        return BankFeeOutcome(
            state=BankFeeState.LEGACY_BATCH_ONLY,
            journal=legacy_journal,
            message=(
                f"Bank fee already posted for line {line_id} under batch "
                f"{effect.batch.batch_id} by {legacy_journal.journal_number}; "
                f"no journal carries the typed identity (legacy row). Nothing "
                f"created."
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

    if not is_effect_live(db, journal):
        return BankFeeOutcome(
            state=BankFeeState.FAILED,
            journal=journal,
            message=(
                f"Bank-fee journal {journal.journal_number} reported a successful "
                f"post for line {line_id}, but no live ledger effect exists."
            ),
            error="posting reported success without a live ledger effect",
        )
    mismatch = _effect_mismatch(
        db,
        journal,
        line=line,
        debit_account_id=finance_cost_account_id,
        credit_account_id=bank_gl_account_id,
    )
    if mismatch is not None:
        return BankFeeOutcome(
            state=BankFeeState.CONTENT_MISMATCH,
            journal=journal,
            message=(
                f"Posted bank-fee journal {journal.journal_number} does not prove "
                f"the effect for line {line_id}: {mismatch}."
            ),
            error=mismatch,
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
    "find_existing_posting_effect",
    "find_existing_fee_journal",
    "post_bank_fee",
]
