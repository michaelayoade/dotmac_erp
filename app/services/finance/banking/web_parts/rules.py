"""BankingRuleWebService component."""

from __future__ import annotations

from app.services.finance.banking.web_parts.base import (
    Account,
    Any,
    BankAccount,
    BankAccountStatus,
    HTMLResponse,
    HTTPException,
    RedirectResponse,
    Request,
    Session,
    UUID,
    WebAuthContext,
    base_context,
    build_active_filters,
    coerce_uuid,
    func,
    is_htmx_request,
    json,
    select,
    templates,
)


class BankingRuleWebService:
    """Banking web service methods for rules."""

    @staticmethod
    def list_rules_context(
        db: Session,
        organization_id: str,
        rule_type: str | None = None,
        page: int = 1,
        per_page: int = 25,
    ) -> dict:
        """Context for transaction rules list page."""
        from app.models.finance.banking.transaction_rule import (
            RuleType,
            TransactionRule,
        )

        org_id = coerce_uuid(organization_id)

        conditions: list[Any] = [TransactionRule.organization_id == org_id]

        if rule_type:
            try:
                rt = RuleType(rule_type)
                conditions.append(TransactionRule.rule_type == rt)
            except ValueError:
                pass

        total = (
            db.scalar(select(func.count(TransactionRule.rule_id)).where(*conditions))
            or 0
        )
        rules = db.scalars(
            select(TransactionRule)
            .where(*conditions)
            .order_by(TransactionRule.sort_order.asc(), TransactionRule.rule_name)
            .offset((page - 1) * per_page)
            .limit(per_page)
        ).all()

        # Get GL accounts for display
        account_map = {}
        account_ids = [r.target_account_id for r in rules if r.target_account_id]
        if account_ids:
            accounts = db.scalars(
                select(Account).where(
                    Account.organization_id == org_id,
                    Account.account_id.in_(account_ids),
                )
            ).all()
            account_map = {
                a.account_id: f"{a.account_code} - {a.account_name}" for a in accounts
            }

        rule_list = []
        for idx, r in enumerate(rules):
            target_account_id = r.target_account_id
            rule_list.append(
                {
                    "rule_id": str(r.rule_id),
                    "rule_name": r.rule_name,
                    "description": r.description or "",
                    "rule_type": r.rule_type.value if r.rule_type else "",
                    "action": r.action.value if r.action else "",
                    "target_account": account_map.get(target_account_id, "")
                    if target_account_id
                    else "",
                    "sort_order": r.sort_order,
                    "position": idx + 1,
                    "auto_apply": r.auto_apply,
                    "is_active": r.is_active,
                    "match_count": r.match_count,
                    "success_count": r.success_count,
                    "success_rate": f"{r.success_rate:.0f}%"
                    if r.success_count + r.reject_count > 0
                    else "N/A",
                }
            )

        # Aggregate stats across ALL rules for the org (not just current page)
        active_count = (
            db.scalar(
                select(func.count(TransactionRule.rule_id)).where(
                    TransactionRule.organization_id == org_id,
                    TransactionRule.is_active.is_(True),
                )
            )
            or 0
        )
        auto_apply_count = (
            db.scalar(
                select(func.count(TransactionRule.rule_id)).where(
                    TransactionRule.organization_id == org_id,
                    TransactionRule.auto_apply.is_(True),
                )
            )
            or 0
        )
        total_matches = (
            db.scalar(
                select(func.coalesce(func.sum(TransactionRule.match_count), 0)).where(
                    TransactionRule.organization_id == org_id
                )
            )
            or 0
        )

        total_pages = (total + per_page - 1) // per_page
        active_filters = build_active_filters(
            params={"rule_type": rule_type},
            labels={"rule_type": "Type"},
            options={
                "rule_type": {
                    t.value: t.value.replace("_", " ").title() for t in RuleType
                }
            },
        )
        return {
            "rules": rule_list,
            "rule_types": [
                {"value": t.value, "label": t.value.replace("_", " ").title()}
                for t in RuleType
            ],
            "rule_type": rule_type or "",
            "selected_type": rule_type or "",
            "active_filters": active_filters,
            "active_count": active_count,
            "auto_apply_count": auto_apply_count,
            "total_matches": total_matches,
            "page": page,
            "limit": per_page,
            "total_count": total,
            "total_pages": total_pages,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": total_pages,
            },
        }

    @staticmethod
    def _normalize_to_combined(rule_type: str, conditions: dict) -> dict:
        """Normalize a single-type rule's conditions into COMBINED format.

        The visual builder always works with the COMBINED structure:
        ``{"operator": "AND", "rules": [{"type": "...", "conditions": {...}}]}``

        Legacy rules stored as single types get wrapped so the builder can
        display them.
        """
        if rule_type == "COMBINED":
            return conditions

        return {
            "operator": "AND",
            "rules": [{"type": rule_type, "conditions": conditions}],
        }

    @staticmethod
    def rule_form_context(
        db: Session,
        organization_id: str,
        rule_id: str | None = None,
    ) -> dict:
        """Context for transaction rule create/edit form."""
        from app.models.finance.banking.payee import Payee
        from app.models.finance.banking.transaction_rule import (
            RuleAction,
            RuleType,
            TransactionRule,
        )
        from app.models.finance.tax.tax_code import TaxCode

        org_id = coerce_uuid(organization_id)

        # Get GL accounts for dropdown
        accounts = db.scalars(
            select(Account)
            .where(Account.organization_id == org_id, Account.is_active.is_(True))
            .order_by(Account.account_code)
        ).all()

        account_options = [
            {
                "value": str(a.account_id),
                "label": f"{a.account_code} - {a.account_name}",
            }
            for a in accounts
        ]

        # Get bank accounts for dropdown
        bank_accounts = db.scalars(
            select(BankAccount)
            .where(
                BankAccount.organization_id == org_id,
                BankAccount.status == BankAccountStatus.active,
            )
            .order_by(BankAccount.account_name)
        ).all()

        bank_account_options = [
            {
                "value": str(ba.bank_account_id),
                "label": f"{ba.bank_name} - {ba.account_name}",
            }
            for ba in bank_accounts
        ]

        # Get payees for dropdown
        payees = db.scalars(
            select(Payee)
            .where(Payee.organization_id == org_id, Payee.is_active.is_(True))
            .order_by(Payee.payee_name)
        ).all()

        payee_options = [
            {"value": str(p.payee_id), "label": p.payee_name} for p in payees
        ]

        # Get tax codes for dropdown
        tax_codes = db.scalars(
            select(TaxCode)
            .where(TaxCode.organization_id == org_id, TaxCode.is_active.is_(True))
            .order_by(TaxCode.tax_code)
        ).all()

        tax_code_options = [
            {
                "value": str(tc.tax_code_id),
                "label": f"{tc.tax_code} - {tc.tax_name} ({tc.tax_rate}%)",
            }
            for tc in tax_codes
        ]

        rule = None
        if rule_id:
            rule = db.get(TransactionRule, coerce_uuid(rule_id))
            if rule and rule.organization_id != org_id:
                rule = None

        rule_data = None
        if rule:
            raw_type = rule.rule_type.value if rule.rule_type else "COMBINED"
            raw_conditions = rule.conditions or {}
            combined_conditions = BankingRuleWebService._normalize_to_combined(
                raw_type, raw_conditions
            )

            rule_data = {
                "rule_id": str(rule.rule_id),
                "rule_name": rule.rule_name,
                "description": rule.description or "",
                "rule_type": raw_type,
                "conditions": combined_conditions,
                "action": rule.action.value if rule.action else "",
                "target_account_id": str(rule.target_account_id)
                if rule.target_account_id
                else "",
                "tax_code_id": str(rule.tax_code_id) if rule.tax_code_id else "",
                "bank_account_id": str(rule.bank_account_id)
                if rule.bank_account_id
                else "",
                "payee_id": str(rule.payee_id) if rule.payee_id else "",
                "auto_apply": rule.auto_apply,
                "min_confidence": rule.min_confidence,
                "applies_to_credits": rule.applies_to_credits,
                "applies_to_debits": rule.applies_to_debits,
                "is_active": rule.is_active,
                "match_count": rule.match_count,
                "success_count": rule.success_count,
                "reject_count": rule.reject_count,
                "created_at": rule.created_at,
            }

        return {
            "rule": rule_data,
            "is_edit": rule is not None,
            "rule_types": [
                {"value": t.value, "label": t.value.replace("_", " ").title()}
                for t in RuleType
            ],
            "actions": [
                {"value": a.value, "label": a.value.replace("_", " ").title()}
                for a in RuleAction
            ],
            "accounts": account_options,
            "bank_accounts": bank_account_options,
            "payees": payee_options,
            "tax_codes": tax_code_options,
        }

    @staticmethod
    def rule_duplicate_form_context(
        db: Session,
        organization_id: str,
        rule_id: str,
    ) -> dict:
        """Context for duplicate-rule form."""
        from app.models.finance.banking.transaction_rule import TransactionRule

        org_id = coerce_uuid(organization_id)
        source_rule = db.get(TransactionRule, coerce_uuid(rule_id))
        if not source_rule or source_rule.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Rule not found")

        bank_accounts = db.scalars(
            select(BankAccount)
            .where(
                BankAccount.organization_id == org_id,
                BankAccount.status == BankAccountStatus.active,
            )
            .order_by(BankAccount.account_name)
        ).all()

        # Exclude the source rule's bank account — it's already covered
        source_ba_id = source_rule.bank_account_id
        return {
            "source_rule": {
                "rule_id": str(source_rule.rule_id),
                "rule_name": source_rule.rule_name,
                "rule_type": source_rule.rule_type.value
                if source_rule.rule_type
                else "",
                "action": source_rule.action.value if source_rule.action else "",
                "bank_account_id": str(source_ba_id) if source_ba_id else "",
                "bank_account_label": next(
                    (
                        f"{ba.bank_name} - {ba.account_name}"
                        for ba in bank_accounts
                        if ba.bank_account_id == source_ba_id
                    ),
                    "All Accounts",
                ),
            },
            "bank_accounts": [
                {
                    "bank_account_id": str(ba.bank_account_id),
                    "label": f"{ba.bank_name} - {ba.account_name}",
                    "is_default": False,
                }
                for ba in bank_accounts
                if ba.bank_account_id != source_ba_id
            ],
        }

    @staticmethod
    def bulk_rule_duplicate_form_context(
        db: Session,
        organization_id: str,
        rule_ids: list[str],
    ) -> dict:
        """Context for bulk duplicate-rule form."""
        from app.models.finance.banking.transaction_rule import TransactionRule

        org_id = coerce_uuid(organization_id)
        parsed_ids = [coerce_uuid(rid) for rid in rule_ids if rid]
        unique_ids = list(dict.fromkeys(parsed_ids))
        if not unique_ids:
            raise ValueError("Select at least one rule")

        rules = db.scalars(
            select(TransactionRule)
            .where(
                TransactionRule.organization_id == org_id,
                TransactionRule.rule_id.in_(unique_ids),
            )
            .order_by(TransactionRule.rule_name.asc())
        ).all()
        if not rules:
            raise ValueError("No valid rules selected")

        bank_accounts = db.scalars(
            select(BankAccount)
            .where(
                BankAccount.organization_id == org_id,
                BankAccount.status == BankAccountStatus.active,
            )
            .order_by(BankAccount.account_name)
        ).all()

        return {
            "source_rules": [
                {
                    "rule_id": str(rule.rule_id),
                    "rule_name": rule.rule_name,
                    "rule_type": rule.rule_type.value if rule.rule_type else "",
                    "action": rule.action.value if rule.action else "",
                }
                for rule in rules
            ],
            "bank_accounts": [
                {
                    "bank_account_id": str(ba.bank_account_id),
                    "label": f"{ba.bank_name} - {ba.account_name}",
                }
                for ba in bank_accounts
            ],
        }

    @staticmethod
    def build_rule_input(form_data: dict) -> dict:
        """Parse and validate raw form data into rule kwargs.

        Raises ``ValueError`` on invalid input.
        """
        from app.models.finance.banking.transaction_rule import RuleAction, RuleType

        rule_name = (form_data.get("rule_name") or "").strip()
        if not rule_name:
            raise ValueError("Rule name is required")

        # Parse conditions JSON from the visual builder
        conditions_raw = form_data.get("conditions_json", "")
        if not conditions_raw:
            raise ValueError("At least one matching condition is required")

        try:
            conditions = json.loads(conditions_raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"Invalid conditions: {exc}") from exc

        # Validate conditions structure
        if not isinstance(conditions, dict):
            raise ValueError("Conditions must be a JSON object")
        sub_rules = conditions.get("rules", [])
        if not sub_rules:
            raise ValueError("At least one matching condition is required")

        valid_sub_types = {rt.value for rt in RuleType if rt != RuleType.COMBINED}
        for sr in sub_rules:
            if sr.get("type") not in valid_sub_types:
                raise ValueError(f"Invalid condition type: {sr.get('type')}")

        # Parse action
        action_str = form_data.get("action", "CATEGORIZE")
        try:
            action = RuleAction(action_str)
        except ValueError:
            raise ValueError(f"Invalid action: {action_str}")

        # Parse optional UUIDs
        target_account_id: UUID | None = None
        if action == RuleAction.CATEGORIZE:
            raw = form_data.get("target_account_id", "")
            if raw:
                target_account_id = UUID(raw)

        tax_code_id: UUID | None = None
        raw_tax = form_data.get("tax_code_id", "")
        if raw_tax:
            tax_code_id = UUID(raw_tax)

        bank_account_id: UUID | None = None
        raw_ba = form_data.get("bank_account_id", "")
        if raw_ba:
            bank_account_id = UUID(raw_ba)

        return {
            "rule_name": rule_name,
            "description": (form_data.get("description") or "").strip() or None,
            "rule_type": RuleType.COMBINED,
            "conditions": conditions,
            "action": action,
            "target_account_id": target_account_id,
            "tax_code_id": tax_code_id,
            "bank_account_id": bank_account_id,
            "auto_apply": form_data.get("auto_apply") == "on",
            "min_confidence": int(form_data.get("min_confidence", 80)),
            "applies_to_credits": form_data.get("applies_to_credits") == "on",
            "applies_to_debits": form_data.get("applies_to_debits") == "on",
            "is_active": form_data.get("is_active") == "on",
        }

    def create_rule_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        form_data: dict,
    ) -> HTMLResponse | RedirectResponse:
        """Handle POST for new rule creation."""
        from app.services.finance.banking.categorization import (
            TransactionCategorizationService,
        )

        try:
            kwargs = self.build_rule_input(form_data)
        except (ValueError, TypeError) as exc:
            context = base_context(
                request, auth, "New Transaction Rule", "banking", db=db
            )
            context.update(self.rule_form_context(db, str(auth.organization_id)))
            context["error"] = str(exc)
            context["form_data"] = form_data
            return templates.TemplateResponse(
                request, "finance/banking/rule_form.html", context
            )

        # is_active isn't a create_rule() param; pop and set after creation
        is_active = kwargs.pop("is_active", True)

        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        service = TransactionCategorizationService()
        rule = service.create_rule(
            db,
            organization_id=org_id,
            created_by=auth.person_id,
            **kwargs,
        )
        rule.is_active = is_active
        db.flush()
        db.commit()
        return RedirectResponse(
            url="/finance/banking/rules?success=Rule+created",
            status_code=303,
        )

    def update_rule_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        rule_id: str,
        form_data: dict,
    ) -> HTMLResponse | RedirectResponse:
        """Handle POST for rule update."""
        from app.services.finance.banking.categorization import (
            TransactionCategorizationService,
        )

        try:
            kwargs = self.build_rule_input(form_data)
        except (ValueError, TypeError) as exc:
            context = base_context(
                request, auth, "Edit Transaction Rule", "banking", db=db
            )
            context.update(
                self.rule_form_context(db, str(auth.organization_id), rule_id=rule_id)
            )
            context["error"] = str(exc)
            context["form_data"] = form_data
            return templates.TemplateResponse(
                request, "finance/banking/rule_form.html", context
            )

        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        service = TransactionCategorizationService()
        rule = service.update_rule(
            db,
            organization_id=org_id,
            rule_id=UUID(rule_id),
            **kwargs,
        )
        if not rule:
            context = base_context(
                request, auth, "Edit Transaction Rule", "banking", db=db
            )
            context.update(
                self.rule_form_context(db, str(auth.organization_id), rule_id=rule_id)
            )
            context["error"] = "Rule not found"
            return templates.TemplateResponse(
                request, "finance/banking/rule_form.html", context
            )

        db.flush()
        db.commit()
        return RedirectResponse(
            url="/finance/banking/rules?success=Rule+updated",
            status_code=303,
        )

    def list_rules_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        rule_type: str | None,
        page: int,
        limit: int = 25,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Transaction Rules", "banking", db=db)
        context.update(
            self.list_rules_context(
                db,
                str(auth.organization_id),
                rule_type=rule_type,
                page=page,
                per_page=limit,
            )
        )
        return templates.TemplateResponse(
            request, "finance/banking/rules.html", context
        )

    def rule_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(request, auth, "New Transaction Rule", "banking", db=db)
        context.update(self.rule_form_context(db, str(auth.organization_id)))
        return templates.TemplateResponse(
            request, "finance/banking/rule_form.html", context
        )

    def rule_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        rule_id: str,
    ) -> HTMLResponse:
        context = base_context(request, auth, "Edit Transaction Rule", "banking", db=db)
        context.update(
            self.rule_form_context(
                db,
                str(auth.organization_id),
                rule_id=rule_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/banking/rule_form.html", context
        )

    def rule_duplicate_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        rule_id: str,
    ) -> HTMLResponse:
        """Render duplicate-rule form."""
        context = base_context(request, auth, "Duplicate Rule", "banking", db=db)
        context.update(
            self.rule_duplicate_form_context(
                db,
                str(auth.organization_id),
                rule_id=rule_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/banking/rule_duplicate.html", context
        )

    def duplicate_rule_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        rule_id: str,
        bank_account_ids: list[str],
        include_global: bool,
    ) -> HTMLResponse | RedirectResponse:
        """Handle POST duplicate-rule action."""
        from app.services.finance.banking.categorization import (
            TransactionCategorizationService,
        )

        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        target_ids = [bid for bid in bank_account_ids if bid]
        try:
            service = TransactionCategorizationService()
            copies = service.duplicate_rule_to_accounts(
                db=db,
                organization_id=org_id,
                source_rule_id=UUID(rule_id),
                bank_account_ids=[UUID(v) for v in target_ids],
                include_global=include_global,
                created_by=auth.person_id,
            )
            db.flush()
            db.commit()
        except (ValueError, TypeError) as exc:
            context = base_context(request, auth, "Duplicate Rule", "banking", db=db)
            context.update(
                self.rule_duplicate_form_context(
                    db,
                    str(auth.organization_id),
                    rule_id=rule_id,
                )
            )
            context["error"] = str(exc)
            context["selected_bank_account_ids"] = target_ids
            context["include_global"] = include_global
            return templates.TemplateResponse(
                request, "finance/banking/rule_duplicate.html", context
            )

        return RedirectResponse(
            url=f"/finance/banking/rules?success=Rule+duplicated+({len(copies)}+copy)",
            status_code=303,
        )

    def bulk_rule_duplicate_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        rule_ids: list[str],
    ) -> HTMLResponse | RedirectResponse:
        """Render bulk duplicate-rule form."""
        context = base_context(request, auth, "Bulk Duplicate Rules", "banking", db=db)
        try:
            context.update(
                self.bulk_rule_duplicate_form_context(
                    db,
                    str(auth.organization_id),
                    rule_ids=rule_ids,
                )
            )
        except ValueError as exc:
            return RedirectResponse(
                url=f"/finance/banking/rules?error={str(exc).replace(' ', '+')}",
                status_code=303,
            )
        return templates.TemplateResponse(
            request, "finance/banking/rule_duplicate_bulk.html", context
        )

    def bulk_duplicate_rules_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        rule_ids: list[str],
        bank_account_ids: list[str],
        include_global: bool,
    ) -> HTMLResponse | RedirectResponse:
        """Handle bulk duplicate-rules action."""
        from app.services.finance.banking.categorization import (
            TransactionCategorizationService,
        )

        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        unique_rule_ids = list(dict.fromkeys(rid for rid in rule_ids if rid))
        unique_bank_ids = list(dict.fromkeys(bid for bid in bank_account_ids if bid))
        service = TransactionCategorizationService()

        try:
            total_copies = 0
            for rid in unique_rule_ids:
                copies = service.duplicate_rule_to_accounts(
                    db=db,
                    organization_id=org_id,
                    source_rule_id=UUID(rid),
                    bank_account_ids=[UUID(v) for v in unique_bank_ids],
                    include_global=include_global,
                    created_by=auth.person_id,
                )
                total_copies += len(copies)
            db.flush()
            db.commit()
        except (ValueError, TypeError) as exc:
            context = base_context(
                request,
                auth,
                "Bulk Duplicate Rules",
                "banking",
                db=db,
            )
            context.update(
                self.bulk_rule_duplicate_form_context(
                    db,
                    str(auth.organization_id),
                    rule_ids=unique_rule_ids,
                )
            )
            context["error"] = str(exc)
            context["selected_bank_account_ids"] = unique_bank_ids
            context["include_global"] = include_global
            return templates.TemplateResponse(
                request, "finance/banking/rule_duplicate_bulk.html", context
            )

        return RedirectResponse(
            url=(
                "/finance/banking/rules?"
                f"success=Rules+duplicated+({total_copies}+copies)"
            ),
            status_code=303,
        )

    def reorder_rules_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        rule_id: str,
        direction: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle POST to reorder a rule up or down.

        Returns the #results-container partial for HTMX swap.
        """
        from app.services.finance.banking.categorization import (
            TransactionCategorizationService,
        )

        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        service = TransactionCategorizationService()
        service.swap_rule_order(db, org_id, UUID(rule_id), direction)
        db.flush()
        db.commit()

        # Check if this is an HTMX request — return partial
        if is_htmx_request(request):
            context = base_context(request, auth, "Transaction Rules", "banking", db=db)
            context.update(self.list_rules_context(db, str(org_id)))
            return templates.TemplateResponse(
                request,
                "finance/banking/_rules_table.html",
                context,
            )

        # Fallback: full redirect
        return RedirectResponse(
            url="/finance/banking/rules?success=Record+saved+successfully",
            status_code=303,
        )

    # ─── Reconciliation POST handlers ───────────────────────────────────
