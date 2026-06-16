from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

try:
    from datetime import UTC  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    UTC = timezone.utc  # type: ignore[assignment]

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.session_context import prime_tenant_context
from app.models.finance.ar.customer import Customer
from app.models.finance.ar.external_sync import (
    EntityType,
    ExternalSource,
    ExternalSync,
)
from app.models.finance.ar.invoice import Invoice
from app.models.finance.ar.invoice_line_tax import InvoiceLineTax
from app.models.finance.tax.tax_code import TaxCode, TaxType
from app.services.dotmac_sub.client import DotmacSubClient, DotmacSubConfig

from ._constants import DEFAULT_BANK_NAME_MAPPING

logger = logging.getLogger(__name__)


class BaseSyncMixin:
    """Core utilities shared by all dotmac_sub sync mixins."""

    SOURCE_PREFIX = "DSUB"

    # Provided by sibling mixins at runtime (combined in DotmacSubSyncService).
    _cache_reseller: Any
    _sync_single_subscriber: Any

    def __init__(
        self,
        db: Session,
        organization_id: UUID,
        ar_control_account_id: UUID,
        default_revenue_account_id: UUID | None = None,
        config: DotmacSubConfig | None = None,
        bank_name_mapping: dict[str, str | None] | None = None,
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self.ar_control_account_id = ar_control_account_id
        self.default_revenue_account_id = default_revenue_account_id
        self.config = config or DotmacSubConfig.for_org(db, organization_id)
        self._client: DotmacSubClient | None = None
        self._bank_name_mapping = bank_name_mapping or DEFAULT_BANK_NAME_MAPPING

        self._reseller_cache: dict[str, UUID] = {}
        self._subscriber_cache: dict[str, UUID] = {}
        self._account_cache: dict[str, UUID] = {}
        self._payment_channel_names: dict[str, str] = {}
        self._bank_account_mapping: dict[str, UUID] = {}
        self._default_bank_account_cache: dict[str, UUID] = {}

        self._sales_tax_code_resolved: bool = False
        self._sales_tax_code: TaxCode | None = None

    def _reprime_tenant_context(self) -> None:
        prime_tenant_context(self.db, self.organization_id)

    @property
    def client(self) -> DotmacSubClient:
        if self._client is None:
            self._client = DotmacSubClient(self.config)
        return self._client

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None

    @property
    def sales_tax_code(self) -> TaxCode | None:
        if not self._sales_tax_code_resolved:
            live = self._resolve_sales_tax()
            if live is not None:
                from types import SimpleNamespace

                self._sales_tax_code = SimpleNamespace(  # type: ignore[assignment]
                    tax_code_id=live.tax_code_id,
                    tax_code=live.tax_code,
                    tax_name=live.tax_name,
                    tax_rate=live.tax_rate,
                    is_inclusive=live.is_inclusive,
                    is_compound=live.is_compound,
                    tax_collected_account_id=live.tax_collected_account_id,
                    tax_paid_account_id=live.tax_paid_account_id,
                )
            self._sales_tax_code_resolved = True
        return self._sales_tax_code

    def _resolve_sales_tax(self) -> TaxCode | None:
        today = date.today()
        stmt = (
            select(TaxCode)
            .where(
                TaxCode.organization_id == self.organization_id,
                TaxCode.tax_type == TaxType.VAT,
                TaxCode.applies_to_sales.is_(True),
                TaxCode.is_active.is_(True),
                TaxCode.effective_from <= today,
                or_(
                    TaxCode.effective_to.is_(None),
                    TaxCode.effective_to >= today,
                ),
            )
            .order_by(
                TaxCode.is_inclusive.desc(),
                TaxCode.effective_from.desc(),
            )
            .limit(1)
        )
        tax_code = self.db.scalar(stmt)
        if not tax_code:
            logger.warning(
                "No active VAT sales tax code for org %s — invoices synced "
                "without tax extraction",
                self.organization_id,
            )
        return tax_code

    def _extract_tax(self, total: Decimal) -> tuple[Decimal, Decimal]:
        tc = self.sales_tax_code
        if not tc or tc.tax_rate == Decimal("0"):
            return total, Decimal("0")
        if tc.is_inclusive:
            divisor = Decimal("1") + tc.tax_rate
            tax_amount = (total * tc.tax_rate / divisor).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            subtotal = total - tax_amount
        else:
            subtotal = total
            tax_amount = (total * tc.tax_rate).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        return subtotal, tax_amount

    def _create_line_tax_record(
        self, line_id: UUID, base_amount: Decimal, tax_amount: Decimal
    ) -> None:
        tc = self.sales_tax_code
        if not tc or tax_amount == Decimal("0"):
            return
        self.db.add(
            InvoiceLineTax(
                line_id=line_id,
                tax_code_id=tc.tax_code_id,
                base_amount=base_amount,
                tax_rate=tc.tax_rate,
                tax_amount=tax_amount,
                is_inclusive=tc.is_inclusive,
                sequence=1,
            )
        )

    def _generate_invoice_number(self, reference_date: date | None = None) -> str:
        from app.models.finance.core_config.numbering_sequence import SequenceType
        from app.services.finance.common.numbering import SyncNumberingService

        return SyncNumberingService(self.db).generate_next_number(
            self.organization_id, SequenceType.INVOICE, reference_date
        )

    def _generate_payment_number(self, reference_date: date | None = None) -> str:
        from app.models.finance.core_config.numbering_sequence import SequenceType
        from app.services.finance.common.numbering import SyncNumberingService

        return SyncNumberingService(self.db).generate_next_number(
            self.organization_id, SequenceType.PAYMENT, reference_date
        )

    def _generate_credit_note_number(self, reference_date: date | None = None) -> str:
        from app.models.finance.core_config.numbering_sequence import SequenceType
        from app.services.finance.common.numbering import SyncNumberingService

        return SyncNumberingService(self.db).generate_next_number(
            self.organization_id, SequenceType.CREDIT_NOTE, reference_date
        )

    def _parse_date(self, date_str: str | None) -> date | None:
        if not date_str:
            return None
        try:
            if "T" in date_str:
                return datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
            return datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            logger.warning("Could not parse date: %s", date_str)
            return None

    def _get_synced_entity(
        self, entity_type: EntityType, external_id: str
    ) -> UUID | None:
        stmt = select(ExternalSync.local_entity_id).where(
            ExternalSync.organization_id == self.organization_id,
            ExternalSync.source == ExternalSource.DOTMAC_SUB,
            ExternalSync.entity_type == entity_type,
            ExternalSync.external_id == external_id,
        )
        return self.db.scalar(stmt)

    def _record_sync(
        self,
        entity_type: EntityType,
        external_id: str,
        local_entity_id: UUID,
        data_hash: str | None = None,
        external_updated_at: datetime | None = None,
    ) -> None:
        existing = self._get_synced_entity(entity_type, external_id)
        if existing:
            stmt = select(ExternalSync).where(
                ExternalSync.organization_id == self.organization_id,
                ExternalSync.source == ExternalSource.DOTMAC_SUB,
                ExternalSync.entity_type == entity_type,
                ExternalSync.external_id == external_id,
            )
            sync_record = self.db.scalar(stmt)
            if sync_record:
                sync_record.synced_at = datetime.now(tz=UTC)
                sync_record.sync_hash = data_hash
                sync_record.local_entity_id = local_entity_id
                if external_updated_at:
                    sync_record.external_updated_at = external_updated_at
        else:
            self.db.add(
                ExternalSync(
                    organization_id=self.organization_id,
                    source=ExternalSource.DOTMAC_SUB,
                    entity_type=entity_type,
                    external_id=external_id,
                    local_entity_id=local_entity_id,
                    sync_hash=data_hash,
                    external_updated_at=external_updated_at,
                )
            )

    def _compute_hash(self, data: dict[str, Any]) -> str:
        json_str = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(json_str.encode()).hexdigest()[:32]

    def _has_changed(
        self, entity_type: EntityType, external_id: str, new_hash: str
    ) -> bool:
        stmt = select(ExternalSync.sync_hash).where(
            ExternalSync.organization_id == self.organization_id,
            ExternalSync.source == ExternalSource.DOTMAC_SUB,
            ExternalSync.entity_type == entity_type,
            ExternalSync.external_id == external_id,
        )
        old_hash = self.db.scalar(stmt)
        return old_hash != new_hash

    def _get_existing_invoice(self, invoice_number: str) -> Invoice | None:
        stmt = select(Invoice).where(
            Invoice.organization_id == self.organization_id,
            Invoice.invoice_number == invoice_number,
        )
        return self.db.scalar(stmt)

    def _map_invoice_status(self, status: str, balance_due: Decimal) -> Any:
        from app.models.finance.ar.invoice import InvoiceStatus

        s = (status or "").lower()
        if s == "paid" or balance_due == Decimal("0"):
            return InvoiceStatus.PAID
        if s == "partially_paid":
            return InvoiceStatus.PARTIALLY_PAID
        if s == "void":
            return InvoiceStatus.VOID
        return InvoiceStatus.POSTED

    def _get_customer_for_account(self, account_id: str) -> UUID | None:
        if account_id in self._account_cache:
            return self._account_cache[account_id]
        mapped = self._get_synced_entity(EntityType.BILLING_ACCOUNT, account_id)
        if mapped:
            self._account_cache[account_id] = mapped
            return mapped
        customer_id = self._resolve_account_owner(account_id)
        if customer_id:
            self._account_cache[account_id] = customer_id
            self._record_sync(EntityType.BILLING_ACCOUNT, account_id, customer_id)
        return customer_id

    def _resolve_account_owner(self, account_id: str) -> UUID | None:
        """Resolve an invoice/payment ``account_id`` to its owning ERP customer.

        Verified against the live dotmac_sub API (2026-06-16): for invoices,
        payments and credit notes ``account_id`` is the **subscriber id** (it
        resolves via ``GET /subscribers/{id}``, not ``/billing-accounts``). So we
        resolve subscriber-first to that subscriber's ERP customer (the reseller
        link is grouping only — invoices/payments post to GL normally).

        Fallback: the 34 reseller-named "billing accounts" are a separate
        consolidation concept; if an ``account_id`` is one of those instead, map
        it to the reseller's ERP *parent* customer (which posts to GL).
        """
        from app.services.dotmac_sub.client import DotmacSubError

        # 1) Already-synced subscriber → child customer.
        cust = self._get_customer_by_dotmac_sub_id(account_id)
        if cust:
            return cust.customer_id

        # 2) Subscriber not yet synced this run → fetch + upsert on demand so the
        #    invoice/payment can attach (subscribers are normally synced first,
        #    but batch limits or webhooks can arrive out of order).
        try:
            sub = self.client.get_subscriber(account_id)
        except DotmacSubError:
            sub = None
        if sub is not None:
            if sub.reseller_id:
                self._cache_reseller(sub.reseller_id)
            from ._types import SyncResult

            self._sync_single_subscriber(
                sub, None, SyncResult(success=True, entity_type="subscribers"), False
            )
            resolved = self._get_customer_by_dotmac_sub_id(account_id)
            if resolved:
                return resolved.customer_id

        # 3) Fallback: a reseller-level billing account → reseller parent customer.
        try:
            account = self.client.get_billing_account(account_id)
        except DotmacSubError:
            account = None
        if account and account.reseller_id:
            return self._reseller_customer_id(account.reseller_id)
        return None

    def _get_customer_by_dotmac_sub_id(self, dotmac_sub_id: str) -> Customer | None:
        stmt = select(Customer).where(
            Customer.organization_id == self.organization_id,
            Customer.dotmac_sub_id == dotmac_sub_id,
        )
        return self.db.scalar(stmt)

    def _reseller_customer_id(self, reseller_id: str) -> UUID | None:
        if reseller_id in self._reseller_cache:
            return self._reseller_cache[reseller_id]
        stmt = select(Customer).where(
            Customer.organization_id == self.organization_id,
            Customer.dotmac_sub_reseller_id == reseller_id,
            Customer.parent_customer_id.is_(None),
        )
        reseller = self.db.scalar(stmt)
        if reseller:
            self._reseller_cache[reseller_id] = reseller.customer_id
            return reseller.customer_id
        return None
