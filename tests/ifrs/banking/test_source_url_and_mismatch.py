"""
Tests for bank reconciliation source URL linking and statement line view
enhancements.

Covers:
- _build_source_url() helper for mapping source types to URLs
- source_url in GL candidates from get_gl_candidates_for_statement()
- source_url in MatchSuggestion from get_statement_match_suggestions()
- _statement_line_view() raw_amount and matched_journal_line_id fields
- statement_detail_context() matched_source_url and line_amounts
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.finance.banking.bank_reconciliation import (
    SOURCE_URL_MAP,
    BankReconciliationService,
    MatchSuggestion,
    _build_source_url,
)
from app.services.finance.banking.web import (
    BankingWebService,
    _statement_line_view,
)
from tests.ifrs.banking.conftest import (
    MockBankAccount,
    MockBankStatement,
    MockBankStatementLine,
    MockJournalEntry,
    MockJournalEntryLine,
)

# ---------------------------------------------------------------------------
# _build_source_url tests
# ---------------------------------------------------------------------------


class TestBuildSourceUrl:
    """Tests for _build_source_url helper."""

    def test_customer_payment(self) -> None:
        doc_id = uuid4()
        url = _build_source_url("CUSTOMER_PAYMENT", doc_id)
        assert url == f"/finance/ar/receipts/{doc_id}"

    def test_supplier_payment(self) -> None:
        doc_id = uuid4()
        url = _build_source_url("SUPPLIER_PAYMENT", doc_id)
        assert url == f"/finance/ap/payments/{doc_id}"

    def test_ar_invoice(self) -> None:
        doc_id = uuid4()
        url = _build_source_url("AR_INVOICE", doc_id)
        assert url == f"/finance/ar/invoices/{doc_id}"

    def test_customer_invoice_alias(self) -> None:
        doc_id = uuid4()
        url = _build_source_url("CUSTOMER_INVOICE", doc_id)
        assert url == f"/finance/ar/invoices/{doc_id}"

    def test_invoice_alias(self) -> None:
        doc_id = uuid4()
        url = _build_source_url("INVOICE", doc_id)
        assert url == f"/finance/ar/invoices/{doc_id}"

    def test_supplier_invoice(self) -> None:
        doc_id = uuid4()
        url = _build_source_url("SUPPLIER_INVOICE", doc_id)
        assert url == f"/finance/ap/invoices/{doc_id}"

    def test_ap_invoice(self) -> None:
        doc_id = uuid4()
        url = _build_source_url("AP_INVOICE", doc_id)
        assert url == f"/finance/ap/invoices/{doc_id}"

    def test_expense(self) -> None:
        doc_id = uuid4()
        url = _build_source_url("EXPENSE", doc_id)
        assert url == f"/finance/expenses/{doc_id}"

    def test_expense_claim(self) -> None:
        doc_id = uuid4()
        url = _build_source_url("EXPENSE_CLAIM", doc_id)
        assert url == f"/finance/expenses/{doc_id}"

    def test_journal_entry_type(self) -> None:
        doc_id = uuid4()
        url = _build_source_url("JOURNAL_ENTRY", doc_id)
        assert url == f"/finance/gl/journals/{doc_id}"

    def test_unknown_type_falls_back_to_journal(self) -> None:
        doc_id = uuid4()
        entry_id = uuid4()
        url = _build_source_url("UNKNOWN_TYPE", doc_id, entry_id)
        assert url == f"/finance/gl/journals/{entry_id}"

    def test_none_type_falls_back_to_journal(self) -> None:
        entry_id = uuid4()
        url = _build_source_url(None, None, entry_id)
        assert url == f"/finance/gl/journals/{entry_id}"

    def test_none_type_none_entry_returns_empty(self) -> None:
        url = _build_source_url(None, None, None)
        assert url == ""

    def test_source_type_with_none_id_falls_back(self) -> None:
        entry_id = uuid4()
        url = _build_source_url("CUSTOMER_PAYMENT", None, entry_id)
        assert url == f"/finance/gl/journals/{entry_id}"

    def test_all_source_url_map_entries_have_placeholder(self) -> None:
        """Every URL pattern must contain {} for the doc ID."""
        for src_type, pattern in SOURCE_URL_MAP.items():
            assert "{}" in pattern, f"{src_type} pattern missing {{}}: {pattern}"


# ---------------------------------------------------------------------------
# MatchSuggestion dataclass tests
# ---------------------------------------------------------------------------


class TestMatchSuggestionSourceUrl:
    """Tests for source_url field on MatchSuggestion."""

    def test_default_source_url_is_empty(self) -> None:
        s = MatchSuggestion(
            statement_line_id=uuid4(),
            journal_line_id=uuid4(),
            confidence=85.0,
        )
        assert s.source_url == ""

    def test_source_url_set(self) -> None:
        s = MatchSuggestion(
            statement_line_id=uuid4(),
            journal_line_id=uuid4(),
            confidence=90.0,
            source_url="/finance/ar/receipts/abc",
        )
        assert s.source_url == "/finance/ar/receipts/abc"


# ---------------------------------------------------------------------------
# _statement_line_view tests
# ---------------------------------------------------------------------------


class TestStatementLineView:
    """Tests for _statement_line_view enhancements."""

    def _make_line(self, **overrides: object) -> MockBankStatementLine:
        defaults = {
            "amount": Decimal("500.00"),
            "is_matched": False,
            "matched_journal_line_id": None,
        }
        defaults.update(overrides)
        line = MockBankStatementLine(**defaults)  # type: ignore[arg-type]
        # _statement_line_view accesses categorization fields
        if not hasattr(line, "categorization_status"):
            line.categorization_status = None  # type: ignore[attr-defined]
        if not hasattr(line, "suggested_account_id"):
            line.suggested_account_id = None  # type: ignore[attr-defined]
        if not hasattr(line, "suggested_rule_id"):
            line.suggested_rule_id = None  # type: ignore[attr-defined]
        if not hasattr(line, "suggested_confidence"):
            line.suggested_confidence = None  # type: ignore[attr-defined]
        if not hasattr(line, "suggested_match_reason"):
            line.suggested_match_reason = None  # type: ignore[attr-defined]
        return line

    def test_raw_amount_present(self) -> None:
        line = self._make_line(amount=Decimal("1234.56"))
        view = _statement_line_view(line)
        assert view["raw_amount"] == 1234.56

    def test_raw_amount_zero_for_none(self) -> None:
        line = self._make_line(amount=None)
        view = _statement_line_view(line)
        assert view["raw_amount"] == 0.0

    def test_matched_journal_line_id_present_when_matched(self) -> None:
        jl_id = uuid4()
        line = self._make_line(
            is_matched=True,
            matched_journal_line_id=jl_id,
        )
        view = _statement_line_view(line)
        assert view["matched_journal_line_id"] == str(jl_id)

    def test_matched_journal_line_id_none_when_unmatched(self) -> None:
        line = self._make_line(is_matched=False)
        view = _statement_line_view(line)
        assert view["matched_journal_line_id"] is None

    def test_raw_amount_is_float(self) -> None:
        line = self._make_line(amount=Decimal("99.99"))
        view = _statement_line_view(line)
        assert isinstance(view["raw_amount"], float)


# ---------------------------------------------------------------------------
# get_gl_candidates_for_statement source_url tests
# ---------------------------------------------------------------------------


class TestGlCandidatesSourceUrl:
    """Tests for source_url in GL candidates."""

    @pytest.fixture
    def service(self) -> BankReconciliationService:
        return BankReconciliationService()

    @pytest.fixture
    def org_id(self) -> object:
        return uuid4()

    def _make_entry(
        self,
        org_id: object,
        source_type: str | None = None,
        source_id: object | None = None,
    ) -> MockJournalEntry:
        entry = MockJournalEntry(organization_id=org_id)
        entry.source_document_type = source_type  # type: ignore[attr-defined]
        entry.source_document_id = source_id  # type: ignore[attr-defined]
        entry.reference = "REF-001"
        return entry

    def _make_gl_line(
        self,
        entry: MockJournalEntry,
        account_id: object,
        debit: Decimal = Decimal("0"),
        credit: Decimal = Decimal("0"),
    ) -> MockJournalEntryLine:
        gl = MockJournalEntryLine(
            entry_id=entry.entry_id,
            account_id=account_id,
            debit_amount=debit,
            credit_amount=credit,
        )
        gl.journal_entry = entry
        gl.line_number = 1  # type: ignore[attr-defined]
        return gl

    def test_candidate_has_source_url_for_customer_payment(
        self, service: BankReconciliationService, org_id: object, mock_db: MagicMock
    ) -> None:
        doc_id = uuid4()
        account_id = uuid4()
        bank_account = MockBankAccount(organization_id=org_id, gl_account_id=account_id)
        statement = MockBankStatement(organization_id=org_id)
        statement.bank_account = bank_account  # type: ignore[attr-defined]
        mock_db.get.return_value = statement

        entry = self._make_entry(org_id, "CUSTOMER_PAYMENT", doc_id)
        gl_line = self._make_gl_line(entry, account_id, debit=Decimal("100"))

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [gl_line]
        mock_db.execute.return_value = mock_result

        with patch.object(service, "_resolve_gl_metadata", return_value={}):
            result = service.get_gl_candidates_for_statement(
                mock_db, org_id, statement.statement_id
            )

        candidates = result["candidates"]
        assert len(candidates) == 1
        assert candidates[0]["source_url"] == f"/finance/ar/receipts/{doc_id}"

    def test_candidate_falls_back_to_journal_url(
        self, service: BankReconciliationService, org_id: object, mock_db: MagicMock
    ) -> None:
        account_id = uuid4()
        bank_account = MockBankAccount(organization_id=org_id, gl_account_id=account_id)
        statement = MockBankStatement(organization_id=org_id)
        statement.bank_account = bank_account  # type: ignore[attr-defined]
        mock_db.get.return_value = statement

        entry = self._make_entry(org_id, None, None)
        gl_line = self._make_gl_line(entry, account_id, credit=Decimal("50"))

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [gl_line]
        mock_db.execute.return_value = mock_result

        with patch.object(service, "_resolve_gl_metadata", return_value={}):
            result = service.get_gl_candidates_for_statement(
                mock_db, org_id, statement.statement_id
            )

        candidates = result["candidates"]
        assert len(candidates) == 1
        assert candidates[0]["source_url"] == f"/finance/gl/journals/{entry.entry_id}"

    def test_empty_statement_returns_empty_candidates(
        self, service: BankReconciliationService, org_id: object, mock_db: MagicMock
    ) -> None:
        mock_db.get.return_value = None
        result = service.get_gl_candidates_for_statement(mock_db, org_id, uuid4())
        assert result == {"candidates": [], "source_types": []}


# ---------------------------------------------------------------------------
# statement_detail_context matched_source_url and line_amounts tests
# ---------------------------------------------------------------------------


class TestStatementDetailContextEnhancements:
    """Tests for matched_source_url and line_amounts in statement_detail_context."""

    def _make_statement(
        self, org_id: object, lines: list[MockBankStatementLine] | None = None
    ) -> MockBankStatement:
        stmt = MockBankStatement(organization_id=org_id)
        stmt.bank_account = MockBankAccount(  # type: ignore[attr-defined]
            organization_id=org_id,
        )
        for ln in lines or []:
            # Add categorization fields if missing
            for attr in (
                "categorization_status",
                "suggested_account_id",
                "suggested_rule_id",
                "suggested_confidence",
                "suggested_match_reason",
            ):
                if not hasattr(ln, attr):
                    setattr(ln, attr, None)
        stmt.lines = lines or []
        return stmt

    @patch("app.services.finance.banking.bank_reconciliation.BankReconciliationService")
    def test_statement_detail_context_applies_line_pagination(
        self, MockReconCls: MagicMock, mock_db: MagicMock
    ) -> None:
        org_id = uuid4()
        lines = [
            MockBankStatementLine(amount=Decimal("100.00"), is_matched=False),
            MockBankStatementLine(amount=Decimal("200.00"), is_matched=False),
            MockBankStatementLine(amount=Decimal("300.00"), is_matched=False),
        ]
        for line in lines:
            line.categorization_status = None  # type: ignore[attr-defined]
            line.suggested_account_id = None  # type: ignore[attr-defined]
            line.suggested_rule_id = None  # type: ignore[attr-defined]
            line.suggested_confidence = None  # type: ignore[attr-defined]
            line.suggested_match_reason = None  # type: ignore[attr-defined]

        statement = self._make_statement(org_id, lines)
        mock_db.get.return_value = statement

        # Mock SQL pagination: total_count=3 via db.scalar, page 2 returns 1 line
        mock_db.scalar.return_value = 3
        line_scalars = MagicMock()
        line_scalars.all.return_value = [lines[2]]  # page 2, limit 2 → 1 line
        empty_scalars = MagicMock()
        empty_scalars.all.return_value = []
        mock_db.scalars.side_effect = [line_scalars, empty_scalars, empty_scalars, empty_scalars]

        mock_recon = MagicMock()
        mock_recon.get_statement_match_suggestions.return_value = {}
        MockReconCls.return_value = mock_recon

        ctx = BankingWebService.statement_detail_context(
            mock_db,
            str(org_id),
            str(statement.statement_id),
            page=2,
            limit=2,
        )

        assert ctx["page"] == 2
        assert ctx["limit"] == 2
        assert ctx["total_count"] == 3
        assert ctx["total_pages"] == 2
        assert len(ctx["lines"]) == 1

    @patch("app.services.finance.banking.bank_reconciliation.BankReconciliationService")
    def test_line_amounts_populated(
        self, MockReconCls: MagicMock, mock_db: MagicMock
    ) -> None:
        org_id = uuid4()
        line = MockBankStatementLine(
            amount=Decimal("1500.00"),
            is_matched=False,
        )
        line.categorization_status = None  # type: ignore[attr-defined]
        line.suggested_account_id = None  # type: ignore[attr-defined]
        line.suggested_rule_id = None  # type: ignore[attr-defined]
        line.suggested_confidence = None  # type: ignore[attr-defined]
        line.suggested_match_reason = None  # type: ignore[attr-defined]
        statement = self._make_statement(org_id, [line])

        mock_db.get.return_value = statement

        # Mock SQL pagination
        mock_db.scalar.return_value = 1
        line_scalars = MagicMock()
        line_scalars.all.return_value = [line]
        empty_scalars = MagicMock()
        empty_scalars.all.return_value = []
        mock_db.scalars.side_effect = [line_scalars, empty_scalars, empty_scalars, empty_scalars]

        mock_recon = MagicMock()
        mock_recon.get_statement_match_suggestions.return_value = {}
        mock_recon.get_gl_candidates_for_statement.return_value = {
            "candidates": [],
            "source_types": [],
        }
        MockReconCls.return_value = mock_recon

        ctx = BankingWebService.statement_detail_context(
            mock_db, str(org_id), str(statement.statement_id)
        )

        assert "line_amounts" in ctx
        lid = str(line.line_id)
        assert ctx["line_amounts"][lid] == 1500.0

    @patch("app.services.finance.banking.bank_reconciliation.BankReconciliationService")
    def test_matched_source_url_resolved(
        self, MockReconCls: MagicMock, mock_db: MagicMock
    ) -> None:
        org_id = uuid4()
        jl_id = uuid4()
        doc_id = uuid4()

        line = MockBankStatementLine(
            amount=Decimal("200.00"),
            is_matched=True,
            matched_journal_line_id=jl_id,
        )
        line.categorization_status = None  # type: ignore[attr-defined]
        line.suggested_account_id = None  # type: ignore[attr-defined]
        line.suggested_rule_id = None  # type: ignore[attr-defined]
        line.suggested_confidence = None  # type: ignore[attr-defined]
        line.suggested_match_reason = None  # type: ignore[attr-defined]
        statement = self._make_statement(org_id, [line])

        mock_db.get.return_value = statement

        # Mock SQL pagination
        mock_db.scalar.return_value = 1
        line_scalars = MagicMock()
        line_scalars.all.return_value = [line]
        empty_scalars = MagicMock()
        empty_scalars.all.return_value = []
        mock_db.scalars.side_effect = [line_scalars, empty_scalars, empty_scalars, empty_scalars]

        # Mock the JournalEntryLine lookup for matched lines
        mock_jl = MockJournalEntryLine(line_id=jl_id)
        mock_entry = MockJournalEntry(organization_id=org_id)
        mock_entry.source_document_type = "CUSTOMER_PAYMENT"  # type: ignore[attr-defined]
        mock_entry.source_document_id = doc_id  # type: ignore[attr-defined]
        mock_jl.journal_entry = mock_entry

        mock_jl_result = MagicMock()
        mock_jl_result.scalars.return_value.all.return_value = [mock_jl]
        mock_db.execute.return_value = mock_jl_result

        mock_recon = MagicMock()
        mock_recon.get_statement_match_suggestions.return_value = {}
        mock_recon.get_gl_candidates_for_statement.return_value = {
            "candidates": [],
            "source_types": [],
        }
        MockReconCls.return_value = mock_recon

        ctx = BankingWebService.statement_detail_context(
            mock_db, str(org_id), str(statement.statement_id)
        )

        matched_line = ctx["lines"][0]
        assert matched_line["matched_source_url"] == f"/finance/ar/receipts/{doc_id}"

    @patch("app.services.finance.banking.bank_reconciliation.BankReconciliationService")
    def test_unmatched_line_has_empty_source_url(
        self, MockReconCls: MagicMock, mock_db: MagicMock
    ) -> None:
        org_id = uuid4()
        line = MockBankStatementLine(
            amount=Decimal("300.00"),
            is_matched=False,
        )
        line.categorization_status = None  # type: ignore[attr-defined]
        line.suggested_account_id = None  # type: ignore[attr-defined]
        line.suggested_rule_id = None  # type: ignore[attr-defined]
        line.suggested_confidence = None  # type: ignore[attr-defined]
        line.suggested_match_reason = None  # type: ignore[attr-defined]
        statement = self._make_statement(org_id, [line])

        mock_db.get.return_value = statement

        # Mock SQL pagination
        mock_db.scalar.return_value = 1
        line_scalars = MagicMock()
        line_scalars.all.return_value = [line]
        empty_scalars = MagicMock()
        empty_scalars.all.return_value = []
        mock_db.scalars.side_effect = [line_scalars, empty_scalars, empty_scalars, empty_scalars]

        mock_recon = MagicMock()
        mock_recon.get_statement_match_suggestions.return_value = {}
        mock_recon.get_gl_candidates_for_statement.return_value = {
            "candidates": [],
            "source_types": [],
        }
        MockReconCls.return_value = mock_recon

        ctx = BankingWebService.statement_detail_context(
            mock_db, str(org_id), str(statement.statement_id)
        )

        unmatched_line = ctx["lines"][0]
        assert unmatched_line["matched_source_url"] == ""

    @patch("app.services.finance.banking.bank_reconciliation.BankReconciliationService")
    def test_suggestion_includes_source_url(
        self, MockReconCls: MagicMock, mock_db: MagicMock
    ) -> None:
        org_id = uuid4()
        line = MockBankStatementLine(
            amount=Decimal("100.00"),
            is_matched=False,
        )
        line.categorization_status = None  # type: ignore[attr-defined]
        line.suggested_account_id = None  # type: ignore[attr-defined]
        line.suggested_rule_id = None  # type: ignore[attr-defined]
        line.suggested_confidence = None  # type: ignore[attr-defined]
        line.suggested_match_reason = None  # type: ignore[attr-defined]
        statement = self._make_statement(org_id, [line])

        mock_db.get.return_value = statement

        # Mock SQL pagination
        mock_db.scalar.return_value = 1
        line_scalars = MagicMock()
        line_scalars.all.return_value = [line]
        empty_scalars = MagicMock()
        empty_scalars.all.return_value = []
        mock_db.scalars.side_effect = [line_scalars, empty_scalars, empty_scalars, empty_scalars]

        doc_id = uuid4()
        suggestion = MatchSuggestion(
            statement_line_id=line.line_id,
            journal_line_id=uuid4(),
            confidence=92.0,
            counterparty_name="Acme Corp",
            payment_number="RCV-001",
            source_url=f"/finance/ar/receipts/{doc_id}",
        )

        mock_recon = MagicMock()
        mock_recon.get_statement_match_suggestions.return_value = {
            line.line_id: suggestion,
        }
        mock_recon.get_gl_candidates_for_statement.return_value = {
            "candidates": [],
            "source_types": [],
        }
        MockReconCls.return_value = mock_recon

        ctx = BankingWebService.statement_detail_context(
            mock_db, str(org_id), str(statement.statement_id)
        )

        sug = ctx["match_suggestions"][str(line.line_id)]
        assert sug["source_url"] == f"/finance/ar/receipts/{doc_id}"

    @patch("app.services.finance.banking.bank_reconciliation.BankReconciliationService")
    def test_multiple_lines_amounts_and_urls(
        self, MockReconCls: MagicMock, mock_db: MagicMock
    ) -> None:
        """Multiple lines — one matched, one unmatched — both get line_amounts."""
        org_id = uuid4()
        jl_id = uuid4()
        doc_id = uuid4()

        matched_line = MockBankStatementLine(
            amount=Decimal("750.00"),
            is_matched=True,
            matched_journal_line_id=jl_id,
        )
        unmatched_line = MockBankStatementLine(
            amount=Decimal("250.00"),
            is_matched=False,
        )
        for ln in [matched_line, unmatched_line]:
            ln.categorization_status = None  # type: ignore[attr-defined]
            ln.suggested_account_id = None  # type: ignore[attr-defined]
            ln.suggested_rule_id = None  # type: ignore[attr-defined]
            ln.suggested_confidence = None  # type: ignore[attr-defined]
            ln.suggested_match_reason = None  # type: ignore[attr-defined]

        statement = self._make_statement(org_id, [matched_line, unmatched_line])

        mock_db.get.return_value = statement

        # Mock SQL pagination
        mock_db.scalar.return_value = 2
        line_scalars = MagicMock()
        line_scalars.all.return_value = [matched_line, unmatched_line]
        empty_scalars = MagicMock()
        empty_scalars.all.return_value = []
        mock_db.scalars.side_effect = [line_scalars, empty_scalars, empty_scalars, empty_scalars]

        # Mock the JournalEntryLine lookup
        mock_jl = MockJournalEntryLine(line_id=jl_id)
        mock_entry = MockJournalEntry(organization_id=org_id)
        mock_entry.source_document_type = "SUPPLIER_PAYMENT"  # type: ignore[attr-defined]
        mock_entry.source_document_id = doc_id  # type: ignore[attr-defined]
        mock_jl.journal_entry = mock_entry

        mock_jl_result = MagicMock()
        mock_jl_result.scalars.return_value.all.return_value = [mock_jl]
        mock_db.execute.return_value = mock_jl_result

        mock_recon = MagicMock()
        mock_recon.get_statement_match_suggestions.return_value = {}
        mock_recon.get_gl_candidates_for_statement.return_value = {
            "candidates": [],
            "source_types": [],
        }
        MockReconCls.return_value = mock_recon

        ctx = BankingWebService.statement_detail_context(
            mock_db, str(org_id), str(statement.statement_id)
        )

        # Both lines have amounts
        assert ctx["line_amounts"][str(matched_line.line_id)] == 750.0
        assert ctx["line_amounts"][str(unmatched_line.line_id)] == 250.0

        # Matched line has source URL
        assert ctx["lines"][0]["matched_source_url"] == f"/finance/ap/payments/{doc_id}"
        # Unmatched line has empty source URL
        assert ctx["lines"][1]["matched_source_url"] == ""


# ---------------------------------------------------------------------------
# _validate_amount_match tests
# ---------------------------------------------------------------------------


class TestValidateAmountMatch:
    """Tests for backend amount mismatch validation."""

    @pytest.fixture
    def service(self) -> BankReconciliationService:
        return BankReconciliationService()

    def test_exact_match_passes(self, service: BankReconciliationService) -> None:
        """Identical amounts should pass without error."""
        service._validate_amount_match(Decimal("1000.00"), Decimal("1000.00"))

    def test_within_absolute_tolerance_passes(
        self, service: BankReconciliationService
    ) -> None:
        """Amounts differing by <= 0.01 (absolute tolerance) pass."""
        service._validate_amount_match(Decimal("1000.00"), Decimal("1000.01"))

    def test_small_relative_difference_passes(
        self, service: BankReconciliationService
    ) -> None:
        """Amount difference within 1% relative threshold passes."""
        # 1000 vs 1005 = 0.5% difference — should pass
        service._validate_amount_match(Decimal("1000.00"), Decimal("1005.00"))

    def test_large_mismatch_raises(self, service: BankReconciliationService) -> None:
        """Large amount mismatch raises HTTPException 400."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            service._validate_amount_match(Decimal("15000.00"), Decimal("50000.00"))
        assert exc_info.value.status_code == 400
        assert "mismatch" in str(exc_info.value.detail).lower()

    def test_large_mismatch_with_force_passes(
        self, service: BankReconciliationService
    ) -> None:
        """force_match=True overrides the mismatch check."""
        service._validate_amount_match(
            Decimal("15000.00"), Decimal("50000.00"), force_match=True
        )

    def test_zero_vs_nonzero_raises(self, service: BankReconciliationService) -> None:
        """Matching zero to non-zero raises (edge case)."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            service._validate_amount_match(Decimal("0"), Decimal("500.00"))

    def test_zero_vs_zero_passes(self, service: BankReconciliationService) -> None:
        """Both sides zero is a valid match."""
        service._validate_amount_match(Decimal("0"), Decimal("0"))

    def test_negative_amounts_compared_by_abs(
        self, service: BankReconciliationService
    ) -> None:
        """Negative amounts are compared by absolute value."""
        # -1000 vs 1000 → abs equal, passes
        service._validate_amount_match(Decimal("-1000.00"), Decimal("1000.00"))

    def test_negative_with_large_mismatch_raises(
        self, service: BankReconciliationService
    ) -> None:
        """Negative amounts with large mismatch still raise."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            service._validate_amount_match(Decimal("-100.00"), Decimal("500.00"))

    def test_threshold_constants_are_positive(self) -> None:
        """Sanity check that threshold constants are positive Decimals."""
        from app.services.finance.banking.bank_reconciliation import (
            AMOUNT_MISMATCH_ABSOLUTE_TOLERANCE,
            AMOUNT_MISMATCH_RELATIVE_THRESHOLD,
        )

        assert AMOUNT_MISMATCH_ABSOLUTE_TOLERANCE > 0
        assert AMOUNT_MISMATCH_RELATIVE_THRESHOLD > 0

    def test_match_statement_line_rejects_mismatch(
        self, service: BankReconciliationService, mock_db: MagicMock
    ) -> None:
        """match_statement_line blocks mismatched amounts."""
        from fastapi import HTTPException

        org_id = uuid4()
        statement = MockBankStatement(organization_id=org_id)
        line = MockBankStatementLine(
            statement_id=statement.statement_id,
            amount=Decimal("15000.00"),
            is_matched=False,
        )
        line.statement = statement

        entry = MockJournalEntry(organization_id=org_id)
        entry.source_document_type = None  # type: ignore[attr-defined]
        entry.source_document_id = None  # type: ignore[attr-defined]
        gl_line = MockJournalEntryLine(
            debit_amount=Decimal("50000.00"),
            credit_amount=Decimal("0"),
        )
        gl_line.journal_entry = entry

        mock_db.get.side_effect = lambda cls, id: (
            line if cls.__name__ == "BankStatementLine" else gl_line
        )

        with pytest.raises(HTTPException) as exc_info:
            service.match_statement_line(mock_db, org_id, line.line_id, gl_line.line_id)
        assert exc_info.value.status_code == 400

    def test_match_statement_line_accepts_with_force(
        self, service: BankReconciliationService, mock_db: MagicMock
    ) -> None:
        """match_statement_line accepts mismatch when force_match=True."""
        org_id = uuid4()
        statement = MockBankStatement(organization_id=org_id)
        line = MockBankStatementLine(
            statement_id=statement.statement_id,
            amount=Decimal("15000.00"),
            is_matched=False,
        )
        line.statement = statement

        entry = MockJournalEntry(organization_id=org_id)
        entry.source_document_type = None  # type: ignore[attr-defined]
        entry.source_document_id = None  # type: ignore[attr-defined]
        gl_line = MockJournalEntryLine(
            debit_amount=Decimal("50000.00"),
            credit_amount=Decimal("0"),
        )
        gl_line.journal_entry = entry

        mock_db.get.side_effect = lambda cls, id: (
            line if cls.__name__ == "BankStatementLine" else gl_line
        )

        result = service.match_statement_line(
            mock_db,
            org_id,
            line.line_id,
            gl_line.line_id,
            force_match=True,
        )
        assert result.is_matched is True
