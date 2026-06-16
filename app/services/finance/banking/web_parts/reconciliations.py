"""BankingReconciliationWebService component."""

from __future__ import annotations

from typing import cast

from app.services.finance.banking.web_parts.base import (
    Any,
    BankAccount,
    BankAccountStatus,
    BankReconciliation,
    BankStatement,
    BankStatementLine,
    BankStatementLineMatch,
    Decimal,
    HTMLResponse,
    HTTPException,
    JSONResponse,
    JournalEntry,
    JournalEntryLine,
    JournalStatus,
    ReconciliationStatus,
    RedirectResponse,
    Request,
    Response,
    Session,
    UUID,
    htmx_response,
    is_htmx_request,
    WebAuthContext,
    _account_view,
    _build_active_filters,
    _format_currency,
    _format_date,
    _gl_line_view,
    _line_amount,
    _parse_date,
    _parse_reconciliation_status,
    _reconciliation_line_view,
    _reconciliation_view,
    _statement_line_view,
    base_context,
    coerce_uuid,
    date,
    func,
    logger,
    resolve_payment_metadata_batch,
    select,
    templates,
)

# Page sizes for the reconciliation detail page tables.
_MATCHED_PAGE_SIZE = 100
_UNMATCHED_PAGE_SIZE = 100


class BankingReconciliationWebService:
    """Banking web service methods for reconciliations."""

    @staticmethod
    def list_reconciliations_context(
        db: Session,
        organization_id: str,
        account_id: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
        page: int,
        limit: int = 50,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        status_value = _parse_reconciliation_status(status)
        from_date = _parse_date(start_date)
        to_date = _parse_date(end_date)

        conditions: list[Any] = [BankReconciliation.organization_id == org_id]

        if account_id:
            conditions.append(
                BankReconciliation.bank_account_id == coerce_uuid(account_id)
            )
        if status_value:
            conditions.append(BankReconciliation.status == status_value)
        if from_date:
            conditions.append(BankReconciliation.reconciliation_date >= from_date)
        if to_date:
            conditions.append(BankReconciliation.reconciliation_date <= to_date)

        total_count = (
            db.scalar(
                select(func.count(BankReconciliation.reconciliation_id)).where(
                    *conditions
                )
            )
            or 0
        )
        reconciliations = db.scalars(
            select(BankReconciliation)
            .where(*conditions)
            .order_by(BankReconciliation.reconciliation_date.desc())
            .limit(limit)
            .offset(offset)
        ).all()

        accounts = db.scalars(
            select(BankAccount)
            .where(BankAccount.organization_id == org_id)
            .order_by(BankAccount.bank_name, BankAccount.account_number)
        ).all()

        in_progress_count = sum(
            1 for recon in reconciliations if recon.status == ReconciliationStatus.draft
        )
        pending_review_count = sum(
            1
            for recon in reconciliations
            if recon.status == ReconciliationStatus.pending_review
        )
        approved_count = sum(
            1
            for recon in reconciliations
            if recon.status == ReconciliationStatus.approved
        )

        total_pages = max(1, (total_count + limit - 1) // limit)

        account_views = [_account_view(account) for account in accounts]
        active_filters = _build_active_filters(
            account_id=account_id,
            accounts=account_views,
            status=status,
            start_date=start_date,
            end_date=end_date,
        )

        return {
            "reconciliations": [
                _reconciliation_view(recon) for recon in reconciliations
            ],
            "accounts": account_views,
            "account_id": account_id,
            "status": status,
            "start_date": start_date,
            "end_date": end_date,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
            "in_progress_count": in_progress_count,
            "pending_review_count": pending_review_count,
            "approved_count": approved_count,
            "statuses": [s.value for s in ReconciliationStatus],
            "active_filters": active_filters,
        }

    @staticmethod
    def reconciliation_form_context(
        db: Session,
        organization_id: str,
        *,
        account_id: str | None = None,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        accounts = db.scalars(
            select(BankAccount)
            .where(
                BankAccount.organization_id == org_id,
                BankAccount.status == BankAccountStatus.active,
            )
            .order_by(BankAccount.bank_name, BankAccount.account_number)
        ).all()
        context: dict = {"accounts": [_account_view(account) for account in accounts]}
        if account_id:
            context["selected_account_id"] = account_id
        return context

    @staticmethod
    def reconciliation_detail_context(
        db: Session,
        organization_id: str,
        reconciliation_id: str,
        matched_page: int = 1,
        stmt_page: int = 1,
        gl_page: int = 1,
    ) -> dict:
        from app.services.finance.banking.bank_reconciliation import (
            bank_reconciliation_service as recon_svc,
        )

        org_id = coerce_uuid(organization_id)
        reconciliation = db.get(BankReconciliation, coerce_uuid(reconciliation_id))
        if not reconciliation or reconciliation.organization_id != org_id:
            return {
                "reconciliation": None,
                "lines": [],
                "unmatched_statement_lines": [],
                "unmatched_gl_lines": [],
                "match_suggestions": {},
            }

        bank_account = reconciliation.bank_account

        # Unmatched statement lines — paginated (a busy account/period can have
        # thousands; rendering them all bloats/times out the page).
        _stmt_base = (
            select(BankStatementLine)
            .join(
                BankStatement,
                BankStatementLine.statement_id == BankStatement.statement_id,
            )
            .where(
                BankStatement.organization_id == org_id,
                BankStatement.bank_account_id == reconciliation.bank_account_id,
                BankStatementLine.is_matched.is_(False),
                BankStatementLine.transaction_date >= reconciliation.period_start,
                BankStatementLine.transaction_date <= reconciliation.period_end,
            )
        )
        stmt_total = (
            db.scalar(select(func.count()).select_from(_stmt_base.subquery())) or 0
        )
        stmt_pages = max(
            1, (stmt_total + _UNMATCHED_PAGE_SIZE - 1) // _UNMATCHED_PAGE_SIZE
        )
        stmt_page = min(max(1, stmt_page), stmt_pages)
        statement_lines = db.scalars(
            _stmt_base.order_by(
                BankStatementLine.transaction_date, BankStatementLine.line_number
            )
            .limit(_UNMATCHED_PAGE_SIZE)
            .offset((stmt_page - 1) * _UNMATCHED_PAGE_SIZE)
        ).all()

        # Unmatched GL entries — paginated for the same reason.
        gl_lines: list[tuple[JournalEntryLine, JournalEntry]] = []
        gl_total = 0
        gl_pages = 1
        gl_page = max(1, gl_page)
        if bank_account:
            _gl_base = (
                select(JournalEntryLine, JournalEntry)
                .join(
                    JournalEntry,
                    JournalEntryLine.journal_entry_id == JournalEntry.journal_entry_id,
                )
                .where(
                    JournalEntry.organization_id == org_id,
                    JournalEntryLine.account_id == bank_account.gl_account_id,
                    JournalEntry.status == JournalStatus.POSTED,
                    JournalEntry.entry_date >= reconciliation.period_start,
                    JournalEntry.entry_date <= reconciliation.period_end,
                )
            )
            gl_total = (
                db.scalar(select(func.count()).select_from(_gl_base.subquery())) or 0
            )
            gl_pages = max(
                1, (gl_total + _UNMATCHED_PAGE_SIZE - 1) // _UNMATCHED_PAGE_SIZE
            )
            gl_page = min(gl_page, gl_pages)
            gl_lines = cast(
                list[tuple[JournalEntryLine, JournalEntry]],
                db.execute(
                    _gl_base.order_by(
                        JournalEntry.entry_date, JournalEntryLine.line_number
                    )
                    .limit(_UNMATCHED_PAGE_SIZE)
                    .offset((gl_page - 1) * _UNMATCHED_PAGE_SIZE)
                ).all(),
            )

        # Batch-resolve payment metadata for GL lines
        metadata_pairs: list[tuple[str | None, UUID | None]] = [
            (
                getattr(entry, "source_document_type", None),
                getattr(entry, "source_document_id", None),
            )
            for _line, entry in gl_lines
        ]
        metadata_map = resolve_payment_metadata_batch(db, metadata_pairs)

        # Build GL line views with metadata
        unmatched_statement_lines = [
            _statement_line_view(line) for line in statement_lines
        ]
        statement_line_amounts = {
            str(line.line_id): float(line.signed_amount) for line in statement_lines
        }
        unmatched_gl_lines = []
        gl_line_amounts: dict[str, float] = {}
        for line, entry in gl_lines:
            doc_id = getattr(entry, "source_document_id", None)
            meta = metadata_map.get(doc_id) if doc_id else None
            line_view = _gl_line_view(line, entry, metadata=meta)
            unmatched_gl_lines.append(line_view)
            gl_line_amounts[str(line.line_id)] = float(
                line_view.get("signed_amount", 0)
            )

        # Generate match suggestions for draft/pending reconciliations
        match_suggestions: dict[str, dict] = {}
        if reconciliation.status in (
            ReconciliationStatus.draft,
            ReconciliationStatus.pending_review,
        ):
            try:
                # Live workspace suggestions first, then overlay suggestions the
                # Celery auto-engine already persisted (match_state='suggested') —
                # the engine's strategy-based matches win per statement line.
                raw_suggestions = recon_svc.get_match_suggestions(
                    db, org_id, reconciliation.reconciliation_id
                )
                persisted = recon_svc.get_persisted_suggestions(
                    db, org_id, reconciliation.reconciliation_id
                )
                for stmt_id, sug in {**raw_suggestions, **persisted}.items():
                    match_suggestions[str(stmt_id)] = {
                        "journal_line_id": str(sug.journal_line_id),
                        "confidence": round(sug.confidence, 1),
                        "counterparty_name": sug.counterparty_name or "",
                        "payment_number": sug.payment_number or "",
                        "source_url": sug.source_url or "",
                        "amount_matched": sug.amount_matched,
                        "from_auto_engine": sug.from_auto_engine,
                        "match_reason": sug.match_reason or "",
                    }
            except Exception:
                logger.exception("Failed to generate match suggestions")

        # Matched Items: derive from CONFIRMED junction rows for this account +
        # period (the authoritative store) so matches made by the workspace OR
        # the auto-engine both appear and can be viewed/unmatched. Fixes the
        # "0 matches" bug where the view only read reconciliation.lines.
        matched_base = (
            select(
                BankStatementLineMatch,
                BankStatementLine,
                JournalEntryLine,
            )
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
                BankStatement.organization_id == org_id,
                BankStatement.bank_account_id == reconciliation.bank_account_id,
                BankStatementLine.transaction_date >= reconciliation.period_start,
                BankStatementLine.transaction_date <= reconciliation.period_end,
                BankStatementLineMatch.match_state == "confirmed",
            )
        )
        # Paginate: a fully-reconciled account can have thousands of matches;
        # rendering them all at once times the page out.
        matched_total = (
            db.scalar(select(func.count()).select_from(matched_base.subquery())) or 0
        )
        matched_pages = max(
            1, (matched_total + _MATCHED_PAGE_SIZE - 1) // _MATCHED_PAGE_SIZE
        )
        matched_page = min(max(1, matched_page), matched_pages)
        matched_rows = db.execute(
            matched_base.order_by(BankStatementLine.transaction_date)
            .limit(_MATCHED_PAGE_SIZE)
            .offset((matched_page - 1) * _MATCHED_PAGE_SIZE)
        ).all()

        matched_items: list[dict] = []
        for match, sl, jl in matched_rows:
            gl_amount = (jl.debit_amount or Decimal("0")) - (
                jl.credit_amount or Decimal("0")
            )
            matched_items.append(
                {
                    "match_id": str(match.match_id),
                    "statement_line_id": str(sl.line_id),
                    "transaction_date": _format_date(sl.transaction_date),
                    "description": sl.description or "",
                    "reference": sl.reference or "",
                    "statement_amount": float(sl.signed_amount),
                    "gl_amount": float(gl_amount),
                    "difference": float(sl.signed_amount - gl_amount),
                    "match_type": (match.match_type or "MANUAL").lower(),
                    "match_group_id": (
                        str(match.match_group_id) if match.match_group_id else None
                    ),
                    "source_type": match.source_type or "",
                    "source_id": str(match.source_id) if match.source_id else None,
                }
            )

        # Reconciling items (adjustments + outstanding) live on the recon lines.
        reconciling_items = [
            _reconciliation_line_view(line)
            for line in reconciliation.lines
            if line.is_adjustment or line.is_outstanding
        ]

        return {
            "reconciliation": _reconciliation_view(reconciliation),
            "lines": matched_items,
            "reconciling_items": reconciling_items,
            "unmatched_statement_lines": unmatched_statement_lines,
            "unmatched_gl_lines": unmatched_gl_lines,
            "match_suggestions": match_suggestions,
            "statement_line_amounts": statement_line_amounts,
            "gl_line_amounts": gl_line_amounts,
            "matched_total": matched_total,
            "matched_page": matched_page,
            "matched_pages": matched_pages,
            "matched_page_size": _MATCHED_PAGE_SIZE,
            "stmt_total": stmt_total,
            "stmt_page": stmt_page,
            "stmt_pages": stmt_pages,
            "gl_total": gl_total,
            "gl_page": gl_page,
            "gl_pages": gl_pages,
            "unmatched_page_size": _UNMATCHED_PAGE_SIZE,
        }

    @staticmethod
    def reconciliation_report_context(
        db: Session,
        organization_id: str,
        reconciliation_id: str,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        reconciliation = db.get(BankReconciliation, coerce_uuid(reconciliation_id))
        if not reconciliation or reconciliation.organization_id != org_id:
            return {"report": None}

        recon_view = _reconciliation_view(reconciliation)

        matched_lines = [
            line
            for line in reconciliation.lines
            if not line.is_outstanding and not line.is_adjustment
        ]
        outstanding_deposits = [
            line
            for line in reconciliation.lines
            if line.is_outstanding and line.outstanding_type == "deposit"
        ]
        outstanding_payments = [
            line
            for line in reconciliation.lines
            if line.is_outstanding and line.outstanding_type == "payment"
        ]
        adjustments = [line for line in reconciliation.lines if line.is_adjustment]

        total_matched = sum(
            (_line_amount(line) for line in matched_lines), Decimal("0")
        )
        total_deposits = sum(
            (_line_amount(line) for line in outstanding_deposits), Decimal("0")
        )
        total_payments = sum(
            (_line_amount(line) for line in outstanding_payments), Decimal("0")
        )
        total_adjustments = sum(
            (_line_amount(line) for line in adjustments), Decimal("0")
        )

        statement_balance = Decimal(str(reconciliation.statement_closing_balance or 0))
        gl_balance = Decimal(str(reconciliation.gl_closing_balance or 0))
        adjusted_statement = statement_balance - total_payments + total_deposits
        adjusted_gl = gl_balance + total_adjustments
        difference = adjusted_statement - adjusted_gl

        report = {
            "reconciliation": recon_view,
            "summary": {
                "statement_balance": _format_currency(
                    statement_balance, reconciliation.currency_code
                ),
                "gl_balance": _format_currency(
                    gl_balance, reconciliation.currency_code
                ),
                "adjusted_book_balance": _format_currency(
                    adjusted_statement, reconciliation.currency_code
                ),
                "difference": _format_currency(
                    difference, reconciliation.currency_code
                ),
                "is_reconciled": difference == Decimal("0"),
            },
            "matched_items": {
                "count": len(matched_lines),
                "total": _format_currency(total_matched, reconciliation.currency_code),
                "items": [_reconciliation_line_view(line) for line in matched_lines],
            },
            "outstanding_deposits": {
                "count": len(outstanding_deposits),
                "total": _format_currency(total_deposits, reconciliation.currency_code),
                "items": [
                    _reconciliation_line_view(line) for line in outstanding_deposits
                ],
            },
            "outstanding_payments": {
                "count": len(outstanding_payments),
                "total": _format_currency(total_payments, reconciliation.currency_code),
                "items": [
                    _reconciliation_line_view(line) for line in outstanding_payments
                ],
            },
            "adjustments": {
                "count": len(adjustments),
                "total": _format_currency(
                    total_adjustments, reconciliation.currency_code
                ),
                "items": [_reconciliation_line_view(line) for line in adjustments],
            },
        }

        return {"report": report}

    # =========================================================================
    # Payee Context Methods
    # =========================================================================
    def list_reconciliations_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        account_id: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
        page: int,
        limit: int = 50,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Bank Reconciliations", "banking", db=db)
        context.update(
            self.list_reconciliations_context(
                db,
                str(auth.organization_id),
                account_id=account_id,
                status=status,
                start_date=start_date,
                end_date=end_date,
                page=page,
                limit=limit,
            )
        )
        return templates.TemplateResponse(
            request,
            "finance/banking/reconciliations.html",
            context,
        )

    def reconciliation_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        account_id: str | None = None,
    ) -> HTMLResponse:
        context = base_context(request, auth, "New Reconciliation", "banking", db=db)
        context.update(
            self.reconciliation_form_context(
                db, str(auth.organization_id), account_id=account_id
            )
        )
        return templates.TemplateResponse(
            request,
            "finance/banking/reconciliation_form.html",
            context,
        )

    def reconciliation_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        reconciliation_id: str,
        matched_page: int = 1,
        stmt_page: int = 1,
        gl_page: int = 1,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Bank Reconciliation", "banking", db=db)
        context.update(
            self.reconciliation_detail_context(
                db,
                str(auth.organization_id),
                reconciliation_id,
                matched_page=matched_page,
                stmt_page=stmt_page,
                gl_page=gl_page,
            )
        )
        return templates.TemplateResponse(
            request, "finance/banking/reconciliation.html", context
        )

    def reconciliation_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        reconciliation_id: str,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Reconciliation Report", "banking", db=db)
        context.update(
            self.reconciliation_report_context(
                db,
                str(auth.organization_id),
                reconciliation_id,
            )
        )
        return templates.TemplateResponse(
            request,
            "finance/banking/reconciliation_report.html",
            context,
        )

    async def create_reconciliation_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        redirect_cls: type,
    ) -> Any:
        """Create a reconciliation from the web form and redirect to detail page."""
        from decimal import Decimal, InvalidOperation
        from uuid import UUID as _UUID

        from app.services.finance.banking.bank_reconciliation import (
            BankReconciliationService,
            ReconciliationInput,
        )

        form = await request.form()
        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        try:
            bank_account_id = _UUID(str(form.get("bank_account_id", "")))
            inp = ReconciliationInput(
                reconciliation_date=date.fromisoformat(
                    str(form.get("reconciliation_date", ""))
                ),
                period_start=date.fromisoformat(str(form.get("period_start", ""))),
                period_end=date.fromisoformat(str(form.get("period_end", ""))),
                statement_opening_balance=Decimal(
                    str(form.get("statement_opening_balance", "0"))
                ),
                statement_closing_balance=Decimal(
                    str(form.get("statement_closing_balance", "0"))
                ),
                notes=str(form.get("notes", "")) or None,
            )
        except (ValueError, InvalidOperation) as e:
            logger.warning("Invalid reconciliation form data: %s", e)
            # Re-render form with error
            context = base_context(
                request, auth, "New Reconciliation", "banking", db=db
            )
            context.update(self.reconciliation_form_context(db, str(org_id)))
            context["error"] = f"Invalid form data: {e}"
            return templates.TemplateResponse(
                request,
                "finance/banking/reconciliation_form.html",
                context,
            )

        svc = BankReconciliationService()
        try:
            user_id = getattr(auth, "user_id", None) or getattr(auth, "person_id", None)
            recon = svc.create_reconciliation(
                db=db,
                organization_id=org_id,
                bank_account_id=bank_account_id,
                input=inp,
                prepared_by=user_id,
            )
            db.flush()
            db.commit()
        except HTTPException:
            raise
        except (ValueError, RuntimeError) as e:
            logger.warning("Reconciliation creation failed: %s", e)
            context = base_context(
                request, auth, "New Reconciliation", "banking", db=db
            )
            context.update(self.reconciliation_form_context(db, str(org_id)))
            context["error"] = str(e)
            return templates.TemplateResponse(
                request,
                "finance/banking/reconciliation_form.html",
                context,
            )

        return redirect_cls(
            url=f"/finance/banking/reconciliations/{recon.reconciliation_id}",
            status_code=303,
        )

    async def reconciliation_action_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        reconciliation_id: str,
        action: str,
    ) -> Response:
        """Handle reconciliation lifecycle actions (auto-match, submit, approve, reject)."""
        from app.services.finance.banking.bank_reconciliation import (
            BankReconciliationService,
        )

        await request.form()  # consume form body for CSRF validation
        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        svc = BankReconciliationService()
        recon_uuid = UUID(reconciliation_id)
        user_id = getattr(auth, "user_id", None) or getattr(auth, "person_id", None)

        try:
            if action == "auto_match":
                svc.auto_match(
                    db=db,
                    organization_id=org_id,
                    reconciliation_id=recon_uuid,
                    created_by=user_id,
                )
            elif action == "submit":
                svc.submit_for_review(db, org_id, recon_uuid)
            elif action == "approve":
                if not user_id:
                    raise HTTPException(status_code=401, detail="User ID required")
                svc.approve(
                    db=db,
                    organization_id=org_id,
                    reconciliation_id=recon_uuid,
                    approved_by=user_id,
                )
            elif action == "reject":
                if not user_id:
                    raise HTTPException(status_code=401, detail="User ID required")
                notes = request.query_params.get("notes", "Rejected via UI")
                svc.reject(
                    db=db,
                    organization_id=org_id,
                    reconciliation_id=recon_uuid,
                    rejected_by=user_id,
                    notes=notes,
                )
            db.flush()
            db.commit()
        except HTTPException:
            raise
        except (ValueError, RuntimeError) as e:
            logger.warning("Reconciliation %s failed: %s", action, e)
            raise HTTPException(status_code=400, detail=str(e))

        # HTMX requests get a 200 + HX-Refresh header
        if is_htmx_request(request):
            return htmx_response(refresh=True)
        return RedirectResponse(
            url=f"/finance/banking/reconciliations/{reconciliation_id}",
            status_code=303,
        )

    async def reconciliation_match_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        reconciliation_id: str,
    ) -> Response:
        """Add a single match from Alpine.js fetch (JSON body)."""
        from app.services.finance.banking.bank_reconciliation import (
            BankReconciliationService,
        )

        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        body = await request.json()
        svc = BankReconciliationService()
        user_id = getattr(auth, "user_id", None) or getattr(auth, "person_id", None)
        force_match = bool(body.get("force_match", False))

        try:
            from app.models.finance.banking.bank_reconciliation import (
                ReconciliationMatchType,
            )
            from app.services.finance.banking.bank_reconciliation import (
                ReconciliationMatchInput,
            )

            match_type_str = body.get("match_type", "manual")
            try:
                match_type = ReconciliationMatchType(match_type_str)
            except ValueError:
                match_type = ReconciliationMatchType.manual

            match_input = ReconciliationMatchInput(
                statement_line_id=UUID(str(body["statement_line_id"])),
                journal_line_id=UUID(str(body["journal_line_id"])),
                match_type=match_type,
            )
            svc.add_match(
                db=db,
                organization_id=org_id,
                reconciliation_id=UUID(reconciliation_id),
                input=match_input,
                created_by=user_id,
                force_match=force_match,
            )
            db.commit()
        except HTTPException:
            raise
        except (ValueError, RuntimeError, KeyError) as e:
            logger.warning("Match creation failed: %s", e)
            return JSONResponse(content={"detail": str(e)}, status_code=400)

        return JSONResponse(content={"status": "ok"}, status_code=200)

    async def reconciliation_unmatch_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        reconciliation_id: str,
    ) -> Response:
        """Reverse a confirmed match for a statement line (JSON body)."""
        from app.services.finance.banking.bank_reconciliation import (
            BankReconciliationService,
        )

        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        body = await request.json()
        svc = BankReconciliationService()
        user_id = getattr(auth, "user_id", None) or getattr(auth, "person_id", None)

        try:
            svc.unmatch(
                db=db,
                organization_id=org_id,
                reconciliation_id=UUID(reconciliation_id),
                statement_line_id=UUID(str(body["statement_line_id"])),
                created_by=user_id,
            )
            db.commit()
        except HTTPException:
            raise
        except (ValueError, RuntimeError, KeyError) as e:
            logger.warning("Unmatch failed: %s", e)
            return JSONResponse(content={"detail": str(e)}, status_code=400)

        return JSONResponse(content={"status": "ok"}, status_code=200)

    async def reconciliation_reconciling_item_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        reconciliation_id: str,
    ) -> Response:
        """Add a reconciling item — adjustment or outstanding item (JSON body).

        This is how a documented difference (bank charge, interest, FX, deposit
        in transit, unpresented cheque) is booked so the reconciliation ties to
        zero, instead of being absorbed into an inexact match.
        """
        from app.services.finance.banking.bank_reconciliation import (
            BankReconciliationService,
        )

        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        body = await request.json()
        svc = BankReconciliationService()
        user_id = getattr(auth, "user_id", None) or getattr(auth, "person_id", None)

        try:
            kind = str(body.get("kind", "adjustment"))
            txn_date = _parse_date(body.get("transaction_date")) or date.today()
            amount = Decimal(str(body["amount"]))
            description = str(body.get("description") or "").strip()
            if not description:
                return JSONResponse(
                    content={"detail": "Description is required"}, status_code=400
                )
            if amount == Decimal("0"):
                return JSONResponse(
                    content={"detail": "Amount must be non-zero"}, status_code=400
                )

            if kind == "outstanding":
                svc.add_outstanding_item(
                    db=db,
                    organization_id=org_id,
                    reconciliation_id=UUID(reconciliation_id),
                    transaction_date=txn_date,
                    amount=amount,
                    description=description,
                    outstanding_type=str(body.get("outstanding_type") or "deposit"),
                    created_by=user_id,
                )
            else:
                svc.add_adjustment(
                    db=db,
                    organization_id=org_id,
                    reconciliation_id=UUID(reconciliation_id),
                    transaction_date=txn_date,
                    amount=amount,
                    description=description,
                    adjustment_type=str(body.get("adjustment_type") or "adjustment"),
                    created_by=user_id,
                )
            db.commit()
        except HTTPException:
            raise
        except (ValueError, RuntimeError, KeyError, TypeError) as e:
            logger.warning("Add reconciling item failed: %s", e)
            return JSONResponse(content={"detail": str(e)}, status_code=400)

        return JSONResponse(content={"status": "ok"}, status_code=200)

    async def reconciliation_multi_match_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        reconciliation_id: str,
    ) -> Response:
        """Add a multi-match from Alpine.js fetch (JSON body)."""
        from app.services.finance.banking.bank_reconciliation import (
            BankReconciliationService,
        )

        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        body = await request.json()
        svc = BankReconciliationService()
        user_id = getattr(auth, "user_id", None) or getattr(auth, "person_id", None)

        try:
            from decimal import Decimal

            svc.add_multi_match(
                db=db,
                organization_id=org_id,
                reconciliation_id=UUID(reconciliation_id),
                statement_line_ids=[UUID(s) for s in body["statement_line_ids"]],
                journal_line_ids=[UUID(s) for s in body["journal_line_ids"]],
                tolerance=Decimal(str(body.get("tolerance", "0.01"))),
                notes=body.get("notes"),
                created_by=user_id,
            )
            db.commit()
        except HTTPException:
            raise
        except (ValueError, RuntimeError, KeyError) as e:
            logger.warning("Multi-match creation failed: %s", e)
            return JSONResponse(content={"detail": str(e)}, status_code=400)

        return JSONResponse(content={"status": "ok"}, status_code=200)

    # ─────────────────────────────────────────────────────────────
    # Bank Account Create / Update (form POST handlers)
    # ─────────────────────────────────────────────────────────────
