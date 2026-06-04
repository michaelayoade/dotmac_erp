"""ReconciliationMatchingService component."""

from __future__ import annotations

from uuid import uuid4

from app.services.finance.banking.reconciliation_parts.base import (
    AutoMatchResult,
    BankAccount,
    BankReconciliation,
    BankReconciliationLine,
    BankStatement,
    BankStatementLine,
    BankStatementLineMatch,
    Decimal,
    HTTPException,
    JournalEntry,
    JournalEntryLine,
    JournalStatus,
    MatchSuggestion,
    PaymentMetadata,
    ReconciliationMatchInput,
    ReconciliationMatchType,
    ReconciliationStatus,
    Session,
    UTC,
    UUID,
    _build_source_url,
    _check_rule_payee_link,
    _list,
    and_,
    date,
    datetime,
    delete,
    logger,
    select,
)


# Date-relation soft threshold for split legs. Timing differences (deposits in
# transit, unpresented cheques) legitimately span a few days, so this is only a
# review FLAG, never a hard block. Tune per banking policy.
_SPLIT_DATE_SOFT_WINDOW_DAYS = 7


class ReconciliationMatchingService:
    """Bank reconciliation methods for matching."""

    @staticmethod
    def _derive_match_source(
        gl_line: JournalEntryLine,
    ) -> tuple[str | None, UUID | None]:
        """Derive (source_type, source_id) for a bank-line ↔ GL match.

        Reads the linked journal entry's ``source_document_type`` and
        ``source_document_id``. Returns ``(None, None)`` if the journal
        entry has no source-document attribution (manual journals, opening
        balances, etc.). Used to keep ``BankStatementLineMatch`` audit-trail
        columns populated automatically — without it, downstream auditors
        cannot trace a bank movement back to its originating customer
        receipt / supplier payment / invoice without re-walking the GL.
        """
        entry = getattr(gl_line, "journal_entry", None) or getattr(
            gl_line, "entry", None
        )
        if entry is None:
            return None, None
        # ``getattr`` with default lets test SimpleNamespace mocks omit these
        # attributes; on real ``JournalEntry`` rows the columns always exist
        # (nullable, so genuine NULL → None passes through unchanged).
        source_type = getattr(entry, "source_document_type", None)
        source_id = getattr(entry, "source_document_id", None)
        return source_type, source_id

    def add_match(
        self,
        db: Session,
        organization_id: UUID,
        reconciliation_id: UUID,
        input: ReconciliationMatchInput,
        created_by: UUID | None = None,
        force_match: bool = False,
    ) -> BankReconciliationLine:
        """Add a match between statement line and GL entry."""
        reconciliation = self._get_for_org(db, organization_id, reconciliation_id)  # type: ignore[attr-defined]

        if reconciliation.status not in [
            ReconciliationStatus.draft,
            ReconciliationStatus.pending_review,
        ]:
            raise HTTPException(
                status_code=400,
                detail="Cannot modify an approved/rejected reconciliation",
            )

        # Get statement line
        statement_line = db.get(BankStatementLine, input.statement_line_id)
        if not statement_line:
            raise HTTPException(
                status_code=404,
                detail=f"Statement line {input.statement_line_id} not found",
            )
        statement = getattr(statement_line, "statement", None)
        if statement is not None and statement.organization_id != organization_id:
            raise HTTPException(
                status_code=404,
                detail=f"Statement line {input.statement_line_id} not found",
            )

        # Get GL line
        gl_line = db.get(JournalEntryLine, input.journal_line_id)
        if not gl_line:
            raise HTTPException(
                status_code=404,
                detail=f"Journal line {input.journal_line_id} not found",
            )
        journal_entry = getattr(gl_line, "journal_entry", None) or getattr(
            gl_line, "entry", None
        )
        if not journal_entry:
            raise HTTPException(
                status_code=404,
                detail=f"Journal line {input.journal_line_id} not found",
            )
        journal_org_id = getattr(journal_entry, "organization_id", None)
        if journal_org_id is not None and journal_org_id != organization_id:
            raise HTTPException(
                status_code=404,
                detail=f"Journal line {input.journal_line_id} not found",
            )

        # Calculate amounts
        statement_amount = statement_line.signed_amount
        gl_amount = (gl_line.debit_amount or Decimal("0")) - (
            gl_line.credit_amount or Decimal("0")
        )
        difference = statement_amount - gl_amount
        # Exact-tie rule: a confirmed match must tie to the kobo. Any residual
        # (bank charge, interest, FX, error) must be booked as a reconciling
        # item, never absorbed into the match. force_match is a deliberate
        # supervisor override only.
        if difference != Decimal("0") and not force_match:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot match: statement {statement_amount:,.2f} does not tie "
                    f"to GL {gl_amount:,.2f} (difference {difference:,.2f}). Add a "
                    "reconciling item (bank charge, interest, FX, etc.) for the "
                    "difference instead of forcing an inexact match."
                ),
            )

        # Create reconciliation line
        recon_line = BankReconciliationLine(
            reconciliation_id=reconciliation_id,
            match_type=input.match_type,
            statement_line_id=input.statement_line_id,
            journal_line_id=input.journal_line_id,
            transaction_date=statement_line.transaction_date,
            description=statement_line.description,
            reference=statement_line.reference,
            statement_amount=statement_amount,
            gl_amount=gl_amount,
            difference=difference,
            is_cleared=True,
            cleared_at=datetime.utcnow(),
            notes=input.notes,
            created_by=created_by,
        )

        db.add(recon_line)

        # Mark statement line as matched
        now = datetime.now(tz=UTC)
        statement_line.is_matched = True
        statement_line.matched_at = now
        statement_line.matched_by = created_by

        # Mirror into the junction: cleanup any stale rows, insert a primary
        # match. Without this, the recon-line path would leave is_matched=True
        # but no junction row, breaking reads that go through matched_gl_lines.
        db.execute(
            delete(BankStatementLineMatch).where(
                BankStatementLineMatch.statement_line_id == input.statement_line_id
            )
        )
        source_type, source_id = self._derive_match_source(gl_line)
        db.add(
            BankStatementLineMatch(
                statement_line_id=input.statement_line_id,
                journal_line_id=input.journal_line_id,
                matched_at=now,
                matched_by=created_by,
                is_primary=True,
                source_type=source_type,
                source_id=source_id,
                match_type="MANUAL",
                match_state="confirmed",
                confirmed_at=now,
                confirmed_by=created_by,
            )
        )

        # Update reconciliation totals
        reconciliation.total_matched += abs(statement_amount)
        reconciliation.calculate_difference()

        db.flush()
        return recon_line

    def unmatch(
        self,
        db: Session,
        organization_id: UUID,
        reconciliation_id: UUID,
        statement_line_id: UUID,
        created_by: UUID | None = None,
    ) -> None:
        """Reverse a confirmed match for a statement line (the whole split group).

        Deletes the confirmed junction rows and any reconciliation line(s) for
        the statement line, clears ``is_matched``, and re-derives the
        difference. Keyed by statement line so it reverses matches made by the
        workspace *or* the auto-engine (which has no reconciliation line).
        """
        reconciliation = self._get_for_org(db, organization_id, reconciliation_id)  # type: ignore[attr-defined]
        if reconciliation.status not in [
            ReconciliationStatus.draft,
            ReconciliationStatus.pending_review,
        ]:
            raise HTTPException(
                status_code=400,
                detail="Cannot modify an approved/rejected reconciliation",
            )

        stmt_line = db.get(BankStatementLine, statement_line_id)
        if not stmt_line:
            raise HTTPException(
                status_code=404,
                detail=f"Statement line {statement_line_id} not found",
            )

        # Reconciliation lines for this statement line (exclude adjustments /
        # outstanding items — those are removed via the reconciling-items panel).
        group_lines = list(
            db.scalars(
                select(BankReconciliationLine).where(
                    BankReconciliationLine.reconciliation_id == reconciliation_id,
                    BankReconciliationLine.statement_line_id == statement_line_id,
                    BankReconciliationLine.is_adjustment.is_(False),
                    BankReconciliationLine.is_outstanding.is_(False),
                )
            ).all()
        )

        db.execute(
            delete(BankStatementLineMatch).where(
                BankStatementLineMatch.statement_line_id == statement_line_id
            )
        )
        stmt_line.is_matched = False
        stmt_line.matched_at = None
        stmt_line.matched_by = None

        reversed_amount = Decimal("0")
        for line in group_lines:
            reversed_amount += abs(line.statement_amount or Decimal("0"))
            db.delete(line)

        reconciliation.total_matched -= reversed_amount
        if reconciliation.total_matched < Decimal("0"):
            reconciliation.total_matched = Decimal("0")
        reconciliation.calculate_difference()
        db.flush()

    def _get_unmatched_lines(
        self,
        db: Session,
        organization_id: UUID,
        reconciliation: BankReconciliation,
    ) -> tuple[_list[BankStatementLine], _list[JournalEntryLine]]:
        """Query unmatched statement lines and GL lines for a reconciliation."""
        bank_account = reconciliation.bank_account

        statement_lines = _list(
            db.execute(
                select(BankStatementLine)
                .join(BankStatement)
                .where(
                    and_(
                        BankStatement.organization_id == organization_id,
                        BankStatement.bank_account_id == reconciliation.bank_account_id,
                        BankStatementLine.is_matched == False,  # noqa: E712
                        BankStatementLine.transaction_date
                        >= reconciliation.period_start,
                        BankStatementLine.transaction_date <= reconciliation.period_end,
                    )
                )
            )
            .scalars()
            .all()
        )

        gl_lines: _list[JournalEntryLine] = []
        if bank_account:
            gl_lines = _list(
                db.execute(
                    select(JournalEntryLine)
                    .join(JournalEntry)
                    .where(
                        and_(
                            JournalEntry.organization_id == organization_id,
                            JournalEntryLine.account_id == bank_account.gl_account_id,
                            JournalEntry.status == JournalStatus.POSTED,
                            JournalEntry.entry_date >= reconciliation.period_start,
                            JournalEntry.entry_date <= reconciliation.period_end,
                        )
                    )
                )
                .scalars()
                .all()
            )

        return statement_lines, gl_lines

    @staticmethod
    def _get_matched_gl_ids(db: Session, organization_id: UUID) -> set[UUID]:
        """Return GL line IDs already matched to any statement line for this org.

        Scoped to the given *organization_id* to prevent cross-tenant leaks.
        """
        from app.models.finance.banking.bank_statement import BankStatementLineMatch

        return {
            row
            for row in db.execute(
                select(BankStatementLineMatch.journal_line_id)
                .join(
                    BankStatementLine,
                    BankStatementLineMatch.statement_line_id
                    == BankStatementLine.line_id,
                )
                .join(
                    BankStatement,
                    BankStatementLine.statement_id == BankStatement.statement_id,
                )
                .where(BankStatement.organization_id == organization_id)
            )
            .scalars()
            .all()
            if row is not None
        }

    def _resolve_gl_metadata(
        self,
        db: Session,
        gl_lines: _list[JournalEntryLine],
    ) -> dict:
        """Batch-resolve payment metadata for GL lines."""
        from app.services.finance.banking.payment_metadata import (
            resolve_payment_metadata_batch,
        )

        pairs: _list[tuple[str | None, UUID | None]] = []
        for gl_line in gl_lines:
            entry = getattr(gl_line, "journal_entry", None) or getattr(
                gl_line, "entry", None
            )
            if entry:
                pairs.append(
                    (
                        getattr(entry, "source_document_type", None),
                        getattr(entry, "source_document_id", None),
                    )
                )
            else:
                pairs.append((None, None))
        return resolve_payment_metadata_batch(db, pairs)

    def _resolve_suggestion_policy(
        self, db: Session, organization_id: UUID
    ) -> tuple[float, Decimal]:
        """Resolve the ``(suggest_threshold, amount_tolerance)`` to use when
        ranking match suggestions, from the org's ``ReconciliationPolicyProfile``.

        Keeps the workspace suggestion floor and amount window aligned with the
        same policy the Celery auto-engine honours, instead of hardcoded magic
        numbers. These values only decide *which suggestions surface* and *how
        wide* the amount window is — they NEVER confirm a match (confirmation
        always enforces an exact kobo tie via ``add_match``). Resolution is
        best-effort: if the policy can't be loaded we fall back to the dataclass
        defaults (70 / ₦0.01).
        """
        from app.services.finance.banking.auto_reconciliation_parts.base import (
            AutoMatchDefaults,
        )
        from app.services.finance.banking.reconciliation_policy_service import (
            reconciliation_policy_service,
        )

        try:
            policy = reconciliation_policy_service.resolve(
                db, organization_id, legacy_config=AutoMatchDefaults()
            )
        except Exception:
            logger.warning(
                "Could not resolve reconciliation policy for org %s; "
                "using default suggestion thresholds",
                organization_id,
                exc_info=True,
            )
            return 70.0, Decimal("0.01")
        return float(policy.suggest_threshold), policy.amount_tolerance

    def auto_match(
        self,
        db: Session,
        organization_id: UUID,
        reconciliation_id: UUID,
        tolerance: Decimal | None = None,
        created_by: UUID | None = None,
    ) -> AutoMatchResult:
        """Generate ranked match SUGGESTIONS (suggest-only).

        This never auto-confirms: it surfaces the best candidate per statement
        line for a human to confirm via ``add_match`` (which enforces the exact
        tie). ``matches_created`` is therefore always 0.

        The suggestion floor and amount tolerance come from the org's
        ``ReconciliationPolicyProfile`` (``suggest_threshold`` / ``amount_tolerance``)
        unless ``tolerance`` is passed explicitly.
        """
        reconciliation = self._get_for_org(db, organization_id, reconciliation_id)  # type: ignore[attr-defined]

        suggest_threshold, policy_tolerance = self._resolve_suggestion_policy(
            db, organization_id
        )
        if tolerance is None:
            tolerance = policy_tolerance

        result = AutoMatchResult(
            matches_found=0,
            matches_created=0,
            unmatched_statement_lines=0,
            unmatched_gl_lines=0,
        )

        statement_lines, gl_lines = self._get_unmatched_lines(
            db, organization_id, reconciliation
        )

        # Pre-resolve payment metadata for payee scoring
        gl_metadata = self._resolve_gl_metadata(db, gl_lines)

        # Build index of GL lines by amount for fast lookup
        gl_by_amount: dict[Decimal, _list[JournalEntryLine]] = {}
        for gl_line in gl_lines:
            amount = (gl_line.debit_amount or Decimal("0")) - (
                gl_line.credit_amount or Decimal("0")
            )
            if amount not in gl_by_amount:
                gl_by_amount[amount] = []
            gl_by_amount[amount].append(gl_line)

        matched_gl_ids: set[UUID] = set()

        for stmt_line in statement_lines:
            stmt_amount = stmt_line.signed_amount

            # Try exact match first
            potential_matches = gl_by_amount.get(stmt_amount, [])

            # Also try with tolerance
            if not potential_matches:
                for gl_amount in gl_by_amount:
                    if abs(gl_amount - stmt_amount) <= tolerance:
                        potential_matches.extend(gl_by_amount[gl_amount])

            # Find best match (by date proximity, reference, and payee)
            best_match = None
            best_score = 0.0

            for gl_line in potential_matches:
                if gl_line.line_id in matched_gl_ids:
                    continue

                score = self._calculate_match_score(
                    stmt_line,
                    gl_line,
                    db=db,
                    gl_metadata=gl_metadata,
                )
                if score > best_score:
                    best_score = score
                    best_match = gl_line

            # Suggest-only: NEVER auto-confirm a match. The score ranks a
            # candidate for human review; confirmation happens via add_match,
            # which enforces the exact (kobo) tie. The tolerance fallback above
            # only widens *suggestions*, it never confirms.
            if best_match and best_score >= suggest_threshold:
                result.matches_found += 1
                matched_gl_ids.add(best_match.line_id)
                result.match_details.append(
                    {
                        "statement_line_id": str(stmt_line.line_id),
                        "gl_line_id": str(best_match.line_id),
                        "confidence": best_score,
                        "suggested": True,
                    }
                )

        # Count remaining unmatched
        result.unmatched_statement_lines = len(
            [s for s in statement_lines if not s.is_matched]
        )
        result.unmatched_gl_lines = len(gl_lines) - len(matched_gl_ids)

        db.flush()
        return result

    def get_match_suggestions(
        self,
        db: Session,
        organization_id: UUID,
        reconciliation_id: UUID,
        min_confidence: float | None = None,
    ) -> dict[UUID, MatchSuggestion]:
        """Get best match suggestion per unmatched statement line.

        Returns a dict keyed by statement_line_id.  Read-only — does NOT
        create any matches. When ``min_confidence`` is not given, the
        suggestion floor and amount tolerance come from the org's
        ``ReconciliationPolicyProfile``.
        """
        reconciliation = self._get_for_org(db, organization_id, reconciliation_id)  # type: ignore[attr-defined]
        statement_lines, gl_lines = self._get_unmatched_lines(
            db, organization_id, reconciliation
        )

        if not statement_lines or not gl_lines:
            return {}

        policy_threshold, tolerance = self._resolve_suggestion_policy(
            db, organization_id
        )
        if min_confidence is None:
            min_confidence = policy_threshold

        gl_metadata = self._resolve_gl_metadata(db, gl_lines)

        # Build GL amount index
        gl_by_amount: dict[Decimal, _list[JournalEntryLine]] = {}
        for gl_line in gl_lines:
            amount = (gl_line.debit_amount or Decimal("0")) - (
                gl_line.credit_amount or Decimal("0")
            )
            gl_by_amount.setdefault(amount, []).append(gl_line)

        suggestions: dict[UUID, MatchSuggestion] = {}

        # Phase 1: compute best candidate + score for every statement line
        _Candidate = tuple[BankStatementLine, JournalEntryLine, float, bool]
        raw: list[_Candidate] = []

        for stmt_line in statement_lines:
            stmt_amount = stmt_line.signed_amount

            candidates = list(gl_by_amount.get(stmt_amount, []))
            amt_matched = bool(candidates)
            if not candidates:
                for gl_amount, lines in gl_by_amount.items():
                    if abs(gl_amount - stmt_amount) <= tolerance:
                        candidates.extend(lines)
                        amt_matched = True

            best_score = 0.0
            best_gl: JournalEntryLine | None = None

            for gl_line in candidates:
                score = self._calculate_match_score(
                    stmt_line, gl_line, db=db, gl_metadata=gl_metadata
                )
                if score > best_score:
                    best_score = score
                    best_gl = gl_line

            if best_gl and best_score >= min_confidence:
                raw.append((stmt_line, best_gl, best_score, amt_matched))

        # Phase 2: greedy assignment — highest score first, consume GL lines
        raw.sort(key=lambda r: r[2], reverse=True)
        consumed_gl_ids: set[UUID] = set()

        for stmt_line, best_gl, best_score, amt_matched in raw:
            if best_gl.line_id in consumed_gl_ids:
                continue
            consumed_gl_ids.add(best_gl.line_id)

            entry = getattr(best_gl, "journal_entry", None) or getattr(
                best_gl, "entry", None
            )
            source_doc_id = (
                getattr(entry, "source_document_id", None) if entry else None
            )
            src_type = getattr(entry, "source_document_type", None) if entry else None
            entry_id = getattr(entry, "entry_id", None) if entry else None
            meta = gl_metadata.get(source_doc_id) if source_doc_id else None

            suggestions[stmt_line.line_id] = MatchSuggestion(
                statement_line_id=stmt_line.line_id,
                journal_line_id=best_gl.line_id,
                confidence=best_score,
                counterparty_name=meta.counterparty_name if meta else None,
                payment_number=meta.payment_number if meta else None,
                source_url=_build_source_url(src_type, source_doc_id, entry_id),
                amount_matched=amt_matched,
            )

        return suggestions

    def get_persisted_suggestions(
        self,
        db: Session,
        organization_id: UUID,
        reconciliation_id: UUID,
    ) -> dict[UUID, MatchSuggestion]:
        """Return suggestions PERSISTED by the Celery auto-engine.

        The background engine writes ``bank_statement_line_matches`` rows with
        ``match_state='suggested'`` using strategies richer than the live
        workspace scorer (exact external reference, settlement windows, etc.).
        These never set ``is_matched`` and must be human-confirmed. Surfacing
        them in the workspace panel lets the accountant act on the engine's
        work instead of it sitting invisible until re-scored live.

        Keyed by statement_line_id; only still-unmatched lines in the
        reconciliation's account + period are returned. The highest-scored
        suggested row wins per statement line.
        """
        reconciliation = self._get_for_org(db, organization_id, reconciliation_id)  # type: ignore[attr-defined]

        rows = db.execute(
            select(BankStatementLineMatch, JournalEntryLine)
            .join(
                BankStatementLine,
                BankStatementLine.line_id == BankStatementLineMatch.statement_line_id,
            )
            .join(
                BankStatement,
                BankStatement.statement_id == BankStatementLine.statement_id,
            )
            .join(
                JournalEntryLine,
                JournalEntryLine.line_id == BankStatementLineMatch.journal_line_id,
            )
            .where(
                BankStatement.organization_id == organization_id,
                BankStatement.bank_account_id == reconciliation.bank_account_id,
                BankStatementLine.transaction_date >= reconciliation.period_start,
                BankStatementLine.transaction_date <= reconciliation.period_end,
                BankStatementLine.is_matched == False,  # noqa: E712
                BankStatementLineMatch.match_state == "suggested",
            )
            .order_by(BankStatementLineMatch.match_score.desc().nullslast())
        ).all()

        suggestions: dict[UUID, MatchSuggestion] = {}
        for match, gl_line in rows:
            stmt_id = match.statement_line_id
            if stmt_id in suggestions:
                continue  # highest score already kept (ordered desc)

            entry = getattr(gl_line, "journal_entry", None) or getattr(
                gl_line, "entry", None
            )
            entry_id = getattr(entry, "entry_id", None) if entry else None
            reason = match.match_reason
            reason_text = (
                reason.get("summary") or reason.get("reason")
                if isinstance(reason, dict)
                else (reason if isinstance(reason, str) else None)
            )

            suggestions[stmt_id] = MatchSuggestion(
                statement_line_id=stmt_id,
                journal_line_id=match.journal_line_id,
                confidence=float(match.match_score or 0),
                source_url=_build_source_url(
                    match.source_type, match.source_id, entry_id
                ),
                amount_matched=True,
                from_auto_engine=True,
                match_reason=reason_text,
            )

        return suggestions

    @staticmethod
    def _customer_root(db: Session, customer_id: UUID) -> UUID:
        """Walk ``parent_customer_id`` to the ultimate parent (cycle-safe).

        A reseller/aggregator or multi-branch customer's child accounts resolve
        to one root, so a single deposit settling several of its children is
        treated as ONE counterparty (legitimate), not many.
        """
        from app.models.finance.ar.customer import Customer

        seen: set[UUID] = set()
        cid = customer_id
        for _ in range(20):  # depth cap guards against bad cycles
            if cid in seen:
                break
            seen.add(cid)
            cust = db.get(Customer, cid)
            if cust is None or cust.parent_customer_id is None:
                break
            cid = cust.parent_customer_id
        return cid

    @staticmethod
    def _counterparty_key(
        db: Session, journal_line: JournalEntryLine
    ) -> tuple[str, UUID] | None:
        """Resolve a GL line to its counterparty's ROOT ``(kind, id)``.

        Customers resolve to their ultimate parent (so aggregator/branch
        batches collapse to one key); suppliers resolve to themselves. Returns
        ``None`` when the journal has no resolvable source document (legacy
        imported entries carry a NULL ``source_document_id``) — so the
        consistency guard treats such legs as unconstrained and never blocks
        historical data.
        """
        je = db.get(JournalEntry, journal_line.journal_entry_id)
        if je is None or je.source_document_id is None:
            return None
        sdt = (je.source_document_type or "").upper()
        sid = je.source_document_id
        cust_id: UUID | None = None
        if sdt in {"CUSTOMER_PAYMENT", "RECEIPT", "AR_PAYMENT_RESTORE"}:
            from app.models.finance.ar.customer_payment import CustomerPayment

            cp = db.get(CustomerPayment, sid)
            cust_id = cp.customer_id if cp else None
        elif sdt in {"INVOICE", "AR_INVOICE_RESTORE"}:
            from app.models.finance.ar.invoice import Invoice

            inv = db.get(Invoice, sid)
            cust_id = inv.customer_id if inv else None
        elif sdt == "SUPPLIER_PAYMENT":
            from app.models.finance.ap.supplier_payment import SupplierPayment

            sp = db.get(SupplierPayment, sid)
            return ("supplier", sp.supplier_id) if sp else None
        elif sdt == "SUPPLIER_INVOICE":
            from app.models.finance.ap.supplier_invoice import SupplierInvoice

            si = db.get(SupplierInvoice, sid)
            return ("supplier", si.supplier_id) if si else None
        if cust_id is None:
            return None
        return ("customer", ReconciliationMatchingService._customer_root(db, cust_id))

    @staticmethod
    def _gl_posting_date(db: Session, journal_line: JournalEntryLine) -> date | None:
        """Posting date of the GL line's journal entry (for the date-span flag)."""
        je = db.get(JournalEntry, journal_line.journal_entry_id)
        return je.posting_date if je else None

    def add_multi_match(
        self,
        db: Session,
        organization_id: UUID,
        reconciliation_id: UUID,
        statement_line_ids: _list[UUID],
        journal_line_ids: _list[UUID],
        tolerance: Decimal = Decimal("0.01"),
        notes: str | None = None,
        created_by: UUID | None = None,
    ) -> _list[BankReconciliationLine]:
        """Match multiple statement lines against multiple GL lines.

        Validates that sum(statement amounts) ≈ sum(GL amounts) within
        *tolerance*, then creates a reconciliation line for each
        statement→GL pair with match_type=split.
        """
        reconciliation = self._get_for_org(db, organization_id, reconciliation_id)  # type: ignore[attr-defined]

        if reconciliation.status not in (
            ReconciliationStatus.draft,
            ReconciliationStatus.pending_review,
        ):
            raise HTTPException(
                status_code=400,
                detail="Cannot modify an approved/rejected reconciliation",
            )

        # Load and validate statement lines
        stmt_lines: _list[BankStatementLine] = []
        for sid in statement_line_ids:
            line = db.get(BankStatementLine, sid)
            if not line:
                raise HTTPException(
                    status_code=404,
                    detail=f"Statement line {sid} not found",
                )
            stmt_lines.append(line)

        # Load and validate GL lines
        gl_lines_loaded: _list[JournalEntryLine] = []
        for gid in journal_line_ids:
            gl_line = db.get(JournalEntryLine, gid)
            if not gl_line:
                raise HTTPException(
                    status_code=404,
                    detail=f"Journal line {gid} not found",
                )
            gl_lines_loaded.append(gl_line)

        # Sum amounts
        stmt_total = sum((sl.signed_amount for sl in stmt_lines), Decimal("0"))
        gl_total = sum(
            (
                (gl.debit_amount or Decimal("0")) - (gl.credit_amount or Decimal("0"))
                for gl in gl_lines_loaded
            ),
            Decimal("0"),
        )

        # Exact-tie rule: a split must tie to the kobo. Any residual is a
        # reconciling item, never a tolerance allowance. (``tolerance`` retained
        # for signature compatibility; no longer used to permit a mismatch.)
        if stmt_total != gl_total:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot match: statement total {stmt_total:,.2f} does not tie "
                    f"to GL total {gl_total:,.2f} (difference "
                    f"{stmt_total - gl_total:,.2f}). Add a reconciling item for the "
                    "difference instead of an inexact split."
                ),
            )

        # A split is one-to-many (one bank line ↔ many GL entries, e.g. a lump
        # deposit covering several receipts) or many-to-one. True N:M is
        # ambiguous — ask the user to match in smaller groups.
        if len(stmt_lines) != 1 and len(gl_lines_loaded) != 1:
            raise HTTPException(
                status_code=400,
                detail=(
                    "A split must have a single bank line (matched to many GL "
                    "entries) or a single GL entry (matched to many bank lines)."
                ),
            )

        # Counterparty-consistency guard (HARD — existence/accuracy assertion):
        # a single bank movement settles ONE economic counterparty. For a
        # one-to-many split, each GL leg resolves to its counterparty's ULTIMATE
        # PARENT, so a reseller/aggregator or multi-branch customer paying for
        # several of its own child accounts in one deposit is allowed. Reject
        # only when legs roll up to genuinely DIFFERENT parents (the matcher
        # grouping unrelated payers to force an amount tie). Legacy legs with no
        # resolvable source document resolve to None and are skipped, so this
        # never blocks historical data.
        date_span_days = 0
        if len(stmt_lines) == 1 and len(gl_lines_loaded) > 1:
            parties = {
                key
                for key in (self._counterparty_key(db, gl) for gl in gl_lines_loaded)
                if key is not None
            }
            if len(parties) > 1:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Cannot match: the selected GL entries belong to "
                        f"{len(parties)} unrelated counterparties (different "
                        "parent customers). A single bank deposit settles one "
                        "payer or its child accounts — match unrelated payers "
                        "against their own bank lines."
                    ),
                )

            # Date-relation soft check (NOT a block — existence of timing
            # differences like deposits-in-transit / unpresented cheques is
            # normal and is handled as reconciling items). A wide spread is a
            # review flag, recorded for the audit trail.
            leg_dates = [
                d
                for d in (self._gl_posting_date(db, gl) for gl in gl_lines_loaded)
                if d is not None
            ]
            if len(leg_dates) > 1:
                date_span_days = (max(leg_dates) - min(leg_dates)).days
                if date_span_days > _SPLIT_DATE_SOFT_WINDOW_DAYS:
                    logger.warning(
                        "Split match on reconciliation %s spans %s days across "
                        "GL legs (review: timing difference vs mis-match).",
                        reconciliation_id,
                        date_span_days,
                    )

        now = datetime.now(tz=UTC)
        group_id = uuid4()
        created_lines: _list[BankReconciliationLine] = []
        seen_stmt: set[UUID] = set()
        seen_gl: set[UUID] = set()

        pairs = (
            [(stmt_lines[0], gl) for gl in gl_lines_loaded]
            if len(stmt_lines) == 1
            else [(sl, gl_lines_loaded[0]) for sl in stmt_lines]
        )
        for idx, (stmt_line, gl_line) in enumerate(pairs):
            gl_amount = (gl_line.debit_amount or Decimal("0")) - (
                gl_line.credit_amount or Decimal("0")
            )
            # Record each side's amount once so the group's recon lines sum to
            # the true totals (no double counting from repeated anchors).
            line_stmt_amount = (
                stmt_line.signed_amount
                if stmt_line.line_id not in seen_stmt
                else Decimal("0")
            )
            line_gl_amount = (
                gl_amount if gl_line.line_id not in seen_gl else Decimal("0")
            )
            seen_stmt.add(stmt_line.line_id)
            seen_gl.add(gl_line.line_id)

            recon_line = BankReconciliationLine(
                reconciliation_id=reconciliation_id,
                match_type=ReconciliationMatchType.split,
                statement_line_id=stmt_line.line_id,
                journal_line_id=gl_line.line_id,
                transaction_date=stmt_line.transaction_date,
                description=stmt_line.description,
                reference=stmt_line.reference,
                statement_amount=line_stmt_amount,
                gl_amount=line_gl_amount,
                difference=Decimal("0"),
                is_cleared=True,
                cleared_at=now,
                notes=notes,
                match_details=(
                    {"date_span_days": date_span_days} if date_span_days else None
                ),
                created_by=created_by,
            )
            db.add(recon_line)
            created_lines.append(recon_line)

            source_type, source_id = self._derive_match_source(gl_line)
            db.add(
                BankStatementLineMatch(
                    statement_line_id=stmt_line.line_id,
                    journal_line_id=gl_line.line_id,
                    matched_at=now,
                    matched_by=created_by,
                    is_primary=(idx == 0),
                    source_type=source_type,
                    source_id=source_id,
                    match_type="SPLIT",
                    match_state="confirmed",
                    confirmed_at=now,
                    confirmed_by=created_by,
                    match_group_id=group_id,
                )
            )

        # Mark statement lines as matched
        for stmt_line in stmt_lines:
            stmt_line.is_matched = True
            stmt_line.matched_at = now
            stmt_line.matched_by = created_by

        # Update reconciliation totals
        reconciliation.total_matched += abs(stmt_total)
        reconciliation.calculate_difference()

        db.flush()
        return created_lines

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------
    def _calculate_match_score(
        self,
        stmt_line: BankStatementLine,
        gl_line: JournalEntryLine,
        *,
        db: Session | None = None,
        gl_metadata: dict[UUID, PaymentMetadata] | None = None,
    ) -> float:
        """Calculate match confidence score.

        Base score: 0-100 (amount 35 + date 25 + reference 25 + payee 15).
        Bonus: up to +10 from categorization alignment.
        """
        score = 0.0

        # --- Amount match (35 points) ---
        stmt_amount = stmt_line.signed_amount
        gl_amount = (gl_line.debit_amount or Decimal("0")) - (
            gl_line.credit_amount or Decimal("0")
        )
        if stmt_amount == gl_amount:
            score += 35
        elif abs(stmt_amount - gl_amount) <= Decimal("0.01"):
            score += 30

        # --- Date proximity (25 points) ---
        entry = getattr(gl_line, "journal_entry", None) or getattr(
            gl_line, "entry", None
        )
        if entry:
            date_diff = abs((stmt_line.transaction_date - entry.entry_date).days)
            if date_diff == 0:
                score += 25
            elif date_diff <= 1:
                score += 20
            elif date_diff <= 3:
                score += 15
            elif date_diff <= 7:
                score += 8

        # --- Reference match (25 points) ---
        if stmt_line.reference and gl_line.description:
            if stmt_line.reference.lower() in gl_line.description.lower():
                score += 25
            elif stmt_line.description:
                stmt_words = set(stmt_line.description.lower().split())
                gl_words = set(gl_line.description.lower().split())
                common = stmt_words & gl_words
                if common:
                    score += min(len(common) * 5, 18)

        # --- Payee / counterparty name (15 points) ---
        meta = self._get_gl_line_metadata(gl_line, gl_metadata)
        if meta and meta.counterparty_name:
            score += self._calculate_payee_name_score(
                stmt_line.payee_payer, meta.counterparty_name
            )

        # --- Categorization bonus (up to +10) ---
        score += self._calculate_categorization_bonus(stmt_line, gl_line, meta, db=db)

        return score

    @staticmethod
    def _get_gl_line_metadata(
        gl_line: JournalEntryLine,
        gl_metadata: dict[UUID, PaymentMetadata] | None,
    ) -> PaymentMetadata | None:
        """Look up pre-resolved metadata for a GL line."""
        if not gl_metadata:
            return None
        entry = getattr(gl_line, "journal_entry", None) or getattr(
            gl_line, "entry", None
        )
        if not entry:
            return None
        source_doc_id = getattr(entry, "source_document_id", None)
        if not source_doc_id:
            return None
        return gl_metadata.get(source_doc_id)

    @staticmethod
    def _calculate_payee_name_score(
        statement_payee: str | None,
        counterparty_name: str | None,
    ) -> float:
        """Score how well the statement payee matches the counterparty name.

        Returns 0-15 points.
        """
        if not statement_payee or not counterparty_name:
            return 0.0

        sp = statement_payee.lower().strip()
        cn = counterparty_name.lower().strip()

        if not sp or not cn:
            return 0.0

        # Exact or substring match
        if sp == cn or sp in cn or cn in sp:
            return 15.0

        # Word overlap
        sp_words = set(sp.split())
        cn_words = set(cn.split())
        # Remove very short filler words
        filler = {"the", "of", "and", "ltd", "inc", "llc", "plc", "co"}
        sp_significant = sp_words - filler
        cn_significant = cn_words - filler

        if not sp_significant or not cn_significant:
            # Fall back to raw word sets
            sp_significant = sp_words
            cn_significant = cn_words

        common = sp_significant & cn_significant
        if not common:
            return 0.0

        overlap = len(common) / max(len(sp_significant), len(cn_significant))
        if overlap >= 0.5:
            return 12.0
        return 8.0

    @staticmethod
    def _calculate_categorization_bonus(
        stmt_line: BankStatementLine,
        gl_line: JournalEntryLine,
        meta: PaymentMetadata | None,
        *,
        db: Session | None = None,
    ) -> float:
        """Bonus points from statement categorization alignment.

        Returns 0-10 points (additive to base 100).
        """
        bonus = 0.0

        # Account match bonus (+5)
        suggested_account_id = getattr(stmt_line, "suggested_account_id", None)
        if suggested_account_id and suggested_account_id == gl_line.account_id:
            bonus += 5.0

        # Module match bonus (+3)
        entry = getattr(gl_line, "journal_entry", None) or getattr(
            gl_line, "entry", None
        )
        if entry:
            source_module = getattr(entry, "source_module", None)
            if source_module and meta:
                if (source_module == "AR" and meta.counterparty_type == "customer") or (
                    source_module == "AP" and meta.counterparty_type == "supplier"
                ):
                    bonus += 3.0

        # Payee-counterparty link bonus (+10)
        if db and meta and meta.counterparty_id:
            rule_id = getattr(stmt_line, "suggested_rule_id", None)
            if rule_id:
                try:
                    bonus += _check_rule_payee_link(db, rule_id, meta.counterparty_id)
                except Exception:
                    logger.debug("Could not check payee link for rule %s", rule_id)

        return bonus

    def get_statement_match_suggestions(
        self,
        db: Session,
        organization_id: UUID,
        statement_id: UUID,
        min_confidence: float | None = None,
        date_buffer_days: int = 7,
    ) -> dict[UUID, MatchSuggestion]:
        """Get best GL transaction match per unmatched statement line.

        Unlike ``get_match_suggestions`` which operates within a
        reconciliation, this works directly against a bank statement —
        enabling match suggestions on the statement detail page before a
        reconciliation is created.

        Returns a dict keyed by statement_line_id.  Read-only — does NOT
        create any matches. When ``min_confidence`` is not given, the
        suggestion floor and amount tolerance come from the org's
        ``ReconciliationPolicyProfile``.
        """
        from datetime import timedelta

        statement = db.get(BankStatement, statement_id)
        if not statement or statement.organization_id != organization_id:
            return {}

        policy_threshold, policy_tolerance = self._resolve_suggestion_policy(
            db, organization_id
        )
        if min_confidence is None:
            min_confidence = policy_threshold

        bank_account = statement.bank_account
        if not bank_account or not bank_account.gl_account_id:
            return {}

        # Get unmatched lines from this statement
        unmatched_lines = _list(
            db.execute(
                select(BankStatementLine).where(
                    and_(
                        BankStatementLine.statement_id == statement_id,
                        BankStatementLine.is_matched == False,  # noqa: E712
                    )
                )
            )
            .scalars()
            .all()
        )

        if not unmatched_lines:
            return {}

        # Get posted GL journal lines for the bank's GL account
        # within the statement period (with buffer for date proximity scoring)
        period_start = statement.period_start - timedelta(days=date_buffer_days)
        period_end = statement.period_end + timedelta(days=date_buffer_days)

        gl_lines = _list(
            db.execute(
                select(JournalEntryLine)
                .join(JournalEntry)
                .where(
                    and_(
                        JournalEntry.organization_id == organization_id,
                        JournalEntryLine.account_id == bank_account.gl_account_id,
                        JournalEntry.status == JournalStatus.POSTED,
                        JournalEntry.entry_date >= period_start,
                        JournalEntry.entry_date <= period_end,
                    )
                )
            )
            .scalars()
            .all()
        )

        if not gl_lines:
            return {}

        # Exclude GL lines already matched to other statement lines
        matched_gl_ids = self._get_matched_gl_ids(db, organization_id)
        gl_lines = [gl for gl in gl_lines if gl.line_id not in matched_gl_ids]

        if not gl_lines:
            return {}

        gl_metadata = self._resolve_gl_metadata(db, gl_lines)

        # Build GL amount index
        gl_by_amount: dict[Decimal, _list[JournalEntryLine]] = {}
        for gl_line in gl_lines:
            amount = (gl_line.debit_amount or Decimal("0")) - (
                gl_line.credit_amount or Decimal("0")
            )
            gl_by_amount.setdefault(amount, []).append(gl_line)

        suggestions: dict[UUID, MatchSuggestion] = {}
        tolerance = policy_tolerance

        # Phase 1: compute best candidate + score for every statement line
        _Candidate = tuple[BankStatementLine, JournalEntryLine, float, bool]
        raw: list[_Candidate] = []

        for stmt_line in unmatched_lines:
            stmt_amount = stmt_line.signed_amount

            # Primary pass: exact or near-exact amount match
            candidates = list(gl_by_amount.get(stmt_amount, []))
            amt_matched = bool(candidates)
            if not candidates:
                for gl_amount, lines in gl_by_amount.items():
                    if abs(gl_amount - stmt_amount) <= tolerance:
                        candidates.extend(lines)
                        amt_matched = True

            # Secondary pass: if no amount match, search by
            # description/reference keywords (e.g. email, payment ref)
            if not candidates:
                stmt_desc = (stmt_line.description or "").lower()
                stmt_ref = (stmt_line.reference or "").lower()
                search_text = f"{stmt_desc} {stmt_ref}".strip()
                if search_text:
                    stmt_words = {
                        w
                        for w in search_text.split()
                        if len(w) >= 4
                        and w
                        not in {
                            "via",
                            "bank",
                            "card",
                            "transfer",
                            "bank_transfer",
                            "payment:",
                        }
                    }
                    if stmt_words:
                        for gl_line in gl_lines:
                            entry = getattr(gl_line, "journal_entry", None) or getattr(
                                gl_line, "entry", None
                            )
                            gl_desc = (getattr(entry, "description", "") or "").lower()
                            gl_ref = (getattr(entry, "reference", "") or "").lower()
                            meta = self._get_gl_line_metadata(gl_line, gl_metadata)
                            cp_name = (
                                (meta.counterparty_name or "").lower() if meta else ""
                            )
                            gl_text = f"{gl_desc} {gl_ref} {cp_name}"
                            common = sum(1 for w in stmt_words if w in gl_text)
                            if common >= 1:
                                candidates.append(gl_line)

            best_score = 0.0
            best_gl: JournalEntryLine | None = None

            for gl_line in candidates:
                score = self._calculate_match_score(
                    stmt_line, gl_line, db=db, gl_metadata=gl_metadata
                )
                if score > best_score:
                    best_score = score
                    best_gl = gl_line

            if best_gl and best_score >= min_confidence:
                raw.append((stmt_line, best_gl, best_score, amt_matched))

        # Phase 2: greedy assignment — highest score first, consume GL lines
        raw.sort(key=lambda r: r[2], reverse=True)
        consumed_gl_ids: set[UUID] = set()

        for stmt_line, best_gl, best_score, amt_matched in raw:
            if best_gl.line_id in consumed_gl_ids:
                continue
            consumed_gl_ids.add(best_gl.line_id)

            entry = getattr(best_gl, "journal_entry", None) or getattr(
                best_gl, "entry", None
            )
            source_doc_id = (
                getattr(entry, "source_document_id", None) if entry else None
            )
            src_type = getattr(entry, "source_document_type", None) if entry else None
            entry_id = getattr(entry, "entry_id", None) if entry else None
            meta = gl_metadata.get(source_doc_id) if source_doc_id else None

            suggestions[stmt_line.line_id] = MatchSuggestion(
                statement_line_id=stmt_line.line_id,
                journal_line_id=best_gl.line_id,
                confidence=best_score,
                counterparty_name=meta.counterparty_name if meta else None,
                payment_number=meta.payment_number if meta else None,
                source_url=_build_source_url(src_type, source_doc_id, entry_id),
                amount_matched=amt_matched,
            )

        return suggestions

    def get_gl_candidates_for_statement(
        self,
        db: Session,
        organization_id: UUID,
        statement_id: UUID,
        max_results: int = 500,
    ) -> dict:
        """Return all posted GL journal lines for a statement's bank account.

        No date restriction — returns the most recent *max_results* entries
        so users can search and match freely.  Client-side filters in the
        modal let them narrow by date, source type, etc.

        Returns dict with keys:
            candidates: list of dicts with GL line details
            source_types: sorted list of distinct source_document_type values
        """
        statement = db.get(BankStatement, statement_id)
        if not statement or statement.organization_id != organization_id:
            return {"candidates": [], "source_types": []}

        bank_account = statement.bank_account
        if not bank_account or not bank_account.gl_account_id:
            return {"candidates": [], "source_types": []}

        # Get GL line IDs already matched so we can flag them in the UI
        matched_gl_ids = self._get_matched_gl_ids(db, organization_id)

        gl_lines: _list[JournalEntryLine] = _list(
            db.execute(
                select(JournalEntryLine)
                .join(JournalEntry)
                .where(
                    and_(
                        JournalEntry.organization_id == organization_id,
                        JournalEntryLine.account_id == bank_account.gl_account_id,
                        JournalEntry.status == JournalStatus.POSTED,
                    )
                )
                .order_by(JournalEntry.entry_date.desc(), JournalEntryLine.line_number)
                .limit(max_results)
            )
            .scalars()
            .all()
        )

        if not gl_lines:
            return {"candidates": [], "source_types": []}

        gl_metadata = self._resolve_gl_metadata(db, gl_lines)

        candidates: _list[dict] = []
        source_types: set[str] = set()
        for gl_line in gl_lines:
            entry = getattr(gl_line, "journal_entry", None) or getattr(
                gl_line, "entry", None
            )
            source_doc_id = (
                getattr(entry, "source_document_id", None) if entry else None
            )
            meta = gl_metadata.get(source_doc_id) if source_doc_id else None

            debit = gl_line.debit_amount or Decimal("0")
            credit = gl_line.credit_amount or Decimal("0")
            amount = debit - credit

            src_type = getattr(entry, "source_document_type", "") or ""
            if src_type:
                source_types.add(src_type)

            entry_id = getattr(entry, "entry_id", None) if entry else None
            source_url = _build_source_url(src_type, source_doc_id, entry_id)

            candidates.append(
                {
                    "journal_line_id": str(gl_line.line_id),
                    "entry_date": entry.entry_date.isoformat() if entry else "",
                    "entry_date_display": (
                        entry.entry_date.strftime("%d %b %Y") if entry else ""
                    ),
                    "description": (
                        getattr(entry, "description", "") or gl_line.description or ""
                    ),
                    "reference": getattr(entry, "reference", "") or "",
                    "amount": float(amount),
                    "amount_display": f"{amount:,.2f}",
                    "source_type": src_type,
                    "source_type_display": src_type.replace("_", " ").title()
                    if src_type
                    else "Journal",
                    "counterparty_name": meta.counterparty_name if meta else "",
                    "payment_number": meta.payment_number if meta else "",
                    "source_url": source_url,
                    "is_already_matched": gl_line.line_id in matched_gl_ids,
                }
            )

        return {
            "candidates": candidates,
            "source_types": sorted(source_types),
        }

    def get_scored_candidates_for_line(
        self,
        db: Session,
        organization_id: UUID,
        statement_id: UUID,
        statement_line_id: UUID,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        source_type: str | None = None,
        search: str | None = None,
        direction: str | None = None,
        hide_matched: bool = False,
        sort: str = "relevance",
        page: int = 1,
        per_page: int = 25,
    ) -> dict:
        """Return GL candidates scored against a specific bank statement line.

        Scores each candidate using ``_calculate_match_score()`` for the
        given bank line, then returns them sorted by score DESC.

        Supports server-side filtering, sorting, and pagination.

        Returns dict with keys:
            candidates: list of dicts (paginated)
            source_types: sorted list of distinct source_document_type values
            total: total number of matching candidates (before pagination)
            page: current page number
            per_page: items per page
            total_pages: total number of pages
        """
        from datetime import timedelta

        empty_result: dict = {
            "candidates": [],
            "source_types": [],
            "total": 0,
            "page": page,
            "per_page": per_page,
            "total_pages": 0,
        }

        stmt_line = db.get(BankStatementLine, statement_line_id)
        if not stmt_line:
            return empty_result

        statement = db.get(BankStatement, statement_id)
        if not statement or statement.organization_id != organization_id:
            return empty_result

        bank_account = statement.bank_account
        if not bank_account or not bank_account.gl_account_id:
            return empty_result

        # Include GL accounts from sibling bank accounts (same bank_name)
        # so we also surface customer payment entries posted to related
        # GL accounts (e.g. Paystack OPEX 1211 vs Paystack settlement 1210).
        all_bank_gl_ids: set[UUID] = {bank_account.gl_account_id}
        if bank_account.bank_name:
            sibling_gl_ids = set(
                db.scalars(
                    select(BankAccount.gl_account_id).where(
                        BankAccount.organization_id == organization_id,
                        BankAccount.bank_name == bank_account.bank_name,
                        BankAccount.gl_account_id.isnot(None),
                    )
                ).all()
            )
            all_bank_gl_ids.update(sibling_gl_ids)

        # Get GL line IDs already matched (scoped to this org)
        matched_gl_ids = self._get_matched_gl_ids(db, organization_id)

        # Date range: use explicit filters if provided, otherwise widen
        # around the statement period for broader candidate discovery.
        if date_from or date_to:
            period_start = date_from or statement.period_start
            period_end = date_to or statement.period_end
        else:
            date_buffer = timedelta(days=30)
            period_start = statement.period_start - date_buffer
            period_end = statement.period_end + date_buffer

        # Build base query conditions
        conditions = [
            JournalEntry.organization_id == organization_id,
            JournalEntryLine.account_id.in_(all_bank_gl_ids),
            JournalEntry.status == JournalStatus.POSTED,
            JournalEntry.entry_date >= period_start,
            JournalEntry.entry_date <= period_end,
        ]

        # Source type filter (applied at SQL level)
        if source_type:
            conditions.append(JournalEntry.source_document_type == source_type)

        gl_lines: _list[JournalEntryLine] = _list(
            db.execute(
                select(JournalEntryLine)
                .join(JournalEntry)
                .where(and_(*conditions))
                .order_by(JournalEntry.entry_date.desc())
            )
            .scalars()
            .all()
        )

        if not gl_lines:
            return empty_result

        gl_metadata = self._resolve_gl_metadata(db, gl_lines)

        scored: _list[tuple[float, dict]] = []
        source_types: set[str] = set()

        for gl_line in gl_lines:
            entry = getattr(gl_line, "journal_entry", None) or getattr(
                gl_line, "entry", None
            )
            source_doc_id = (
                getattr(entry, "source_document_id", None) if entry else None
            )
            meta = gl_metadata.get(source_doc_id) if source_doc_id else None

            debit = gl_line.debit_amount or Decimal("0")
            credit = gl_line.credit_amount or Decimal("0")
            amount = debit - credit

            src_type = getattr(entry, "source_document_type", "") or ""
            if src_type:
                source_types.add(src_type)

            is_matched = gl_line.line_id in matched_gl_ids

            # Apply post-query filters that cannot be done in SQL
            if hide_matched and is_matched:
                continue

            if direction == "debit" and amount <= 0:
                continue
            if direction == "credit" and amount >= 0:
                continue

            description = getattr(entry, "description", "") or gl_line.description or ""
            reference = getattr(entry, "reference", "") or ""
            counterparty_name = meta.counterparty_name if meta else ""
            payment_number = meta.payment_number if meta else ""

            # Text search filter (case-insensitive, matches description,
            # reference, counterparty, payment number, or amount)
            if search:
                q = search.lower()
                searchable = (
                    f"{description} {reference} {counterparty_name} "
                    f"{payment_number} {amount:,.2f}"
                ).lower()
                if q not in searchable:
                    continue

            entry_id = getattr(entry, "entry_id", None) if entry else None
            source_url = _build_source_url(src_type, source_doc_id, entry_id)

            # Score against this specific bank line
            score = self._calculate_match_score(
                stmt_line, gl_line, db=db, gl_metadata=gl_metadata
            )

            scored.append(
                (
                    score,
                    {
                        "journal_line_id": str(gl_line.line_id),
                        "entry_date": entry.entry_date.isoformat() if entry else "",
                        "entry_date_display": (
                            entry.entry_date.strftime("%d %b %Y") if entry else ""
                        ),
                        "description": description,
                        "reference": reference,
                        "amount": float(amount),
                        "amount_display": f"{amount:,.2f}",
                        "source_type": src_type,
                        "source_type_display": (
                            src_type.replace("_", " ").title()
                            if src_type
                            else "Journal"
                        ),
                        "counterparty_name": counterparty_name,
                        "payment_number": payment_number,
                        "source_url": source_url,
                        "is_already_matched": is_matched,
                        "match_score": round(score, 1),
                    },
                )
            )

        # Sort
        if sort == "amount" and stmt_line:
            bank_amt = abs(float(stmt_line.amount or 0))
            scored.sort(key=lambda x: abs(abs(x[1]["amount"]) - bank_amt))
        elif sort == "date":
            scored.sort(key=lambda x: x[1]["entry_date"], reverse=True)
        else:
            # Default: score DESC
            scored.sort(key=lambda x: x[0], reverse=True)

        total = len(scored)
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))
        start = (page - 1) * per_page
        end = start + per_page

        candidates = [item[1] for item in scored[start:end]]

        return {
            "candidates": candidates,
            "source_types": sorted(source_types),
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
        }

    def multi_match_statement_line(
        self,
        db: Session,
        organization_id: UUID,
        statement_line_id: UUID,
        journal_line_ids: _list[UUID],
        matched_by: UUID | None = None,
        tolerance: Decimal = Decimal("0.01"),
        force_match: bool = False,
    ) -> BankStatementLine:
        """Match one statement line to multiple GL journal lines.

        Validates that the sum of GL amounts approximates the bank line
        amount within *tolerance*, then creates junction table rows and
        updates the statement line.
        """
        from app.models.finance.banking.bank_statement import BankStatementLineMatch

        stmt_line = db.get(BankStatementLine, statement_line_id)
        if not stmt_line:
            raise HTTPException(
                status_code=404,
                detail=f"Statement line {statement_line_id} not found",
            )

        statement = stmt_line.statement
        if not statement or statement.organization_id != organization_id:
            raise HTTPException(
                status_code=404,
                detail=f"Statement line {statement_line_id} not found",
            )

        if stmt_line.is_matched:
            raise HTTPException(
                status_code=400,
                detail="Statement line is already matched",
            )

        if not journal_line_ids:
            raise HTTPException(
                status_code=400,
                detail="At least one journal_line_id is required",
            )

        # Load and validate all GL lines
        gl_lines: _list[JournalEntryLine] = []
        for jl_id in journal_line_ids:
            gl_line = db.get(JournalEntryLine, jl_id)
            if not gl_line:
                raise HTTPException(
                    status_code=404,
                    detail=f"Journal line {jl_id} not found",
                )
            entry = getattr(gl_line, "journal_entry", None) or getattr(
                gl_line, "entry", None
            )
            if entry:
                journal_org_id = getattr(entry, "organization_id", None)
                if journal_org_id is not None and journal_org_id != organization_id:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Journal line {jl_id} not found",
                    )
            gl_lines.append(gl_line)

        # Validate amount match: sum of GL amounts vs bank line
        stmt_amount = stmt_line.signed_amount
        gl_total = sum(
            (
                (gl.debit_amount or Decimal("0")) - (gl.credit_amount or Decimal("0"))
                for gl in gl_lines
            ),
            Decimal("0"),
        )

        if not force_match:
            self._validate_amount_match(stmt_amount, gl_total, force_match=False)  # type: ignore[attr-defined]

        # Remove any stale junction rows (idempotent cleanup)
        db.execute(
            delete(BankStatementLineMatch).where(
                BankStatementLineMatch.statement_line_id == statement_line_id
            )
        )

        # Create junction table rows
        now = datetime.now(tz=UTC)
        for i, gl_line in enumerate(gl_lines):
            source_type, source_id = self._derive_match_source(gl_line)
            match_row = BankStatementLineMatch(
                statement_line_id=statement_line_id,
                journal_line_id=gl_line.line_id,
                matched_at=now,
                matched_by=matched_by,
                is_primary=(i == 0),
                source_type=source_type,
                source_id=source_id,
            )
            db.add(match_row)

        # Update statement line — primary match is first GL line
        stmt_line.is_matched = True
        stmt_line.matched_at = now
        stmt_line.matched_by = matched_by

        # Update statement counters
        statement.matched_lines = (statement.matched_lines or 0) + 1
        statement.unmatched_lines = max((statement.unmatched_lines or 0) - 1, 0)

        db.flush()

        logger.info(
            "Multi-matched statement line %s to %d GL lines",
            statement_line_id,
            len(gl_lines),
        )

        return stmt_line

    def match_statement_line(
        self,
        db: Session,
        organization_id: UUID,
        statement_line_id: UUID,
        journal_line_id: UUID,
        matched_by: UUID | None = None,
        force_match: bool = False,
        source_type: str | None = None,
        source_id: UUID | None = None,
        match_state: str = "confirmed",
    ) -> BankStatementLine:
        """Directly match a statement line to a GL journal line.

        ``match_state='confirmed'`` (human action) sets ``is_matched`` and is
        validated for an exact tie. ``match_state='suggested'`` (auto-engine)
        only records a candidate junction row — it does NOT set ``is_matched``
        and is not amount-validated, because suggestions are never auto-applied.
        """
        stmt_line = db.get(BankStatementLine, statement_line_id)
        if not stmt_line:
            raise HTTPException(
                status_code=404,
                detail=f"Statement line {statement_line_id} not found",
            )

        # Validate ownership via statement
        statement = stmt_line.statement
        if not statement or statement.organization_id != organization_id:
            raise HTTPException(
                status_code=404,
                detail=f"Statement line {statement_line_id} not found",
            )

        existing_match = db.execute(
            select(BankStatementLineMatch).where(
                BankStatementLineMatch.statement_line_id == statement_line_id,
                BankStatementLineMatch.journal_line_id == journal_line_id,
            )
        ).scalar_one_or_none()

        # Idempotency: this exact pair already exists.
        # Keep statement flags in sync in case of legacy/stale states.
        if isinstance(existing_match, BankStatementLineMatch):
            # Heal stale is_matched for any existing match that isn't an explicit
            # suggestion (None/legacy rows are treated as confirmed).
            if not stmt_line.is_matched and (
                getattr(existing_match, "match_state", None) != "suggested"
            ):
                stmt_line.is_matched = True
                stmt_line.matched_at = existing_match.matched_at
                stmt_line.matched_by = existing_match.matched_by
                statement.matched_lines = (statement.matched_lines or 0) + 1
                statement.unmatched_lines = max((statement.unmatched_lines or 0) - 1, 0)
                db.flush()
            logger.info(
                "Statement line %s already matched to GL line %s; no-op",
                statement_line_id,
                journal_line_id,
            )
            return stmt_line

        if stmt_line.is_matched and match_state != "suggested":
            raise HTTPException(
                status_code=400,
                detail="Statement line is already matched",
            )

        # Validate GL line exists and belongs to org
        gl_line = db.get(JournalEntryLine, journal_line_id)
        if not gl_line:
            raise HTTPException(
                status_code=404,
                detail=f"Journal line {journal_line_id} not found",
            )
        journal_entry = getattr(gl_line, "journal_entry", None) or getattr(
            gl_line, "entry", None
        )
        if journal_entry:
            journal_org_id = getattr(journal_entry, "organization_id", None)
            if journal_org_id is not None and journal_org_id != organization_id:
                raise HTTPException(
                    status_code=404,
                    detail=f"Journal line {journal_line_id} not found",
                )

        statement_amount = stmt_line.signed_amount
        gl_amount = (gl_line.debit_amount or Decimal("0")) - (
            gl_line.credit_amount or Decimal("0")
        )
        now = datetime.now(tz=UTC)
        is_suggestion = match_state == "suggested"

        if not is_suggestion:
            # Confirmed matches must tie exactly and mark the line matched.
            # Suggestions are recorded without validation and never set
            # is_matched (a human confirms them later).
            self._validate_amount_match(  # type: ignore[attr-defined]
                statement_amount,
                gl_amount,
                force_match=force_match,
            )
            stmt_line.is_matched = True
            stmt_line.matched_at = now
            stmt_line.matched_by = matched_by
            # Remove any stale junction rows first (idempotent cleanup)
            db.execute(
                delete(BankStatementLineMatch).where(
                    BankStatementLineMatch.statement_line_id == statement_line_id
                )
            )

        # Fall back to deriving source from the linked journal entry when the
        # caller did not pass an explicit override (most callers don't).
        if source_type is None and source_id is None:
            source_type, source_id = self._derive_match_source(gl_line)

        match_row = BankStatementLineMatch(
            statement_line_id=statement_line_id,
            journal_line_id=journal_line_id,
            matched_at=now,
            matched_by=matched_by,
            is_primary=True,
            source_type=source_type,
            source_id=source_id,
            match_state=match_state,
            confirmed_at=now if not is_suggestion else None,
            confirmed_by=matched_by if not is_suggestion else None,
        )
        db.add(match_row)

        # Update statement counters only when a match is actually confirmed.
        if not is_suggestion:
            statement.matched_lines = (statement.matched_lines or 0) + 1
            statement.unmatched_lines = max((statement.unmatched_lines or 0) - 1, 0)

        db.flush()

        logger.info(
            "Matched statement line %s to GL line %s (direct, source=%s/%s)",
            statement_line_id,
            journal_line_id,
            source_type or "none",
            source_id or "none",
        )

        return stmt_line

    def unmatch_statement_line(
        self,
        db: Session,
        organization_id: UUID,
        statement_line_id: UUID,
    ) -> BankStatementLine:
        """Remove a direct match from a statement line.

        Only works for lines matched directly (not via reconciliation).
        """
        stmt_line = db.get(BankStatementLine, statement_line_id)
        if not stmt_line:
            raise HTTPException(
                status_code=404,
                detail=f"Statement line {statement_line_id} not found",
            )

        statement = stmt_line.statement
        if not statement or statement.organization_id != organization_id:
            raise HTTPException(
                status_code=404,
                detail=f"Statement line {statement_line_id} not found",
            )

        if not stmt_line.is_matched:
            raise HTTPException(
                status_code=400,
                detail="Statement line is not matched",
            )

        # Delete all junction table rows for this line
        from sqlalchemy import delete as sa_delete

        from app.models.finance.banking.bank_statement import BankStatementLineMatch

        db.execute(
            sa_delete(BankStatementLineMatch).where(
                BankStatementLineMatch.statement_line_id == statement_line_id
            )
        )

        # Clear match
        stmt_line.is_matched = False
        stmt_line.matched_at = None
        stmt_line.matched_by = None

        # Update statement counters
        statement.matched_lines = max((statement.matched_lines or 0) - 1, 0)
        statement.unmatched_lines = (statement.unmatched_lines or 0) + 1

        db.flush()

        logger.info("Unmatched statement line %s (direct)", statement_line_id)

        return stmt_line

    def create_journal_and_match(
        self,
        db: Session,
        organization_id: UUID,
        statement_line_id: UUID,
        counterparty_account_id: UUID,
        description: str | None = None,
        matched_by: UUID | None = None,
    ) -> BankStatementLine:
        """Create a journal entry for a bank line and match it immediately.

        This handles the case where a bank line has no GL entry to match
        against.  Creates a 2-line journal (Dr bank GL / Cr counterparty GL
        for credits, or Dr counterparty GL / Cr bank GL for debits), posts
        it, and matches the bank line to the journal's bank-side line.

        Args:
            db: Database session
            organization_id: Organization scope
            statement_line_id: Bank statement line to match
            counterparty_account_id: GL account for the other side
            description: Optional journal description override
            matched_by: User performing the match

        Returns:
            Updated BankStatementLine

        Raises:
            HTTPException(404): Statement line not found
            HTTPException(400): Line already matched, or journal creation fails
        """
        from app.models.finance.gl.journal_entry import JournalType
        from app.services.finance.gl.journal import (
            JournalInput,
            JournalLineInput,
            JournalService,
        )

        stmt_line = db.get(BankStatementLine, statement_line_id)
        if not stmt_line:
            raise HTTPException(
                status_code=404,
                detail=f"Statement line {statement_line_id} not found",
            )

        statement = stmt_line.statement
        if not statement or statement.organization_id != organization_id:
            raise HTTPException(
                status_code=404,
                detail=f"Statement line {statement_line_id} not found",
            )

        if stmt_line.is_matched:
            raise HTTPException(
                status_code=400,
                detail="Statement line is already matched",
            )

        # Get the bank account's GL account
        bank_account = db.get(BankAccount, statement.bank_account_id)
        if not bank_account or not bank_account.gl_account_id:
            raise HTTPException(
                status_code=400,
                detail="Bank account has no linked GL account",
            )

        bank_gl_account_id = bank_account.gl_account_id
        amount = stmt_line.amount  # Always positive
        is_credit = str(stmt_line.transaction_type).lower() == "credit"

        # Build description
        if not description:
            description = (
                f"Bank {('deposit' if is_credit else 'payment')}: "
                f"{stmt_line.description or ''}"
            )[:200]

        # For a credit (money IN): Dr bank GL, Cr counterparty GL
        # For a debit (money OUT): Dr counterparty GL, Cr bank GL
        if is_credit:
            line1 = JournalLineInput(
                account_id=bank_gl_account_id,
                debit_amount=amount,
                credit_amount=Decimal("0"),
                description=description[:200],
            )
            line2 = JournalLineInput(
                account_id=counterparty_account_id,
                debit_amount=Decimal("0"),
                credit_amount=amount,
                description=description[:200],
            )
            bank_line_idx = 0  # line1 is the bank side
        else:
            line1 = JournalLineInput(
                account_id=counterparty_account_id,
                debit_amount=amount,
                credit_amount=Decimal("0"),
                description=description[:200],
            )
            line2 = JournalLineInput(
                account_id=bank_gl_account_id,
                debit_amount=Decimal("0"),
                credit_amount=amount,
                description=description[:200],
            )
            bank_line_idx = 1  # line2 is the bank side

        user_id = matched_by or UUID("00000000-0000-0000-0000-000000000000")

        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=stmt_line.transaction_date,
            posting_date=stmt_line.transaction_date,
            description=description[:200],
            lines=[line1, line2],
            source_module="BANKING",
            source_document_type="BANK_RECONCILIATION",
        )

        # Create journal
        journal = JournalService.create_journal(
            db, organization_id, journal_input, user_id
        )

        # Submit and approve
        JournalService.submit_journal(
            db, organization_id, journal.journal_entry_id, user_id
        )
        JournalService.approve_journal(
            db, organization_id, journal.journal_entry_id, user_id
        )

        # Post
        JournalService.post_journal(
            db,
            organization_id,
            journal.journal_entry_id,
            user_id,
            idempotency_key=f"bank-recon:{statement_line_id}",
        )

        # Get the bank-side journal line for matching
        journal_lines = list(
            db.scalars(
                select(JournalEntryLine)
                .where(JournalEntryLine.journal_entry_id == journal.journal_entry_id)
                .order_by(JournalEntryLine.line_number)
            ).all()
        )
        bank_journal_line = journal_lines[bank_line_idx]

        # Match
        self.match_statement_line(
            db=db,
            organization_id=organization_id,
            statement_line_id=statement_line_id,
            journal_line_id=bank_journal_line.line_id,
            matched_by=matched_by,
            force_match=True,
        )

        logger.info(
            "Created journal %s and matched to statement line %s",
            journal.journal_number,
            statement_line_id,
        )

        return stmt_line
