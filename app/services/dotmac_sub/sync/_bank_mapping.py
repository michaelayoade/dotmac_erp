from __future__ import annotations

import logging
import re
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.models.finance.banking.bank_account import BankAccount, BankAccountStatus
from app.models.finance.gl.account import Account

logger = logging.getLogger(__name__)


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


class BankMappingMixin:
    """Map dotmac_sub payment channels → ERP bank accounts."""

    db: Any
    client: Any
    organization_id: UUID
    _bank_name_mapping: dict[str, str | None]
    _payment_channel_names: dict[str, str]
    _bank_account_mapping: dict[str, UUID]
    _default_bank_account_cache: dict[str, UUID]

    def _load_payment_channels(self) -> None:
        if self._payment_channel_names:
            return
        try:
            for ch in self.client.get_payment_channels():
                cid = str(ch.get("id", ""))
                name = ch.get("name") or ch.get("code") or ""
                if cid:
                    self._payment_channel_names[cid] = name
        except Exception:  # noqa: BLE001
            logger.warning("Could not load dotmac_sub payment channels", exc_info=True)

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
        self, channel_id: str | None, currency_code: str
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
                    acct = self._match_bank_account(erp_fragment, currency_code)
                    if acct and channel_id:
                        self._bank_account_mapping[cache_key] = acct
                    # An explicitly configured but unavailable bank is not
                    # permission to post to an unrelated default account.
                    return acct
        return self._get_default_bank_account(currency_code)

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
        for acct in self.db.scalars(stmt).all():
            haystack = _norm(f"{acct.account_name} {acct.bank_name}")
            if frag and frag in haystack:
                return acct.bank_account_id
        return None
