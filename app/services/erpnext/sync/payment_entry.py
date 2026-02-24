"""
Payment Entry Sync Service - ERPNext to DotMac ERP.

Syncs ERPNext Payment Entries:
- payment_type=Receive → ar.customer_payment
- payment_type=Pay → ap.supplier_payment
- payment_type=Pay + party_type=Employee + Expense Claim refs
  → update expense.expense_claim paid metadata

Handles dedup against Splynx-originated payments via splynx_id.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.ap.supplier import Supplier
from app.models.finance.ar.customer import Customer
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
        self._customer_exact_lookup: dict[str, uuid.UUID] | None = None
        self._customer_norm_lookup: dict[str, uuid.UUID] | None = None

    @staticmethod
    def _normalize_party_name(value: str | None) -> str:
        if not value:
            return ""
        norm = value.strip().lower()
        norm = norm.replace("&", "and")
        norm = re.sub(r"\s+", " ", norm)
        norm = re.sub(r"\s*-\s*\d+$", "", norm)  # "Name - 1"
        norm = re.sub(r"\s+\d+\)$", ")", norm)  # "(Name 1)" -> "(Name)"
        return norm

    def _ensure_customer_lookups(self) -> None:
        if (
            self._customer_exact_lookup is not None
            and self._customer_norm_lookup is not None
        ):
            return

        rows = self.db.execute(
            select(
                Customer.customer_id,
                Customer.customer_code,
                Customer.legal_name,
                Customer.trading_name,
                Customer.erpnext_id,
            ).where(Customer.organization_id == self.organization_id)
        ).all()

        exact: dict[str, uuid.UUID] = {}
        norm_multi: dict[str, set[uuid.UUID]] = {}

        for row in rows:
            customer_id = row.customer_id
            for key in (row.erpnext_id, row.customer_code):
                if key and key not in exact:
                    exact[str(key)] = customer_id

            for key in (
                row.erpnext_id,
                row.customer_code,
                row.legal_name,
                row.trading_name,
            ):
                nk = self._normalize_party_name(key)
                if not nk:
                    continue
                norm_multi.setdefault(nk, set()).add(customer_id)

        norm_unique = {k: next(iter(v)) for k, v in norm_multi.items() if len(v) == 1}

        self._customer_exact_lookup = exact
        self._customer_norm_lookup = norm_unique

    def fetch_records(self, client: Any, since: datetime | None = None):
        """Fetch Payment Entries with references."""
        # For matching OPEX outflows we only need *outgoing* payments.
        # Receiving-side (AR) payments can be synced separately once Customer
        # master data is clean, but they are not required for expense matching.
        filters: dict[str, Any] = {
            "docstatus": 1,  # Only submitted
            "payment_type": "Pay",
        }

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

    def _resolve_customer_id(self, source_name: str | None) -> uuid.UUID | None:
        """Resolve Customer by sync_entity first, then direct customer fields."""
        if not source_name:
            return None

        candidate = self._resolve_entity_id(source_name, "Customer")
        if candidate and self.db.get(Customer, candidate):
            return candidate

        self._ensure_customer_lookups()
        assert self._customer_exact_lookup is not None  # noqa: S101  # nosec B101
        assert self._customer_norm_lookup is not None  # noqa: S101  # nosec B101

        if source_name in self._customer_exact_lookup:
            return self._customer_exact_lookup[source_name]

        source_norm = self._normalize_party_name(source_name)
        if source_norm in self._customer_norm_lookup:
            return self._customer_norm_lookup[source_norm]

        # Fallback for truncated external IDs: allow short prefix differences.
        prefix_candidates: set[uuid.UUID] = set()
        for key, customer_id in self._customer_norm_lookup.items():
            if not source_norm:
                break
            if key.startswith(source_norm) or source_norm.startswith(key):
                if abs(len(key) - len(source_norm)) <= 3:
                    prefix_candidates.add(customer_id)
        if len(prefix_candidates) == 1:
            return next(iter(prefix_candidates))

        customer = self.db.scalar(
            select(Customer).where(
                Customer.organization_id == self.organization_id,
                Customer.erpnext_id == source_name,
            )
        )
        if customer:
            return customer.customer_id

        customer = self.db.scalar(
            select(Customer).where(
                Customer.organization_id == self.organization_id,
                Customer.customer_code == source_name,
            )
        )
        if customer:
            return customer.customer_id

        return None

    def _resolve_supplier_id(self, source_name: str | None) -> uuid.UUID | None:
        """Resolve Supplier by sync_entity first, then direct supplier fields."""
        if not source_name:
            return None

        candidate = self._resolve_entity_id(source_name, "Supplier")
        if candidate and self.db.get(Supplier, candidate):
            return candidate

        supplier = self.db.scalar(
            select(Supplier).where(
                Supplier.organization_id == self.organization_id,
                Supplier.erpnext_id == source_name,
            )
        )
        if supplier:
            return supplier.supplier_id

        supplier = self.db.scalar(
            select(Supplier).where(
                Supplier.organization_id == self.organization_id,
                Supplier.supplier_code == source_name,
            )
        )
        if supplier:
            return supplier.supplier_id

        return None

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
        data.pop("_custom_expense_claim", None)

        supplier_id = self._resolve_supplier_id(supplier_source)
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

    def _sync_employee_expense_payment(
        self,
        data: dict[str, Any],
        source_name: str,
    ) -> uuid.UUID | None:
        """Apply employee reimbursement Payment Entry to synced Expense Claims.

        ERPNext commonly records expense reimbursements as:
        - Payment Entry.payment_type = "Pay"
        - Payment Entry.party_type = "Employee"
        - Payment Entry Reference.reference_doctype = "Expense Claim"

        We use this to backfill ExpenseClaim.paid_on/payment_reference.
        """
        from app.models.expense.expense_claim import ExpenseClaim, ExpenseClaimStatus

        refs = data.get("_references", []) or []
        payment_date = data.get("payment_date")
        # ACC-PAY-* source_name is usually the best external payment key.
        payment_ref = source_name or data.get("reference")

        claim_ids: list[uuid.UUID] = []

        # Primary path: child-table references
        candidates: list[str] = []
        for ref_data in refs:
            ref_doctype = ref_data.get("_reference_doctype", "")
            ref_name = ref_data.get("_reference_source_name")
            if ref_doctype == "Expense Claim" and ref_name:
                candidates.append(str(ref_name))

        # Fallback path: instance-specific custom fields / reference_no
        # Some reimbursements do not populate the child "references" rows.
        if not candidates:
            for maybe in (
                data.get("_custom_expense_claim"),
                data.get("reference"),
            ):
                if isinstance(maybe, str) and maybe.startswith("HR-EXP-"):
                    candidates.append(maybe)

        for ref_name in candidates:
            claim_id = self._resolve_entity_id(ref_name, "Expense Claim")
            if not claim_id:
                claim = self.db.scalar(
                    select(ExpenseClaim).where(
                        ExpenseClaim.organization_id == self.organization_id,
                        ExpenseClaim.erpnext_id == ref_name,
                    )
                )
                claim_id = claim.claim_id if claim else None

            if not claim_id:
                logger.warning(
                    "Cannot apply employee payment %s: expense claim '%s' not found",
                    source_name,
                    ref_name,
                )
                continue

            claim = self.db.get(ExpenseClaim, claim_id)
            if not claim:
                continue

            claim.status = ExpenseClaimStatus.PAID
            if payment_date is not None:
                claim.paid_on = payment_date
            if payment_ref:
                claim.payment_reference = str(payment_ref)[:100]
                claim.updated_by_id = self.user_id
                claim_ids.append(claim_id)

        if not claim_ids:
            return None
        return claim_ids[0]

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

        # Some ERPNext list endpoints can omit key fields; don't try to
        # sync incomplete records.
        if not payment_type:
            result.skipped_count += 1
            logger.warning(
                "Skipping Payment Entry %s: missing payment_type",
                source_name,
            )
            return None

        # Unsupported types (e.g. Internal Transfer) should be skipped.
        if payment_type not in {"Receive", "Pay"}:
            result.skipped_count += 1
            logger.info(
                "Skipping Payment Entry %s: unsupported payment_type=%s",
                source_name,
                payment_type,
            )
            return None

        if payment_type == "Receive" and (party_type != "Customer" or not party_source):
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

        if payment_type == "Pay" and (party_type != "Supplier" or not party_source):
            # Handle employee reimbursements tied to Expense Claims.
            if party_type == "Employee" and party_source:
                sync_entity = self.get_sync_entity(source_name)
                if not sync_entity:
                    sync_entity = self.create_sync_entity(source_name)

                if sync_entity.sync_status == SyncStatus.SYNCED:
                    if not self.should_update(sync_entity, source_modified):
                        result.skipped_count += 1
                        return None

                claim_id = self._sync_employee_expense_payment(data, source_name)
                if claim_id is None:
                    result.skipped_count += 1
                    logger.info(
                        "Skipping Payment Entry %s: no Expense Claim references",
                        source_name,
                    )
                    return None

                sync_entity.target_table = "expense.expense_claim"
                sync_entity.source_modified = source_modified
                sync_entity.mark_synced(claim_id)
                result.synced_count += 1
                logger.info(
                    "Synced employee expense payment: %s -> claim %s",
                    source_name,
                    claim_id,
                )
                return None

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
            # AR payment — if Customer mapping is missing (or stale), skip rather
            # than failing the whole run. We still want AP/Employee payments to
            # sync even if AR master data is incomplete.
            if payment_type == "Receive" and party_type == "Customer" and party_source:
                customer_id = self._resolve_customer_id(party_source)
                if customer_id is None:
                    result.skipped_count += 1
                    logger.warning(
                        "Skipping AR Payment Entry %s: Customer '%s' not mapped",
                        source_name,
                        party_source,
                    )
                    return None

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
        customer_id = self._resolve_customer_id(customer_source)
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
            functional_amount = amount * (exchange_rate or Decimal("1"))

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
