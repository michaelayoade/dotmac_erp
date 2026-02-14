"""
Payment Entry Sync Service - ERPNext to DotMac ERP.

Syncs ERPNext Payment Entries:
- payment_type=Receive → ar.customer_payment
- payment_type=Pay → ap.supplier_payment

Handles dedup against Splynx-originated payments via splynx_id.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.ar.customer_payment import (
    CustomerPayment,
    PaymentMethod,
    PaymentStatus,
)

from ..mappings.payment_entry import PaymentEntryMapping, PaymentEntryReferenceMapping
from .base import BaseSyncService, SyncResult

logger = logging.getLogger(__name__)

# Map ERPNext mode_of_payment → DotMac PaymentMethod
_PAYMENT_METHOD_MAP: dict[str, PaymentMethod] = {
    "Cash": PaymentMethod.CASH,
    "Bank Draft": PaymentMethod.CHECK,
    "Cheque": PaymentMethod.CHECK,
    "Bank Transfer": PaymentMethod.BANK_TRANSFER,
    "Wire Transfer": PaymentMethod.BANK_TRANSFER,
    "Credit Card": PaymentMethod.CARD,
    "Debit Card": PaymentMethod.CARD,
    "Direct Debit": PaymentMethod.DIRECT_DEBIT,
}

# Map ERPNext mode_of_payment → DotMac AP PaymentMethod
_AP_PAYMENT_METHOD_MAP: dict[str, str] = {
    "Cash": "CASH",
    "Bank Draft": "CHECK",
    "Cheque": "CHECK",
    "Bank Transfer": "BANK_TRANSFER",
    "Wire Transfer": "WIRE",
    "Credit Card": "CARD",
}


class PaymentEntrySyncService(BaseSyncService[CustomerPayment]):
    """Sync Payment Entries from ERPNext.

    Handles both AR (Receive) and AP (Pay) payment types.
    AP payments are synced via a separate _sync_ap_payment path.
    """

    source_doctype = "Payment Entry"
    target_table = "ar.customer_payment"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        super().__init__(db, organization_id, user_id)
        self._mapping = PaymentEntryMapping()
        self._ref_mapping = PaymentEntryReferenceMapping()
        self._payment_cache: dict[str, CustomerPayment] = {}

    def fetch_records(self, client: Any, since: datetime | None = None):
        """Fetch Payment Entries with references."""
        filters: dict[str, Any] = {"docstatus": 1}  # Only submitted

        if since:
            for pe in client.get_modified_since(
                doctype="Payment Entry",
                since=since,
                filters=filters,
            ):
                # Incremental list calls may omit amount/party fields; fetch full doc.
                try:
                    full_doc = client.get_document("Payment Entry", pe["name"])
                    if pe.get("modified"):
                        full_doc["modified"] = pe["modified"]
                    yield full_doc
                except Exception:
                    pe["references"] = client.list_documents(
                        doctype="Payment Entry Reference",
                        filters={"parent": pe["name"]},
                        parent="Payment Entry",
                    )
                    yield pe
        else:
            yield from client.get_payment_entries(filters=filters)

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        result = self._mapping.transform_record(record)
        # Transform references
        result["_references"] = [
            self._ref_mapping.transform_record(ref)
            for ref in record.get("references", [])
        ]
        return result

    def _resolve_entity_id(
        self, source_name: str | None, source_doctype: str
    ) -> uuid.UUID | None:
        if not source_name:
            return None

        from app.models.sync import SyncEntity

        sync_entity = self.db.execute(
            select(SyncEntity).where(
                SyncEntity.organization_id == self.organization_id,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == source_doctype,
                SyncEntity.source_name == source_name,
            )
        ).scalar_one_or_none()

        if sync_entity and sync_entity.target_id:
            return sync_entity.target_id
        return None

    def _resolve_account_id(self, account_source_name: str | None) -> uuid.UUID | None:
        return self._resolve_entity_id(account_source_name, "Account")

    def _map_payment_method(self, mode: str | None) -> PaymentMethod:
        if not mode:
            return PaymentMethod.BANK_TRANSFER
        return _PAYMENT_METHOD_MAP.get(mode, PaymentMethod.BANK_TRANSFER)

    def _check_splynx_dedup(self, splynx_id: str | None) -> CustomerPayment | None:
        """Check if a Splynx-originated payment already exists."""
        if not splynx_id:
            return None
        return self.db.scalar(
            select(CustomerPayment).where(
                CustomerPayment.organization_id == self.organization_id,
                CustomerPayment.splynx_id == str(splynx_id),
            )
        )

    def _generate_payment_number(self, reference_date=None) -> str:
        from app.models.finance.core_config.numbering_sequence import SequenceType
        from app.services.finance.common.numbering import SyncNumberingService

        svc = SyncNumberingService(self.db)
        return svc.generate_next_number(
            self.organization_id, SequenceType.PAYMENT, reference_date
        )

    def _create_allocations(
        self,
        payment_id: uuid.UUID,
        payment_date,
        references: list[dict[str, Any]],
    ) -> None:
        """Create payment allocations from Payment Entry References."""
        from app.models.finance.ar.payment_allocation import PaymentAllocation

        for ref_data in references:
            ref_doctype = ref_data.get("_reference_doctype", "")
            ref_name = ref_data.get("_reference_source_name")
            allocated_amount = ref_data.get("allocated_amount", Decimal("0"))

            if ref_doctype != "Sales Invoice" or not ref_name:
                continue

            # Resolve invoice
            invoice_id = self._resolve_entity_id(ref_name, "Sales Invoice")
            if not invoice_id:
                # Try looking up by erpnext_id directly on invoice
                from app.models.finance.ar.invoice import Invoice

                inv = self.db.scalar(
                    select(Invoice).where(
                        Invoice.organization_id == self.organization_id,
                        Invoice.erpnext_id == ref_name,
                    )
                )
                if inv:
                    invoice_id = inv.invoice_id

            if not invoice_id:
                logger.warning(
                    "Cannot allocate payment: invoice '%s' not found", ref_name
                )
                continue

            allocation = PaymentAllocation(
                payment_id=payment_id,
                invoice_id=invoice_id,
                allocated_amount=allocated_amount,
                allocation_date=payment_date,
            )
            self.db.add(allocation)

    def _is_ap_payment(self, data: dict[str, Any]) -> bool:
        """Check if this is an AP payment (Pay type)."""
        payment_type: str = data.get("_payment_type", "")
        return payment_type == "Pay"

    def _sync_ap_payment(self, data: dict[str, Any]) -> uuid.UUID:
        """Sync an AP payment entry to ap.supplier_payment.

        Returns the payment_id of the created SupplierPayment.
        """
        from app.models.finance.ap.supplier_payment import SupplierPayment

        supplier_source = data.pop("_party_source_name", None)
        paid_from_acct = data.pop("_paid_from_account", None)
        mode = data.pop("_mode_of_payment", None)
        refs = data.pop("_references", [])
        data.pop("_payment_type", None)
        data.pop("_party_type", None)
        data.pop("_paid_to_account", None)
        data.pop("_docstatus", None)
        data.pop("_splynx_id", None)
        data.pop("_source_name", None)
        data.pop("_source_modified", None)
        data.pop("_received_amount", None)
        data.pop("_reference_date", None)

        supplier_id = self._resolve_entity_id(supplier_source, "Supplier")
        if not supplier_id:
            raise ValueError(f"Supplier '{supplier_source}' not found")

        bank_account_id = self._resolve_account_id(paid_from_acct)

        # Map payment method for AP
        ap_method = _AP_PAYMENT_METHOD_MAP.get(mode or "", "BANK_TRANSFER")

        functional_amount = data.get("functional_currency_amount")
        if not functional_amount:
            exchange_rate = data.get("exchange_rate", Decimal("1"))
            functional_amount = data.get("amount", Decimal("0")) * (
                exchange_rate or Decimal("1")
            )

        # Generate AP payment number
        from app.models.finance.core_config.numbering_sequence import SequenceType
        from app.services.finance.common.numbering import SyncNumberingService

        svc = SyncNumberingService(self.db)
        payment_number = svc.generate_next_number(
            self.organization_id, SequenceType.PAYMENT, data.get("payment_date")
        )

        payment = SupplierPayment(
            organization_id=self.organization_id,
            payment_number=payment_number[:30],
            payment_date=data["payment_date"],
            supplier_id=supplier_id,
            payment_method=ap_method,
            status="CLEARED",
            currency_code=data.get("currency_code", "NGN")[:3],
            amount=data.get("amount", Decimal("0")),
            functional_currency_amount=functional_amount,
            exchange_rate=data.get("exchange_rate", Decimal("1")),
            bank_account_id=bank_account_id,
            reference=data.get("reference"),
            created_by_user_id=self.user_id,
        )
        self.db.add(payment)
        self.db.flush()

        # Create AP allocations
        if refs:
            self._create_ap_allocations(payment.payment_id, data["payment_date"], refs)

        return payment.payment_id

    def _create_ap_allocations(
        self,
        payment_id: uuid.UUID,
        payment_date,
        references: list[dict[str, Any]],
    ) -> None:
        """Create AP payment allocations."""
        from app.models.finance.ap.ap_payment_allocation import APPaymentAllocation

        for ref_data in references:
            ref_doctype = ref_data.get("_reference_doctype", "")
            ref_name = ref_data.get("_reference_source_name")
            allocated_amount = ref_data.get("allocated_amount", Decimal("0"))

            if ref_doctype != "Purchase Invoice" or not ref_name:
                continue

            invoice_id = self._resolve_entity_id(ref_name, "Purchase Invoice")
            if not invoice_id:
                logger.warning(
                    "Cannot allocate AP payment: invoice '%s' not found", ref_name
                )
                continue

            allocation = APPaymentAllocation(
                payment_id=payment_id,
                invoice_id=invoice_id,
                allocated_amount=allocated_amount,
                allocation_date=payment_date,
            )
            self.db.add(allocation)

    def _sync_single_record(
        self, record: dict[str, Any], result: SyncResult
    ) -> CustomerPayment | None:
        """Override to handle AP payments in a separate path.

        ERPNext Payment Entries can be payment_type=Receive (AR) or
        payment_type=Pay (AP).  The base class is typed as
        ``BaseSyncService[CustomerPayment]`` so we can't return a
        ``SupplierPayment`` from ``create_entity``.  Instead we intercept
        AP payments here, create the ``SupplierPayment`` directly, mark
        the ``SyncEntity`` as SYNCED, and return ``None`` so the base
        class never sees them.
        """
        from app.models.sync import SyncStatus

        source_name = self.get_unique_key(record)
        source_modified = record.get("modified")
        if isinstance(source_modified, str):
            try:
                source_modified = datetime.fromisoformat(source_modified)
            except ValueError:
                source_modified = None

        # Transform first so we can inspect _payment_type
        data = self.transform_record(record)

        payment_type = data.get("_payment_type")
        party_type = data.get("_party_type")
        party_source = data.get("_party_source_name")

        if payment_type == "Receive" and (
            party_type != "Customer" or not party_source
        ):
            # Skip unsupported AR payment-party combinations (e.g. Internal Transfer).
            result.skipped_count += 1
            logger.info(
                "Skipping Payment Entry %s: payment_type=%s party_type=%s party=%s",
                source_name,
                payment_type,
                party_type,
                party_source,
            )
            return None

        if payment_type == "Pay" and (
            party_type != "Supplier" or not party_source
        ):
            # Skip non-supplier AP payments (e.g. employee reimbursements).
            result.skipped_count += 1
            logger.info(
                "Skipping Payment Entry %s: payment_type=%s party_type=%s party=%s",
                source_name,
                payment_type,
                party_type,
                party_source,
            )
            return None

        if not self._is_ap_payment(data):
            # AR payment — delegate to the standard base class flow
            return super()._sync_single_record(record, result)

        # --- AP payment path ---
        sync_entity = self.get_sync_entity(source_name)
        if not sync_entity:
            sync_entity = self.create_sync_entity(source_name)

        # Skip if already synced and not modified
        if sync_entity.sync_status == SyncStatus.SYNCED:
            if not self.should_update(sync_entity, source_modified):
                result.skipped_count += 1
                return None

        ap_payment_id = self._sync_ap_payment(data)

        # Track in sync_entity with target_table override
        sync_entity.target_table = "ap.supplier_payment"
        sync_entity.source_modified = source_modified
        sync_entity.mark_synced(ap_payment_id)

        result.synced_count += 1
        logger.info("Synced AP payment: %s", source_name)
        return None

    def create_entity(self, data: dict[str, Any]) -> CustomerPayment:
        # AR payment path (AP payments handled in _sync_single_record override)
        data.pop("_source_name", None)
        data.pop("_source_modified", None)
        customer_source = data.pop("_party_source_name", None)
        data.pop("_party_type", None)
        data.pop("_payment_type", None)
        paid_to_acct = data.pop("_paid_to_account", None)
        data.pop("_paid_from_account", None)
        mode = data.pop("_mode_of_payment", None)
        refs = data.pop("_references", [])
        splynx_id = data.pop("_splynx_id", None)
        data.pop("_docstatus", None)
        data.pop("_received_amount", None)
        data.pop("_reference_date", None)

        # Splynx dedup
        existing = self._check_splynx_dedup(splynx_id)
        if existing:
            logger.info(
                "Splynx dedup: payment already exists as %s",
                existing.payment_number,
            )
            return existing

        # Resolve customer
        customer_id = self._resolve_entity_id(customer_source, "Customer")
        if not customer_id:
            raise ValueError(f"Customer '{customer_source}' not found")

        bank_account_id = self._resolve_account_id(paid_to_acct)

        payment_method = self._map_payment_method(mode)

        amount = data.get("amount")
        if amount is None:
            amount = data.get("_received_amount", Decimal("0"))

        functional_amount = data.get("functional_currency_amount")
        if not functional_amount:
            exchange_rate = data.get("exchange_rate", Decimal("1"))
            functional_amount = amount * (
                exchange_rate or Decimal("1")
            )

        payment_number = self._generate_payment_number(data.get("payment_date"))

        payment = CustomerPayment(
            organization_id=self.organization_id,
            payment_number=payment_number[:30],
            payment_date=data["payment_date"],
            customer_id=customer_id,
            payment_method=payment_method,
            status=PaymentStatus.CLEARED,
            currency_code=data.get("currency_code", "NGN")[:3],
            amount=amount,
            gross_amount=amount,
            functional_currency_amount=functional_amount,
            exchange_rate=data.get("exchange_rate", Decimal("1")),
            bank_account_id=bank_account_id,
            reference=data.get("reference"),
            created_by_user_id=self.user_id,
            erpnext_id=None,  # Set by base class
            last_synced_at=datetime.now(UTC),
        )

        self.db.add(payment)
        self.db.flush()

        # Create allocations
        if refs:
            self._create_allocations(payment.payment_id, data["payment_date"], refs)

        return payment

    def update_entity(
        self, entity: CustomerPayment, data: dict[str, Any]
    ) -> CustomerPayment:
        # Pop all internal fields
        data.pop("_source_name", None)
        data.pop("_source_modified", None)
        data.pop("_party_source_name", None)
        data.pop("_party_type", None)
        data.pop("_payment_type", None)
        data.pop("_paid_to_account", None)
        data.pop("_paid_from_account", None)
        data.pop("_mode_of_payment", None)
        data.pop("_references", [])
        data.pop("_splynx_id", None)
        data.pop("_docstatus", None)
        data.pop("_received_amount", None)
        data.pop("_reference_date", None)

        amount = data.get("amount")
        if amount is None:
            amount = data.get("_received_amount")
        if amount is not None:
            entity.amount = amount
        entity.last_synced_at = datetime.now(UTC)
        return entity

    def get_entity_id(self, entity: CustomerPayment) -> uuid.UUID:
        return entity.payment_id

    def find_existing_entity(self, source_name: str) -> CustomerPayment | None:
        if source_name in self._payment_cache:
            return self._payment_cache[source_name]

        sync_entity = self.get_sync_entity(source_name)
        if sync_entity and sync_entity.target_id:
            payment = self.db.get(CustomerPayment, sync_entity.target_id)
            if payment:
                self._payment_cache[source_name] = payment
                return payment

        # Fallback for historical rows that may have erpnext_id but no sync_entity link.
        payment = self.db.scalar(
            select(CustomerPayment).where(
                CustomerPayment.organization_id == self.organization_id,
                CustomerPayment.erpnext_id == source_name,
            )
        )
        if payment:
            self._payment_cache[source_name] = payment
            return payment

        return None
