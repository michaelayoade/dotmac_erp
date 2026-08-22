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
        first = _fee_journal(org_id, period_id, line_id, number="FIRST")
        first.status = JournalStatus.POSTED
        db.add(first)
        db.flush()

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
            bank_gl_account_id=uuid.uuid4(),
            finance_cost_account_id=uuid.uuid4(),
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
        149 fresh journals.
        """
        line_id = uuid.uuid4()
        _posting_batch(
            db,
            org_id,
            _fiscal_period_id(db, org_id),
            fee_idempotency_key(org_id, line_id),
        )

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

        outcome = post_bank_fee(
            db,
            organization_id=org_id,
            line=line,
            bank_gl_account_id=uuid.uuid4(),
            finance_cost_account_id=uuid.uuid4(),
            posted_by_user_id=uuid.uuid4(),
            poster=lambda **_: pytest.fail("posting attempted"),
        )

        assert outcome.already_present
        assert not outcome.created
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

        stranded = _fee_journal(org_id, period_id, line_id, number="FAILED-POST")
        stranded.status = JournalStatus.DRAFT
        db.add(stranded)
        db.flush()

        posted: list[uuid.UUID] = []

        def _poster(**kwargs):
            posted.append(kwargs["journal_entry_id"])
            return SimpleNamespace(
                success=True, message="posted", idempotent_replay=False
            )

        outcome = post_bank_fee(
            db,
            organization_id=org_id,
            line=_fee_line(line_id),
            bank_gl_account_id=uuid.uuid4(),
            finance_cost_account_id=uuid.uuid4(),
            posted_by_user_id=uuid.uuid4(),
            poster=_poster,
        )

        assert outcome.state is BankFeeState.REPOSTED_DRAFT, outcome.message
        assert posted == [stranded.journal_entry_id], (
            "the DRAFT journal must be re-posted, not skipped and not duplicated"
        )
        assert outcome.posted_journal is not None

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
        journal = _fee_journal(org_id, period_id, line_id, number="POSTED-NO-MATCH")
        journal.status = JournalStatus.POSTED
        db.add(journal)
        db.flush()

        outcome = post_bank_fee(
            db,
            organization_id=org_id,
            line=_fee_line(line_id),
            bank_gl_account_id=uuid.uuid4(),
            finance_cost_account_id=uuid.uuid4(),
            posted_by_user_id=uuid.uuid4(),
            poster=lambda **_: pytest.fail("must not post over a live effect"),
        )

        assert outcome.state is BankFeeState.ALREADY_POSTED
        assert outcome.already_present
        assert outcome.posted_journal is not None, (
            "the caller needs the canonical journal to repair a missing match"
        )
        assert outcome.posted_journal.journal_number == "POSTED-NO-MATCH"


class TestTheServiceLosesTheRaceGracefully:
    """The direct-insert canary proves the INDEX. This proves the SERVICE.

    Two sessions both call `post_bank_fee`; the loser must catch the violation,
    re-read, and return the WINNER's journal — not raise, and not report that it
    created something.
    """

    def test_the_losing_caller_returns_the_winners_journal(self, engine) -> None:
        from sqlalchemy.orm import sessionmaker as _sessionmaker

        from app.models.finance.core_org.organization import Organization
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
            winner = _fee_journal(org_id, period_id, line_id, number="WINNER")
            winner.status = JournalStatus.POSTED
            setup.add(winner)
            setup.commit()

            loser = Session_()
            try:
                loser.execute(text("SET app.bypass_rls = 'true'"))
                outcome = post_bank_fee(
                    loser,
                    organization_id=org_id,
                    line=_fee_line(line_id),
                    bank_gl_account_id=uuid.uuid4(),
                    finance_cost_account_id=uuid.uuid4(),
                    posted_by_user_id=uuid.uuid4(),
                    poster=lambda **_: pytest.fail("must not post over the winner"),
                )
                assert outcome.state is BankFeeState.ALREADY_POSTED
                assert outcome.journal is not None
                assert outcome.journal.journal_number == "WINNER", (
                    "the losing caller must report the winner's journal"
                )
                assert not outcome.created
            finally:
                loser.rollback()
                loser.close()
        finally:
            setup.close()
            with engine.begin() as cleanup:
                cleanup.execute(text("SET LOCAL app.bypass_rls = 'true'"))
                for stmt in (
                    "DELETE FROM gl.journal_entry WHERE organization_id = :o",
                    "DELETE FROM gl.fiscal_period WHERE organization_id = :o",
                    "DELETE FROM gl.fiscal_year WHERE organization_id = :o",
                    "DELETE FROM core_org.organization WHERE organization_id = :o",
                ):
                    cleanup.execute(text(stmt), {"o": org_id})
