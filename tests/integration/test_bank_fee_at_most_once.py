"""Canaries for the bank-fee at-most-once boundary.

These exist because the boundary that was missing is the one that only a
database can hold. `gl.posting_batch.idempotency_key` was already globally
unique, so POSTING a bank fee was at-most-once. CREATING the journal was not —
and creation ran first. Production ended up with **12,117 APPROVED bank-fee
journals for 149 statement lines**, one line carrying 85.

Every test here drives the real PostgreSQL constraint rather than a mock,
because an application-level check is exactly what a concurrent caller defeats.
The concurrent canary opens two independent connections and commits on both; it
would pass against a pure-Python guard and still be wrong, which is the point.

The tests deliberately build `JournalEntry` rows directly instead of running the
full posting stack. The claim under test is "a second bank-fee journal for one
statement line cannot exist", and that claim is about the row, not about the
path that writes it — a path-level test would pass the moment any one adapter
was fixed while the others still bypassed it.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus, JournalType
from app.services.finance.banking.bank_fee_posting import (
    SOURCE_DOCUMENT_TYPE,
    fee_idempotency_key,
    find_existing_fee_journal,
    post_bank_fee,
)

pytestmark = pytest.mark.integration


def _fiscal_period_id(db: Session, org_id: uuid.UUID) -> uuid.UUID:
    """A fiscal period to hang the journals on; `fiscal_period_id` is NOT NULL.

    Built through the ORM rather than hand-written SQL. The first version of
    this helper guessed a `status` column on `gl.fiscal_year` that does not
    exist — the models are the schema, so use them.
    """
    from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
    from app.models.finance.gl.fiscal_year import FiscalYear

    year = FiscalYear(
        fiscal_year_id=uuid.uuid4(),
        organization_id=org_id,
        year_code=f"FY{uuid.uuid4().hex[:6]}",
        year_name="Canary Year",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
    )
    db.add(year)
    db.flush()

    period = FiscalPeriod(
        fiscal_period_id=uuid.uuid4(),
        organization_id=org_id,
        fiscal_year_id=year.fiscal_year_id,
        period_number=1,
        period_name="Canary",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
        status=PeriodStatus.OPEN,
    )
    db.add(period)
    db.flush()
    return uuid.UUID(str(period.fiscal_period_id))


def _account(db: Session, org_id: uuid.UUID, code: str, name: str):
    """A real chart-of-accounts row.

    `journal_entry_line.account_id` and `posted_ledger_line.account_id` are
    foreign keys. Passing a random UUID makes the INSERT fail, which is a test
    defect masquerading as a behaviour finding.
    """
    from app.models.finance.gl.account import Account, AccountType, NormalBalance
    from app.models.finance.gl.account_category import AccountCategory, IFRSCategory

    category = AccountCategory(
        category_id=uuid.uuid4(),
        organization_id=org_id,
        category_code=f"CAT-{uuid.uuid4().hex[:6]}",
        category_name="Canary Category",
        ifrs_category=IFRSCategory.EXPENSES,
        hierarchy_level=1,
        display_order=1,
    )
    db.add(category)
    db.flush()

    account = Account(
        account_id=uuid.uuid4(),
        organization_id=org_id,
        category_id=category.category_id,
        account_code=code,
        account_name=name,
        account_type=AccountType.POSTING,
        normal_balance=NormalBalance.DEBIT,
    )
    db.add(account)
    db.flush()
    return account


def _with_lines(db: Session, journal, debit_account, credit_account, amount: Decimal):
    """Give a journal the two lines a bank fee actually has.

    Header-only journals were what the first canaries used, and the content and
    liveness checks rightly refused them — a POSTED bank fee with no lines
    cannot exist in production, so a fixture that builds one is testing a shape
    the system does not have.
    """
    from app.models.finance.gl.journal_entry_line import JournalEntryLine

    for number, account, dr, cr in (
        (1, debit_account, amount, Decimal("0")),
        (2, credit_account, Decimal("0"), amount),
    ):
        db.add(
            JournalEntryLine(
                line_id=uuid.uuid4(),
                journal_entry_id=journal.journal_entry_id,
                line_number=number,
                account_id=account.account_id,
                debit_amount=dr,
                credit_amount=cr,
                debit_amount_functional=dr,
                credit_amount_functional=cr,
                currency_code=journal.currency_code,
                exchange_rate=journal.exchange_rate,
            )
        )
    db.flush()
    return journal


def _post_to_ledger(db: Session, journal, period_id: uuid.UUID, org_id: uuid.UUID):
    """Give a POSTED journal the ledger rows that make its effect LIVE.

    `is_effect_live` requires them deliberately: a journal can carry POSTED
    status and have nothing in `gl.posted_ledger_line`, and that is not an
    effect.
    """
    from app.models.finance.gl.journal_entry_line import JournalEntryLine
    from app.models.finance.gl.posted_ledger_line import PostedLedgerLine
    from app.models.finance.gl.posting_batch import BatchStatus, PostingBatch

    idempotency_key = (
        fee_idempotency_key(org_id, journal.source_document_id)
        if journal.source_document_id is not None
        else f"canary-{uuid.uuid4()}"
    )
    batch = PostingBatch(
        batch_id=uuid.uuid4(),
        organization_id=org_id,
        fiscal_period_id=period_id,
        idempotency_key=idempotency_key,
        source_module="BANKING",
        batch_description="canary",
        total_entries=2,
        posted_entries=2,
        failed_entries=0,
        status=BatchStatus.POSTED,
        submitted_by_user_id=uuid.uuid4(),
        correlation_id=journal.correlation_id,
    )
    db.add(batch)
    db.flush()

    rows = db.scalars(
        select(JournalEntryLine).where(
            JournalEntryLine.journal_entry_id == journal.journal_entry_id
        )
    ).all()
    for row in rows:
        db.add(
            PostedLedgerLine(
                ledger_line_id=uuid.uuid4(),
                posting_year=journal.posting_date.year,
                organization_id=org_id,
                journal_entry_id=journal.journal_entry_id,
                journal_line_id=row.line_id,
                posting_batch_id=batch.batch_id,
                fiscal_period_id=period_id,
                account_id=row.account_id,
                account_code="6080",
                entry_date=journal.entry_date,
                posting_date=journal.posting_date,
                debit_amount=row.debit_amount_functional,
                credit_amount=row.credit_amount_functional,
                original_currency_code=row.currency_code,
                original_debit_amount=row.debit_amount,
                original_credit_amount=row.credit_amount,
                exchange_rate=row.exchange_rate,
                business_unit_id=row.business_unit_id,
                cost_center_id=row.cost_center_id,
                project_id=row.project_id,
                segment_id=row.segment_id,
                source_module=journal.source_module,
                source_document_type=journal.source_document_type,
                source_document_id=journal.source_document_id,
                posted_by_user_id=uuid.uuid4(),
                correlation_id=journal.correlation_id,
            )
        )
    journal.posting_batch_id = batch.batch_id
    db.flush()
    return journal


def _legacy_live_batch(
    db: Session,
    org_id: uuid.UUID,
    period_id: uuid.UUID,
    line_id: uuid.UUID,
    cost,
    bank,
):
    """A legacy fee: POSTED journal with NULL source id, real lines, real ledger
    rows, and a batch keyed on the statement line.

    A batch row alone is no longer enough to block a create — the lookup follows
    it to a live effect — so a fixture that wants to block must build one.
    """
    from app.models.finance.gl.posted_ledger_line import PostedLedgerLine
    from app.models.finance.gl.posting_batch import PostingBatch

    journal = _fee_journal(
        org_id, period_id, line_id, number=f"LEGACY-{uuid.uuid4().hex[:6]}"
    )
    journal.status = JournalStatus.POSTED
    journal.source_document_id = None  # legacy: identity only in the key
    db.add(journal)
    db.flush()
    _with_lines(db, journal, cost, bank, Decimal("10"))
    _post_to_ledger(db, journal, period_id, org_id)

    batch_id = db.scalar(
        select(PostedLedgerLine.posting_batch_id).where(
            PostedLedgerLine.journal_entry_id == journal.journal_entry_id
        )
    )
    batch = db.get(PostingBatch, batch_id)
    assert batch is not None
    batch.idempotency_key = fee_idempotency_key(org_id, line_id)
    batch.correlation_id = f"bank-fee-{line_id}"
    db.flush()
    return journal


def _sqlstate(exc: BaseException) -> str | None:
    """The SQLSTATE the driver reported, not the message text.

    Asserting on a message is asserting on prose. `55P03` (lock_not_available)
    and `23505` (unique_violation) are the two facts these canaries are about.
    """
    orig = getattr(exc, "orig", exc)
    return getattr(orig, "sqlstate", None) or getattr(
        getattr(orig, "diag", None), "sqlstate", None
    )


def _posting_batch(db: Session, org_id: uuid.UUID, period_id: uuid.UUID, key: str):
    """A POSTED batch under `key`, built through the ORM.

    The first version of this helper hand-wrote the INSERT and omitted TWO
    NOT NULL columns. The model is the schema.
    """
    from app.models.finance.gl.posting_batch import BatchStatus, PostingBatch

    batch = PostingBatch(
        batch_id=uuid.uuid4(),
        organization_id=org_id,
        fiscal_period_id=period_id,
        idempotency_key=key,
        source_module="BANKING",
        batch_description="legacy canary",
        total_entries=2,
        posted_entries=2,
        failed_entries=0,
        status=BatchStatus.POSTED,
        submitted_by_user_id=uuid.uuid4(),
    )
    db.add(batch)
    db.flush()
    return batch


def _fee_journal(
    org_id: uuid.UUID, period_id: uuid.UUID, line_id: uuid.UUID, *, number: str
) -> JournalEntry:
    return JournalEntry(
        journal_entry_id=uuid.uuid4(),
        organization_id=org_id,
        journal_number=number,
        journal_type=JournalType.STANDARD,
        entry_date=date(2026, 1, 15),
        posting_date=date(2026, 1, 15),
        fiscal_period_id=period_id,
        description="Bank charge - canary",
        currency_code="NGN",
        exchange_rate=Decimal("1"),
        total_debit=Decimal("10"),
        total_credit=Decimal("10"),
        total_debit_functional=Decimal("10"),
        total_credit_functional=Decimal("10"),
        status=JournalStatus.APPROVED,
        source_module="BANKING",
        source_document_type=SOURCE_DOCUMENT_TYPE,
        source_document_id=line_id,
        correlation_id=f"bank-fee-{line_id}",
        created_by_user_id=uuid.uuid4(),
    )


class TestTheDatabaseBoundary:
    """The constraint that makes the guarantee, tested directly."""

    def test_a_second_journal_for_one_statement_line_is_refused(
        self, db: Session, org_id: uuid.UUID
    ) -> None:
        """THE canary. Without the partial unique index this passes silently,
        and that silence is the entire production defect."""
        period_id = _fiscal_period_id(db, org_id)
        line_id = uuid.uuid4()

        db.add(_fee_journal(org_id, period_id, line_id, number="CANARY-1"))
        db.flush()

        db.add(_fee_journal(org_id, period_id, line_id, number="CANARY-2"))
        with pytest.raises(IntegrityError):
            db.flush()

    def test_the_index_is_scoped_to_bank_fees_only(
        self, db: Session, org_id: uuid.UUID
    ) -> None:
        """Other producers legitimately post several journals per document, so
        the constraint must not be a statement about every source type."""
        period_id = _fiscal_period_id(db, org_id)
        document_id = uuid.uuid4()

        for n in ("OTHER-1", "OTHER-2"):
            j = _fee_journal(org_id, period_id, document_id, number=n)
            j.source_document_type = "CUSTOMER_PAYMENT"
            j.correlation_id = None
            db.add(j)
        db.flush()  # must not raise

    def test_legacy_null_source_ids_do_not_collide(
        self, db: Session, org_id: uuid.UUID
    ) -> None:
        """Every pre-existing bank-fee journal has a NULL `source_document_id`.

        13,955 of them. If NULLs collided the migration could not have been
        applied at all, so this is the property that made it safe.
        """
        period_id = _fiscal_period_id(db, org_id)
        for n in ("LEGACY-1", "LEGACY-2", "LEGACY-3"):
            j = _fee_journal(org_id, period_id, uuid.uuid4(), number=n)
            j.source_document_id = None
            db.add(j)
        db.flush()  # must not raise


class TestCrossOrganizationIsolation:
    def test_the_same_line_id_under_two_organizations_is_allowed(
        self, db: Session, org_id: uuid.UUID
    ) -> None:
        """The constraint is per organization. A tenant must never be able to
        block another tenant's write, and a uuid collision across tenants is not
        a duplicate."""
        from app.models.finance.core_org.organization import Organization

        other = Organization(
            organization_code=f"TEST-{uuid.uuid4().hex[:8].upper()}",
            legal_name="Other Organization",
            functional_currency_code="NGN",
            presentation_currency_code="NGN",
            fiscal_year_end_month=12,
            fiscal_year_end_day=31,
            is_active=True,
        )
        db.add(other)
        db.flush()
        other_id = uuid.UUID(str(other.organization_id))

        line_id = uuid.uuid4()
        db.add(
            _fee_journal(org_id, _fiscal_period_id(db, org_id), line_id, number="ORG-A")
        )
        db.add(
            _fee_journal(
                other_id, _fiscal_period_id(db, other_id), line_id, number="ORG-B"
            )
        )
        db.flush()  # must not raise

    def test_the_lookup_does_not_cross_organizations(
        self, db: Session, org_id: uuid.UUID
    ) -> None:
        line_id = uuid.uuid4()
        db.add(
            _fee_journal(
                org_id, _fiscal_period_id(db, org_id), line_id, number="SCOPED"
            )
        )
        db.flush()

        assert find_existing_fee_journal(db, organization_id=org_id, line_id=line_id)
        assert (
            find_existing_fee_journal(db, organization_id=uuid.uuid4(), line_id=line_id)
            is None
        )


class TestTheOwnerRefusesToCreateTwice:
    def test_the_second_invocation_creates_no_journal(
        self, db: Session, org_id: uuid.UUID
    ) -> None:
        """Sequential canary: invoke twice for one line, and the second must
        create nothing at all — not an extra journal, not a match.

        The first journal is POSTED deliberately. An APPROVED one would be an
        orphan, which is a DIFFERENT outcome on purpose (see
        `test_an_approved_orphan_is_surfaced_not_silently_skipped`) — treating
        an unposted orphan as "already present" is exactly the conflation this
        module exists to remove.
        """
        period_id = _fiscal_period_id(db, org_id)
        line_id = uuid.uuid4()
        cost = _account(db, org_id, "6080", "Finance Cost")
        bank = _account(db, org_id, "1204", "Bank")
        first = _fee_journal(org_id, period_id, line_id, number="FIRST")
        first.status = JournalStatus.POSTED
        db.add(first)
        db.flush()
        _with_lines(db, first, cost, bank, Decimal("10"))
        _post_to_ledger(db, first, period_id, org_id)

        before = db.scalar(
            select(func.count(JournalEntry.journal_entry_id)).where(
                JournalEntry.organization_id == org_id
            )
        )

        line = type(
            "_Line",
            (),
            {
                "line_id": line_id,
                "amount": Decimal("-10"),
                "transaction_date": date(2026, 1, 15),
                "description": "Bank charge",
                "reference": "CANARY",
                "line_number": 1,
            },
        )()

        def _poster(**_kwargs):  # pragma: no cover - must never be reached
            raise AssertionError("posting attempted for an already-recorded fee")

        outcome = post_bank_fee(
            db,
            organization_id=org_id,
            line=line,
            bank_gl_account_id=bank.account_id,
            finance_cost_account_id=cost.account_id,
            posted_by_user_id=uuid.uuid4(),
            poster=_poster,
        )

        assert outcome.already_present
        assert not outcome.created
        assert outcome.journal is not None
        assert outcome.journal.journal_number == "FIRST"
        assert outcome.posted_journal is not None, (
            "the canonical journal must come back so a missing statement match "
            "can still be repaired"
        )

        after = db.scalar(
            select(func.count(JournalEntry.journal_entry_id)).where(
                JournalEntry.organization_id == org_id
            )
        )
        assert after == before, "the second invocation created a journal"

    def test_an_existing_posting_batch_alone_stops_the_create(
        self, db: Session, org_id: uuid.UUID
    ) -> None:
        """Legacy bank-fee journals carry a NULL `source_document_id` — their
        identity survives only in the idempotency key.

        Without this second pre-check the first run of the new code would look
        at 149 already-posted statement lines, find no typed identity, and mint
        149 fresh journals. The legacy effect has to be LIVE for the check to
        fire, which is why this builds ledger rows rather than a bare batch.
        """
        period_id = _fiscal_period_id(db, org_id)
        line_id = uuid.uuid4()
        cost = _account(db, org_id, "6080", "Finance Cost")
        bank = _account(db, org_id, "1204", "Bank")
        legacy = _legacy_live_batch(db, org_id, period_id, line_id, cost, bank)

        before = db.scalar(
            select(func.count(JournalEntry.journal_entry_id)).where(
                JournalEntry.organization_id == org_id
            )
        )

        outcome = post_bank_fee(
            db,
            organization_id=org_id,
            line=_fee_line(line_id),
            bank_gl_account_id=bank.account_id,
            finance_cost_account_id=cost.account_id,
            posted_by_user_id=uuid.uuid4(),
            poster=lambda **_: pytest.fail("posting attempted"),
        )

        assert outcome.already_present
        assert not outcome.created
        assert outcome.posted_journal is legacy, (
            "the canonical legacy journal must come back so matching can be "
            "repaired after a crash"
        )
        after = db.scalar(
            select(func.count(JournalEntry.journal_entry_id)).where(
                JournalEntry.organization_id == org_id
            )
        )
        assert after == before


class TestConcurrentInvocation:
    """Two callers, two connections, both committing.

    An application-level pre-check passes in both, so this is the case that
    separates a real boundary from a hopeful one.
    """

    def test_only_one_of_two_concurrent_creates_survives(self, engine) -> None:
        line_id = uuid.uuid4()
        org_id = uuid.uuid4()
        created: list[str] = []
        refused = 0

        Session_ = sessionmaker(bind=engine)
        s1, s2 = Session_(), Session_()
        period_ids: list[uuid.UUID] = []
        try:
            from app.models.finance.core_org.organization import Organization

            setup = Session_()
            try:
                setup.execute(text("SET app.bypass_rls = 'true'"))
                org = Organization(
                    organization_id=org_id,
                    organization_code=f"CANARY-{uuid.uuid4().hex[:8].upper()}",
                    legal_name="Concurrency Canary",
                    functional_currency_code="NGN",
                    presentation_currency_code="NGN",
                    fiscal_year_end_month=12,
                    fiscal_year_end_day=31,
                    is_active=True,
                )
                setup.add(org)
                setup.flush()
                period_ids.append(_fiscal_period_id(setup, org_id))
                setup.commit()
            finally:
                setup.close()

            for session in (s1, s2):
                session.execute(text("SET app.bypass_rls = 'true'"))

            # RACER-A inserts and holds the row uncommitted.
            s1.add(_fee_journal(org_id, period_ids[0], line_id, number="RACER-A"))
            s1.flush()

            # RACER-B tries the same line while A still holds it. PostgreSQL
            # BLOCKS the second inserter on a unique index until the first
            # transaction resolves — so the canary asserts the block rather than
            # waiting for it, which is both the real behaviour and terminating.
            s2.execute(text("SET LOCAL lock_timeout = '2s'"))
            s2.add(_fee_journal(org_id, period_ids[0], line_id, number="RACER-B"))
            with pytest.raises((OperationalError, IntegrityError)) as blocked:
                s2.flush()
            assert _sqlstate(blocked.value) == "55P03", (
                "expected lock_not_available: the second inserter must be BLOCKED "
                f"by the first, got SQLSTATE {_sqlstate(blocked.value)}"
            )
            s2.rollback()
            refused += 1

            s1.commit()
            created.append("RACER-A")

            # And once A is committed, B's retry is refused outright.
            s2.execute(text("SET app.bypass_rls = 'true'"))
            s2.add(_fee_journal(org_id, period_ids[0], line_id, number="RACER-B-RETRY"))
            with pytest.raises(IntegrityError) as violated:
                s2.flush()
            assert _sqlstate(violated.value) == "23505", (
                f"expected unique_violation, got {_sqlstate(violated.value)}"
            )
            assert "uq_journal_entry_bank_fee_source" in str(violated.value), (
                "the refusal must come from the bank-fee identity index, not from "
                "some other constraint that happens to fire"
            )
            s2.rollback()

            assert refused == 1, "both concurrent creates were accepted"
            assert len(created) == 1, f"expected one survivor, got {created}"
        finally:
            s1.close()
            s2.close()
            with engine.begin() as cleanup:
                cleanup.execute(text("SET LOCAL app.bypass_rls = 'true'"))
                cleanup.execute(
                    text("DELETE FROM gl.journal_entry WHERE organization_id = :o"),
                    {"o": org_id},
                )
                cleanup.execute(
                    text("DELETE FROM gl.fiscal_period WHERE organization_id = :o"),
                    {"o": org_id},
                )
                cleanup.execute(
                    text("DELETE FROM gl.fiscal_year WHERE organization_id = :o"),
                    {"o": org_id},
                )
                cleanup.execute(
                    text(
                        "DELETE FROM core_org.organization WHERE organization_id = :o"
                    ),
                    {"o": org_id},
                )


def _fee_line(line_id: uuid.UUID):
    """The minimal statement-line shape `post_bank_fee` reads."""
    return type(
        "_Line",
        (),
        {
            "line_id": line_id,
            "amount": Decimal("-10"),
            "transaction_date": date(2026, 1, 15),
            "description": "Bank charge",
            "reference": "CANARY",
            "line_number": 1,
        },
    )()


class TestAReversalIsNotADuplicate:
    """ERP reversals PRESERVE the original's source identity.

    So a linked reversal of a bank-fee journal carries the same
    `source_document_id`. If the unique index did not exclude
    `is_reversal = true` it would refuse to let anyone reverse a bank fee —
    which is not a safety property, it is a bug: the correcting reversal is
    exactly what the Gate D remediation of the 429 duplicate postings needs.
    """

    def test_a_linked_reversal_of_a_bank_fee_is_allowed(
        self, db: Session, org_id: uuid.UUID
    ) -> None:
        period_id = _fiscal_period_id(db, org_id)
        line_id = uuid.uuid4()

        original = _fee_journal(org_id, period_id, line_id, number="ORIGINAL")
        original.status = JournalStatus.POSTED
        db.add(original)
        db.flush()

        reversal = _fee_journal(org_id, period_id, line_id, number="REVERSAL")
        reversal.is_reversal = True
        reversal.reversed_journal_id = original.journal_entry_id
        reversal.status = JournalStatus.POSTED
        db.add(reversal)
        db.flush()  # must not raise

    def test_two_non_reversal_journals_are_still_refused(
        self, db: Session, org_id: uuid.UUID
    ) -> None:
        """The reversal exemption must not become a hole: only `is_reversal`
        rows are outside the index."""
        period_id = _fiscal_period_id(db, org_id)
        line_id = uuid.uuid4()
        db.add(_fee_journal(org_id, period_id, line_id, number="ONE"))
        db.flush()
        db.add(_fee_journal(org_id, period_id, line_id, number="TWO"))
        with pytest.raises(IntegrityError):
            db.flush()


class TestAFailedPostIsRetried:
    """A failed post reverts the journal to DRAFT while it keeps the typed id.

    `BasePostingAdapter._revert_unposted_journal` does that deliberately. The
    danger is the NEXT invocation: if it only asks "does a row exist?" it
    reports success and skips the line forever. The fee never posts and nothing
    says so.
    """

    def test_a_draft_journal_is_reposted_not_skipped(
        self, db: Session, org_id: uuid.UUID
    ) -> None:
        from app.services.finance.banking.bank_fee_posting import BankFeeState

        period_id = _fiscal_period_id(db, org_id)
        line_id = uuid.uuid4()
        cost = _account(db, org_id, "6080", "Finance Cost")
        bank = _account(db, org_id, "1204", "Bank")

        stranded = _fee_journal(org_id, period_id, line_id, number="FAILED-POST")
        stranded.status = JournalStatus.DRAFT
        db.add(stranded)
        db.flush()
        # Content that still MATCHES the line — the drift case has its own canary.
        _with_lines(db, stranded, cost, bank, Decimal("10"))

        posted: list[uuid.UUID] = []

        def _poster(**kwargs):
            posted.append(kwargs["journal_entry_id"])
            journal = kwargs["db"].get(JournalEntry, kwargs["journal_entry_id"])
            journal.status = JournalStatus.POSTED
            kwargs["db"].flush()
            _post_to_ledger(kwargs["db"], journal, period_id, org_id)
            return SimpleNamespace(
                success=True, message="posted", idempotent_replay=False
            )

        outcome = post_bank_fee(
            db,
            organization_id=org_id,
            line=_fee_line(line_id),
            bank_gl_account_id=bank.account_id,
            finance_cost_account_id=cost.account_id,
            posted_by_user_id=uuid.uuid4(),
            poster=_poster,
        )

        assert outcome.state is BankFeeState.REPOSTED_DRAFT, outcome.message
        assert posted == [stranded.journal_entry_id], (
            "the DRAFT journal must be re-posted, not skipped and not duplicated"
        )
        assert outcome.posted_journal is not None

    def test_a_success_result_without_a_live_effect_is_not_success(
        self, db: Session, org_id: uuid.UUID
    ) -> None:
        from app.services.finance.banking.bank_fee_posting import BankFeeState

        period_id = _fiscal_period_id(db, org_id)
        line_id = uuid.uuid4()
        cost = _account(db, org_id, "6080", "Finance Cost")
        bank = _account(db, org_id, "1204", "Bank")
        stranded = _fee_journal(org_id, period_id, line_id, number="FALSE-SUCCESS")
        stranded.status = JournalStatus.DRAFT
        db.add(stranded)
        db.flush()
        _with_lines(db, stranded, cost, bank, Decimal("10"))

        outcome = post_bank_fee(
            db,
            organization_id=org_id,
            line=_fee_line(line_id),
            bank_gl_account_id=bank.account_id,
            finance_cost_account_id=cost.account_id,
            posted_by_user_id=uuid.uuid4(),
            poster=lambda **_: SimpleNamespace(
                success=True,
                message="posted",
                idempotent_replay=False,
            ),
        )

        assert outcome.state is BankFeeState.FAILED
        assert not outcome.ok
        assert outcome.posted_journal is None

    def test_an_approved_orphan_is_surfaced_not_silently_skipped(
        self, db: Session, org_id: uuid.UUID
    ) -> None:
        """APPROVED-and-unposted is the shape that produced 12,117 rows.

        It must not be auto-posted — that is a Finance disposition — and it must
        not be reported as done either.
        """
        from app.services.finance.banking.bank_fee_posting import BankFeeState

        period_id = _fiscal_period_id(db, org_id)
        line_id = uuid.uuid4()
        db.add(_fee_journal(org_id, period_id, line_id, number="ORPHAN"))
        db.flush()

        outcome = post_bank_fee(
            db,
            organization_id=org_id,
            line=_fee_line(line_id),
            bank_gl_account_id=uuid.uuid4(),
            finance_cost_account_id=uuid.uuid4(),
            posted_by_user_id=uuid.uuid4(),
            poster=lambda **_: pytest.fail("an APPROVED orphan must not be posted"),
        )

        assert outcome.state is BankFeeState.APPROVED_ORPHAN
        assert outcome.needs_attention
        assert not outcome.ok, "an unposted orphan is not a successful outcome"
        assert not outcome.already_present, (
            "an unposted orphan is not an effect that is already present"
        )
        assert outcome.posted_journal is None


class TestACrashBeforeMatchingIsRepairable:
    """Posting and statement matching are two steps, and a crash can land
    between them.

    If the second invocation stops at "a journal exists", the statement line is
    never matched — permanently. The owner therefore returns the CANONICAL
    posted journal even when it creates nothing, so the caller can redo the
    match.
    """

    def test_an_already_posted_line_still_yields_its_journal(
        self, db: Session, org_id: uuid.UUID
    ) -> None:
        from app.services.finance.banking.bank_fee_posting import BankFeeState

        period_id = _fiscal_period_id(db, org_id)
        line_id = uuid.uuid4()
        cost = _account(db, org_id, "6080", "Finance Cost")
        bank = _account(db, org_id, "1204", "Bank")
        journal = _fee_journal(org_id, period_id, line_id, number="POSTED-NO-MATCH")
        journal.status = JournalStatus.POSTED
        db.add(journal)
        db.flush()
        _with_lines(db, journal, cost, bank, Decimal("10"))
        _post_to_ledger(db, journal, period_id, org_id)

        outcome = post_bank_fee(
            db,
            organization_id=org_id,
            line=_fee_line(line_id),
            bank_gl_account_id=bank.account_id,
            finance_cost_account_id=cost.account_id,
            posted_by_user_id=uuid.uuid4(),
            poster=lambda **_: pytest.fail("must not post over a live effect"),
        )

        assert outcome.state is BankFeeState.ALREADY_POSTED
        assert outcome.already_present
        assert outcome.posted_journal is not None, (
            "the caller needs the canonical journal to repair a missing match"
        )
        assert outcome.posted_journal.journal_number == "POSTED-NO-MATCH"


class TestTheCollisionHandler:
    """The `IntegrityError` handler in `post_bank_fee`, exercised directly.

    **This is not a concurrency canary.** It drives the handler by simulating
    the window with a stub; the genuine two-session proof is
    `TestTwoSessionsRaceForOneLine` below. The earlier name claimed concurrency
    it did not exercise, and a test that overstates what it proves is a false
    success of its own.

    An earlier version of this test committed the winner BEFORE calling
    `post_bank_fee`, so the loser returned at pre-check 1 and the handler was
    never reached. It asserted a true thing about the wrong code path and was a
    duplicate of `test_an_already_posted_line_still_yields_its_journal` wearing
    a race's name.

    A genuine two-thread race against a unique index is not deterministic: the
    second inserter BLOCKS until the first transaction resolves, so the outcome
    depends on commit timing. What IS deterministic — and is what the handler
    actually has to survive — is the interleaving where the pre-checks ran
    before the winner committed and the create then collides.

    So the winner is committed, and the loser's PRE-CHECKS are stubbed to return
    nothing, reproducing exactly that window. Everything after the pre-checks is
    the real code path: the real INSERT, the real index, the real
    `IntegrityError`, the real re-read.
    """

    def test_the_handler_returns_the_winners_journal(self, engine, monkeypatch) -> None:
        from sqlalchemy.orm import sessionmaker as _sessionmaker

        from app.models.finance.core_org.organization import Organization
        from app.services.finance.banking import bank_fee_posting
        from app.services.finance.banking.bank_fee_posting import BankFeeState

        Session_ = _sessionmaker(bind=engine)
        org_id = uuid.uuid4()
        line_id = uuid.uuid4()
        setup = Session_()
        try:
            setup.execute(text("SET app.bypass_rls = 'true'"))
            setup.add(
                Organization(
                    organization_id=org_id,
                    organization_code=f"RACE-{uuid.uuid4().hex[:8].upper()}",
                    legal_name="Service Race Canary",
                    functional_currency_code="NGN",
                    presentation_currency_code="NGN",
                    fiscal_year_end_month=12,
                    fiscal_year_end_day=31,
                    is_active=True,
                )
            )
            setup.flush()
            period_id = _fiscal_period_id(setup, org_id)
            cost = _account(setup, org_id, "6080", "Finance Cost")
            bank = _account(setup, org_id, "1204", "Bank")
            winner = _fee_journal(org_id, period_id, line_id, number="WINNER")
            winner.status = JournalStatus.POSTED
            setup.add(winner)
            setup.flush()
            _with_lines(setup, winner, cost, bank, Decimal("10"))
            _post_to_ledger(setup, winner, period_id, org_id)
            setup.commit()

            loser = Session_()
            try:
                loser.execute(text("SET app.bypass_rls = 'true'"))

                # The race window, modelled exactly: the PRE-CHECK sees nothing
                # (the winner had not committed when it ran), and the handler's
                # RE-READ after the violation sees the winner (it has by then).
                # A stub that always returned nothing would break the re-read
                # too, and the test would assert against its own scaffolding.
                real_find = bank_fee_posting.find_fee_journals
                calls = {"n": 0}

                def _first_call_sees_nothing(*args, **kwargs):
                    calls["n"] += 1
                    if calls["n"] == 1:
                        return []
                    return real_find(*args, **kwargs)

                monkeypatch.setattr(
                    bank_fee_posting, "find_fee_journals", _first_call_sees_nothing
                )
                monkeypatch.setattr(
                    bank_fee_posting,
                    "find_existing_posting_effect",
                    lambda *a, **k: None,
                )

                outcome = bank_fee_posting.post_bank_fee(
                    loser,
                    organization_id=org_id,
                    line=_fee_line(line_id),
                    bank_gl_account_id=bank.account_id,
                    finance_cost_account_id=cost.account_id,
                    posted_by_user_id=uuid.uuid4(),
                    poster=lambda **_: pytest.fail(
                        "the create must fail before anything is posted"
                    ),
                )

                assert outcome.state is BankFeeState.LOST_RACE, (
                    f"expected the IntegrityError handler, got {outcome.state}: "
                    f"{outcome.message}"
                )
                assert not outcome.created
                assert outcome.journal is not None, (
                    "the loser must re-read and report the winner's journal"
                )
                assert outcome.journal.journal_number == "WINNER"
                assert calls["n"] >= 2, (
                    "the handler must RE-READ after the violation; one lookup "
                    "means the collision path never ran"
                )
            finally:
                loser.rollback()
                loser.close()
        finally:
            setup.close()
            with engine.begin() as cleanup:
                cleanup.execute(text("SET LOCAL app.bypass_rls = 'true'"))
                for stmt in (
                    "DELETE FROM gl.posted_ledger_line WHERE organization_id = :o",
                    "DELETE FROM gl.journal_entry_line WHERE journal_entry_id IN "
                    "(SELECT journal_entry_id FROM gl.journal_entry WHERE organization_id = :o)",
                    "UPDATE gl.journal_entry SET posting_batch_id = NULL WHERE organization_id = :o",
                    "DELETE FROM gl.posting_batch WHERE organization_id = :o",
                    "DELETE FROM gl.journal_entry WHERE organization_id = :o",
                    "DELETE FROM gl.account WHERE organization_id = :o",
                    "DELETE FROM gl.account_category WHERE organization_id = :o",
                    "DELETE FROM gl.fiscal_period WHERE organization_id = :o",
                    "DELETE FROM gl.fiscal_year WHERE organization_id = :o",
                    # Creating a journal allocates a document number, which
                    # leaves a numbering_sequence row referencing the org.
                    "DELETE FROM core_config.numbering_sequence WHERE organization_id = :o",
                    "DELETE FROM core_org.organization WHERE organization_id = :o",
                ):
                    cleanup.execute(text(stmt), {"o": org_id})


class TestAReversedFeeIsNotReportedAsPosted:
    """The reversal lookup ambiguity, pinned.

    Because the index exempts reversals, a reversed bank fee leaves TWO journals
    carrying one `line_id`. An unfiltered `.first()` off an unordered query could
    return the REVERSAL — which is POSTED — and report "already posted" at
    exactly the moment no live effect exists.
    """

    def test_the_lookup_ignores_the_reversal(
        self, db: Session, org_id: uuid.UUID
    ) -> None:
        from app.services.finance.banking.bank_fee_posting import find_fee_journals

        period_id = _fiscal_period_id(db, org_id)
        line_id = uuid.uuid4()

        original = _fee_journal(org_id, period_id, line_id, number="ORIGINAL")
        original.status = JournalStatus.REVERSED
        db.add(original)
        db.flush()

        reversal = _fee_journal(org_id, period_id, line_id, number="THE-REVERSAL")
        reversal.is_reversal = True
        reversal.reversed_journal_id = original.journal_entry_id
        reversal.status = JournalStatus.POSTED
        db.add(reversal)
        db.flush()

        found = find_fee_journals(db, organization_id=org_id, line_id=line_id)
        assert [j.journal_number for j in found] == ["ORIGINAL"], (
            "the reversal must never be mistaken for the primary journal"
        )

    def test_a_reversed_fee_reports_not_live_not_already_posted(
        self, db: Session, org_id: uuid.UUID
    ) -> None:
        from app.services.finance.banking.bank_fee_posting import BankFeeState

        period_id = _fiscal_period_id(db, org_id)
        line_id = uuid.uuid4()

        cost = _account(db, org_id, "6080", "Finance Cost")
        bank = _account(db, org_id, "1204", "Bank")
        original = _fee_journal(org_id, period_id, line_id, number="WAS-POSTED")
        original.status = JournalStatus.POSTED
        db.add(original)
        db.flush()
        _with_lines(db, original, cost, bank, Decimal("10"))
        _post_to_ledger(db, original, period_id, org_id)

        reversal = _fee_journal(org_id, period_id, line_id, number="REVERSAL")
        reversal.is_reversal = True
        reversal.reversed_journal_id = original.journal_entry_id
        reversal.status = JournalStatus.POSTED
        db.add(reversal)
        db.flush()
        original.reversal_journal_id = reversal.journal_entry_id
        db.flush()

        outcome = post_bank_fee(
            db,
            organization_id=org_id,
            line=_fee_line(line_id),
            bank_gl_account_id=uuid.uuid4(),
            finance_cost_account_id=uuid.uuid4(),
            posted_by_user_id=uuid.uuid4(),
            poster=lambda **_: pytest.fail("must not post"),
        )
        assert outcome.state is BankFeeState.NOT_LIVE
        assert not outcome.ok, "a reversed effect is not a successful outcome"
        assert not outcome.already_present, (
            "a reversed fee has no live effect; calling it present hides that"
        )


class TestABatchRowIsNotAPostedEffect:
    """PENDING, PROCESSING, FAILED and PARTIALLY_POSTED batches all exist
    without a posted effect. Treating any of them as "already posted" would
    strand the fee permanently and say nothing."""

    @pytest.mark.parametrize(
        "status", ["PENDING", "PROCESSING", "FAILED", "PARTIALLY_POSTED"]
    )
    def test_an_unposted_batch_does_not_block_creation(
        self, db: Session, org_id: uuid.UUID, status: str
    ) -> None:
        from app.models.finance.gl.posting_batch import BatchStatus
        from app.services.finance.banking.bank_fee_posting import (
            existing_posting_batch,
        )

        line_id = uuid.uuid4()
        batch = _posting_batch(
            db,
            org_id,
            _fiscal_period_id(db, org_id),
            fee_idempotency_key(org_id, line_id),
        )
        batch.status = BatchStatus[status]
        db.flush()

        assert (
            existing_posting_batch(db, organization_id=org_id, line_id=line_id) is None
        ), f"a {status} batch is not a posted effect"

    def test_a_posted_batch_with_a_live_effect_blocks(
        self, db: Session, org_id: uuid.UUID
    ) -> None:
        """A batch ROW is not enough — it must lead to a live effect."""
        from app.services.finance.banking.bank_fee_posting import (
            existing_posting_batch,
        )

        period_id = _fiscal_period_id(db, org_id)
        line_id = uuid.uuid4()
        cost = _account(db, org_id, "6080", "Finance Cost")
        bank = _account(db, org_id, "1204", "Bank")
        _legacy_live_batch(db, org_id, period_id, line_id, cost, bank)

        assert (
            existing_posting_batch(db, organization_id=org_id, line_id=line_id)
            is not None
        )

    def test_a_posted_batch_with_no_ledger_rows_does_not_block(
        self, db: Session, org_id: uuid.UUID
    ) -> None:
        from app.services.finance.banking.bank_fee_posting import (
            existing_posting_batch,
        )

        line_id = uuid.uuid4()
        _posting_batch(
            db,
            org_id,
            _fiscal_period_id(db, org_id),
            fee_idempotency_key(org_id, line_id),
        )
        assert (
            existing_posting_batch(db, organization_id=org_id, line_id=line_id) is None
        ), "a batch that wrote no ledger rows is not a live effect"


class TestStaleDraftContentIsNotReposted:
    """Re-posting a DRAFT is a recovery, not a rewrite.

    If the DRAFT no longer says what the statement line says, posting it puts
    stale content into the ledger under the line's identity — and the identity
    is what makes it hard to correct afterwards.
    """

    def test_a_draft_whose_amount_drifted_is_refused(
        self, db: Session, org_id: uuid.UUID
    ) -> None:
        from app.services.finance.banking.bank_fee_posting import BankFeeState

        period_id = _fiscal_period_id(db, org_id)
        line_id = uuid.uuid4()
        cost = _account(db, org_id, "6080", "Finance Cost")
        bank = _account(db, org_id, "1204", "Bank")
        stale = _fee_journal(org_id, period_id, line_id, number="STALE-DRAFT")
        stale.status = JournalStatus.DRAFT
        db.add(stale)
        db.flush()

        # The statement line says 10; this DRAFT says 99.
        _with_lines(db, stale, cost, bank, Decimal("99"))

        outcome = post_bank_fee(
            db,
            organization_id=org_id,
            line=_fee_line(line_id),
            bank_gl_account_id=uuid.uuid4(),
            finance_cost_account_id=uuid.uuid4(),
            posted_by_user_id=uuid.uuid4(),
            poster=lambda **_: pytest.fail("stale content must not be posted"),
        )

        assert outcome.state is BankFeeState.CONTENT_MISMATCH
        assert outcome.needs_attention
        assert "99" in outcome.message


class TestMultiplePrimariesAreNeverResolvedByGuessing:
    """Two primaries for one line must report AMBIGUOUS, never a guess.

    This state is only reachable when the unique index is ABSENT — before the
    migration is deployed, or if someone drops it. The first version of this
    test tried to reproduce that faithfully by disabling triggers and dropping
    the index inside the test transaction. That was wrong twice over: it needs
    privileges the test role should not need, and a test that mutates schema to
    reach a branch is testing the schema, not the branch.

    The decision is what matters here, and it lives in `post_bank_fee`. The
    index is already proven by `TestTheDatabaseBoundary`, so this stubs the
    lookup to present the multiplicity and asserts the decision — no DDL.
    """

    def test_two_primaries_report_ambiguous(
        self, db: Session, org_id: uuid.UUID, monkeypatch
    ) -> None:
        from app.services.finance.banking import bank_fee_posting
        from app.services.finance.banking.bank_fee_posting import BankFeeState

        period_id = _fiscal_period_id(db, org_id)
        line_id = uuid.uuid4()
        one = _fee_journal(org_id, period_id, line_id, number="ONE")
        db.add(one)
        db.flush()

        # A second primary that the index would refuse — presented to the
        # decision the way an un-migrated database would present it.
        two = _fee_journal(org_id, period_id, uuid.uuid4(), number="TWO")
        db.add(two)
        db.flush()

        monkeypatch.setattr(
            bank_fee_posting, "find_fee_journals", lambda *a, **k: [one, two]
        )

        outcome = bank_fee_posting.post_bank_fee(
            db,
            organization_id=org_id,
            line=_fee_line(line_id),
            bank_gl_account_id=uuid.uuid4(),
            finance_cost_account_id=uuid.uuid4(),
            posted_by_user_id=uuid.uuid4(),
            poster=lambda **_: pytest.fail("must not post while ambiguous"),
        )

        assert outcome.state is BankFeeState.AMBIGUOUS
        assert outcome.needs_attention
        assert not outcome.ok, "ambiguity is an error, not a quiet outcome"
        assert "ONE" in outcome.message and "TWO" in outcome.message, (
            "both journals must be named so the operator can resolve it"
        )


class TestTwoSessionsRaceForOneLine:
    """The directed two-session proof, with real concurrency.

    Two independent connections both call `post_bank_fee` for one statement
    line, overlapping in time. PostgreSQL BLOCKS the second inserter on the
    unique index until the first transaction resolves, so this needs two threads
    — a single-threaded test would deadlock itself, which is why the earlier
    version simulated the window instead.

    The loser must come back with the winner's journal and having created
    nothing. Nothing is stubbed: real sessions, real index, real block, real
    `IntegrityError`.
    """

    def test_exactly_one_of_two_concurrent_callers_creates(
        self, engine, monkeypatch
    ) -> None:
        import threading

        from sqlalchemy.orm import sessionmaker as _sessionmaker

        from app.models.finance.core_org.organization import Organization
        from app.models.finance.gl.posted_ledger_line import PostedLedgerLine
        from app.models.finance.gl.posting_batch import PostingBatch
        from app.services.finance.banking import bank_fee_posting
        from app.services.finance.banking.bank_fee_posting import BankFeeState

        Session_ = _sessionmaker(bind=engine)
        org_id = uuid.uuid4()
        line_id = uuid.uuid4()

        setup = Session_()
        try:
            setup.execute(text("SET app.bypass_rls = 'true'"))
            setup.add(
                Organization(
                    organization_id=org_id,
                    organization_code=f"RACE2-{uuid.uuid4().hex[:8].upper()}",
                    legal_name="Two Session Race",
                    functional_currency_code="NGN",
                    presentation_currency_code="NGN",
                    fiscal_year_end_month=12,
                    fiscal_year_end_day=31,
                    is_active=True,
                )
            )
            setup.flush()
            period_id = _fiscal_period_id(setup, org_id)
            cost = _account(setup, org_id, "6080", "Finance Cost")
            bank = _account(setup, org_id, "1204", "Bank")
            cost_id, bank_id = cost.account_id, bank.account_id

            # Pre-allocate the JOURNAL numbering sequence. Without it BOTH
            # callers try to create it and one loses on `uq_sequence_type`
            # BEFORE reaching the bank-fee index — a real race, but a different
            # one, and only reachable for an organization that has never posted
            # a journal. Production orgs have had this row for thousands of
            # journals; leaving it out would make this test assert the wrong
            # collision.
            from app.models.finance.core_config.numbering_sequence import (
                NumberingSequence,
                SequenceType,
            )

            setup.add(
                NumberingSequence(
                    sequence_id=uuid.uuid4(),
                    organization_id=org_id,
                    sequence_type=SequenceType.JOURNAL,
                    prefix="JE",
                    current_number=0,
                )
            )
            setup.commit()
        finally:
            setup.close()

        results: dict[str, object] = {}
        both_prechecks_empty = threading.Barrier(2, timeout=30)
        real_effect_lookup = bank_fee_posting.find_existing_posting_effect

        def _synchronize_after_both_prechecks(*args, **kwargs):
            effect = real_effect_lookup(*args, **kwargs)
            if effect is None:
                # This is pre-check 2. Both callers can return only after each
                # has already observed no typed journal and no legacy effect.
                both_prechecks_empty.wait()
            return effect

        monkeypatch.setattr(
            bank_fee_posting,
            "find_existing_posting_effect",
            _synchronize_after_both_prechecks,
        )

        def _really_post(**kwargs):
            """Post for real, rather than claiming to.

            A stub that returns `success=True` while leaving the journal
            unposted makes the WINNER look like an orphan to the loser — which
            is correct classification of an incorrect fixture. The winner has to
            end up genuinely live for the loser's outcome to mean anything.
            """
            session = kwargs["db"]
            journal = session.get(JournalEntry, kwargs["journal_entry_id"])
            journal.status = JournalStatus.POSTED
            session.flush()
            _post_to_ledger(session, journal, period_id, org_id)
            return SimpleNamespace(
                success=True, message="posted", idempotent_replay=False
            )

        def _attempt(name: str) -> None:
            session = Session_()
            try:
                session.execute(text("SET app.bypass_rls = 'true'"))
                results[name] = bank_fee_posting.post_bank_fee(
                    session,
                    organization_id=org_id,
                    line=_fee_line(line_id),
                    bank_gl_account_id=bank_id,
                    finance_cost_account_id=cost_id,
                    posted_by_user_id=uuid.uuid4(),
                    poster=_really_post,
                )
                session.commit()
            except BaseException as exc:  # noqa: BLE001 - recorded, asserted below
                session.rollback()
                results[name] = exc
            finally:
                session.close()

        threads = [
            threading.Thread(target=_attempt, args=(name,), daemon=True)
            for name in ("A", "B")
        ]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)
                assert not thread.is_alive(), "a caller blocked indefinitely"

            outcomes = [results.get("A"), results.get("B")]
            for outcome in outcomes:
                assert not isinstance(outcome, BaseException), (
                    f"a concurrent caller raised instead of resolving: {outcome!r}"
                )

            states = {o.state for o in outcomes}  # type: ignore[union-attr]
            assert states == {BankFeeState.CREATED, BankFeeState.LOST_RACE}, (
                "both callers passed both prechecks, so the only admissible "
                f"states are one create and one index loser; got {states}"
            )

            check = Session_()
            try:
                check.execute(text("SET app.bypass_rls = 'true'"))
                surviving = check.scalars(
                    select(JournalEntry).where(
                        JournalEntry.organization_id == org_id,
                        JournalEntry.source_document_id == line_id,
                        JournalEntry.is_reversal.is_(False),
                    )
                ).all()
                assert len(surviving) == 1, (
                    f"{len(surviving)} primary journals survived a race that must "
                    f"leave exactly one"
                )
                live_rows = check.scalars(
                    select(PostedLedgerLine).where(
                        PostedLedgerLine.organization_id == org_id,
                        PostedLedgerLine.journal_entry_id
                        == surviving[0].journal_entry_id,
                    )
                ).all()
                assert len(live_rows) == 2, (
                    "one two-line journal effect, not merely one identity row, "
                    "must survive the collision"
                )
                assert len({row.posting_batch_id for row in live_rows}) == 1
                batch_count = check.scalar(
                    select(func.count(PostingBatch.batch_id)).where(
                        PostingBatch.organization_id == org_id,
                        PostingBatch.idempotency_key
                        == fee_idempotency_key(org_id, line_id),
                    )
                )
                assert batch_count == 1, "the race produced more than one effect batch"
            finally:
                check.close()
        finally:
            with engine.begin() as cleanup:
                cleanup.execute(text("SET LOCAL app.bypass_rls = 'true'"))
                for stmt in (
                    "DELETE FROM gl.posted_ledger_line WHERE organization_id = :o",
                    "DELETE FROM gl.journal_entry_line WHERE journal_entry_id IN "
                    "(SELECT journal_entry_id FROM gl.journal_entry WHERE organization_id = :o)",
                    "UPDATE gl.journal_entry SET posting_batch_id = NULL WHERE organization_id = :o",
                    "DELETE FROM gl.posting_batch WHERE organization_id = :o",
                    "DELETE FROM gl.journal_entry WHERE organization_id = :o",
                    "DELETE FROM gl.account WHERE organization_id = :o",
                    "DELETE FROM gl.account_category WHERE organization_id = :o",
                    "DELETE FROM gl.fiscal_period WHERE organization_id = :o",
                    "DELETE FROM gl.fiscal_year WHERE organization_id = :o",
                    # Creating a journal allocates a document number, which
                    # leaves a numbering_sequence row referencing the org.
                    "DELETE FROM core_config.numbering_sequence WHERE organization_id = :o",
                    "DELETE FROM core_org.organization WHERE organization_id = :o",
                ):
                    cleanup.execute(text(stmt), {"o": org_id})


class TestAnUnrelatedIntegrityErrorIsNotALostRace:
    """The handler must classify the failure, not assume it."""

    def test_a_named_non_identity_violation_escapes_the_outer_handler(
        self, db: Session, org_id: uuid.UUID, monkeypatch
    ) -> None:
        """Drive the real outer handler with a faithful named DB violation."""
        from app.services.finance.banking import bank_fee_posting
        from app.services.finance.posting.base import BasePostingAdapter

        _fiscal_period_id(db, org_id)
        monkeypatch.setattr(bank_fee_posting, "find_fee_journals", lambda *a, **k: [])
        monkeypatch.setattr(
            bank_fee_posting, "find_existing_posting_effect", lambda *a, **k: None
        )

        unrelated = IntegrityError(
            "INSERT INTO gl.journal_entry ...",
            {},
            SimpleNamespace(
                diag=SimpleNamespace(constraint_name="uq_journal_number")
            ),
        )

        def _raise_unrelated(*_args, **_kwargs):
            raise unrelated

        monkeypatch.setattr(
            BasePostingAdapter,
            "create_and_approve_journal",
            _raise_unrelated,
        )

        with pytest.raises(IntegrityError) as raised:
            bank_fee_posting.post_bank_fee(
                db,
                organization_id=org_id,
                line=_fee_line(uuid.uuid4()),
                bank_gl_account_id=uuid.uuid4(),
                finance_cost_account_id=uuid.uuid4(),
                posted_by_user_id=uuid.uuid4(),
                poster=lambda **_: SimpleNamespace(
                    success=True, message="", idempotent_replay=False
                ),
            )

        assert raised.value is unrelated, (
            "the outer handler must propagate the original non-identity error"
        )

    def test_the_predicate_only_matches_the_identity_index(self) -> None:
        from app.services.finance.banking.bank_fee_posting import (
            _is_fee_identity_violation,
        )

        identity = IntegrityError(
            "stmt",
            {},
            Exception(
                "duplicate key value violates unique constraint "
                '"uq_journal_entry_bank_fee_source"'
            ),
        )
        other = IntegrityError(
            "stmt",
            {},
            Exception(
                'duplicate key value violates unique constraint "uq_journal_number"'
            ),
        )
        assert _is_fee_identity_violation(identity)
        assert not _is_fee_identity_violation(other)


class TestAReplayIsNeverASuccessfulPost:
    """`success=True` with `idempotent_replay=True` means NOTHING was posted."""

    def test_a_replayed_draft_repost_is_not_reported_as_posted(
        self, db: Session, org_id: uuid.UUID
    ) -> None:
        from app.services.finance.banking.bank_fee_posting import BankFeeState

        period_id = _fiscal_period_id(db, org_id)
        line_id = uuid.uuid4()
        cost = _account(db, org_id, "6080", "Finance Cost")
        bank = _account(db, org_id, "1204", "Bank")

        stranded = _fee_journal(org_id, period_id, line_id, number="REPLAYED")
        stranded.status = JournalStatus.DRAFT
        db.add(stranded)
        db.flush()
        _with_lines(db, stranded, cost, bank, Decimal("10"))

        outcome = post_bank_fee(
            db,
            organization_id=org_id,
            line=_fee_line(line_id),
            bank_gl_account_id=bank.account_id,
            finance_cost_account_id=cost.account_id,
            posted_by_user_id=uuid.uuid4(),
            poster=lambda **_: SimpleNamespace(
                success=True, message="Already posted", idempotent_replay=True
            ),
        )

        assert outcome.state is BankFeeState.REPLAY_LEFT_UNPOSTED, (
            "a replay posted nothing; reporting REPOSTED_DRAFT strands the DRAFT"
        )
        assert outcome.needs_attention
        assert not outcome.ok


class TestAPostedBatchIsNotALiveEffect:
    """Reversing a journal leaves its original batch POSTED.

    The batch records that a posting happened, not that its effect stands.
    """

    def test_a_batch_whose_journal_was_reversed_does_not_block(
        self, db: Session, org_id: uuid.UUID
    ) -> None:
        from app.services.finance.banking.bank_fee_posting import (
            existing_posting_batch,
        )
        from app.models.finance.gl.posted_ledger_line import PostedLedgerLine

        period_id = _fiscal_period_id(db, org_id)
        line_id = uuid.uuid4()
        cost = _account(db, org_id, "6080", "Finance Cost")
        bank = _account(db, org_id, "1204", "Bank")

        journal = _fee_journal(org_id, period_id, line_id, number="LEGACY-REVERSED")
        journal.status = JournalStatus.POSTED
        journal.source_document_id = None  # legacy: identity only in the key
        db.add(journal)
        db.flush()
        _with_lines(db, journal, cost, bank, Decimal("10"))
        _post_to_ledger(db, journal, period_id, org_id)

        # Point the batch at this line's key, then reverse the journal.
        batch_id = db.scalar(
            select(PostedLedgerLine.posting_batch_id).where(
                PostedLedgerLine.journal_entry_id == journal.journal_entry_id
            )
        )
        db.execute(
            text(
                "UPDATE gl.posting_batch SET idempotency_key = :k WHERE batch_id = :b"
            ),
            {"k": fee_idempotency_key(org_id, line_id), "b": batch_id},
        )
        reversal = _fee_journal(org_id, period_id, uuid.uuid4(), number="THE-REVERSAL")
        reversal.is_reversal = True
        reversal.status = JournalStatus.POSTED
        db.add(reversal)
        db.flush()
        journal.reversal_journal_id = reversal.journal_entry_id
        db.flush()

        assert (
            existing_posting_batch(db, organization_id=org_id, line_id=line_id) is None
        ), "a POSTED batch whose journal was reversed is not a live effect"


class TestContentComparisonChecksAccounts:
    """Matching totals on the WRONG accounts is not matching content."""

    def test_right_amount_wrong_accounts_is_refused(
        self, db: Session, org_id: uuid.UUID
    ) -> None:
        from app.services.finance.banking.bank_fee_posting import BankFeeState

        period_id = _fiscal_period_id(db, org_id)
        line_id = uuid.uuid4()
        cost = _account(db, org_id, "6080", "Finance Cost")
        bank = _account(db, org_id, "1204", "Bank")
        wrong = _account(db, org_id, "9999", "Not The Bank")

        stale = _fee_journal(org_id, period_id, line_id, number="WRONG-ACCOUNTS")
        stale.status = JournalStatus.DRAFT
        db.add(stale)
        db.flush()
        # Correct amount and direction, wrong credit account.
        _with_lines(db, stale, cost, wrong, Decimal("10"))

        outcome = post_bank_fee(
            db,
            organization_id=org_id,
            line=_fee_line(line_id),
            bank_gl_account_id=bank.account_id,
            finance_cost_account_id=cost.account_id,
            posted_by_user_id=uuid.uuid4(),
            poster=lambda **_: pytest.fail("a wrong-account journal must not post"),
        )
        assert outcome.state is BankFeeState.CONTENT_MISMATCH
        assert "bank account" in outcome.message

    @pytest.mark.parametrize("journal_number", ["JE202603-0227", "JE202603-0230"])
    def test_a_finance_disposition_journal_number_never_waives_accounts(
        self, db: Session, org_id: uuid.UUID, journal_number: str
    ) -> None:
        """Journal numbers repeat by tenant and are never runtime exemptions."""
        from app.services.finance.banking.bank_fee_posting import BankFeeState

        period_id = _fiscal_period_id(db, org_id)
        line_id = uuid.uuid4()
        cost = _account(db, org_id, "6080", "Finance Cost")
        bank = _account(db, org_id, "1204", "Bank")
        wrong = _account(db, org_id, "9999", "Not The Bank")
        stale = _fee_journal(org_id, period_id, line_id, number=journal_number)
        stale.status = JournalStatus.DRAFT
        db.add(stale)
        db.flush()
        _with_lines(db, stale, cost, wrong, Decimal("10"))

        outcome = post_bank_fee(
            db,
            organization_id=org_id,
            line=_fee_line(line_id),
            bank_gl_account_id=bank.account_id,
            finance_cost_account_id=cost.account_id,
            posted_by_user_id=uuid.uuid4(),
            poster=lambda **_: pytest.fail("a journal number must not waive accounts"),
        )

        assert outcome.state is BankFeeState.CONTENT_MISMATCH
        assert not outcome.ok


class TestPostedEffectEquivalence:
    """Identity and liveness are necessary, but never economic equivalence."""

    def test_a_typed_posted_journal_on_the_wrong_account_is_not_success(
        self, db: Session, org_id: uuid.UUID
    ) -> None:
        from app.services.finance.banking.bank_fee_posting import BankFeeState

        period_id = _fiscal_period_id(db, org_id)
        line_id = uuid.uuid4()
        cost = _account(db, org_id, "6080", "Finance Cost")
        bank = _account(db, org_id, "1204", "Bank")
        wrong = _account(db, org_id, "9999", "Not The Bank")
        journal = _fee_journal(org_id, period_id, line_id, number="POSTED-WRONG")
        journal.status = JournalStatus.POSTED
        db.add(journal)
        db.flush()
        _with_lines(db, journal, cost, wrong, Decimal("10"))
        _post_to_ledger(db, journal, period_id, org_id)

        outcome = post_bank_fee(
            db,
            organization_id=org_id,
            line=_fee_line(line_id),
            bank_gl_account_id=bank.account_id,
            finance_cost_account_id=cost.account_id,
            posted_by_user_id=uuid.uuid4(),
            poster=lambda **_: pytest.fail("an existing POSTED row must not post"),
        )

        assert outcome.state is BankFeeState.CONTENT_MISMATCH
        assert outcome.needs_attention
        assert not outcome.ok

    def test_a_typed_posted_journal_for_the_wrong_source_date_is_not_success(
        self, db: Session, org_id: uuid.UUID
    ) -> None:
        from app.services.finance.banking.bank_fee_posting import BankFeeState

        period_id = _fiscal_period_id(db, org_id)
        line_id = uuid.uuid4()
        cost = _account(db, org_id, "6080", "Finance Cost")
        bank = _account(db, org_id, "1204", "Bank")
        journal = _fee_journal(org_id, period_id, line_id, number="POSTED-WRONG-DATE")
        journal.entry_date = date(2026, 1, 14)
        journal.posting_date = date(2026, 1, 14)
        journal.status = JournalStatus.POSTED
        db.add(journal)
        db.flush()
        _with_lines(db, journal, cost, bank, Decimal("10"))
        _post_to_ledger(db, journal, period_id, org_id)

        outcome = post_bank_fee(
            db,
            organization_id=org_id,
            line=_fee_line(line_id),
            bank_gl_account_id=bank.account_id,
            finance_cost_account_id=cost.account_id,
            posted_by_user_id=uuid.uuid4(),
            poster=lambda **_: pytest.fail("an existing POSTED row must not post"),
        )

        assert outcome.state is BankFeeState.CONTENT_MISMATCH
        assert "line date" in outcome.message
        assert not outcome.ok

    def test_a_typed_posted_journal_in_the_wrong_bank_currency_is_not_success(
        self, db: Session, org_id: uuid.UUID
    ) -> None:
        from app.models.finance.banking.bank_account import (
            BankAccount,
            BankAccountType,
        )
        from app.services.finance.banking.bank_fee_posting import BankFeeState

        period_id = _fiscal_period_id(db, org_id)
        line_id = uuid.uuid4()
        cost = _account(db, org_id, "6080", "Finance Cost")
        bank = _account(db, org_id, "1204", "Bank")
        db.add(
            BankAccount(
                organization_id=org_id,
                bank_name="Canary Bank",
                account_number=uuid.uuid4().hex,
                account_name="USD Canary",
                account_type=BankAccountType.checking,
                currency_code="USD",
                gl_account_id=bank.account_id,
            )
        )
        journal = _fee_journal(org_id, period_id, line_id, number="POSTED-NGN")
        journal.status = JournalStatus.POSTED
        db.add(journal)
        db.flush()
        _with_lines(db, journal, cost, bank, Decimal("10"))
        _post_to_ledger(db, journal, period_id, org_id)

        outcome = post_bank_fee(
            db,
            organization_id=org_id,
            line=_fee_line(line_id),
            bank_gl_account_id=bank.account_id,
            finance_cost_account_id=cost.account_id,
            posted_by_user_id=uuid.uuid4(),
            poster=lambda **_: pytest.fail("an existing POSTED row must not post"),
        )

        assert outcome.state is BankFeeState.CONTENT_MISMATCH
        assert "bank account currency is USD" in outcome.message
        assert not outcome.ok

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("credit_amount", Decimal("9")),
            ("original_currency_code", "USD"),
            ("posting_date", date(2026, 2, 1)),
        ],
    )
    def test_posted_ledger_drift_is_not_success(
        self,
        db: Session,
        org_id: uuid.UUID,
        field: str,
        value: object,
    ) -> None:
        from app.models.finance.gl.posted_ledger_line import PostedLedgerLine
        from app.services.finance.banking.bank_fee_posting import BankFeeState

        period_id = _fiscal_period_id(db, org_id)
        line_id = uuid.uuid4()
        cost = _account(db, org_id, "6080", "Finance Cost")
        bank = _account(db, org_id, "1204", "Bank")
        journal = _fee_journal(org_id, period_id, line_id, number=f"DRIFT-{field}")
        journal.status = JournalStatus.POSTED
        db.add(journal)
        db.flush()
        _with_lines(db, journal, cost, bank, Decimal("10"))
        _post_to_ledger(db, journal, period_id, org_id)
        credit = db.scalars(
            select(PostedLedgerLine).where(
                PostedLedgerLine.journal_entry_id == journal.journal_entry_id,
                PostedLedgerLine.credit_amount > 0,
            )
        ).one()
        setattr(credit, field, value)
        db.flush()

        outcome = post_bank_fee(
            db,
            organization_id=org_id,
            line=_fee_line(line_id),
            bank_gl_account_id=bank.account_id,
            finance_cost_account_id=cost.account_id,
            posted_by_user_id=uuid.uuid4(),
            poster=lambda **_: pytest.fail("drift must not cause a new post"),
        )

        assert outcome.state is BankFeeState.CONTENT_MISMATCH
        assert not outcome.ok

    def test_a_legacy_batch_with_the_wrong_effect_is_not_success(
        self, db: Session, org_id: uuid.UUID
    ) -> None:
        from app.services.finance.banking.bank_fee_posting import BankFeeState

        period_id = _fiscal_period_id(db, org_id)
        line_id = uuid.uuid4()
        cost = _account(db, org_id, "6080", "Finance Cost")
        bank = _account(db, org_id, "1204", "Bank")
        wrong = _account(db, org_id, "9999", "Not The Bank")
        _legacy_live_batch(db, org_id, period_id, line_id, cost, wrong)

        outcome = post_bank_fee(
            db,
            organization_id=org_id,
            line=_fee_line(line_id),
            bank_gl_account_id=bank.account_id,
            finance_cost_account_id=cost.account_id,
            posted_by_user_id=uuid.uuid4(),
            poster=lambda **_: pytest.fail("a legacy effect must not be duplicated"),
        )

        assert outcome.state is BankFeeState.CONTENT_MISMATCH
        assert outcome.needs_attention
        assert not outcome.ok
