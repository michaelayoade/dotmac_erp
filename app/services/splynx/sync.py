"""
Splynx Sync Service.

Syncs customers, invoices, payments, and credit notes from Splynx
to Dotmac ERP AR module.
"""

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, TypedDict
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.finance.ar.customer import Customer, CustomerType
from app.models.finance.ar.customer_payment import (
    CustomerPayment,
    PaymentMethod,
    PaymentStatus,
)
from app.models.finance.ar.external_sync import EntityType, ExternalSource, ExternalSync
from app.models.finance.ar.invoice import Invoice, InvoiceStatus, InvoiceType
from app.models.finance.ar.invoice_line import InvoiceLine
from app.models.finance.ar.payment_allocation import PaymentAllocation
from app.services.splynx.client import (
    SplynxClient,
    SplynxConfig,
    SplynxCreditNote,
    SplynxCustomer,
    SplynxError,
    SplynxInvoice,
    SplynxPayment,
    SplynxPaymentMethod,
)

logger = logging.getLogger(__name__)

# Represents automated/system-initiated actions in audit columns.
# Used as fallback when no real user ID is available (e.g. batch sync).
SYSTEM_USER_ID = UUID("00000000-0000-0000-0000-000000000000")


class PaystackReconcileResult(TypedDict):
    matched_by_reference: int
    matched_by_date_amount: int
    matched_by_customer: int
    ambiguous_matches: int
    unmatched_payments: int
    unmatched_statements: int
    total_matched_amount: Decimal
    errors: list[str]


class BankReconcileResult(TypedDict):
    bank_name: str
    matched_by_date_amount: int
    matched_by_customer: int
    ambiguous_matches: int
    unmatched_payments: int
    unmatched_statements: int
    total_matched_amount: Decimal
    errors: list[str]


class BulkReconcileResult(TypedDict):
    bank_name: str
    bulk_matches: int
    payments_matched: int
    total_matched_amount: Decimal
    errors: list[str]


@dataclass
class SyncResult:
    """Result of a sync operation."""

    success: bool
    entity_type: str
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "success": self.success,
            "entity_type": self.entity_type,
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "errors": self.errors,
            "message": self.message,
        }


@dataclass
class FullSyncResult:
    """Result of a full sync operation (all entity types)."""

    customers: SyncResult
    invoices: SyncResult
    payments: SyncResult
    credit_notes: SyncResult
    total_errors: int = 0
    duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "customers": self.customers.to_dict(),
            "invoices": self.invoices.to_dict(),
            "payments": self.payments.to_dict(),
            "credit_notes": self.credit_notes.to_dict(),
            "total_errors": self.total_errors,
            "duration_seconds": self.duration_seconds,
        }


class SplynxSyncService:
    """
    Service for syncing data from Splynx to Dotmac ERP.

    Syncs:
    - Customers -> AR Customers
    - Invoices -> AR Invoices
    - Payments -> AR Receipts (tracked via correlation_id)
    - Credit Notes -> AR Invoices (type=CREDIT_NOTE)
    """

    # Prefix for Splynx-sourced records
    SOURCE_PREFIX = "SPLYNX"

    # Default mapping from Splynx payment method name fragments to ERP
    # bank account name fragments.  Override via constructor parameter.
    DEFAULT_BANK_NAME_MAPPING: dict[str, str | None] = {
        "zenith 461": "zenith 461",
        "zenith 523": "zenith 523",
        "paystack": "paystack collections",
        "pay stack": "paystack collections",
        "uba": "uba 96",
        "flutterwave": "flutterwave",
        "flutter wave": "flutterwave",
        "fluterwave": "flutterwave",
        "dotmac usd": "zenith usd",
        "cash": None,  # Cash doesn't map to a bank account
    }

    def __init__(
        self,
        db: Session,
        organization_id: UUID,
        ar_control_account_id: UUID,
        default_revenue_account_id: UUID | None = None,
        config: SplynxConfig | None = None,
        bank_name_mapping: dict[str, str | None] | None = None,
    ):
        """
        Initialize sync service.

        Args:
            db: Database session
            organization_id: Target organization ID
            ar_control_account_id: AR control account for invoices
            default_revenue_account_id: Default revenue account for invoice lines
            config: Optional Splynx config (uses settings if not provided)
            bank_name_mapping: Splynx payment-method name fragment ->
                ERP bank-account name fragment.  ``None`` values mean
                "no bank account" (e.g. cash).  Falls back to
                ``DEFAULT_BANK_NAME_MAPPING`` when not provided.
        """
        self.db = db
        self.organization_id = organization_id
        self.ar_control_account_id = ar_control_account_id
        self.default_revenue_account_id = default_revenue_account_id
        self.config = config or SplynxConfig.from_settings()
        self._client: SplynxClient | None = None
        self._bank_name_mapping = bank_name_mapping or self.DEFAULT_BANK_NAME_MAPPING

        # Cache for customer ID mapping (splynx_id -> erp_customer_id)
        self._customer_cache: dict[int, UUID] = {}

        # Payment method -> ERP bank account mapping
        self._payment_method_cache: dict[int, SplynxPaymentMethod] = {}
        self._bank_account_mapping: dict[
            int, UUID
        ] = {}  # splynx_method_id -> erp_bank_account_id
        self._default_bank_account_cache: dict[str, UUID] = {}

    @property
    def client(self) -> SplynxClient:
        """Lazy-initialize Splynx client."""
        if self._client is None:
            self._client = SplynxClient(self.config)
        return self._client

    def close(self) -> None:
        """Close the client connection."""
        if self._client:
            self._client.close()
            self._client = None

    def _load_payment_methods(self) -> None:
        """Load payment methods from Splynx and build bank account mapping."""
        if self._payment_method_cache:
            return  # Already loaded

        methods = self.client.get_payment_methods()
        for m in methods:
            self._payment_method_cache[m.id] = m

        # Build bank account mapping by matching names
        self._build_bank_account_mapping()

    def _build_bank_account_mapping(self) -> None:
        """
        Build mapping from Splynx payment method names to ERP bank accounts.

        Matches by partial name using ``self._bank_name_mapping``.
        """
        from app.models.finance.banking.bank_account import BankAccount

        def _normalize_text(value: str | None) -> str:
            if not value:
                return ""
            # Keep alphanumeric only so we match variants like
            # "Pay stack", "Paystack", "Flutter-Wave", "Fluterwave".
            return re.sub(r"[^a-z0-9]+", "", value.lower())

        def _matches_account_pattern(pattern: str, account_search: str) -> bool:
            if pattern in account_search:
                return True
            # Handle compact patterns like "zenith461" by checking alpha+digit
            # fragments independently in the normalized account corpus.
            m = re.fullmatch(r"([a-z]+)(\d+)", pattern)
            if m:
                return m.group(1) in account_search and m.group(2) in account_search
            return False

        # Get ERP bank accounts via ORM (prefer primary first for stable mapping)
        stmt = (
            select(BankAccount)
            .where(BankAccount.organization_id == self.organization_id)
            .order_by(BankAccount.is_primary.desc(), BankAccount.created_at.asc())
        )
        accounts = self.db.scalars(stmt).all()

        account_candidates: list[tuple[UUID, str]] = []
        for acct in accounts:
            search_parts = [
                acct.bank_name,
                acct.account_name,
                f"{acct.bank_name} {acct.account_name}",
                acct.account_number,
            ]
            if acct.account_number and len(acct.account_number) >= 4:
                search_parts.append(acct.account_number[-4:])

            normalized_search = " ".join(
                part for part in (_normalize_text(p) for p in search_parts) if part
            )
            account_candidates.append((acct.bank_account_id, normalized_search))

        normalized_rules = {
            _normalize_text(splynx_pattern): (
                _normalize_text(erp_pattern) if erp_pattern else None
            )
            for splynx_pattern, erp_pattern in self._bank_name_mapping.items()
        }

        for method_id, method in self._payment_method_cache.items():
            normalized_method_name = _normalize_text(method.name)

            for splynx_pattern, erp_pattern in normalized_rules.items():
                if not splynx_pattern or splynx_pattern not in normalized_method_name:
                    continue
                if not erp_pattern:
                    # Explicitly unsupported/no-bank mapping (e.g. cash)
                    break

                for bank_account_id, account_search in account_candidates:
                    if _matches_account_pattern(erp_pattern, account_search):
                        self._bank_account_mapping[method_id] = bank_account_id
                        logger.debug(
                            "Mapped Splynx method '%s' via pattern '%s'",
                            method.name,
                            splynx_pattern,
                        )
                        break
                break

        logger.info(
            "Built bank account mapping: %d of %d payment methods mapped",
            len(self._bank_account_mapping),
            len(self._payment_method_cache),
        )

    def _get_default_bank_account(self, currency_code: str) -> UUID | None:
        """Get the organization's default active bank account for a currency."""
        from app.models.finance.banking.bank_account import BankAccount

        code = (currency_code or "NGN").upper()
        cached = self._default_bank_account_cache.get(code)
        if cached:
            return cached

        stmt = (
            select(BankAccount.bank_account_id)
            .where(
                BankAccount.organization_id == self.organization_id,
                BankAccount.currency_code == code,
                BankAccount.status == "active",
            )
            .order_by(BankAccount.is_primary.desc(), BankAccount.created_at.asc())
        )
        bank_account_id = self.db.scalar(stmt)
        if bank_account_id:
            self._default_bank_account_cache[code] = bank_account_id
        return bank_account_id

    def _get_bank_account_for_payment(
        self, payment_type: int, currency_code: str
    ) -> UUID | None:
        """Get ERP bank account ID for a Splynx payment type."""
        self._load_payment_methods()
        mapped = self._bank_account_mapping.get(payment_type)
        if mapped:
            return mapped
        return self._get_default_bank_account(currency_code)

    def _get_payment_method_name(self, payment_type: int) -> str:
        """Get payment method name for display."""
        self._load_payment_methods()
        method = self._payment_method_cache.get(payment_type)
        return method.name if method else f"Method {payment_type}"

    def _map_payment_method(self, payment_type: int) -> PaymentMethod:
        """Map Splynx payment type to ERP PaymentMethod enum."""
        self._load_payment_methods()
        method = self._payment_method_cache.get(payment_type)
        if not method:
            return PaymentMethod.BANK_TRANSFER

        name_lower = method.name.lower()
        if "cash" in name_lower:
            return PaymentMethod.CASH
        elif (
            "paystack" in name_lower
            or "flutterwave" in name_lower
            or "flutter wave" in name_lower
            or "fluterwave" in name_lower
        ):
            return PaymentMethod.CARD
        elif "remita" in name_lower:
            return PaymentMethod.DIRECT_DEBIT
        else:
            return PaymentMethod.BANK_TRANSFER

    def _make_customer_code(self, splynx_id: int) -> str:
        """Generate customer code from Splynx ID."""
        return f"{self.SOURCE_PREFIX}-{splynx_id}"

    def _generate_invoice_number(self, reference_date: date | None = None) -> str:
        """Generate sequential invoice number via numbering service."""
        from app.models.finance.core_config.numbering_sequence import SequenceType
        from app.services.finance.common.numbering import SyncNumberingService

        svc = SyncNumberingService(self.db)
        return svc.generate_next_number(
            self.organization_id, SequenceType.INVOICE, reference_date
        )

    def _generate_payment_number(self, reference_date: date | None = None) -> str:
        """Generate sequential payment number via numbering service."""
        from app.models.finance.core_config.numbering_sequence import SequenceType
        from app.services.finance.common.numbering import SyncNumberingService

        svc = SyncNumberingService(self.db)
        return svc.generate_next_number(
            self.organization_id, SequenceType.PAYMENT, reference_date
        )

    def _generate_credit_note_number(self, reference_date: date | None = None) -> str:
        """Generate sequential credit note number via numbering service."""
        from app.models.finance.core_config.numbering_sequence import SequenceType
        from app.services.finance.common.numbering import SyncNumberingService

        svc = SyncNumberingService(self.db)
        return svc.generate_next_number(
            self.organization_id, SequenceType.CREDIT_NOTE, reference_date
        )

    def _parse_date(self, date_str: str | None) -> date | None:
        """Parse date string from Splynx."""
        if not date_str:
            return None
        try:
            # Try ISO format first
            if "T" in date_str:
                return datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
            return datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            try:
                return datetime.strptime(date_str, "%d/%m/%Y").date()
            except ValueError:
                logger.warning("Could not parse date: %s", date_str)
                return None

    def _get_existing_customer(self, customer_code: str) -> Customer | None:
        """Get existing customer by code."""
        stmt = select(Customer).where(
            Customer.organization_id == self.organization_id,
            Customer.customer_code == customer_code,
        )
        return self.db.scalar(stmt)

    def _get_customer_by_splynx_id(self, splynx_id: int) -> Customer | None:
        """Get existing customer by splynx_id column (set by dedup migration)."""
        # Match exact ID or comma-separated list containing this ID
        sid = str(splynx_id)
        stmt = select(Customer).where(
            Customer.organization_id == self.organization_id,
            Customer.splynx_id.is_not(None),
            Customer.splynx_id == sid,
        )
        return self.db.scalar(stmt)

    def _find_existing_customer(
        self, splynx_customer: SplynxCustomer
    ) -> Customer | None:
        """Try to match a Splynx customer to an existing ERP customer."""
        name = (splynx_customer.name or splynx_customer.login or "").strip()
        email = (splynx_customer.email or "").strip().lower()
        phone = (splynx_customer.phone or "").strip().lower()

        if splynx_customer.id:
            try:
                stmt = select(Customer).where(
                    Customer.organization_id == self.organization_id,
                    Customer.primary_contact["splynx_id"].astext
                    == str(splynx_customer.id),
                )
                customer = self.db.scalar(stmt)
                if customer:
                    return customer
            except NotImplementedError:
                logger.debug(
                    "primary_contact JSON lookup not supported; skipping splynx_id match"
                )

        contact_filters = []
        if email:
            try:
                contact_filters.append(
                    func.lower(Customer.primary_contact["email"].astext) == email
                )
            except NotImplementedError:
                logger.debug(
                    "primary_contact JSON lookup not supported; skipping email match"
                )
        if phone:
            try:
                contact_filters.append(
                    func.lower(Customer.primary_contact["phone"].astext) == phone
                )
            except NotImplementedError:
                logger.debug(
                    "primary_contact JSON lookup not supported; skipping phone match"
                )
        if contact_filters:
            stmt = select(Customer).where(
                Customer.organization_id == self.organization_id,
                or_(*contact_filters),
            )
            customer = self.db.scalar(stmt)
            if customer:
                return customer

        if name:
            stmt = select(Customer).where(
                Customer.organization_id == self.organization_id,
                or_(
                    func.lower(Customer.legal_name) == name.lower(),
                    func.lower(Customer.trading_name) == name.lower(),
                ),
            )
            matches = self.db.scalars(stmt).all()
            if len(matches) == 1:
                return matches[0]

        return None

    def _get_existing_invoice(self, invoice_number: str) -> Invoice | None:
        """Get existing invoice by number."""
        stmt = select(Invoice).where(
            Invoice.organization_id == self.organization_id,
            Invoice.invoice_number == invoice_number,
        )
        return self.db.scalar(stmt)

    def _load_customer_cache(self) -> None:
        """Load customer ID mapping cache."""
        # Primary: load from splynx_id column (set by dedup migration)
        stmt = select(Customer).where(
            Customer.organization_id == self.organization_id,
            Customer.splynx_id.is_not(None),
        )
        customers = self.db.scalars(stmt).all()
        for customer in customers:
            # splynx_id may be comma-separated after dedup merge
            for sid in (customer.splynx_id or "").split(","):
                sid = sid.strip()
                if sid:
                    try:
                        self._customer_cache[int(sid)] = customer.customer_id
                    except ValueError:
                        pass

        # Fallback: also load from old SPLYNX-{id} customer codes
        stmt2 = select(Customer).where(
            Customer.organization_id == self.organization_id,
            Customer.customer_code.like(f"{self.SOURCE_PREFIX}-%"),
            Customer.splynx_id.is_(None),  # Only if not already indexed above
        )
        customers2 = self.db.scalars(stmt2).all()
        for customer in customers2:
            try:
                splynx_id = int(
                    customer.customer_code.replace(f"{self.SOURCE_PREFIX}-", "")
                )
                if splynx_id not in self._customer_cache:
                    self._customer_cache[splynx_id] = customer.customer_id
            except ValueError:
                pass

    def _get_or_create_customer_id(self, splynx_customer_id: int) -> UUID | None:
        """Get ERP customer ID for Splynx customer, or None if not synced."""
        if splynx_customer_id in self._customer_cache:
            return self._customer_cache[splynx_customer_id]

        # Primary: look up by splynx_id column (set by dedup migration)
        customer = self._get_customer_by_splynx_id(splynx_customer_id)
        if customer:
            self._customer_cache[splynx_customer_id] = customer.customer_id
            return customer.customer_id

        # Fallback: try sync tracking table
        local_id = self._get_synced_entity(EntityType.CUSTOMER, str(splynx_customer_id))
        if local_id:
            self._customer_cache[splynx_customer_id] = local_id
            return local_id

        # Fallback: Try to find by customer code
        customer_code = self._make_customer_code(splynx_customer_id)
        customer = self._get_existing_customer(customer_code)
        if customer:
            self._customer_cache[splynx_customer_id] = customer.customer_id
            return customer.customer_id

        # Fallback: fetch customer from Splynx and match by contact info/name
        try:
            splynx_customer = self.client.get_customer(splynx_customer_id)
            existing = self._find_existing_customer(splynx_customer)
            if existing:
                self._customer_cache[splynx_customer_id] = existing.customer_id
                # Record sync mapping for future lookups
                data_hash = self._compute_hash(
                    {
                        "name": splynx_customer.name,
                        "login": splynx_customer.login,
                        "email": splynx_customer.email,
                        "phone": splynx_customer.phone,
                        "status": splynx_customer.status,
                        "company": splynx_customer.company,
                        "street_1": splynx_customer.street_1,
                        "street_2": splynx_customer.street_2,
                        "city": splynx_customer.city,
                        "zip_code": splynx_customer.zip_code,
                    }
                )
                self._record_sync(
                    EntityType.CUSTOMER,
                    str(splynx_customer_id),
                    existing.customer_id,
                    data_hash,
                )
                return existing.customer_id
        except SplynxError:
            # Ignore lookup failures and fall through to None
            pass

        return None

    # =========================================================================
    # Sync Tracking Methods
    # =========================================================================

    def _get_synced_entity(
        self,
        entity_type: EntityType,
        external_id: str,
    ) -> UUID | None:
        """Get local entity ID for a synced external entity."""
        stmt = select(ExternalSync.local_entity_id).where(
            ExternalSync.organization_id == self.organization_id,
            ExternalSync.source == ExternalSource.SPLYNX,
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
        """Record a sync mapping."""
        # Check if already exists
        existing = self._get_synced_entity(entity_type, external_id)
        if existing:
            # Update existing record
            stmt = select(ExternalSync).where(
                ExternalSync.organization_id == self.organization_id,
                ExternalSync.source == ExternalSource.SPLYNX,
                ExternalSync.entity_type == entity_type,
                ExternalSync.external_id == external_id,
            )
            sync_record = self.db.scalar(stmt)
            if sync_record:
                sync_record.synced_at = datetime.now(tz=UTC)
                sync_record.sync_hash = data_hash
                if external_updated_at:
                    sync_record.external_updated_at = external_updated_at
        else:
            # Create new record
            sync_record = ExternalSync(
                organization_id=self.organization_id,
                source=ExternalSource.SPLYNX,
                entity_type=entity_type,
                external_id=external_id,
                local_entity_id=local_entity_id,
                sync_hash=data_hash,
                external_updated_at=external_updated_at,
            )
            self.db.add(sync_record)

    def _compute_hash(self, data: dict) -> str:
        """Compute hash of data for change detection."""
        json_str = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(json_str.encode()).hexdigest()[:32]

    def _has_changed(
        self,
        entity_type: EntityType,
        external_id: str,
        new_hash: str,
    ) -> bool:
        """Check if entity has changed since last sync."""
        stmt = select(ExternalSync.sync_hash).where(
            ExternalSync.organization_id == self.organization_id,
            ExternalSync.source == ExternalSource.SPLYNX,
            ExternalSync.entity_type == entity_type,
            ExternalSync.external_id == external_id,
        )
        old_hash = self.db.scalar(stmt)
        return old_hash != new_hash

    # =========================================================================
    # Customer Sync
    # =========================================================================

    def sync_customers(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        created_by_user_id: UUID | None = None,
        batch_size: int | None = None,
        skip_unchanged: bool = True,
    ) -> SyncResult:
        """
        Sync customers from Splynx.

        Args:
            date_from: Only sync customers created after this date
            date_to: Only sync customers created before this date
            created_by_user_id: User ID to record as creator
            batch_size: Max number of records to sync (None = all)
            skip_unchanged: Skip records that haven't changed (default True)
        """
        result = SyncResult(success=True, entity_type="customers")
        processed = 0

        try:
            for splynx_customer in self.client.get_customers(
                date_from=date_from,
                date_to=date_to,
            ):
                # Check batch limit
                if batch_size and processed >= batch_size:
                    result.message = f"Batch limit ({batch_size}) reached"
                    break

                try:
                    savepoint = self.db.begin_nested()
                    self._sync_single_customer(
                        splynx_customer, created_by_user_id, result, skip_unchanged
                    )
                    savepoint.commit()
                    processed += 1

                    # Commit + expunge periodically to prevent OOM
                    if processed % 500 == 0:
                        self.db.commit()
                        self.db.expunge_all()
                        logger.info("Progress: %d customers processed", processed)

                except Exception as e:
                    try:
                        savepoint.rollback()
                    except Exception:
                        self.db.rollback()
                    result.errors.append(f"Customer {splynx_customer.id}: {str(e)}")
                    logger.exception("Error syncing customer %s", splynx_customer.id)

            self.db.flush()
            result.message = (
                f"Synced {result.created} new, {result.updated} updated, "
                f"{result.skipped} skipped customers"
            )
            logger.info(result.message)

        except SplynxError as e:
            result.success = False
            result.message = f"Splynx API error: {e.message}"
            result.errors.append(result.message)
            logger.error(result.message)

        return result

    def _sync_single_customer(
        self,
        splynx_customer: SplynxCustomer,
        created_by_user_id: UUID | None,
        result: SyncResult,
        skip_unchanged: bool = True,
    ) -> None:
        """Sync a single customer."""
        external_id = str(splynx_customer.id)

        # Compute hash for change detection
        data_hash = self._compute_hash(
            {
                "name": splynx_customer.name,
                "login": splynx_customer.login,
                "email": splynx_customer.email,
                "phone": splynx_customer.phone,
                "status": splynx_customer.status,
                "company": splynx_customer.company,
                "street_1": splynx_customer.street_1,
                "street_2": splynx_customer.street_2,
                "city": splynx_customer.city,
                "zip_code": splynx_customer.zip_code,
            }
        )

        # Check if already synced and unchanged
        if skip_unchanged and not self._has_changed(
            EntityType.CUSTOMER, external_id, data_hash
        ):
            result.skipped += 1
            return

        # Check for existing record: splynx_id column → sync tracking → code → name
        existing = self._get_customer_by_splynx_id(splynx_customer.id)
        if not existing:
            local_id = self._get_synced_entity(EntityType.CUSTOMER, external_id)
            if local_id:
                existing = self.db.get(Customer, local_id)
        if not existing:
            customer_code = self._make_customer_code(splynx_customer.id)
            existing = self._get_existing_customer(customer_code)
        if not existing:
            existing = self._find_existing_customer(splynx_customer)

        customer_code = self._make_customer_code(splynx_customer.id)

        if existing:
            # Update existing customer
            existing.legal_name = splynx_customer.name or splynx_customer.login
            existing.trading_name = splynx_customer.company
            existing.is_active = splynx_customer.status == "active"
            existing.primary_contact = {
                "email": splynx_customer.email,
                "phone": splynx_customer.phone,
                "splynx_id": splynx_customer.id,  # Track Splynx ID
            }
            existing.billing_address = {
                "street_1": splynx_customer.street_1,
                "street_2": splynx_customer.street_2,
                "city": splynx_customer.city,
                "zip_code": splynx_customer.zip_code,
            }
            # Backfill splynx_id if not yet set
            if not existing.splynx_id:
                existing.splynx_id = str(splynx_customer.id)
            result.updated += 1
            self._customer_cache[splynx_customer.id] = existing.customer_id
            # Record sync
            self._record_sync(
                EntityType.CUSTOMER, external_id, existing.customer_id, data_hash
            )
        else:
            # Create new customer
            customer = Customer(
                organization_id=self.organization_id,
                customer_code=customer_code,
                customer_type=CustomerType.INDIVIDUAL
                if not splynx_customer.company
                else CustomerType.COMPANY,
                legal_name=splynx_customer.name or splynx_customer.login,
                trading_name=splynx_customer.company,
                is_active=splynx_customer.status == "active",
                ar_control_account_id=self.ar_control_account_id,
                default_revenue_account_id=self.default_revenue_account_id,
                primary_contact={
                    "email": splynx_customer.email,
                    "phone": splynx_customer.phone,
                    "splynx_id": splynx_customer.id,  # Track Splynx ID
                },
                billing_address={
                    "street_1": splynx_customer.street_1,
                    "street_2": splynx_customer.street_2,
                    "city": splynx_customer.city,
                    "zip_code": splynx_customer.zip_code,
                },
                splynx_id=str(splynx_customer.id),
                created_by_user_id=created_by_user_id,
            )
            self.db.add(customer)
            self.db.flush()  # Get generated ID
            result.created += 1
            self._customer_cache[splynx_customer.id] = customer.customer_id
            # Record sync
            self._record_sync(
                EntityType.CUSTOMER, external_id, customer.customer_id, data_hash
            )

    # =========================================================================
    # Invoice Sync
    # =========================================================================

    def sync_invoices(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        status: str | None = None,
        created_by_user_id: UUID | None = None,
        batch_size: int | None = None,
        skip_unchanged: bool = True,
    ) -> SyncResult:
        """
        Sync invoices from Splynx.

        Args:
            date_from: Only sync invoices created after this date
            date_to: Only sync invoices created before this date
            status: Filter by Splynx status (paid, unpaid, partially_paid)
            created_by_user_id: User ID to record as creator
            batch_size: Max number of records to sync (None = all)
            skip_unchanged: Skip records that haven't changed (default True)
        """
        result = SyncResult(success=True, entity_type="invoices")
        processed = 0

        # Ensure customer cache is loaded
        if not self._customer_cache:
            self._load_customer_cache()

        try:
            for splynx_invoice in self.client.get_invoices(
                date_from=date_from,
                date_to=date_to,
                status=status,
            ):
                # Check batch limit
                if batch_size and processed >= batch_size:
                    result.message = f"Batch limit ({batch_size}) reached"
                    break

                try:
                    savepoint = self.db.begin_nested()
                    self._sync_single_invoice(
                        splynx_invoice, created_by_user_id, result, skip_unchanged
                    )
                    savepoint.commit()
                    processed += 1

                    # Commit + expunge periodically to prevent OOM
                    if processed % 500 == 0:
                        self.db.commit()
                        self.db.expunge_all()
                        logger.info("Progress: %d invoices processed", processed)

                except Exception as e:
                    try:
                        savepoint.rollback()
                    except Exception:
                        self.db.rollback()
                    result.errors.append(f"Invoice {splynx_invoice.number}: {str(e)}")
                    logger.exception("Error syncing invoice %s", splynx_invoice.number)

            self.db.flush()
            result.message = (
                f"Synced {result.created} new, {result.updated} updated, "
                f"{result.skipped} skipped invoices"
            )
            logger.info(result.message)

        except SplynxError as e:
            result.success = False
            result.message = f"Splynx API error: {e.message}"
            result.errors.append(result.message)
            logger.error(result.message)

        return result

    def _sync_single_invoice(
        self,
        splynx_invoice: SplynxInvoice,
        created_by_user_id: UUID | None,
        result: SyncResult,
        skip_unchanged: bool = True,
    ) -> None:
        """Sync a single invoice."""
        external_id = str(splynx_invoice.id)

        # Compute hash for change detection
        data_hash = self._compute_hash(
            {
                "number": splynx_invoice.number,
                "total": str(splynx_invoice.total),
                "total_due": str(splynx_invoice.total_due),
                "status": splynx_invoice.status,
                "date_created": splynx_invoice.date_created,
            }
        )

        # Check if already synced and unchanged
        if skip_unchanged and not self._has_changed(
            EntityType.INVOICE, external_id, data_hash
        ):
            result.skipped += 1
            return

        # Check for existing via sync tracking, splynx_id, or legacy invoice number
        local_id = self._get_synced_entity(EntityType.INVOICE, external_id)
        existing = None
        if local_id:
            existing = self.db.get(Invoice, local_id)
        if not existing:
            # Try by splynx_id column
            stmt = select(Invoice).where(
                Invoice.organization_id == self.organization_id,
                Invoice.splynx_id == str(splynx_invoice.id),
            )
            existing = self.db.scalar(stmt)
        if not existing:
            # Legacy fallback: old SPL-INV-{id} format
            existing = self._get_existing_invoice(f"SPL-INV-{splynx_invoice.id}")

        # Get customer ID
        customer_id = self._get_or_create_customer_id(splynx_invoice.customer_id)
        if not customer_id:
            result.skipped += 1
            result.errors.append(
                f"Invoice {splynx_invoice.number}: Customer {splynx_invoice.customer_id} not synced"
            )
            return

        # Parse dates
        invoice_date = self._parse_date(splynx_invoice.date_created) or date.today()
        due_date = self._parse_date(splynx_invoice.date_till) or invoice_date

        # Calculate amount paid (total - due)
        amount_paid = splynx_invoice.total - splynx_invoice.total_due

        # Map status
        status = self._map_invoice_status(
            splynx_invoice.status, splynx_invoice.total_due
        )

        currency_code = splynx_invoice.currency or "NGN"

        if existing:
            # Update existing invoice — apply ALL mutable fields
            existing.customer_id = customer_id
            existing.invoice_date = invoice_date
            existing.due_date = due_date
            existing.currency_code = currency_code
            existing.subtotal = splynx_invoice.total
            existing.total_amount = splynx_invoice.total
            existing.functional_currency_amount = splynx_invoice.total
            existing.amount_paid = amount_paid
            existing.status = status
            existing.notes = splynx_invoice.note
            existing.splynx_id = str(splynx_invoice.id)
            existing.splynx_number = splynx_invoice.number
            existing.last_synced_at = datetime.now(UTC)

            # Replace invoice lines with parsed items
            self._replace_invoice_lines(
                existing.invoice_id, splynx_invoice, is_credit_note=False
            )

            result.updated += 1
            # Record sync
            self._record_sync(
                EntityType.INVOICE, external_id, existing.invoice_id, data_hash
            )
            # GL posting deferred to backfill script
        else:
            # Generate own sequential number
            invoice_number = self._generate_invoice_number(invoice_date)

            # Create new invoice
            invoice = Invoice(
                organization_id=self.organization_id,
                customer_id=customer_id,
                invoice_number=invoice_number,
                invoice_type=InvoiceType.STANDARD,
                invoice_date=invoice_date,
                due_date=due_date,
                currency_code=currency_code,
                subtotal=splynx_invoice.total,
                tax_amount=Decimal("0"),
                total_amount=splynx_invoice.total,
                amount_paid=amount_paid,
                functional_currency_amount=splynx_invoice.total,
                status=status,
                ar_control_account_id=self.ar_control_account_id,
                source_document_type="splynx_invoice",
                correlation_id=f"splynx-inv-{splynx_invoice.id}",
                notes=splynx_invoice.note,
                internal_notes=f"Imported from Splynx. Original ID: {splynx_invoice.id}",
                created_by_user_id=created_by_user_id or SYSTEM_USER_ID,
                splynx_id=str(splynx_invoice.id),
                splynx_number=splynx_invoice.number,
                last_synced_at=datetime.now(UTC),
            )
            self.db.add(invoice)
            self.db.flush()

            # Add invoice lines from Splynx items (or single fallback line)
            self._create_invoice_lines(
                invoice.invoice_id, splynx_invoice, is_credit_note=False
            )

            result.created += 1
            # Record sync
            self._record_sync(
                EntityType.INVOICE, external_id, invoice.invoice_id, data_hash
            )
            # GL posting deferred to backfill script

    def _create_invoice_lines(
        self,
        invoice_id: UUID,
        splynx_doc: SplynxInvoice | SplynxCreditNote,
        *,
        is_credit_note: bool = False,
    ) -> None:
        """Create InvoiceLine records from Splynx items array.

        Falls back to a single summary line if items array is empty or missing.
        """
        if not self.default_revenue_account_id:
            return

        items = getattr(splynx_doc, "items", []) or []
        label = "Credit Note" if is_credit_note else "Invoice"

        if items:
            for seq, item in enumerate(items, 1):
                qty = Decimal(str(item.get("quantity", 1)))
                price = Decimal(str(item.get("unit_price", item.get("price", 0))))
                total = Decimal(str(item.get("total", 0)))
                if total == Decimal("0") and qty and price:
                    total = qty * price
                desc = item.get("description") or item.get("service_name") or ""
                if not desc:
                    desc = f"Splynx {label} {splynx_doc.number} - line {seq}"

                line = InvoiceLine(
                    invoice_id=invoice_id,
                    line_number=seq,
                    description=desc,
                    quantity=qty,
                    unit_price=price,
                    discount_percentage=Decimal("0"),
                    discount_amount=Decimal("0"),
                    line_amount=total,
                    tax_amount=Decimal("0"),
                    revenue_account_id=self.default_revenue_account_id,
                )
                self.db.add(line)
        else:
            # Fallback: single summary line
            line = InvoiceLine(
                invoice_id=invoice_id,
                line_number=1,
                description=f"Splynx {label} {splynx_doc.number}",
                quantity=Decimal("1"),
                unit_price=splynx_doc.total,
                discount_percentage=Decimal("0"),
                discount_amount=Decimal("0"),
                line_amount=splynx_doc.total,
                tax_amount=Decimal("0"),
                revenue_account_id=self.default_revenue_account_id,
            )
            self.db.add(line)

    def _replace_invoice_lines(
        self,
        invoice_id: UUID,
        splynx_doc: SplynxInvoice | SplynxCreditNote,
        *,
        is_credit_note: bool = False,
    ) -> None:
        """Delete existing lines and recreate from current Splynx data."""
        # Delete old lines
        from sqlalchemy import delete

        stmt = delete(InvoiceLine).where(InvoiceLine.invoice_id == invoice_id)
        self.db.execute(stmt)
        # Create fresh lines
        self._create_invoice_lines(
            invoice_id, splynx_doc, is_credit_note=is_credit_note
        )

    def _ensure_invoice_gl_posted(
        self,
        invoice: Invoice,
        created_by_user_id: UUID | None = None,
    ) -> None:
        """Post invoice to GL if it has a postable status but no journal entry."""
        from app.services.finance.ar.invoice import ARInvoiceService

        ARInvoiceService.ensure_gl_posted(
            self.db, invoice, posted_by_user_id=created_by_user_id
        )

    def _map_invoice_status(
        self, splynx_status: str, total_due: Decimal
    ) -> InvoiceStatus:
        """Map Splynx invoice status to ERP status."""
        status_lower = splynx_status.lower()
        if status_lower == "paid" or total_due == Decimal("0"):
            return InvoiceStatus.PAID
        elif status_lower == "partially_paid":
            return InvoiceStatus.PARTIALLY_PAID
        elif status_lower == "unpaid":
            return InvoiceStatus.POSTED
        else:
            return InvoiceStatus.POSTED

    # =========================================================================
    # Payment Sync
    # =========================================================================

    def sync_payments(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        created_by_user_id: UUID | None = None,
        batch_size: int | None = None,
        skip_unchanged: bool = True,
    ) -> SyncResult:
        """
        Sync payments from Splynx.

        Creates CustomerPayment records with bank account tracking and
        allocates payments to their associated invoices.

        Args:
            date_from: Only sync payments after this date
            date_to: Only sync payments before this date
            created_by_user_id: User ID to record as creator
            batch_size: Max number of records to sync (None = all)
            skip_unchanged: Skip records that haven't changed (default True)
        """
        result = SyncResult(success=True, entity_type="payments")
        processed = 0

        # Preload payment methods to build bank account mapping
        self._load_payment_methods()

        # Ensure customer cache is loaded (needed for currency resolution)
        if not self._customer_cache:
            self._load_customer_cache()

        try:
            for splynx_payment in self.client.get_payments(
                date_from=date_from,
                date_to=date_to,
            ):
                if batch_size and processed >= batch_size:
                    result.message = f"Batch limit ({batch_size}) reached"
                    break

                try:
                    savepoint = self.db.begin_nested()
                    self._sync_single_payment(
                        splynx_payment, result, created_by_user_id, skip_unchanged
                    )
                    savepoint.commit()
                    processed += 1

                    # Commit + expunge periodically to prevent OOM
                    if processed % 500 == 0:
                        self.db.commit()
                        self.db.expunge_all()
                        logger.info("Progress: %d payments processed", processed)

                except Exception as e:
                    try:
                        savepoint.rollback()
                    except Exception:
                        self.db.rollback()
                    result.errors.append(f"Payment {splynx_payment.id}: {str(e)}")
                    logger.exception("Error syncing payment %s", splynx_payment.id)

            self.db.flush()
            result.message = (
                f"Synced {result.created} new, {result.updated} updated, "
                f"{result.skipped} skipped payments"
            )
            logger.info(result.message)

        except SplynxError as e:
            result.success = False
            result.message = f"Splynx API error: {e.message}"
            result.errors.append(result.message)
            logger.error(result.message)

        return result

    def _sync_single_payment(
        self,
        splynx_payment: SplynxPayment,
        result: SyncResult,
        created_by_user_id: UUID | None = None,
        skip_unchanged: bool = True,
    ) -> None:
        """
        Sync a single payment.

        Creates CustomerPayment record with bank account mapping and
        allocates payment to the associated invoice.  Payments without
        an invoice_id are created as unapplied (prepayment / advance);
        future syncs will allocate them once Splynx assigns the invoice.
        """
        external_id = str(splynx_payment.id)

        # Compute hash for change detection
        data_hash = self._compute_hash(
            {
                "invoice_id": splynx_payment.invoice_id,
                "amount": str(splynx_payment.amount),
                "date": splynx_payment.date,
                "payment_type": splynx_payment.payment_type,
                "reference": splynx_payment.reference,
            }
        )

        # Check if already synced and unchanged
        if skip_unchanged and not self._has_changed(
            EntityType.PAYMENT, external_id, data_hash
        ):
            result.skipped += 1
            return

        # Check if already synced (update path)
        local_id = self._get_synced_entity(EntityType.PAYMENT, external_id)

        # Resolve invoice (optional — payments may be unapplied prepayments)
        invoice: Invoice | None = None
        if splynx_payment.invoice_id:
            correlation_id = f"splynx-inv-{splynx_payment.invoice_id}"
            stmt = select(Invoice).where(
                Invoice.organization_id == self.organization_id,
                Invoice.correlation_id == correlation_id,
            )
            invoice = self.db.scalar(stmt)

            if not invoice:
                result.skipped += 1
                result.errors.append(
                    f"Payment {splynx_payment.id}: Invoice {splynx_payment.invoice_id} not synced"
                )
                return

        # Resolve customer — from invoice if available, otherwise from Splynx customer_id
        customer_id: UUID
        if invoice:
            customer_id = invoice.customer_id
            currency_code = invoice.currency_code or "NGN"
        else:
            resolved_cust = self._get_or_create_customer_id(splynx_payment.customer_id)
            if not resolved_cust:
                result.skipped += 1
                result.errors.append(
                    f"Payment {splynx_payment.id}: Customer {splynx_payment.customer_id} not synced"
                )
                return
            customer_id = resolved_cust
            # Use customer's currency or org default
            customer = self.db.get(Customer, customer_id)
            currency_code = (customer.currency_code if customer else None) or "NGN"

        # Get bank account for this payment method, with fallback by currency
        bank_account_id = self._get_bank_account_for_payment(
            splynx_payment.payment_type, currency_code
        )
        payment_method = self._map_payment_method(splynx_payment.payment_type)
        method_name = self._get_payment_method_name(splynx_payment.payment_type)

        # Parse payment date
        payment_date = self._parse_date(splynx_payment.date) or date.today()

        if local_id:
            payment = self.db.get(CustomerPayment, local_id)
            if not payment:
                # Stale sync record; fall through to create path
                local_id = None
            else:
                # Update existing payment and allocation
                alloc_stmt = select(PaymentAllocation).where(
                    PaymentAllocation.payment_id == payment.payment_id
                )
                allocation = self.db.scalar(alloc_stmt)
                old_allocated_amount = (
                    allocation.allocated_amount if allocation else Decimal("0")
                )
                old_invoice_id = allocation.invoice_id if allocation else None

                # Adjust old invoice if allocation exists
                if allocation:
                    old_invoice = self.db.get(Invoice, allocation.invoice_id)
                else:
                    old_invoice = None

                # Update payment fields
                payment.customer_id = customer_id
                payment.payment_date = payment_date
                payment.payment_method = payment_method
                payment.currency_code = currency_code
                payment.gross_amount = splynx_payment.amount
                payment.amount = splynx_payment.amount
                payment.functional_currency_amount = splynx_payment.amount
                payment.bank_account_id = bank_account_id
                payment.reference = (
                    splynx_payment.reference or splynx_payment.receipt_number
                )
                payment.description = (
                    f"Splynx payment via {method_name}. {splynx_payment.comment or ''}"
                ).strip()
                payment.splynx_id = str(splynx_payment.id)
                payment.splynx_receipt_number = splynx_payment.receipt_number
                payment.last_synced_at = datetime.now(UTC)

                if invoice:
                    # Payment has an invoice — create or update allocation
                    if allocation:
                        if allocation.invoice_id != invoice.invoice_id and old_invoice:
                            # Remove from old invoice
                            old_invoice.amount_paid = max(
                                Decimal("0"),
                                old_invoice.amount_paid - allocation.allocated_amount,
                            )
                            if old_invoice.amount_paid >= old_invoice.total_amount:
                                old_invoice.status = InvoiceStatus.PAID
                            elif old_invoice.amount_paid > Decimal("0"):
                                old_invoice.status = InvoiceStatus.PARTIALLY_PAID
                            else:
                                old_invoice.status = InvoiceStatus.POSTED

                        # Update allocation to new invoice/amount/date
                        allocation.invoice_id = invoice.invoice_id
                        allocation.allocated_amount = splynx_payment.amount
                        allocation.allocation_date = payment_date
                    else:
                        # Previously unapplied → now has invoice: create allocation
                        allocation = PaymentAllocation(
                            payment_id=payment.payment_id,
                            invoice_id=invoice.invoice_id,
                            allocated_amount=splynx_payment.amount,
                            allocation_date=payment_date,
                        )
                        self.db.add(allocation)

                    # Update invoice amount_paid
                    if old_invoice_id == invoice.invoice_id:
                        # If allocation existed on same invoice, compute delta
                        delta = splynx_payment.amount - old_allocated_amount
                        invoice.amount_paid = min(
                            max(Decimal("0"), invoice.amount_paid + delta),
                            invoice.total_amount,
                        )
                    else:
                        invoice.amount_paid = min(
                            invoice.amount_paid + splynx_payment.amount,
                            invoice.total_amount,
                        )

                    if invoice.amount_paid >= invoice.total_amount:
                        invoice.status = InvoiceStatus.PAID
                    elif invoice.amount_paid > Decimal("0"):
                        invoice.status = InvoiceStatus.PARTIALLY_PAID
                    else:
                        invoice.status = InvoiceStatus.POSTED
                # else: no invoice — payment stays unapplied (no allocation change)

                # Record sync tracking
                self._record_sync(
                    EntityType.PAYMENT, external_id, payment.payment_id, data_hash
                )
                # GL posting deferred to backfill script
                result.updated += 1
                return

        # Generate sequential payment number
        payment_number = self._generate_payment_number(payment_date)

        # Create CustomerPayment record
        payment = CustomerPayment(
            organization_id=self.organization_id,
            customer_id=customer_id,
            payment_number=payment_number,
            payment_date=payment_date,
            payment_method=payment_method,
            currency_code=currency_code,
            gross_amount=splynx_payment.amount,
            amount=splynx_payment.amount,
            wht_amount=Decimal("0"),
            functional_currency_amount=splynx_payment.amount,
            bank_account_id=bank_account_id,
            reference=splynx_payment.reference or splynx_payment.receipt_number,
            description=f"Splynx payment via {method_name}. {splynx_payment.comment or ''}".strip(),
            status=PaymentStatus.CLEARED,
            correlation_id=f"splynx-pmt-{splynx_payment.id}",
            created_by_user_id=created_by_user_id or SYSTEM_USER_ID,
            splynx_id=str(splynx_payment.id),
            splynx_receipt_number=splynx_payment.receipt_number,
            last_synced_at=datetime.now(UTC),
        )
        self.db.add(payment)
        self.db.flush()  # Get payment_id

        # Allocate to invoice if available; otherwise unapplied prepayment
        if invoice:
            allocation = PaymentAllocation(
                payment_id=payment.payment_id,
                invoice_id=invoice.invoice_id,
                allocated_amount=splynx_payment.amount,
                allocation_date=payment_date,
            )
            self.db.add(allocation)

            # Update invoice amount_paid
            invoice.amount_paid = min(
                invoice.amount_paid + splynx_payment.amount,
                invoice.total_amount,
            )

            if invoice.amount_paid >= invoice.total_amount:
                invoice.status = InvoiceStatus.PAID
            elif invoice.amount_paid > Decimal("0"):
                invoice.status = InvoiceStatus.PARTIALLY_PAID
        else:
            logger.info(
                "Payment %s created as unapplied (no invoice_id from Splynx)",
                splynx_payment.id,
            )

        # Record sync tracking
        self._record_sync(
            EntityType.PAYMENT, external_id, payment.payment_id, data_hash
        )

        result.created += 1
        # GL posting deferred to backfill script

    def _ensure_payment_gl_posted(
        self,
        payment: CustomerPayment,
        created_by_user_id: UUID | None = None,
    ) -> None:
        """Post payment to GL if it has CLEARED status but no journal entry."""
        from app.services.finance.ar.customer_payment import CustomerPaymentService

        CustomerPaymentService.ensure_gl_posted(
            self.db, payment, posted_by_user_id=created_by_user_id
        )

    def auto_allocate_unapplied_payments(self) -> dict[str, Any]:
        """Auto-allocate unapplied Splynx payments to unique open invoices.

        Strict Tier-A policy:
        - Same customer
        - Exact amount match against invoice balance due (2dp)
        - Exactly one invoice candidate
        """
        open_statuses = {
            InvoiceStatus.POSTED,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.OVERDUE,
        }

        unapplied_payments = list(
            self.db.scalars(
                select(CustomerPayment).where(
                    CustomerPayment.organization_id == self.organization_id,
                    CustomerPayment.splynx_id.isnot(None),
                    CustomerPayment.status == PaymentStatus.CLEARED,
                    ~select(PaymentAllocation.allocation_id)
                    .where(PaymentAllocation.payment_id == CustomerPayment.payment_id)
                    .exists(),
                )
            ).all()
        )
        if not unapplied_payments:
            return {
                "allocated": 0,
                "ambiguous": 0,
                "no_candidate": 0,
                "errors": [],
            }

        open_invoices = list(
            self.db.scalars(
                select(Invoice).where(
                    Invoice.organization_id == self.organization_id,
                    Invoice.invoice_type == InvoiceType.STANDARD,
                    Invoice.status.in_(open_statuses),
                )
            ).all()
        )

        def _to_cents(value: Decimal) -> int:
            return int(value.quantize(Decimal("0.01")) * 100)

        invoice_index: dict[tuple[UUID, int], list[Invoice]] = {}
        for inv in open_invoices:
            balance_due = getattr(
                inv, "balance_due", inv.total_amount - inv.amount_paid
            )
            if balance_due <= Decimal("0"):
                continue
            key = (inv.customer_id, _to_cents(balance_due))
            invoice_index.setdefault(key, []).append(inv)

        allocated = 0
        ambiguous = 0
        no_candidate = 0
        errors: list[str] = []
        used_invoice_ids: set[UUID] = set()

        sorted_payments = sorted(
            unapplied_payments,
            key=lambda p: (p.customer_id, p.payment_date, str(p.payment_id)),
        )
        for payment in sorted_payments:
            try:
                key = (payment.customer_id, _to_cents(payment.amount))
                candidates = [
                    inv
                    for inv in invoice_index.get(key, [])
                    if inv.invoice_id not in used_invoice_ids
                ]
                if len(candidates) != 1:
                    if len(candidates) > 1:
                        ambiguous += 1
                    else:
                        no_candidate += 1
                    continue

                invoice = candidates[0]
                allocation = PaymentAllocation(
                    payment_id=payment.payment_id,
                    invoice_id=invoice.invoice_id,
                    allocated_amount=payment.amount,
                    allocation_date=payment.payment_date,
                )
                self.db.add(allocation)

                invoice.amount_paid = min(
                    invoice.total_amount,
                    invoice.amount_paid + payment.amount,
                )
                if invoice.amount_paid >= invoice.total_amount:
                    invoice.status = InvoiceStatus.PAID
                elif invoice.amount_paid > Decimal("0"):
                    invoice.status = InvoiceStatus.PARTIALLY_PAID
                else:
                    invoice.status = InvoiceStatus.POSTED

                used_invoice_ids.add(invoice.invoice_id)
                allocated += 1
            except Exception as exc:
                logger.exception(
                    "Auto-allocation error for payment %s: %s",
                    payment.payment_id,
                    exc,
                )
                errors.append(f"Payment {payment.payment_id}: {exc}")

        self.db.flush()
        logger.info(
            "Auto-allocation complete: %d allocated, %d ambiguous, %d no-candidate",
            allocated,
            ambiguous,
            no_candidate,
        )
        return {
            "allocated": allocated,
            "ambiguous": ambiguous,
            "no_candidate": no_candidate,
            "errors": errors,
        }

    def _set_invoice_status_from_amount_paid(self, invoice: Invoice) -> None:
        """Set invoice status from amount_paid using AR sync rules."""
        if invoice.amount_paid >= invoice.total_amount:
            invoice.status = InvoiceStatus.PAID
        elif invoice.amount_paid > Decimal("0"):
            invoice.status = InvoiceStatus.PARTIALLY_PAID
        else:
            invoice.status = InvoiceStatus.POSTED

    def _recompute_invoice_paid_from_allocations(self, invoice: Invoice) -> Decimal:
        """Recompute invoice.amount_paid from all payment allocations."""
        total_allocated = self.db.scalar(
            select(
                func.coalesce(func.sum(PaymentAllocation.allocated_amount), 0)
            ).where(PaymentAllocation.invoice_id == invoice.invoice_id)
        )
        allocated = Decimal(str(total_allocated or 0))
        invoice.amount_paid = min(invoice.total_amount, allocated)
        self._set_invoice_status_from_amount_paid(invoice)
        return allocated

    def repair_payment_invoice_relationships(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        batch_size: int | None = None,
    ) -> dict[str, Any]:
        """Repair payment->invoice allocations using Splynx payment.invoice_id links.

        This is idempotent and safe to run repeatedly:
        - maps local payments by splynx payment id
        - finds target invoice by splynx invoice id (fallback to correlation_id)
        - fixes or creates payment allocations
        - recomputes invoice amount_paid/status from allocations
        """
        summary: dict[str, Any] = {
            "processed": 0,
            "already_correct": 0,
            "fixed": 0,
            "created_allocations": 0,
            "relinked_allocations": 0,
            "updated_amounts": 0,
            "no_invoice_link": 0,
            "missing_local_payment": 0,
            "missing_local_invoice": 0,
            "customer_mismatch": 0,
            "overallocated_invoices": 0,
            "errors": [],
        }

        touched_invoice_ids: set[UUID] = set()
        processed = 0
        for splynx_payment in self.client.get_payments(
            date_from=date_from,
            date_to=date_to,
        ):
            if batch_size and processed >= batch_size:
                break
            processed += 1
            summary["processed"] += 1

            try:
                if not splynx_payment.invoice_id:
                    summary["no_invoice_link"] += 1
                    continue

                payment = self.db.scalar(
                    select(CustomerPayment).where(
                        CustomerPayment.organization_id == self.organization_id,
                        CustomerPayment.splynx_id == str(splynx_payment.id),
                    )
                )
                if not payment:
                    summary["missing_local_payment"] += 1
                    continue

                invoice = self.db.scalar(
                    select(Invoice).where(
                        Invoice.organization_id == self.organization_id,
                        Invoice.splynx_id == str(splynx_payment.invoice_id),
                    )
                )
                if not invoice:
                    invoice = self.db.scalar(
                        select(Invoice).where(
                            Invoice.organization_id == self.organization_id,
                            Invoice.correlation_id
                            == f"splynx-inv-{splynx_payment.invoice_id}",
                        )
                    )
                if not invoice:
                    summary["missing_local_invoice"] += 1
                    continue

                if payment.customer_id != invoice.customer_id:
                    summary["customer_mismatch"] += 1
                    continue

                allocation_date = (
                    self._parse_date(splynx_payment.date) or payment.payment_date
                )
                existing_allocation = self.db.scalar(
                    select(PaymentAllocation).where(
                        PaymentAllocation.payment_id == payment.payment_id
                    )
                )

                if existing_allocation:
                    allocation_matches = (
                        existing_allocation.invoice_id == invoice.invoice_id
                        and existing_allocation.allocated_amount
                        == splynx_payment.amount
                        and existing_allocation.allocation_date == allocation_date
                    )
                    if allocation_matches:
                        summary["already_correct"] += 1
                        continue

                    touched_invoice_ids.add(existing_allocation.invoice_id)
                    if existing_allocation.invoice_id != invoice.invoice_id:
                        summary["relinked_allocations"] += 1
                    if existing_allocation.allocated_amount != splynx_payment.amount:
                        summary["updated_amounts"] += 1

                    existing_allocation.invoice_id = invoice.invoice_id
                    existing_allocation.allocated_amount = splynx_payment.amount
                    existing_allocation.allocation_date = allocation_date
                    touched_invoice_ids.add(invoice.invoice_id)
                    summary["fixed"] += 1
                    continue

                self.db.add(
                    PaymentAllocation(
                        payment_id=payment.payment_id,
                        invoice_id=invoice.invoice_id,
                        allocated_amount=splynx_payment.amount,
                        allocation_date=allocation_date,
                    )
                )
                touched_invoice_ids.add(invoice.invoice_id)
                summary["created_allocations"] += 1
                summary["fixed"] += 1
            except Exception as exc:
                logger.exception(
                    "Relationship repair error for Splynx payment %s: %s",
                    splynx_payment.id,
                    exc,
                )
                summary["errors"].append(f"Payment {splynx_payment.id}: {exc}")

        for invoice_id in touched_invoice_ids:
            invoice = self.db.get(Invoice, invoice_id)
            if not invoice:
                continue
            allocated_total = self._recompute_invoice_paid_from_allocations(invoice)
            if allocated_total > invoice.total_amount:
                summary["overallocated_invoices"] += 1

        self.db.flush()
        logger.info("Splynx relationship repair summary: %s", summary)
        return summary

    # =========================================================================
    # Credit Note Sync
    # =========================================================================

    def sync_credit_notes(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        created_by_user_id: UUID | None = None,
        batch_size: int | None = None,
        skip_unchanged: bool = True,
    ) -> SyncResult:
        """
        Sync credit notes from Splynx.

        Credit notes are stored as invoices with type=CREDIT_NOTE.

        Args:
            date_from: Only sync credit notes after this date
            date_to: Only sync credit notes before this date
            created_by_user_id: User ID to record as creator
            batch_size: Max number of records to sync (None = all)
            skip_unchanged: Skip records that haven't changed (default True)
        """
        result = SyncResult(success=True, entity_type="credit_notes")
        processed = 0

        # Ensure customer cache is loaded
        if not self._customer_cache:
            self._load_customer_cache()

        try:
            for splynx_cn in self.client.get_credit_notes(
                date_from=date_from,
                date_to=date_to,
            ):
                # Check batch limit
                if batch_size and processed >= batch_size:
                    result.message = f"Batch limit ({batch_size}) reached"
                    break

                try:
                    savepoint = self.db.begin_nested()
                    self._sync_single_credit_note(splynx_cn, created_by_user_id, result)
                    savepoint.commit()
                    processed += 1

                    # Commit + expunge periodically to prevent OOM
                    if processed % 500 == 0:
                        self.db.commit()
                        self.db.expunge_all()
                        logger.info("Progress: %d credit notes processed", processed)

                except Exception as e:
                    try:
                        savepoint.rollback()
                    except Exception:
                        self.db.rollback()
                    result.errors.append(f"Credit Note {splynx_cn.number}: {str(e)}")
                    logger.exception("Error syncing credit note %s", splynx_cn.number)

            self.db.flush()
            result.message = (
                f"Synced {result.created} new, {result.updated} updated, "
                f"{result.skipped} skipped credit notes"
            )
            logger.info(result.message)

        except SplynxError as e:
            result.success = False
            result.message = f"Splynx API error: {e.message}"
            result.errors.append(result.message)
            logger.error(result.message)

        return result

    def _sync_single_credit_note(
        self,
        splynx_cn: SplynxCreditNote,
        created_by_user_id: UUID | None,
        result: SyncResult,
    ) -> None:
        """Sync a single credit note."""
        external_id = str(splynx_cn.id)

        # Compute hash for change detection
        data_hash = self._compute_hash(
            {
                "number": splynx_cn.number,
                "total": str(splynx_cn.total),
                "status": splynx_cn.status,
                "date_created": splynx_cn.date_created,
                "note": splynx_cn.note,
            }
        )

        # Check via sync tracking, splynx_id, or legacy number
        local_id = self._get_synced_entity(EntityType.CREDIT_NOTE, external_id)
        existing = None
        if local_id:
            existing = self.db.get(Invoice, local_id)
        if not existing:
            stmt = select(Invoice).where(
                Invoice.organization_id == self.organization_id,
                Invoice.splynx_id == str(splynx_cn.id),
                Invoice.invoice_type == InvoiceType.CREDIT_NOTE,
            )
            existing = self.db.scalar(stmt)
        if not existing:
            existing = self._get_existing_invoice(f"SPL-CN-{splynx_cn.id}")

        # Get customer ID
        customer_id = self._get_or_create_customer_id(splynx_cn.customer_id)
        if not customer_id:
            result.skipped += 1
            result.errors.append(
                f"Credit Note {splynx_cn.number}: Customer {splynx_cn.customer_id} not synced"
            )
            return

        # Parse date
        cn_date = self._parse_date(splynx_cn.date_created) or date.today()

        if existing:
            # Update existing credit note
            existing.customer_id = customer_id
            existing.invoice_date = cn_date
            existing.due_date = cn_date
            existing.subtotal = splynx_cn.total
            existing.total_amount = splynx_cn.total
            existing.functional_currency_amount = splynx_cn.total
            existing.notes = splynx_cn.note
            existing.splynx_id = str(splynx_cn.id)
            existing.splynx_number = splynx_cn.number
            existing.last_synced_at = datetime.now(UTC)

            # Replace lines with parsed items
            self._replace_invoice_lines(
                existing.invoice_id, splynx_cn, is_credit_note=True
            )

            result.updated += 1
            # Record sync tracking
            self._record_sync(
                EntityType.CREDIT_NOTE,
                external_id,
                existing.invoice_id,
                data_hash,
            )
        else:
            # Generate own sequential credit note number
            invoice_number = self._generate_credit_note_number(cn_date)

            # Create new credit note (as invoice with type=CREDIT_NOTE)
            # Note: amounts are stored positive; InvoiceType.CREDIT_NOTE
            # signals the AR subsystem to treat them as reductions.
            invoice = Invoice(
                organization_id=self.organization_id,
                customer_id=customer_id,
                invoice_number=invoice_number,
                invoice_type=InvoiceType.CREDIT_NOTE,
                invoice_date=cn_date,
                due_date=cn_date,
                currency_code="NGN",
                subtotal=splynx_cn.total,
                tax_amount=Decimal("0"),
                total_amount=splynx_cn.total,
                amount_paid=Decimal("0"),
                functional_currency_amount=splynx_cn.total,
                status=InvoiceStatus.POSTED,
                ar_control_account_id=self.ar_control_account_id,
                source_document_type="splynx_credit_note",
                correlation_id=f"splynx-cn-{splynx_cn.id}",
                notes=splynx_cn.note,
                internal_notes=f"Imported from Splynx. Original ID: {splynx_cn.id}",
                created_by_user_id=created_by_user_id or SYSTEM_USER_ID,
                splynx_id=str(splynx_cn.id),
                splynx_number=splynx_cn.number,
                last_synced_at=datetime.now(UTC),
            )
            self.db.add(invoice)
            self.db.flush()

            # Add credit note lines from Splynx items (or single fallback line)
            self._create_invoice_lines(
                invoice.invoice_id, splynx_cn, is_credit_note=True
            )

            result.created += 1
            # Record sync tracking
            self._record_sync(
                EntityType.CREDIT_NOTE,
                external_id,
                invoice.invoice_id,
                data_hash,
            )

    # =========================================================================
    # Full Sync
    # =========================================================================

    def sync_all(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        created_by_user_id: UUID | None = None,
    ) -> FullSyncResult:
        """
        Sync all entity types from Splynx.

        Syncs in order: customers, invoices, payments, credit_notes.

        Args:
            date_from: Only sync records created after this date
            date_to: Only sync records created before this date
            created_by_user_id: User ID to record as creator
        """
        import time

        start_time = time.time()

        logger.info(
            "Starting full Splynx sync from %s to %s",
            date_from or "beginning",
            date_to or "now",
        )

        # Sync in dependency order
        customers_result = self.sync_customers(
            date_from=date_from,
            date_to=date_to,
            created_by_user_id=created_by_user_id,
        )
        invoices_result = self.sync_invoices(
            date_from=date_from,
            date_to=date_to,
            created_by_user_id=created_by_user_id,
        )
        payments_result = self.sync_payments(
            date_from=date_from,
            date_to=date_to,
            created_by_user_id=created_by_user_id,
        )
        credit_notes_result = self.sync_credit_notes(
            date_from=date_from,
            date_to=date_to,
            created_by_user_id=created_by_user_id,
        )

        duration = time.time() - start_time
        total_errors = (
            len(customers_result.errors)
            + len(invoices_result.errors)
            + len(payments_result.errors)
            + len(credit_notes_result.errors)
        )

        result = FullSyncResult(
            customers=customers_result,
            invoices=invoices_result,
            payments=payments_result,
            credit_notes=credit_notes_result,
            total_errors=total_errors,
            duration_seconds=round(duration, 2),
        )

        logger.info(
            "Full Splynx sync completed in %.2fs with %d errors",
            duration,
            total_errors,
        )

        return result

    # =========================================================================
    # Bank Reconciliation
    # =========================================================================

    def reconcile_paystack_payments(
        self,
        dry_run: bool = False,
    ) -> PaystackReconcileResult:
        """
        Reconcile Splynx Paystack payments with bank statement lines.

        Three-tier matching:
        1. Exact match by Paystack reference (for payments with ref in comment)
        2. Unique match by date + amount (when only one payment/line has that combo)
        3. Customer-based match by date + amount (when customer has unique payment)

        Args:
            dry_run: If True, don't commit changes, just return match stats

        Returns:
            Dict with reconciliation statistics
        """
        from collections import defaultdict
        from datetime import datetime

        from sqlalchemy import text

        logger.info("Starting Paystack payment reconciliation (dry_run=%s)", dry_run)

        result: PaystackReconcileResult = {
            "matched_by_reference": 0,
            "matched_by_date_amount": 0,
            "matched_by_customer": 0,
            "ambiguous_matches": 0,
            "unmatched_payments": 0,
            "unmatched_statements": 0,
            "total_matched_amount": Decimal("0"),
            "errors": [],
        }

        # Get Paystack bank account IDs
        paystack_accounts = self.db.execute(
            text("""
                SELECT bank_account_id
                FROM banking.bank_accounts
                WHERE organization_id = :org_id
                  AND (LOWER(account_name) LIKE '%paystack%' OR LOWER(bank_name) LIKE '%paystack%')
            """),
            {"org_id": self.organization_id},
        ).fetchall()

        if not paystack_accounts:
            result["errors"].append("No Paystack bank accounts found")
            return result

        paystack_account_ids = [row.bank_account_id for row in paystack_accounts]
        logger.info("Found %d Paystack bank accounts", len(paystack_account_ids))

        # Step 1: Match by reference
        # Get payments with Paystack references
        payments_with_refs = self.db.execute(
            text("""
                SELECT
                    cp.payment_id,
                    cp.payment_date,
                    cp.amount,
                    cp.description,
                    LOWER(SUBSTRING(cp.description FROM '[0-9a-f]{12,14}')) as paystack_ref
                FROM ar.customer_payment cp
                WHERE cp.organization_id = :org_id
                  AND cp.correlation_id LIKE 'splynx-pmt-%'
                  AND cp.bank_account_id = ANY(:account_ids)
                  AND cp.description ~ '[0-9a-f]{12,14}'
            """),
            {"org_id": self.organization_id, "account_ids": paystack_account_ids},
        ).fetchall()

        logger.info(
            "Found %d payments with Paystack references", len(payments_with_refs)
        )

        # Build reference lookup for statement lines
        statement_refs = self.db.execute(
            text("""
                SELECT
                    bsl.line_id,
                    LOWER(bsl.reference) as ref,
                    bsl.amount,
                    bsl.transaction_date,
                    bsl.is_matched
                FROM banking.bank_statement_lines bsl
                JOIN banking.bank_statements bs ON bsl.statement_id = bs.statement_id
                WHERE bs.organization_id = :org_id
                  AND bs.bank_account_id = ANY(:account_ids)
                  AND bsl.transaction_type = 'credit'
                  AND bsl.is_matched = false
            """),
            {"org_id": self.organization_id, "account_ids": paystack_account_ids},
        ).fetchall()

        ref_to_line = {row.ref: row for row in statement_refs if row.ref}
        logger.info("Found %d unmatched statement lines", len(statement_refs))

        matched_payment_ids = set()
        matched_line_ids = set()

        # Match by reference
        for payment in payments_with_refs:
            if payment.paystack_ref and payment.paystack_ref in ref_to_line:
                line = ref_to_line[payment.paystack_ref]
                if line.line_id not in matched_line_ids:
                    matched_payment_ids.add(payment.payment_id)
                    matched_line_ids.add(line.line_id)
                    result["matched_by_reference"] += 1
                    result["total_matched_amount"] += payment.amount

                    if not dry_run:
                        # Update statement line
                        self.db.execute(
                            text("""
                                UPDATE banking.bank_statement_lines
                                SET is_matched = true,
                                    matched_at = :now,
                                    notes = COALESCE(notes, '') || E'\n' || :note
                                WHERE line_id = :line_id
                            """),
                            {
                                "line_id": line.line_id,
                                "now": datetime.now(tz=UTC),
                                "note": f" [Matched to Splynx payment {payment.payment_id}]",
                            },
                        )

        logger.info("Matched %d payments by reference", result["matched_by_reference"])

        # Step 2: Match by date + amount for remaining
        # Get unmatched payments (include customer_id for tier 3 matching)
        unmatched_payments = self.db.execute(
            text("""
                SELECT
                    cp.payment_id,
                    cp.customer_id,
                    cp.payment_date,
                    cp.amount
                FROM ar.customer_payment cp
                WHERE cp.organization_id = :org_id
                  AND cp.correlation_id LIKE 'splynx-pmt-%'
                  AND cp.bank_account_id = ANY(:account_ids)
                  AND cp.payment_id != ALL(:matched_ids)
            """),
            {
                "org_id": self.organization_id,
                "account_ids": paystack_account_ids,
                "matched_ids": list(matched_payment_ids)
                or [UUID("00000000-0000-0000-0000-000000000000")],
            },
        ).fetchall()

        # Get remaining unmatched statement lines
        unmatched_lines = self.db.execute(
            text("""
                SELECT
                    bsl.line_id,
                    bsl.transaction_date,
                    bsl.amount
                FROM banking.bank_statement_lines bsl
                JOIN banking.bank_statements bs ON bsl.statement_id = bs.statement_id
                WHERE bs.organization_id = :org_id
                  AND bs.bank_account_id = ANY(:account_ids)
                  AND bsl.transaction_type = 'credit'
                  AND bsl.is_matched = false
                  AND bsl.line_id != ALL(:matched_ids)
            """),
            {
                "org_id": self.organization_id,
                "account_ids": paystack_account_ids,
                "matched_ids": list(matched_line_ids)
                or [UUID("00000000-0000-0000-0000-000000000000")],
            },
        ).fetchall()

        # Build date+amount index for statement lines
        # Key: (date, amount_cents) -> list of line_ids
        line_index: dict[tuple[object, int], list[UUID]] = {}
        for line in unmatched_lines:
            # Round to 2 decimals to handle precision differences
            amount_cents = int(line.amount * 100)
            key = (line.transaction_date, amount_cents)
            if key not in line_index:
                line_index[key] = []
            line_index[key].append(line.line_id)

        # Tier 2: Match payments with unique date+amount
        ambiguous_payments = []  # Collect for tier 3

        for payment in unmatched_payments:
            if payment.payment_id in matched_payment_ids:
                continue

            amount_cents = int(payment.amount * 100)
            key = (payment.payment_date, amount_cents)

            if key in line_index and line_index[key]:
                available_lines = [
                    lid for lid in line_index[key] if lid not in matched_line_ids
                ]

                if len(available_lines) == 1:
                    # Unique match
                    line_id = available_lines[0]
                    matched_payment_ids.add(payment.payment_id)
                    matched_line_ids.add(line_id)
                    result["matched_by_date_amount"] += 1
                    result["total_matched_amount"] += payment.amount

                    if not dry_run:
                        self.db.execute(
                            text("""
                                UPDATE banking.bank_statement_lines
                                SET is_matched = true,
                                    matched_at = :now,
                                    notes = COALESCE(notes, '') || E'\n' || :note
                                WHERE line_id = :line_id
                            """),
                            {
                                "line_id": line_id,
                                "now": datetime.now(tz=UTC),
                                "note": f" [Matched to Splynx payment {payment.payment_id} by date+amount]",
                            },
                        )
                elif len(available_lines) > 1:
                    # Multiple possible matches - save for tier 3
                    ambiguous_payments.append(payment)

        logger.info(
            "Tier 2 complete: %d matched by date+amount, %d ambiguous for tier 3",
            result["matched_by_date_amount"],
            len(ambiguous_payments),
        )

        # Tier 3: Customer-based matching for ambiguous payments
        # Group ambiguous payments by (customer_id, date, amount_cents)
        customer_payment_groups: dict[tuple[UUID, object, int], list[Any]] = (
            defaultdict(list)
        )
        for payment in ambiguous_payments:
            if payment.payment_id in matched_payment_ids:
                continue
            amount_cents = int(payment.amount * 100)
            customer_key = (payment.customer_id, payment.payment_date, amount_cents)
            customer_payment_groups[customer_key].append(payment)

        # For each customer with unique payment on date+amount, try to match
        for (
            _customer_id,
            pay_date,
            amount_cents,
        ), payments in customer_payment_groups.items():
            if len(payments) != 1:
                # Customer has multiple payments with same date+amount - still ambiguous
                result["ambiguous_matches"] += len(payments)
                continue

            payment = payments[0]
            if payment.payment_id in matched_payment_ids:
                continue

            # Check if there's an available bank line for this date+amount
            key = (pay_date, amount_cents)
            if key in line_index:
                available_lines = [
                    lid for lid in line_index[key] if lid not in matched_line_ids
                ]

                if available_lines:
                    # Take the first available line for this customer's unique payment
                    line_id = available_lines[0]
                    matched_payment_ids.add(payment.payment_id)
                    matched_line_ids.add(line_id)
                    result["matched_by_customer"] += 1
                    result["total_matched_amount"] += payment.amount

                    if not dry_run:
                        self.db.execute(
                            text("""
                                UPDATE banking.bank_statement_lines
                                SET is_matched = true,
                                    matched_at = :now,
                                    notes = COALESCE(notes, '') || E'\n' || :note
                                WHERE line_id = :line_id
                            """),
                            {
                                "line_id": line_id,
                                "now": datetime.now(tz=UTC),
                                "note": f" [Matched to Splynx payment {payment.payment_id} by customer+date+amount]",
                            },
                        )
                else:
                    # No available lines for this date+amount
                    result["ambiguous_matches"] += 1
            else:
                result["ambiguous_matches"] += 1

        # Count final unmatched
        result["unmatched_payments"] = len(unmatched_payments) - (
            result["matched_by_date_amount"] + result["matched_by_customer"]
        )
        result["unmatched_statements"] = len(unmatched_lines) - len(matched_line_ids)

        if not dry_run:
            self.db.flush()

        logger.info(
            "Reconciliation complete: %d by ref, %d by date+amount, %d by customer, %d ambiguous",
            result["matched_by_reference"],
            result["matched_by_date_amount"],
            result["matched_by_customer"],
            result["ambiguous_matches"],
        )

        return result

    def reconcile_bank_payments(
        self,
        bank_account_ids: list[UUID],
        bank_name: str = "Bank",
        dry_run: bool = False,
    ) -> BankReconcileResult:
        """
        Reconcile Splynx payments with bank statement lines for specific bank accounts.

        Two-tier matching (no Paystack reference for non-Paystack banks):
        1. Unique match by date + amount
        2. Customer-based match by date + amount

        Args:
            bank_account_ids: List of bank account UUIDs to reconcile
            bank_name: Display name for logging
            dry_run: If True, don't commit changes, just return match stats

        Returns:
            Dict with reconciliation statistics
        """
        from collections import defaultdict
        from datetime import datetime

        from sqlalchemy import text

        logger.info(
            "Starting %s payment reconciliation (dry_run=%s)", bank_name, dry_run
        )

        result: BankReconcileResult = {
            "bank_name": bank_name,
            "matched_by_date_amount": 0,
            "matched_by_customer": 0,
            "ambiguous_matches": 0,
            "unmatched_payments": 0,
            "unmatched_statements": 0,
            "total_matched_amount": Decimal("0"),
            "errors": [],
        }

        if not bank_account_ids:
            result["errors"].append("No bank account IDs provided")
            return result

        # Get unmatched payments for these bank accounts
        payments = self.db.execute(
            text("""
                SELECT
                    cp.payment_id,
                    cp.customer_id,
                    cp.payment_date,
                    cp.amount
                FROM ar.customer_payment cp
                WHERE cp.organization_id = :org_id
                  AND cp.correlation_id LIKE 'splynx-pmt-%'
                  AND cp.bank_account_id = ANY(:account_ids)
            """),
            {"org_id": self.organization_id, "account_ids": bank_account_ids},
        ).fetchall()

        logger.info("Found %d Splynx payments for %s", len(payments), bank_name)

        # Get unmatched statement lines for these bank accounts
        statement_lines = self.db.execute(
            text("""
                SELECT
                    bsl.line_id,
                    bsl.transaction_date,
                    bsl.amount
                FROM banking.bank_statement_lines bsl
                JOIN banking.bank_statements bs ON bsl.statement_id = bs.statement_id
                WHERE bs.organization_id = :org_id
                  AND bs.bank_account_id = ANY(:account_ids)
                  AND bsl.transaction_type = 'credit'
                  AND bsl.is_matched = false
            """),
            {"org_id": self.organization_id, "account_ids": bank_account_ids},
        ).fetchall()

        logger.info(
            "Found %d unmatched statement lines for %s", len(statement_lines), bank_name
        )

        if not statement_lines:
            result["unmatched_payments"] = len(payments)
            result["errors"].append(
                f"No unmatched bank statement lines for {bank_name}"
            )
            return result

        matched_payment_ids: set[UUID] = set()
        matched_line_ids: set[UUID] = set()

        # Build date+amount index for statement lines
        line_index: dict[tuple[object, int], list[UUID]] = {}
        for line in statement_lines:
            amount_cents = int(line.amount * 100)
            key = (line.transaction_date, amount_cents)
            if key not in line_index:
                line_index[key] = []
            line_index[key].append(line.line_id)

        # Tier 1: Match payments with unique date+amount
        ambiguous_payments = []

        for payment in payments:
            amount_cents = int(payment.amount * 100)
            key = (payment.payment_date, amount_cents)

            if key in line_index and line_index[key]:
                available_lines = [
                    lid for lid in line_index[key] if lid not in matched_line_ids
                ]

                if len(available_lines) == 1:
                    line_id = available_lines[0]
                    matched_payment_ids.add(payment.payment_id)
                    matched_line_ids.add(line_id)
                    result["matched_by_date_amount"] += 1
                    result["total_matched_amount"] += payment.amount

                    if not dry_run:
                        self.db.execute(
                            text("""
                                UPDATE banking.bank_statement_lines
                                SET is_matched = true,
                                    matched_at = :now,
                                    notes = COALESCE(notes, '') || E'\n' || :note
                                WHERE line_id = :line_id
                            """),
                            {
                                "line_id": line_id,
                                "now": datetime.now(tz=UTC),
                                "note": f" [Matched to Splynx payment {payment.payment_id} by date+amount]",
                            },
                        )
                elif len(available_lines) > 1:
                    ambiguous_payments.append(payment)

        logger.info(
            "Tier 1 (%s): %d matched by date+amount, %d ambiguous",
            bank_name,
            result["matched_by_date_amount"],
            len(ambiguous_payments),
        )

        # Tier 2: Customer-based matching
        customer_payment_groups: dict[tuple[UUID, object, int], list[Any]] = (
            defaultdict(list)
        )
        for payment in ambiguous_payments:
            if payment.payment_id in matched_payment_ids:
                continue
            amount_cents = int(payment.amount * 100)
            customer_key = (payment.customer_id, payment.payment_date, amount_cents)
            customer_payment_groups[customer_key].append(payment)

        for (
            _customer_id,
            pay_date,
            amount_cents,
        ), group_payments in customer_payment_groups.items():
            if len(group_payments) != 1:
                result["ambiguous_matches"] += len(group_payments)
                continue

            payment = group_payments[0]
            if payment.payment_id in matched_payment_ids:
                continue

            key = (pay_date, amount_cents)
            if key in line_index:
                available_lines = [
                    lid for lid in line_index[key] if lid not in matched_line_ids
                ]

                if available_lines:
                    line_id = available_lines[0]
                    matched_payment_ids.add(payment.payment_id)
                    matched_line_ids.add(line_id)
                    result["matched_by_customer"] += 1
                    result["total_matched_amount"] += payment.amount

                    if not dry_run:
                        self.db.execute(
                            text("""
                                UPDATE banking.bank_statement_lines
                                SET is_matched = true,
                                    matched_at = :now,
                                    notes = COALESCE(notes, '') || E'\n' || :note
                                WHERE line_id = :line_id
                            """),
                            {
                                "line_id": line_id,
                                "now": datetime.now(tz=UTC),
                                "note": f" [Matched to Splynx payment {payment.payment_id} by customer+date+amount]",
                            },
                        )
                else:
                    result["ambiguous_matches"] += 1
            else:
                result["ambiguous_matches"] += 1

        # Count final stats
        result["unmatched_payments"] = len(payments) - len(matched_payment_ids)
        result["unmatched_statements"] = len(statement_lines) - len(matched_line_ids)

        if not dry_run:
            self.db.flush()

        logger.info(
            "%s reconciliation complete: %d by date+amount, %d by customer, %d ambiguous",
            bank_name,
            result["matched_by_date_amount"],
            result["matched_by_customer"],
            result["ambiguous_matches"],
        )

        return result

    def reconcile_bulk_payments(
        self,
        bank_account_ids: list[UUID],
        bank_name: str = "Bank",
        dry_run: bool = False,
    ) -> BulkReconcileResult:
        """
        Match bulk payments where customer's multiple Splynx payments on same day
        sum to a single bank statement line.

        This handles cases where a customer pays once but Splynx records
        multiple payments (e.g., one payment per invoice).

        Args:
            bank_account_ids: Bank accounts to reconcile
            bank_name: Display name for logging
            dry_run: If True, don't commit changes

        Returns:
            Dict with match statistics
        """
        from datetime import datetime

        from sqlalchemy import text

        logger.info(
            "Starting bulk payment reconciliation for %s (dry_run=%s)",
            bank_name,
            dry_run,
        )

        result: BulkReconcileResult = {
            "bank_name": bank_name,
            "bulk_matches": 0,
            "payments_matched": 0,
            "total_matched_amount": Decimal("0"),
            "errors": [],
        }

        if not bank_account_ids:
            return result

        # Find customer daily totals that match unmatched bank lines
        matches = self.db.execute(
            text("""
                WITH customer_daily_totals AS (
                    SELECT
                        cp.customer_id,
                        cp.payment_date,
                        cp.bank_account_id,
                        SUM(cp.amount) as total_amount,
                        COUNT(*) as payment_count,
                        ARRAY_AGG(cp.payment_id) as payment_ids
                    FROM ar.customer_payment cp
                    WHERE cp.organization_id = :org_id
                      AND cp.correlation_id LIKE 'splynx-pmt-%'
                      AND cp.bank_account_id = ANY(:account_ids)
                      AND cp.payment_date >= '2022-01-01'
                    GROUP BY cp.customer_id, cp.payment_date, cp.bank_account_id
                    HAVING COUNT(*) > 1
                ),
                unmatched_bank_lines AS (
                    SELECT
                        bsl.line_id,
                        bsl.transaction_date,
                        bsl.amount,
                        bs.bank_account_id
                    FROM banking.bank_statement_lines bsl
                    JOIN banking.bank_statements bs ON bsl.statement_id = bs.statement_id
                    WHERE bs.organization_id = :org_id
                      AND bs.bank_account_id = ANY(:account_ids)
                      AND bsl.transaction_type = 'credit'
                      AND bsl.is_matched = false
                )
                SELECT
                    cdt.customer_id,
                    cdt.payment_date,
                    cdt.total_amount,
                    cdt.payment_count,
                    cdt.payment_ids,
                    ubl.line_id
                FROM customer_daily_totals cdt
                JOIN unmatched_bank_lines ubl
                    ON cdt.payment_date = ubl.transaction_date
                    AND ROUND(cdt.total_amount::numeric, 2) = ROUND(ubl.amount::numeric, 2)
                    AND cdt.bank_account_id = ubl.bank_account_id
            """),
            {"org_id": self.organization_id, "account_ids": bank_account_ids},
        ).fetchall()

        logger.info(
            "Found %d potential bulk payment matches for %s", len(matches), bank_name
        )

        matched_line_ids: set[UUID] = set()

        for match in matches:
            # Skip if this line was already matched
            if match.line_id in matched_line_ids:
                continue

            matched_line_ids.add(match.line_id)
            result["bulk_matches"] += 1
            result["payments_matched"] += match.payment_count
            result["total_matched_amount"] += match.total_amount

            if not dry_run:
                # Mark the bank line as matched
                self.db.execute(
                    text("""
                        UPDATE banking.bank_statement_lines
                        SET is_matched = true,
                            matched_at = :now,
                            notes = COALESCE(notes, '') || E'\n' || :note
                        WHERE line_id = :line_id
                    """),
                    {
                        "line_id": match.line_id,
                        "now": datetime.now(tz=UTC),
                        "note": f" [Bulk match: {match.payment_count} Splynx payments sum to this amount]",
                    },
                )

        if not dry_run:
            self.db.flush()

        logger.info(
            "%s bulk reconciliation: %d bank lines matched to %d payments (NGN %s)",
            bank_name,
            result["bulk_matches"],
            result["payments_matched"],
            f"{result['total_matched_amount']:,.2f}",
        )

        return result

    def reconcile_all_banks(self, dry_run: bool = False) -> dict:
        """
        Reconcile Splynx payments for all bank accounts that have both
        payments and bank statements.

        Returns:
            Dict with results per bank and totals
        """
        from sqlalchemy import text

        logger.info("Starting reconciliation for all banks (dry_run=%s)", dry_run)

        # Find all bank accounts with Splynx payments
        banks = self.db.execute(
            text("""
                SELECT DISTINCT
                    ba.bank_account_id,
                    ba.account_name,
                    ba.bank_name,
                    COUNT(cp.payment_id) as payment_count
                FROM ar.customer_payment cp
                JOIN banking.bank_accounts ba ON cp.bank_account_id = ba.bank_account_id
                WHERE cp.organization_id = :org_id
                  AND cp.correlation_id LIKE 'splynx-pmt-%'
                GROUP BY ba.bank_account_id, ba.account_name, ba.bank_name
                HAVING COUNT(cp.payment_id) > 0
                ORDER BY COUNT(cp.payment_id) DESC
            """),
            {"org_id": self.organization_id},
        ).fetchall()

        totals: dict[str, int | Decimal] = {
            "matched_by_date_amount": 0,
            "matched_by_customer": 0,
            "matched_by_reference": 0,
            "ambiguous_matches": 0,
            "total_matched_amount": Decimal("0"),
        }
        results: dict[str, Any] = {
            "banks": {},
            "totals": totals,
        }

        for bank in banks:
            bank_id = bank.bank_account_id
            bank_display = f"{bank.account_name} ({bank.bank_name})"
            bank_result: BankReconcileResult | PaystackReconcileResult

            # Check if this is Paystack (use special method with reference matching)
            if (
                "paystack" in bank.account_name.lower()
                or "paystack" in bank.bank_name.lower()
            ):
                bank_result = self.reconcile_paystack_payments(dry_run=dry_run)
                totals["matched_by_reference"] += bank_result.get(
                    "matched_by_reference", 0
                )
            else:
                bank_result = self.reconcile_bank_payments(
                    bank_account_ids=[bank_id],
                    bank_name=bank_display,
                    dry_run=dry_run,
                )

            results["banks"][bank.account_name] = bank_result

            # Update totals
            totals["matched_by_date_amount"] += bank_result.get(
                "matched_by_date_amount", 0
            )
            totals["matched_by_customer"] += bank_result.get("matched_by_customer", 0)
            totals["ambiguous_matches"] += bank_result.get("ambiguous_matches", 0)
            totals["total_matched_amount"] += bank_result.get(
                "total_matched_amount", Decimal("0")
            )

        # Phase 2: Bulk payment matching for remaining unmatched
        # (Multiple Splynx payments on same day summing to one bank line)
        all_bank_ids = [bank.bank_account_id for bank in banks]
        bulk_result = self.reconcile_bulk_payments(
            bank_account_ids=all_bank_ids,
            bank_name="All Banks",
            dry_run=dry_run,
        )

        results["bulk_matching"] = bulk_result
        totals["bulk_matches"] = bulk_result.get("bulk_matches", 0)
        totals["bulk_payments_matched"] = bulk_result.get("payments_matched", 0)
        totals["total_matched_amount"] += bulk_result.get(
            "total_matched_amount", Decimal("0")
        )

        return results
