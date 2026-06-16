"""BankingAccountWebService component."""

from __future__ import annotations

from app.services.finance.banking.web_parts.base import (
    Account,
    Any,
    BankAccount,
    BankAccountStatus,
    BankReconciliation,
    BankStatement,
    BankStatementLine,
    Decimal,
    HTMLResponse,
    HTTPException,
    JournalEntryLine,
    ReconciliationStatus,
    RedirectResponse,
    Request,
    Response,
    Session,
    UUID,
    WebAuthContext,
    _account_view,
    _build_match_detail,
    _format_currency,
    _format_date,
    _parse_account_status,
    _statement_line_view,
    _statement_status_label,
    apply_sort,
    base_context,
    coerce_uuid,
    func,
    get_currency_context,
    logger,
    or_,
    org_context_service,
    resolve_payment_metadata,
    select,
    templates,
)
from app.services.common import PaginationParams, paginate


class BankingAccountWebService:
    """Banking web service methods for accounts."""

    @staticmethod
    def list_accounts_context(
        db: Session,
        organization_id: str,
        search: str | None,
        status: str | None,
        page: int,
        limit: int = 50,
        sort: str | None = None,
        sort_dir: str | None = None,
    ) -> dict:
        org_id = coerce_uuid(organization_id)

        status_value = _parse_account_status(status)

        conditions: list[Any] = [BankAccount.organization_id == org_id]
        if status_value:
            conditions.append(BankAccount.status == status_value)
        if search:
            search_pattern = f"%{search}%"
            conditions.append(
                or_(
                    BankAccount.bank_name.ilike(search_pattern),
                    BankAccount.account_name.ilike(search_pattern),
                    BankAccount.account_number.ilike(search_pattern),
                    BankAccount.branch_name.ilike(search_pattern),
                )
            )

        account_sort_map: dict[str, Any] = {
            "bank_name": BankAccount.bank_name,
            "account_name": BankAccount.account_name,
            "account_number": BankAccount.account_number,
            "status": BankAccount.status,
        }
        list_stmt = apply_sort(
            select(BankAccount).where(*conditions),
            sort,
            sort_dir,
            account_sort_map,
            default=[BankAccount.bank_name.asc(), BankAccount.account_name.asc()],
        )
        result = paginate(db, list_stmt, PaginationParams.from_page(page, limit))
        total_count = result.total
        accounts = result.items

        active_count = (
            db.scalar(
                select(func.count(BankAccount.bank_account_id)).where(
                    *conditions, BankAccount.status == BankAccountStatus.active
                )
            )
            or 0
        )
        total_balance = db.scalar(
            select(
                func.coalesce(func.sum(BankAccount.last_statement_balance), 0)
            ).where(*conditions)
        ) or Decimal("0")
        pending_recon = (
            db.scalar(
                select(func.count(BankReconciliation.reconciliation_id)).where(
                    BankReconciliation.organization_id == org_id,
                    BankReconciliation.status.in_(
                        [
                            ReconciliationStatus.draft,
                            ReconciliationStatus.pending_review,
                        ]
                    ),
                )
            )
            or 0
        )

        total_pages = result.total_pages

        return {
            "accounts": [_account_view(account) for account in accounts],
            "search": search,
            "status": status,
            "sort": sort,
            "sort_dir": sort_dir,
            "page": page,
            "limit": limit,
            "offset": result.offset,
            "total_count": total_count,
            "total_pages": total_pages,
            "active_count": active_count,
            "total_balance": _format_currency(total_balance),
            "pending_recon": pending_recon,
            "statuses": [s.value for s in BankAccountStatus],
        }

    @staticmethod
    def account_form_context(
        db: Session,
        organization_id: str,
        account_id: str | None = None,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        account = None
        if account_id:
            account = db.get(BankAccount, coerce_uuid(account_id))

        gl_accounts = db.scalars(
            select(Account)
            .where(
                Account.organization_id == org_id,
                Account.is_active.is_(True),
            )
            .order_by(Account.account_code)
        ).all()

        context = {
            "account": _account_view(account) if account else None,
            "gl_accounts": gl_accounts,
        }
        context.update(get_currency_context(db, organization_id))
        return context

    @staticmethod
    def account_detail_context(
        db: Session,
        organization_id: str,
        account_id: str,
    ) -> dict:
        org_id = coerce_uuid(organization_id)
        account = db.get(BankAccount, coerce_uuid(account_id))
        if not account or account.organization_id != org_id:
            account = None
        transactions: list[dict] = []
        if account:
            rows = db.execute(
                select(BankStatementLine, BankStatement)
                .join(
                    BankStatement,
                    BankStatementLine.statement_id == BankStatement.statement_id,
                )
                .where(
                    BankStatement.organization_id == org_id,
                    BankStatement.bank_account_id == account.bank_account_id,
                )
                .order_by(
                    BankStatementLine.transaction_date.desc(),
                    BankStatementLine.line_number.desc(),
                )
                .limit(50)
            ).all()
            for line, statement in rows:
                view = _statement_line_view(line)
                view.update(
                    {
                        "statement_id": statement.statement_id,
                        "statement_number": statement.statement_number,
                        "statement_date": _format_date(statement.statement_date),
                    }
                )
                transactions.append(view)

        return {
            "account": _account_view(account) if account else None,
            "transactions": transactions,
        }

    @staticmethod
    def transaction_detail_context(
        db: Session,
        organization_id: str,
        line_id: str,
    ) -> dict:
        """Build context for the transaction line detail page."""
        from app.services.finance.banking.bank_reconciliation import (
            _build_source_url,
        )

        org_id = coerce_uuid(organization_id)
        line = db.get(BankStatementLine, coerce_uuid(line_id))
        if not line:
            return {"line": None}

        # Verify org ownership via parent statement
        statement = line.statement
        if not statement or statement.organization_id != org_id:
            return {"line": None}

        account = statement.bank_account
        currency = statement.currency_code

        # Base line view
        line_view = _statement_line_view(line, currency)
        # Add extra fields not in the list helper
        line_view["value_date"] = _format_date(line.value_date)
        line_view["check_number"] = line.check_number
        line_view["bank_category"] = line.bank_category
        line_view["bank_code"] = line.bank_code
        line_view["notes"] = line.notes
        line_view["transaction_id"] = line.transaction_id
        line_view["matched_at"] = (
            line.matched_at.strftime("%d %b %Y %H:%M") if line.matched_at else None
        )

        # Parent statement/account info
        statement_view = {
            "statement_id": str(statement.statement_id),
            "statement_number": statement.statement_number,
            "statement_date": _format_date(statement.statement_date),
            "status": _statement_status_label(statement.status),
        }
        account_view = (
            {
                "bank_account_id": str(account.bank_account_id),
                "account_name": account.account_name,
                "bank_name": account.bank_name,
                "account_number": account.account_number,
            }
            if account
            else None
        )

        # Matched GL entries (via multi-match junction table)
        gl_matches: list[dict] = []
        for match in line.matched_gl_lines:
            jl = db.get(JournalEntryLine, match.journal_line_id)
            if not jl:
                continue
            entry = getattr(jl, "journal_entry", None) or getattr(jl, "entry", None)
            if not entry:
                continue
            source_url = _build_source_url(
                getattr(entry, "source_document_type", None),
                getattr(entry, "source_document_id", None),
                getattr(entry, "entry_id", None),
            )
            meta = resolve_payment_metadata(
                db,
                getattr(entry, "source_document_type", None),
                getattr(entry, "source_document_id", None),
            )
            gl_matches.append(
                {
                    "journal_line_id": str(jl.line_id),
                    "entry_id": str(entry.entry_id),
                    "entry_date": _format_date(entry.entry_date),
                    "description": jl.description or entry.description or "",
                    "reference": entry.reference or "",
                    "debit_amount": _format_currency(jl.debit_amount, currency),
                    "credit_amount": _format_currency(jl.credit_amount, currency),
                    "account_name": (
                        f"{jl.account.account_code} - {jl.account.account_name}"
                        if jl.account
                        else ""
                    ),
                    "source_url": source_url,
                    "match_detail": _build_match_detail(
                        db, entry, source_url, metadata=meta
                    ),
                    "match_type": match.match_type or "",
                    "match_score": float(match.match_score)
                    if match.match_score
                    else None,
                    "is_primary": match.is_primary,
                    "matched_at": (
                        match.matched_at.strftime("%d %b %Y %H:%M")
                        if match.matched_at
                        else None
                    ),
                }
            )

        # Suggested account name lookup
        suggested_account_name = None
        if line.suggested_account_id:
            acct = db.get(Account, line.suggested_account_id)
            if acct:
                suggested_account_name = f"{acct.account_code} - {acct.account_name}"

        # Suggested rule name lookup
        suggested_rule_name = None
        if line.suggested_rule_id:
            from app.models.finance.banking.transaction_rule import TransactionRule

            rule = db.get(TransactionRule, line.suggested_rule_id)
            if rule:
                suggested_rule_name = rule.rule_name

        return {
            "line": line_view,
            "statement": statement_view,
            "account": account_view,
            "gl_matches": gl_matches,
            "suggested_account_name": suggested_account_name,
            "suggested_rule_name": suggested_rule_name,
            "currency_code": currency,
        }

    def list_accounts_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None,
        status: str | None,
        page: int,
        limit: int = 50,
        sort: str | None = None,
        sort_dir: str | None = None,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Bank Accounts", "banking", db=db)
        context.update(
            self.list_accounts_context(
                db,
                str(auth.organization_id),
                search=search,
                status=status,
                page=page,
                limit=limit,
                sort=sort,
                sort_dir=sort_dir,
            )
        )
        return templates.TemplateResponse(
            request, "finance/banking/accounts.html", context
        )

    def account_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "New Bank Account", "banking", db=db)
        context.update(self.account_form_context(db, str(auth.organization_id)))
        return templates.TemplateResponse(
            request, "finance/banking/account_form.html", context
        )

    def account_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        account_id: str,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Bank Account Details", "banking", db=db)
        context.update(
            self.account_detail_context(
                db,
                str(auth.organization_id),
                account_id,
            )
        )

        # Mono Connect integration context
        from app.models.domain_settings import SettingDomain
        from app.services.settings_spec import resolve_value

        mono_enabled = resolve_value(db, SettingDomain.banking, "mono_enabled")
        context["mono_enabled"] = bool(mono_enabled)
        if mono_enabled:
            context["mono_public_key"] = str(
                resolve_value(db, SettingDomain.banking, "mono_public_key") or ""
            )

            # Get user email for Mono customer data
            if auth.person_id:
                from app.models.person import Person

                person = db.get(Person, auth.person_id)
                context["mono_user_email"] = person.email if person else ""
            else:
                context["mono_user_email"] = ""

        return templates.TemplateResponse(
            request, "finance/banking/account_detail.html", context
        )

    def transaction_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        line_id: str,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Transaction Detail", "banking", db=db)
        context.update(
            self.transaction_detail_context(
                db,
                str(auth.organization_id),
                line_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/banking/transaction_detail.html", context
        )

    def account_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        account_id: str,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Edit Bank Account", "banking", db=db)
        context.update(
            self.account_form_context(
                db,
                str(auth.organization_id),
                account_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/banking/account_form.html", context
        )

    async def bulk_export_accounts_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> Response:
        """Handle bulk export bank accounts request."""
        from app.schemas.bulk_actions import BulkExportRequest
        from app.services.finance.banking.bulk import get_account_bulk_service

        body = await request.json()
        req = BulkExportRequest(**body)
        service = get_account_bulk_service(
            db,
            coerce_uuid(auth.organization_id),
            coerce_uuid(auth.user_id),
        )
        return await service.bulk_export(req.ids, req.format)

    async def export_all_accounts_response(
        self,
        auth: WebAuthContext,
        db: Session,
        search: str = "",
        status: str = "",
    ) -> Response:
        """Export all bank accounts matching filters."""
        from app.services.finance.banking.bulk import get_account_bulk_service

        service = get_account_bulk_service(
            db,
            coerce_uuid(auth.organization_id),
            coerce_uuid(auth.user_id),
        )
        return await service.export_all(search=search, status=status)

    async def create_account_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> Response:
        """Handle POST to create a new bank account from form data."""
        from app.models.finance.banking.bank_account import BankAccountType
        from app.services.finance.banking.bank_account import (
            BankAccountInput,
            bank_account_service,
        )

        org_id = auth.organization_id
        user_id = auth.user_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        form = await request.form()
        try:
            data = BankAccountInput(
                bank_name=str(form.get("bank_name", "")),
                account_number=str(form.get("account_number", "")),
                account_name=str(form.get("account_name", "")),
                gl_account_id=UUID(str(form["gl_account_id"])),
                currency_code=(
                    str(form.get("currency_code", "")).strip()
                    or org_context_service.get_functional_currency(db, org_id)
                ),
                account_type=BankAccountType(str(form.get("account_type", "checking"))),
                bank_code=str(form.get("bank_code", "")) or None,
                branch_code=str(form.get("branch_code", "")) or None,
                branch_name=str(form.get("branch_name", "")) or None,
                iban=str(form.get("iban", "")) or None,
                contact_name=str(form.get("contact_name", "")) or None,
                contact_phone=str(form.get("contact_phone", "")) or None,
                contact_email=str(form.get("contact_email", "")) or None,
                notes=str(form.get("notes", "")) or None,
                allow_overdraft="allow_overdraft" in form,
                overdraft_limit=Decimal(str(form["overdraft_limit"]))
                if form.get("overdraft_limit")
                else None,
            )
            account = bank_account_service.create(
                db, org_id, data, coerce_uuid(user_id) if user_id else None
            )
            db.commit()
            return RedirectResponse(
                url=f"/finance/banking/accounts/{account.bank_account_id}",
                status_code=303,
            )
        except (ValueError, RuntimeError) as exc:
            logger.warning("Bank account creation failed: %s", exc)
            return RedirectResponse(
                url=f"/finance/banking/accounts/new?error={exc}",
                status_code=303,
            )

    async def update_account_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        account_id: str,
    ) -> Response:
        """Handle POST to update an existing bank account from form data."""
        from app.models.finance.banking.bank_account import BankAccountType
        from app.services.finance.banking.bank_account import (
            BankAccountInput,
            bank_account_service,
        )

        org_id = auth.organization_id
        user_id = auth.user_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        # Load existing account to preserve account_number (read-only in edit)
        existing = db.get(BankAccount, coerce_uuid(account_id))
        if not existing or existing.organization_id != coerce_uuid(org_id):
            raise HTTPException(status_code=404, detail="Bank account not found")

        form = await request.form()
        try:
            data = BankAccountInput(
                bank_name=str(form.get("bank_name", "")),
                account_number=existing.account_number,
                account_name=str(form.get("account_name", "")),
                gl_account_id=UUID(str(form["gl_account_id"])),
                currency_code=str(form.get("currency_code", existing.currency_code)),
                account_type=BankAccountType(str(form.get("account_type", "checking"))),
                bank_code=str(form.get("bank_code", "")) or None,
                branch_code=str(form.get("branch_code", "")) or None,
                branch_name=str(form.get("branch_name", "")) or None,
                iban=str(form.get("iban", "")) or None,
                contact_name=str(form.get("contact_name", "")) or None,
                contact_phone=str(form.get("contact_phone", "")) or None,
                contact_email=str(form.get("contact_email", "")) or None,
                notes=str(form.get("notes", "")) or None,
                allow_overdraft="allow_overdraft" in form,
                overdraft_limit=Decimal(str(form["overdraft_limit"]))
                if form.get("overdraft_limit")
                else None,
            )
            bank_account_service.update(
                db,
                org_id,
                coerce_uuid(account_id),
                data,
                coerce_uuid(user_id) if user_id else None,
            )
            db.commit()
            return RedirectResponse(
                url=f"/finance/banking/accounts/{account_id}",
                status_code=303,
            )
        except (ValueError, RuntimeError) as exc:
            logger.warning("Bank account update failed: %s", exc)
            return RedirectResponse(
                url=f"/finance/banking/accounts/{account_id}/edit?error={exc}",
                status_code=303,
            )

    def unlink_mono_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        account_id: str,
    ) -> RedirectResponse:
        """Disconnect Mono for a bank account from the web UI."""
        from app.services.finance.banking.bank_account import bank_account_service

        del request
        if auth.organization_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        bank_account_service.unlink_mono(
            db,
            auth.organization_id,
            coerce_uuid(account_id),
            require_linked=False,
            updated_by=coerce_uuid(auth.user_id) if auth.user_id else None,
        )
        db.flush()
        db.commit()
        return RedirectResponse(
            url=f"/finance/banking/accounts/{account_id}",
            status_code=303,
        )

    # ─────────────────────────────────────────────────────────────
    # Payee Create / Update (form POST handlers)
    # ─────────────────────────────────────────────────────────────
