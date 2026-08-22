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

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
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
    """A fiscal period to hang the journals on; `fiscal_period_id` is NOT NULL."""
    year_id = uuid.uuid4()
    db.execute(
        text("""
            INSERT INTO gl.fiscal_year
              (fiscal_year_id, organization_id, year_code, start_date, end_date, status)
            VALUES (:y, :o, :code, DATE '2026-01-01', DATE '2026-12-31', 'OPEN')
        """),
        {"y": year_id, "o": org_id, "code": f"FY{uuid.uuid4().hex[:6]}"},
    )
    period_id = uuid.uuid4()
    db.execute(
        text("""
            INSERT INTO gl.fiscal_period
              (fiscal_period_id, organization_id, fiscal_year_id, period_number,
               period_name, start_date, end_date, status)
            VALUES (:p, :o, :y, 1, 'Canary', DATE '2026-01-01', DATE '2026-01-31', 'OPEN')
        """),
        {"p": period_id, "o": org_id, "y": year_id},
    )
    db.flush()
    return period_id


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
        create nothing at all — not an extra journal, not a match."""
        period_id = _fiscal_period_id(db, org_id)
        line_id = uuid.uuid4()
        db.add(_fee_journal(org_id, period_id, line_id, number="FIRST"))
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
        db.execute(
            text("""
                INSERT INTO gl.posting_batch
                  (batch_id, organization_id, idempotency_key, source_module,
                   batch_description, total_entries, posted_entries, failed_entries,
                   status, submitted_at)
                VALUES (:b, :o, :k, 'BANKING', 'legacy', 2, 2, 0, 'POSTED', now())
            """),
            {"b": uuid.uuid4(), "o": org_id, "k": fee_idempotency_key(org_id, line_id)},
        )
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
        period_ids = []
        try:
            with engine.begin() as setup:
                setup.execute(text("SET LOCAL app.bypass_rls = 'true'"))
                setup.execute(
                    text("""
                        INSERT INTO core_org.organization
                          (organization_id, organization_code, legal_name,
                           functional_currency_code, presentation_currency_code,
                           fiscal_year_end_month, fiscal_year_end_day, is_active)
                        VALUES (:o, :c, 'Concurrency Canary', 'NGN', 'NGN', 12, 31, true)
                    """),
                    {"o": org_id, "c": f"CANARY-{uuid.uuid4().hex[:8].upper()}"},
                )
                year_id, period_id = uuid.uuid4(), uuid.uuid4()
                setup.execute(
                    text("""
                        INSERT INTO gl.fiscal_year
                          (fiscal_year_id, organization_id, year_code, start_date, end_date, status)
                        VALUES (:y, :o, :code, DATE '2026-01-01', DATE '2026-12-31', 'OPEN')
                    """),
                    {"y": year_id, "o": org_id, "code": f"FY{uuid.uuid4().hex[:6]}"},
                )
                setup.execute(
                    text("""
                        INSERT INTO gl.fiscal_period
                          (fiscal_period_id, organization_id, fiscal_year_id, period_number,
                           period_name, start_date, end_date, status)
                        VALUES (:p, :o, :y, 1, 'Canary', DATE '2026-01-01', DATE '2026-01-31', 'OPEN')
                    """),
                    {"p": period_id, "o": org_id, "y": year_id},
                )
                period_ids.append(period_id)

            for session, number in ((s1, "RACER-A"), (s2, "RACER-B")):
                session.execute(text("SET app.bypass_rls = 'true'"))
                session.add(_fee_journal(org_id, period_ids[0], line_id, number=number))

            # Both flush before either commits — the overlap a pre-check misses.
            s1.flush()
            try:
                s2.flush()
                s2.commit()
                created.append("RACER-B")
            except IntegrityError:
                refused += 1
                s2.rollback()
            s1.commit()
            created.append("RACER-A")

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
