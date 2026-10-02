from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.models.domain_settings import SettingDomain
from app.models.finance.banking.bank_account import BankAccount, BankAccountStatus
from app.models.finance.banking.bank_statement import (
    BankStatement,
    BankStatementLine,
    StatementLineType,
)
from app.models.finance.gl.account import Account
from app.services.dotmac_sub.client import PaymentRecord
from app.services.settings_spec import resolve_value


def _norm(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", "", value.lower())


class MissingPaymentBankMappingError(ValueError):
    """A receipt cannot be posted until its bank/channel configuration is repaired."""

    error_code = "dotmac_sub_payment_bank_mapping_missing"

    def __init__(
        self, payment_id: str, channel_id: str | None, currency_code: str
    ) -> None:
        self.source_payment_id = payment_id
        self.channel_id = channel_id
        self.currency_code = currency_code
        super().__init__(
            f"Payment {payment_id}: no active ERP bank/GL mapping for "
            f"channel {channel_id or '<unset>'} in {currency_code}; "
            "configure the mapping and replay this source payment"
        )


class PostedPaymentBankCorrectionRequiredError(ValueError):
    """A bank mapping repair cannot silently mutate an already-posted receipt."""

    error_code = "dotmac_sub_posted_payment_bank_correction_required"

    def __init__(self, payment_id: str) -> None:
        super().__init__(
            f"Payment {payment_id}: corrected bank mapping requires an audited "
            "accounting correction; posted receipt left unchanged"
        )


class BankMappingMixin:
    """Map dotmac_sub payment channels → ERP bank accounts."""

    db: Any
    organization_id: UUID
    _bank_name_mapping: dict[str, str | None]
    _payment_channel_names: dict[str, str]
    _bank_account_mapping: dict[str, UUID]
    _default_bank_account_cache: dict[str, UUID]

    def _channel_name(self, channel_id: str | None) -> str:
        if not channel_id:
            return ""
        return self._payment_channel_names.get(channel_id, "")

    def _get_default_bank_account(self, currency_code: str) -> UUID | None:
        if currency_code in self._default_bank_account_cache:
            return self._default_bank_account_cache[currency_code]
        stmt = (
            select(BankAccount.bank_account_id)
            .join(Account, Account.account_id == BankAccount.gl_account_id)
            .where(
                BankAccount.organization_id == self.organization_id,
                BankAccount.status == BankAccountStatus.active,
                BankAccount.currency_code == currency_code,
                Account.organization_id == self.organization_id,
                Account.is_active.is_(True),
                Account.is_posting_allowed.is_(True),
            )
            .limit(1)
        )
        bank_account_id = self.db.scalar(stmt)
        if bank_account_id:
            self._default_bank_account_cache[currency_code] = bank_account_id
        return bank_account_id

    def _get_bank_account_for_channel(
        self,
        channel_id: str | None,
        currency_code: str,
        *,
        payment: PaymentRecord | None = None,
    ) -> UUID | None:
        name = _norm(self._channel_name(channel_id))
        cache_key = f"{channel_id or ''}:{currency_code}"
        if name:
            cached = self._bank_account_mapping.get(cache_key)
            if cached:
                return cached
            for fragment, erp_fragment in self._bank_name_mapping.items():
                if _norm(fragment) and _norm(fragment) in name:
                    if erp_fragment is None:
                        return None
                    acct = (
                        self._get_paystack_collection_bank(currency_code)
                        if _norm(fragment) == "paystack"
                        else self._match_bank_account(erp_fragment, currency_code)
                    )
                    if acct and channel_id:
                        self._bank_account_mapping[cache_key] = acct
                    # An explicitly configured but unavailable bank is not
                    # permission to post to an unrelated default account.
                    return acct
        # Missing/unknown source channels are not permission to choose the
        # first bank. A legacy Paystack reference must have provider-imported
        # evidence; otherwise the existing preflight quarantines the row.
        if payment is not None:
            return self._paystack_bank_from_statement(payment, currency_code)
        return None

    def _get_paystack_collection_bank(self, currency_code: str) -> UUID | None:
        collection_id = resolve_value(
            self.db,
            SettingDomain.payments,
            "paystack_collection_bank_account_id",
            organization_id=self.organization_id,
        )
        if not collection_id:
            return None
        try:
            bank_id = UUID(str(collection_id))
        except (ValueError, TypeError):
            return None
        return self.db.scalar(
            select(BankAccount.bank_account_id)
            .join(Account, Account.account_id == BankAccount.gl_account_id)
            .where(
                BankAccount.bank_account_id == bank_id,
                BankAccount.organization_id == self.organization_id,
                BankAccount.status == BankAccountStatus.active,
                BankAccount.currency_code == currency_code,
                Account.organization_id == self.organization_id,
                Account.is_active.is_(True),
                Account.is_posting_allowed.is_(True),
            )
        )

    def _paystack_bank_from_statement(
        self, payment: PaymentRecord, currency_code: str
    ) -> UUID | None:
        """Resolve a missing channel from an exact, unique Paystack reference.

        This is bank-routing evidence, not reconciliation: it neither creates
        a receipt nor confirms a statement match or books any fee.
        """
        reference = payment.external_id
        if not reference:
            return None
        bank_id = self._get_paystack_collection_bank(currency_code)
        if bank_id is None:
            return None
        lines = list(
            self.db.scalars(
                select(BankStatementLine)
                .join(
                    BankStatement,
                    BankStatement.statement_id == BankStatementLine.statement_id,
                )
                .where(
                    BankStatement.organization_id == self.organization_id,
                    BankStatement.bank_account_id == bank_id,
                    BankStatement.currency_code == currency_code,
                    BankStatement.import_source == "paystack_collections",
                    BankStatementLine.transaction_type == StatementLineType.credit,
                    BankStatementLine.reference == reference,
                )
                .limit(2)
            ).all()
        )
        if len(lines) != 1:
            return None
        line = lines[0]
        raw = line.raw_data if isinstance(line.raw_data, dict) else {}
        provider_id = raw.get("paystack_id")
        if not provider_id or str(provider_id) != line.transaction_id:
            return None
        try:
            net = Decimal(str(line.amount))
            gross = Decimal(str(raw["gross_amount"]))
            fee = Decimal(str(raw["fees"]))
        except (KeyError, InvalidOperation, ValueError, TypeError):
            return None
        if (
            not all(amount.is_finite() for amount in (net, gross, fee))
            or net <= 0
            or fee < 0
            or net + fee != gross
            or payment.amount not in {net, gross}
            or payment.currency.upper() != currency_code.upper()
        ):
            return None
        return bank_id

    def _match_bank_account(self, erp_fragment: str, currency_code: str) -> UUID | None:
        frag = _norm(erp_fragment)
        stmt = (
            select(BankAccount)
            .join(Account, Account.account_id == BankAccount.gl_account_id)
            .where(
                BankAccount.organization_id == self.organization_id,
                BankAccount.status == BankAccountStatus.active,
                BankAccount.currency_code == currency_code,
                Account.organization_id == self.organization_id,
                Account.is_active.is_(True),
                Account.is_posting_allowed.is_(True),
            )
        )
        matches = []
        for acct in self.db.scalars(stmt).all():
            haystack = _norm(f"{acct.account_name} {acct.bank_name}")
            if frag and frag in haystack:
                matches.append(acct.bank_account_id)
        return matches[0] if len(matches) == 1 else None
