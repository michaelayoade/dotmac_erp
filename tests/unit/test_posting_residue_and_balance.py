"""The two defects that let three journals post one micro-unit out of balance.

`JE202604-40653`, `JE202604-40818` and `JE202604-42111` are the same recurring
AR invoice to one customer. Their revenue split is three seventeenths whose
exact sum is `125,812.500000`; each line rounded UP at six places, so the stored
lines summed to `125,812.500001`.

Two independent defects were required for that to reach the ledger:

1. the allocator rounded each revenue line independently and rebalanced nothing;
2. `LedgerPostingService` rejected only when the difference EXCEEDED
   `Decimal("0.000001")`, so a difference of exactly one micro-unit passed.

Both are covered here. The seventeenths case is a regression canary reproducing
the real invoice, and `test_exactly_one_micro_unit_is_refused` is the
sensitivity proof for the boundary: it fails against the old `>` comparison and
passes against exact equality, so it is the evidence the fix landed rather than
a test that would pass either way.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.finance.posting.residue import (
    ResidueAllocationError,
    allocate_residue,
    quantize,
)

D = Decimal


class TestResidueAllocation:
    def test_the_seventeenths_regression_canary(self) -> None:
        """The real invoice. Three lines, each rounding up, one micro-unit over.

        Independently rounded these are 103,610.294118 + 19,735.294118 +
        2,466.911765 = 125,812.500001. The allocator must land them on
        125,812.500000 exactly, and must move the LARGEST line.
        """
        unrounded = [
            D(1_761_375) / D(17),
            D(335_500) / D(17),
            D(83_875) / D(34),
        ]
        target = D("125812.500000")

        # Establish the defect the canary is about: naive rounding is over.
        naive = [quantize(value) for value in unrounded]
        assert sum(naive, D(0)) == D("125812.500001")

        allocated = allocate_residue(unrounded, target)

        assert sum(allocated, D(0)) == target
        assert allocated[0] == D("103610.294117"), "residue belongs on the largest line"
        assert allocated[1] == D("19735.294118")
        assert allocated[2] == D("2466.911765")

    def test_the_residue_goes_to_the_largest_absolute_line(self) -> None:
        allocated = allocate_residue([D("1.000000"), D("50.000000"), D("3.000000")], D("54.000001"))
        assert allocated == [D("1.000000"), D("50.000001"), D("3.000000")]

    def test_largest_absolute_counts_sign_correctly(self) -> None:
        """A discount line is negative; magnitude is what makes a line the least
        distorted place to put a residue, not its direction."""
        allocated = allocate_residue([D("10.000000"), D("-90.000000")], D("-80.000001"))
        assert allocated == [D("10.000000"), D("-90.000001")]

    def test_ties_break_to_the_earliest_line(self) -> None:
        """Determinism matters more than a prettier split: the same input must
        always produce the same journal."""
        allocated = allocate_residue([D("25.000000"), D("25.000000")], D("50.000001"))
        assert allocated == [D("25.000001"), D("25.000000")]

    def test_an_exact_split_is_left_alone(self) -> None:
        amounts = [D("10.000000"), D("20.000000")]
        assert allocate_residue(amounts, D("30.000000")) == amounts

    def test_a_shortfall_is_allocated_as_readily_as_an_excess(self) -> None:
        allocated = allocate_residue([D("10.000000"), D("20.000000")], D("29.999999"))
        assert sum(allocated, D(0)) == D("29.999999")
        assert allocated[1] == D("19.999999")

    def test_allocating_across_no_lines_fails_loudly(self) -> None:
        """Returning an unbalanced result quietly is precisely how the original
        defect reached production."""
        with pytest.raises(ResidueAllocationError):
            allocate_residue([], D("1.000000"))
        assert allocate_residue([], D("0")) == []

    def test_the_result_always_sums_to_the_target(self) -> None:
        """Property check over splits that do not divide evenly."""
        for divisor in (3, 6, 7, 9, 11, 13, 17, 23):
            total = D("1000.000000")
            share = total / D(divisor)
            allocated = allocate_residue([share] * divisor, total)
            assert sum(allocated, D(0)) == total, divisor


class TestExactBalanceEnforcement:
    """The posting boundary must require equality, not near-equality."""

    @staticmethod
    def _entries(*amounts: tuple[Decimal, Decimal]):
        """One `PostingEntry` per (debit, credit) pair."""
        import uuid

        from app.services.finance.gl.ledger_posting import PostingEntry

        return [
            PostingEntry(
                account_id=uuid.uuid4(),
                debit_amount=debit,
                credit_amount=credit,
                debit_amount_functional=debit,
                credit_amount_functional=credit,
            )
            for debit, credit in amounts
        ]

    @classmethod
    def _two_sided(cls, debit: Decimal, credit: Decimal):
        return cls._entries((debit, D("0")), (D("0"), credit))

    def test_a_balanced_journal_is_accepted(self) -> None:
        from app.services.finance.gl.ledger_posting import LedgerPostingService

        LedgerPostingService._validate_balance(
            self._two_sided(D("135375.000000"), D("135375.000000"))
        )

    def test_exactly_one_micro_unit_is_refused(self) -> None:
        """THE sensitivity proof.

        The old boundary was `abs(debit - credit) > Decimal("0.000001")`, so a
        difference of exactly one micro-unit passed — which is how the three
        real journals posted. This test fails against that comparison and
        passes against exact equality, so it is evidence the fix landed rather
        than a test that would pass either way.
        """
        from fastapi import HTTPException

        from app.services.finance.gl.ledger_posting import LedgerPostingService

        with pytest.raises(HTTPException) as excinfo:
            LedgerPostingService._validate_balance(
                self._two_sided(D("135375.000000"), D("135375.000001"))
            )
        assert "unbalanced" in str(excinfo.value.detail).lower()

    def test_the_real_journal_shape_is_refused(self) -> None:
        """The exact revenue/VAT/receivable shape of JE202604-40653."""
        from fastapi import HTTPException

        from app.services.finance.gl.ledger_posting import LedgerPostingService

        credits = [
            D("103610.294118"),
            D("7875.000000"),
            D("19735.294118"),
            D("1500.000000"),
            D("2466.911765"),
            D("187.500000"),
        ]
        assert sum(credits, D(0)) == D("135375.000001")

        with pytest.raises(HTTPException):
            LedgerPostingService._validate_balance(
                self._two_sided(D("135375.000000"), sum(credits, D(0)))
            )

    def test_the_boundary_carries_no_tolerance_constant(self) -> None:
        """A tolerance that still exists is a tolerance someone can widen.

        The old `BALANCE_TOLERANCE` is gone, replaced by `PERSISTED_SCALE`,
        which is a statement about storage rather than permission to be wrong.
        """
        from app.services.finance.gl.ledger_posting import LedgerPostingService

        assert not hasattr(LedgerPostingService, "BALANCE_TOLERANCE")
        assert D("0.000001") == LedgerPostingService.PERSISTED_SCALE


class TestBacklogToleranceIsAligned:
    """The backlog poster carried a separate, far larger tolerance.

    `posting_backlog.py` is the service that would post the APPROVED backlog,
    and it treated anything under `Decimal("0.01")` as balanced — 10,000x the
    GL boundary, declared as mirroring the AR/AP *payment* tolerance. Payment
    dust is a settlement concept; a journal is balanced or it is not.

    It was also incompatible with `dotmac-accounting`, which refuses an
    unbalanced journal outright: anything posted under that tolerance would
    have been rejected at backfill.
    """

    def test_the_backlog_poster_requires_exact_balance(self) -> None:
        from app.services.finance.gl import posting_backlog

        assert not hasattr(posting_backlog, "IMBALANCE_TOLERANCE")

    @staticmethod
    def _journal(imbalance: Decimal):
        import uuid

        from app.services.finance.gl.posting_backlog import ApprovedJournal

        return ApprovedJournal(
            journal_entry_id=uuid.uuid4(),
            journal_number="JE-TEST",
            imbalance=imbalance,
            period_status="OPEN",
            source_module="AR",
        )

    def test_a_sub_kobo_imbalance_is_not_postable(self) -> None:
        """Under the old Decimal("0.01") tolerance this was "balanced"."""
        journal = self._journal(D("0.005000"))
        assert not journal.is_balanced
        assert not journal.is_postable

    def test_one_micro_unit_is_not_postable_either(self) -> None:
        assert not self._journal(D("0.000001")).is_balanced

    def test_an_exactly_balanced_journal_is_postable(self) -> None:
        journal = self._journal(D("0"))
        assert journal.is_balanced
        assert journal.is_postable


class TestTotalsAreTakenAsStored:
    """Quantise each line, THEN add — because that is what the column holds."""

    def test_summing_first_answers_the_wrong_question(self) -> None:
        from app.services.finance.posting.residue import sum_at_persisted_scale

        amounts = [D("100.0000005"), D("100.0000005")]

        # Sum-then-quantise: 200.000001. Stored, these rows total 200.000002.
        assert sum(amounts, D(0)).quantize(D("0.000001")) == D("200.000001")
        assert sum_at_persisted_scale(amounts) == D("200.000002")

    def test_rounding_matches_postgresql_not_python(self) -> None:
        """`NUMERIC` rounds half AWAY FROM ZERO; `Decimal` defaults to half-even.

        Under half-even, `100.0000005` stores as `100.000000` in the check and
        `100.000001` in the database — the validated numbers would not be the
        stored ones.
        """
        from app.services.finance.posting.residue import quantize

        assert quantize(D("100.0000005")) == D("100.000001")
        assert quantize(D("100.0000015")) == D("100.000002")
        assert quantize(D("-100.0000005")) == D("-100.000001")

    def test_a_sub_micro_split_that_only_shows_up_once_stored(self) -> None:
        """THE sensitivity proof for per-entry quantisation.

        Debit `100.0000005` and credit `100.0000004` differ by a ten-millionth.
        Summing first and quantising the totals makes both `100.000001` and the
        journal looks balanced; stored, the debit is `100.000001` and the credit
        `100.000000`. Only per-entry quantisation catches it.
        """
        from fastapi import HTTPException

        from app.services.finance.gl.ledger_posting import LedgerPostingService

        with pytest.raises(HTTPException):
            LedgerPostingService._validate_balance(
                TestExactBalanceEnforcement._two_sided(
                    D("100.0000005"), D("100.0000004")
                )
            )


class TestJournalServiceBoundary:
    """`JournalService` is the FIRST gate — and carried the same comparison.

    `create_journal` and `update_journal` each had their own
    `abs(total_debit - total_credit) > Decimal("0.000001")`, so the three real
    journals passed here before they ever reached `LedgerPostingService`. Both
    now delegate to one check.
    """

    @staticmethod
    def _lines(*amounts: tuple[Decimal, Decimal]):
        import uuid

        from app.services.finance.gl.journal import JournalLineInput

        return [
            JournalLineInput(
                account_id=uuid.uuid4(),
                debit_amount=debit,
                credit_amount=credit,
            )
            for debit, credit in amounts
        ]

    def test_a_balanced_journal_is_accepted(self) -> None:
        from app.services.finance.gl.journal import JournalService

        JournalService._require_balanced(
            self._lines((D("135375.000000"), D("0")), (D("0"), D("135375.000000")))
        )

    def test_exactly_one_micro_unit_is_refused(self) -> None:
        from fastapi import HTTPException

        from app.services.finance.gl.journal import JournalService

        with pytest.raises(HTTPException) as excinfo:
            JournalService._require_balanced(
                self._lines((D("135375.000000"), D("0")), (D("0"), D("135375.000001")))
            )
        assert "unbalanced" in str(excinfo.value.detail).lower()

    def test_the_real_journal_shape_is_refused(self) -> None:
        """JE202604-40653: three seventeenths, each rounded up, plus exact VAT."""
        from fastapi import HTTPException

        from app.services.finance.gl.journal import JournalService

        credits = [
            D("103610.294118"),
            D("7875.000000"),
            D("19735.294118"),
            D("1500.000000"),
            D("2466.911765"),
            D("187.500000"),
        ]
        assert sum(credits, D(0)) == D("135375.000001")

        lines = self._lines((D("135375.000000"), D("0")))
        lines.extend(self._lines(*[(D("0"), amount) for amount in credits]))

        with pytest.raises(HTTPException):
            JournalService._require_balanced(lines)


class TestTheAllocatorIsActuallyWired:
    """The allocator only prevents a recurrence if the AR adapter CALLS it.

    These drive `_absorb_rounding_residue` — the seam `post_invoice` runs over
    its journal lines just before handing them to `JournalService` — with the
    real invoice's numbers.
    """

    @staticmethod
    def _line(debit: Decimal, credit: Decimal):
        import uuid

        from app.services.finance.gl.journal import JournalLineInput

        return JournalLineInput(
            account_id=uuid.uuid4(),
            debit_amount=debit,
            credit_amount=credit,
            debit_amount_functional=debit,
            credit_amount_functional=credit,
        )

    def _real_invoice_lines(self):
        """AR debit, three revenue credits (rounded up), three VAT credits."""
        lines = [self._line(D("135375.000000"), D("0"))]
        revenue_indexes = []
        for revenue, vat in (
            (D("103610.294118"), D("7875.000000")),
            (D("19735.294118"), D("1500.000000")),
            (D("2466.911765"), D("187.500000")),
        ):
            revenue_indexes.append(len(lines))
            lines.append(self._line(D("0"), revenue))
            lines.append(self._line(D("0"), vat))
        return lines, revenue_indexes

    def test_the_real_invoice_now_balances(self) -> None:
        from app.services.finance.ar.posting.invoice import _absorb_rounding_residue

        lines, revenue_indexes = self._real_invoice_lines()
        assert sum((line.credit_amount for line in lines), D(0)) == D("135375.000001")

        _absorb_rounding_residue(lines, revenue_indexes)

        assert sum((line.debit_amount for line in lines), D(0)) == sum(
            (line.credit_amount for line in lines), D(0)
        )
        assert sum(
            (line.debit_amount_functional for line in lines), D(0)
        ) == sum((line.credit_amount_functional for line in lines), D(0))

    def test_the_largest_revenue_line_carries_it(self) -> None:
        from app.services.finance.ar.posting.invoice import _absorb_rounding_residue

        lines, revenue_indexes = self._real_invoice_lines()
        _absorb_rounding_residue(lines, revenue_indexes)

        assert lines[revenue_indexes[0]].credit_amount == D("103610.294117")
        assert lines[revenue_indexes[1]].credit_amount == D("19735.294118")
        assert lines[revenue_indexes[2]].credit_amount == D("2466.911765")

    def test_a_journal_that_already_balances_is_untouched(self) -> None:
        from app.services.finance.ar.posting.invoice import _absorb_rounding_residue

        lines = [self._line(D("100.000000"), D("0")), self._line(D("0"), D("100.000000"))]
        _absorb_rounding_residue(lines, [1])
        assert lines[1].credit_amount == D("100.000000")

    def test_a_real_imbalance_is_refused_not_absorbed(self) -> None:
        """A kobo is not a rounding residue.

        The bound is one micro-unit per line — the most that rounding this many
        lines could produce. Anything larger is a data defect, and quietly
        moving it onto a revenue account would be the plug this exists to avoid.
        """
        from app.services.finance.ar.posting.invoice import _absorb_rounding_residue
        from app.services.finance.posting.residue import ResidueAllocationError

        lines = [self._line(D("100.010000"), D("0")), self._line(D("0"), D("100.000000"))]
        with pytest.raises(ResidueAllocationError):
            _absorb_rounding_residue(lines, [1])

    def test_an_imbalance_with_no_revenue_line_is_refused(self) -> None:
        from app.services.finance.ar.posting.invoice import _absorb_rounding_residue
        from app.services.finance.posting.residue import ResidueAllocationError

        lines = [
            self._line(D("100.000000"), D("0")),
            self._line(D("0"), D("100.000001")),
        ]
        with pytest.raises(ResidueAllocationError):
            _absorb_rounding_residue(lines, [])
