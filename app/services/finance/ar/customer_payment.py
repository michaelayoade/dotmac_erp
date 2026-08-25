"""
CustomerPaymentService - AR payment receipt processing.

Manages customer payment creation, posting, and allocation to invoices.
"""

from __future__ import annotations

import logging
import uuid as uuid_lib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.finance.ar.customer import Customer
from app.models.finance.ar.customer_payment import (
    CustomerPayment,
    PaymentMethod,
    PaymentStatus,
)
from app.models.finance.ar.invoice import Invoice, InvoiceStatus
from app.models.finance.ar.payment_allocation import PaymentAllocation
from app.models.finance.core_config.numbering_sequence import SequenceType
from app.models.finance.tax.tax_code import TaxCode, TaxType
from app.models.finance.audit.audit_log import AuditAction
from app.services.audit_dispatcher import fire_audit_event
from app.services.common import NotFoundError, ValidationError, coerce_uuid
from app.services.finance.ar.input_utils import (
    parse_date_str,
    parse_decimal,
    parse_json_list,
    require_uuid,
    resolve_currency_code,
)
from app.services.finance.ar.payment_status import (
    PAYMENT_DUST,
    apply_payment_status,
)
from app.services.finance.platform.sequence import SequenceService
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


class RefundReversalError(ValidationError):
    """The ledger leg of a refund could not be posted, so NOTHING was changed.

    Raised before any allocation is reversed and before any status is written.
    The caller is free to report and move on knowing the payment, its invoices
    and the GL are all exactly as they were — which is the property
    ``void_payment`` and ``mark_bounced`` did not have before ADR-0008 (they
    logged the failure and completed the void anyway, leaving the ledger
    carrying cash the subledger had already given back).
    """


#: Statuses from which no further refund decision can be taken. A payment that
#: is already VOID cannot subsequently be BOUNCED; a refunded receipt cannot be
#: refunded twice. Re-requesting the status a payment already holds is a no-op,
#: not an error — see :meth:`CustomerPaymentService.refund_payment`.
REFUND_TERMINAL_STATUSES = frozenset(
    {
        PaymentStatus.VOID,
        PaymentStatus.BOUNCED,
        PaymentStatus.REVERSED,
    }
)


def _reverse_vat_cash_basis_for_payment(
    db: Session,
    organization_id: UUID,
    payment: CustomerPayment,
    user_id: UUID,
    reason: str,
    action: str,
) -> None:
    from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
    from app.services.finance.gl.reversal import ReversalService
    from app.services.finance.tax.tax_transaction import tax_transaction_service

    vat_reclass_journal = db.scalar(
        select(JournalEntry).where(
            JournalEntry.organization_id == organization_id,
            JournalEntry.source_module == "AR",
            JournalEntry.source_document_type == "CUSTOMER_PAYMENT_VAT_RECLASS",
            JournalEntry.source_document_id == payment.payment_id,
        )
    )
    if (
        vat_reclass_journal
        and vat_reclass_journal.status == JournalStatus.POSTED
        and not vat_reclass_journal.reversal_journal_id
    ):
        result = ReversalService.create_reversal(
            db=db,
            organization_id=organization_id,
            original_journal_id=vat_reclass_journal.journal_entry_id,
            reversal_date=date.today(),
            created_by_user_id=user_id,
            reason=reason,
            auto_post=True,
            idempotency_key=(
                f"{organization_id}:AR:PAY:{payment.payment_id}:{action}:vat-reversal:v1"
            ),
        )
        if not result.success:
            logger.warning(
                "VAT reclass reversal failed for payment %s: %s",
                payment.payment_id,
                result.message,
            )

    deleted = tax_transaction_service.delete_cash_recognition_for_source(
        db,
        organization_id,
        "CUSTOMER_PAYMENT",
        payment.payment_id,
    )
    if deleted:
        logger.info(
            "Deleted %d cash-basis VAT rows for payment %s",
            deleted,
            payment.payment_id,
        )


@dataclass
class PaymentAllocationInput:
    """Input for allocating payment to an invoice."""

    invoice_id: UUID
    amount: Decimal


@dataclass
class CustomerPaymentInput:
    """Input for creating a customer payment."""

    customer_id: UUID
    payment_date: date
    payment_method: PaymentMethod
    currency_code: str
    amount: Decimal  # Net amount received (after WHT)
    bank_account_id: UUID | None = None
    allocations: list[PaymentAllocationInput] = field(default_factory=list)
    exchange_rate: Decimal | None = None
    reference: str | None = None
    description: str | None = None
    correlation_id: str | None = None
    # Withholding Tax (WHT) - when customer deducts WHT before paying
    gross_amount: Decimal | None = None  # If not provided, defaults to amount (no WHT)
    wht_code_id: UUID | None = None  # WHT tax code applied
    wht_amount: Decimal = field(default_factory=lambda: Decimal("0"))  # WHT deducted
    wht_certificate_number: str | None = None  # Certificate received from customer
    # Consolidated (reseller) payment: when True, allocations may target invoices
    # of the customer's sub-accounts (the whole account family). The family must
    # share the parent's AR control account so the existing single-line AR-control
    # credit posting stays GL-correct.
    consolidated: bool = False


def _resolve_receipt_amounts(
    input: CustomerPaymentInput,
) -> tuple[Decimal, Decimal, Decimal]:
    """Return canonical ``(net, gross, WHT)`` settlement amounts."""
    net_amount = input.amount
    wht_amount = input.wht_amount or Decimal("0")
    if net_amount < Decimal("0") or wht_amount < Decimal("0"):
        raise ValidationError("Payment and WHT amounts cannot be negative")

    if input.gross_amount is None:
        gross_amount = net_amount + wht_amount
    else:
        gross_amount = input.gross_amount
        expected_wht = gross_amount - net_amount
        if expected_wht < Decimal("0"):
            raise ValidationError("Gross payment amount cannot be less than net cash")
        if wht_amount == Decimal("0") and gross_amount != net_amount:
            wht_amount = expected_wht
        elif (expected_wht - wht_amount).copy_abs() > Decimal("0.01"):
            raise ValidationError(
                f"WHT amount ({wht_amount}) doesn't match gross - net ({expected_wht})"
            )

    return net_amount, gross_amount, wht_amount


class CustomerPaymentService(ListResponseMixin):
    """
    Service for customer payment receipt processing.

    Manages payment creation, posting, and invoice allocation.
    """

    @staticmethod
    def build_receipt_input(
        customer_id: UUID,
        receipt_date: date,
        payment_method_str: str,
        bank_account_id: UUID,
        currency_code: str,
        allocations_raw: list[dict[str, Any]],
        reference: str | None = None,
    ) -> CustomerPaymentInput:
        """Build CustomerPaymentInput from raw API params.

        Raises:
            ValueError: If payment_method is invalid.
        """
        allocations = [
            PaymentAllocationInput(
                invoice_id=a["invoice_id"],
                amount=a["amount"],
            )
            for a in allocations_raw
        ]
        total_amount = sum((alloc.amount for alloc in allocations), Decimal("0"))
        try:
            payment_method = PaymentMethod(payment_method_str)
        except ValueError:
            raise ValueError(f"Invalid payment method: {payment_method_str}")

        return CustomerPaymentInput(
            customer_id=customer_id,
            payment_date=receipt_date,
            payment_method=payment_method,
            bank_account_id=bank_account_id,
            currency_code=currency_code,
            amount=total_amount,
            reference=reference,
            allocations=allocations,
        )

    @staticmethod
    def create_payment(
        db: Session,
        organization_id: UUID,
        input: CustomerPaymentInput,
        created_by_user_id: UUID,
    ) -> CustomerPayment:
        """
        Create a new customer payment receipt.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Payment input data
            created_by_user_id: User creating the payment

        Returns:
            Created CustomerPayment
        """
        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(created_by_user_id)
        customer_id = coerce_uuid(input.customer_id)

        # Validate customer
        customer = db.get(Customer, customer_id)
        if not customer or customer.organization_id != org_id:
            raise NotFoundError("Customer not found")

        if not customer.is_active:
            raise ValidationError("Customer is not active")

        # Consolidated (reseller) payment: resolve the account family and require
        # a single shared AR control account, so the AR-control credit posting
        # stays GL-correct when paying sub-account invoices.
        family_ids: set[UUID] = {customer_id}
        if input.consolidated:
            from app.services.finance.ar.customer_family import CustomerFamilyResolver

            family_ids = set(CustomerFamilyResolver(db).family_ids(org_id, customer_id))
            distinct_controls = set(
                db.scalars(
                    select(Customer.ar_control_account_id)
                    .where(Customer.customer_id.in_(family_ids))
                    .distinct()
                ).all()
            )
            if distinct_controls != {customer.ar_control_account_id}:
                raise ValidationError(
                    "Consolidated payment requires the whole account family to "
                    "share one AR control account"
                )

        net_amount, gross_amount, wht_amount = _resolve_receipt_amounts(input)

        # Validate allocations
        if input.allocations:
            allocation_total = sum(a.amount for a in input.allocations)
            if allocation_total > gross_amount:
                raise ValidationError("Allocation total exceeds gross payment amount")

            for alloc in input.allocations:
                # Lock the invoice row to prevent over-allocation race condition.
                # Select only the PK so the FOR UPDATE does not pull in the
                # lazy="joined" customer (Postgres rejects FOR UPDATE on the
                # nullable side of that outer join).
                db.execute(
                    select(Invoice.invoice_id)
                    .where(Invoice.invoice_id == coerce_uuid(alloc.invoice_id))
                    .with_for_update()
                )
                invoice = db.get(Invoice, coerce_uuid(alloc.invoice_id))
                if not invoice or invoice.organization_id != org_id:
                    raise NotFoundError(f"Invoice {alloc.invoice_id} not found")
                if invoice.customer_id not in family_ids:
                    raise ValidationError(
                        f"Invoice {invoice.invoice_number} belongs to different customer"
                    )
                payable_statuses = [
                    InvoiceStatus.POSTED,
                    InvoiceStatus.PARTIALLY_PAID,
                    InvoiceStatus.OVERDUE,
                ]
                if invoice.status not in payable_statuses:
                    raise ValidationError(
                        f"Invoice {invoice.invoice_number} is not payable"
                    )
                if alloc.amount > invoice.balance_due:
                    raise ValidationError(
                        f"Allocation exceeds balance due on {invoice.invoice_number}"
                    )

        validated_wht_code_id: UUID | None = None
        if input.wht_code_id:
            wht_code = db.get(TaxCode, coerce_uuid(input.wht_code_id))
            if not wht_code or wht_code.organization_id != org_id:
                raise NotFoundError("WHT tax code not found")
            if wht_code.tax_type != TaxType.WITHHOLDING:
                raise ValidationError("Selected tax code is not a WITHHOLDING tax code")
            validated_wht_code_id = wht_code.tax_code_id

        if wht_amount > Decimal("0") and not validated_wht_code_id:
            raise ValidationError(
                "WHT tax code is required when WHT amount is specified"
            )

        # Generate payment number (after all validation passes)
        payment_number = SequenceService.get_next_number(
            db, org_id, SequenceType.RECEIPT
        )

        # If customer has WHT applicable and no WHT provided, warn (but don't block)
        # The user may have a valid reason (exemption, etc.)

        # Calculate functional currency amount
        exchange_rate = input.exchange_rate or Decimal("1.0")
        functional_amount = net_amount * exchange_rate

        # Create payment
        payment = CustomerPayment(
            organization_id=org_id,
            customer_id=customer_id,
            payment_number=payment_number,
            payment_date=input.payment_date,
            payment_method=input.payment_method,
            currency_code=input.currency_code,
            gross_amount=gross_amount,
            amount=net_amount,
            wht_code_id=validated_wht_code_id,
            wht_amount=wht_amount,
            wht_certificate_number=input.wht_certificate_number,
            exchange_rate=exchange_rate,
            functional_currency_amount=functional_amount,
            bank_account_id=input.bank_account_id,
            reference=input.reference,
            description=input.description,
            status=PaymentStatus.PENDING,
            created_by_user_id=user_id,
            correlation_id=input.correlation_id or str(uuid_lib.uuid4()),
        )

        db.add(payment)
        db.flush()

        # Create allocations
        for alloc in input.allocations:
            allocation = PaymentAllocation(
                payment_id=payment.payment_id,
                invoice_id=coerce_uuid(alloc.invoice_id),
                allocated_amount=alloc.amount,
                allocation_date=payment.payment_date,
            )
            db.add(allocation)

        db.flush()

        return payment

    @staticmethod
    def build_input_from_payload(
        db: Session,
        organization_id: UUID,
        payload: dict,
    ) -> CustomerPaymentInput:
        """Build CustomerPaymentInput from raw payload (strings or JSON)."""
        payment_date = (
            parse_date_str(payload.get("payment_date"), "Payment date") or date.today()
        )

        method_str = payload.get("payment_method", "BANK_TRANSFER")
        try:
            payment_method = PaymentMethod(method_str)
        except ValueError:
            payment_method = PaymentMethod.BANK_TRANSFER

        allocations: list[PaymentAllocationInput] = []
        allocations_data = parse_json_list(payload.get("allocations"), "Allocations")
        for alloc in allocations_data:
            if alloc.get("invoice_id") and alloc.get("amount"):
                allocations.append(
                    PaymentAllocationInput(
                        invoice_id=require_uuid(alloc.get("invoice_id"), "Invoice"),
                        amount=parse_decimal(alloc.get("amount"), "Allocation amount"),
                    )
                )

        has_wht = payload.get("has_wht") in ("true", "1", True, "on")
        wht_code_id = coerce_uuid(payload.get("wht_code_id")) if has_wht else None
        wht_amount = (
            parse_decimal(payload.get("wht_amount", "0"), "WHT amount")
            if has_wht
            else Decimal("0")
        )
        gross_amount = (
            parse_decimal(payload.get("gross_amount"), "Gross amount")
            if has_wht and payload.get("gross_amount") is not None
            else None
        )
        wht_certificate_number = (
            payload.get("wht_certificate_number") if has_wht else None
        )

        customer_id = require_uuid(payload.get("customer_id"), "Customer")
        currency_code = resolve_currency_code(
            db, coerce_uuid(organization_id), payload.get("currency_code")
        )

        exchange_rate: Decimal | None = None
        if payload.get("exchange_rate") not in (None, ""):
            exchange_rate = parse_decimal(payload.get("exchange_rate"), "Exchange rate")

        return CustomerPaymentInput(
            customer_id=customer_id,
            payment_date=payment_date,
            payment_method=payment_method,
            currency_code=currency_code,
            exchange_rate=exchange_rate,
            amount=parse_decimal(payload.get("amount", 0), "Amount"),
            bank_account_id=coerce_uuid(payload.get("bank_account_id"))
            if payload.get("bank_account_id")
            else None,
            reference=payload.get("reference"),
            description=payload.get("description"),
            allocations=allocations,
            gross_amount=gross_amount,
            wht_code_id=wht_code_id,
            wht_amount=wht_amount,
            wht_certificate_number=wht_certificate_number,
        )

    @staticmethod
    def post_payment(
        db: Session,
        organization_id: UUID,
        payment_id: UUID,
        posted_by_user_id: UUID,
        posting_date: date | None = None,
    ) -> CustomerPayment:
        """
        Post a payment to the general ledger and apply allocations.

        Args:
            db: Database session
            organization_id: Organization scope
            payment_id: Payment to post
            posted_by_user_id: User posting
            posting_date: Optional posting date

        Returns:
            Updated CustomerPayment
        """

        org_id = coerce_uuid(organization_id)
        pay_id = coerce_uuid(payment_id)
        user_id = coerce_uuid(posted_by_user_id)

        payment = db.get(CustomerPayment, pay_id)
        if not payment or payment.organization_id != org_id:
            raise NotFoundError("Payment not found")

        if payment.status != PaymentStatus.PENDING:
            raise ValidationError(
                f"Cannot post payment with status '{payment.status.value}'"
            )
        if not payment.bank_account_id:
            raise ValidationError("Bank account is required to post payment")

        from app.services.finance.ar.ar_posting_adapter import ARPostingAdapter

        # The service owns the workflow transition; the posting adapter is the
        # sole owner of journal and tax-transaction construction.
        payment.status = PaymentStatus.APPROVED
        try:
            posting_result = ARPostingAdapter.post_payment(
                db=db,
                organization_id=org_id,
                payment_id=pay_id,
                posting_date=posting_date or payment.payment_date,
                posted_by_user_id=user_id,
            )
        except Exception:
            payment.status = PaymentStatus.PENDING
            raise
        if not posting_result.success or posting_result.journal_entry_id is None:
            payment.status = PaymentStatus.PENDING
            raise ValidationError(posting_result.message or "Payment posting failed")

        payment.status = PaymentStatus.CLEARED
        payment.posted_by_user_id = user_id
        payment.posted_at = datetime.now(UTC)
        payment.journal_entry_id = posting_result.journal_entry_id
        payment.posting_batch_id = posting_result.posting_batch_id

        # Apply allocations to invoices
        allocations = list(
            db.scalars(
                select(PaymentAllocation).where(PaymentAllocation.payment_id == pay_id)
            ).all()
        )

        for alloc in allocations:
            invoice = db.get(Invoice, alloc.invoice_id)
            if invoice:
                invoice.amount_paid += alloc.allocated_amount
                apply_payment_status(invoice)

        db.flush()

        return payment

    @staticmethod
    def ensure_gl_posted(
        db: Session,
        payment: CustomerPayment,
        posted_by_user_id: UUID | None = None,
    ) -> bool:
        """
        Ensure a payment in CLEARED status has its GL journal entries.

        For payments created via sync/import that are already CLEARED but were
        never posted through the GL pipeline, this idempotently creates the
        missing journal entries.

        Does NOT change the payment status — only fills in missing GL entries.

        Args:
            db: Database session
            payment: Payment to check and post if needed
            posted_by_user_id: User to attribute posting to (defaults to creator)

        Returns:
            True if GL entries were created, False if already posted or not applicable
        """
        if payment.status != PaymentStatus.CLEARED:
            return False
        if payment.journal_entry_id is not None:
            return False  # Already has GL entries
        # A fully withheld receipt can have zero bank cash but still has a gross
        # AR settlement and WHT receivable to post.
        if (payment.gross_amount or Decimal("0")) == Decimal("0"):
            return False

        try:
            from app.services.finance.ar.ar_posting_adapter import ARPostingAdapter

            user_id = (
                posted_by_user_id
                or payment.created_by_user_id
                or UUID("00000000-0000-0000-0000-000000000000")
            )
            result = ARPostingAdapter.post_payment(
                db=db,
                organization_id=payment.organization_id,
                payment_id=payment.payment_id,
                posting_date=payment.payment_date,
                posted_by_user_id=user_id,
                idempotency_key=f"ensure-gl-pmt-{payment.payment_id}",
            )
            if result.success:
                payment.journal_entry_id = result.journal_entry_id
                payment.posting_batch_id = result.posting_batch_id
                logger.info(
                    "Auto-posted payment %s (journal %s)",
                    payment.payment_id,
                    result.journal_entry_id,
                )
                return True
            else:
                logger.warning(
                    "Auto-post failed for payment %s: %s",
                    payment.payment_id,
                    result.message,
                )
                return False
        except Exception as e:
            logger.exception("Error auto-posting payment %s: %s", payment.payment_id, e)
            return False

    # ------------------------------------------------------------------
    # Refunds (ADR-0008)
    #
    # `refund_payment` is the ONE place a customer refund is decided. Before
    # it, refund was a shape stamped onto five aggregates by eleven writers
    # with no owner: two near-identical bodies here, an assignment in the Sub
    # sync adapter, and — for a refund Paystack had actually paid out —
    # nothing at all, because `charge.refund` matched no webhook branch.
    #
    # One behaviour, three reasons: a void, a bounce and a refund differ in
    # WHY and in the terminal status they settle on, not in what has to
    # happen to the ledger, the allocations or the derived invoice verdicts.
    # ------------------------------------------------------------------

    @staticmethod
    def refund_payment(
        db: Session,
        organization_id: UUID,
        payment_id: UUID,
        amount: Decimal | None = None,
        reason: str = "",
        refunded_by_user_id: UUID | None = None,
        *,
        outcome_status: PaymentStatus = PaymentStatus.REVERSED,
        idempotency_suffix: str = "refund",
        allowed_from: frozenset[PaymentStatus] | None = None,
        credit_note_id: UUID | None = None,
    ) -> CustomerPayment:
        """Refund a customer receipt. The single owner of that decision.

        Reverses the ledger, gives the invoices their balance back, lets the
        paid-status owner re-derive each verdict, records who refunded what and
        why, and settles the payment's terminal status.

        Args:
            db: Database session.
            organization_id: Organization scope.
            payment_id: The receipt the money is going back out of.
            amount: Cash being returned. ``None`` means the whole receipt.
                A materially smaller amount is REFUSED — see below.
            reason: Business reason, recorded on the GL reversal and the audit
                row. Callers pass the fully-formed sentence
                (``"Payment voided: ..."``) so the ledger reads the same as it
                did before this consolidation.
            refunded_by_user_id: Actor. Falls back to the payment's creator,
                which is what the unattended paths (sync, webhook) get.
            outcome_status: Terminal status to settle on. ``REVERSED`` for a
                refund, ``VOID`` for a void, ``BOUNCED`` for a bounce.
            idempotency_suffix: Distinguishes this refund's GL reversal from
                every other kind of reversal in the ledger. ``void`` and
                ``bounce`` reproduce the pre-ADR-0008 keys byte for byte.
            allowed_from: Optional whitelist of source statuses, for callers
                that are narrower than "anything not already terminal".
            credit_note_id: Recorded in the audit row when the refund was
                issued alongside a credit note, so the two stop being
                disconnected facts.

        Returns:
            The payment, in its terminal status.

        Raises:
            NotFoundError: Payment not found in this organization.
            ValidationError: Refused source status, or a PARTIAL refund —
                which this repository cannot represent (``create_reversal``
                reverses a whole journal and there is no Refund aggregate to
                hold a second one). ADR-0008 states the aggregate as a
                follow-up; silently reversing the whole receipt instead would
                be a cash error.
            RefundReversalError: The GL reversal failed. Nothing was changed.

        Idempotent: a second call against a payment already in
        ``outcome_status`` returns it untouched — no ledger row, no allocation
        movement, no audit noise.
        """
        org_id = coerce_uuid(organization_id)
        pay_id = coerce_uuid(payment_id)

        payment = db.get(CustomerPayment, pay_id)
        if not payment or payment.organization_id != org_id:
            raise NotFoundError("Payment not found")

        # Idempotency first: the sync adapter and the webhook both re-present
        # the same refund, and neither should produce a second ledger row.
        if payment.status == outcome_status:
            logger.info(
                "Payment %s is already %s — refund request is a no-op",
                pay_id,
                outcome_status.value,
            )
            return payment

        if allowed_from is not None and payment.status not in allowed_from:
            raise ValidationError(
                f"Cannot settle payment as '{outcome_status.value}' "
                f"from status '{payment.status.value}'"
            )
        if payment.status in REFUND_TERMINAL_STATUSES:
            raise ValidationError(
                f"Payment is already settled as '{payment.status.value}'"
            )

        actor_id = (
            coerce_uuid(refunded_by_user_id)
            if refunded_by_user_id is not None
            else payment.created_by_user_id
        )

        # A refund is measured against the cash that actually arrived. Dust is
        # the paid-status owner's threshold, shared so "too small to matter"
        # cannot drift apart between the two decisions.
        refunded_amount = payment.amount if amount is None else amount
        if amount is not None and (payment.amount - amount) > PAYMENT_DUST:
            raise ValidationError(
                f"Partial refunds are not supported: {amount} of "
                f"{payment.amount} on payment {pay_id}. A partial refund needs "
                "a first-class Refund record (stated ADR-0008 follow-up); "
                "reversing the whole receipt instead would misstate cash."
            )

        # Only a CLEARED receipt has reached the ledger and the invoices.
        was_cleared = payment.status == PaymentStatus.CLEARED

        # 1. The ledger leg goes FIRST, and its failure is fatal to the whole
        #    refund. Reversing the allocations first (what void/bounce used to
        #    do) means a failed GL reversal leaves the invoices credited and
        #    the ledger still holding the cash.
        if was_cleared and payment.journal_entry_id:
            CustomerPaymentService._reverse_payment_journal(
                db,
                org_id,
                payment,
                actor_id=actor_id,
                reason=reason,
                idempotency_suffix=idempotency_suffix,
            )
            _reverse_vat_cash_basis_for_payment(
                db,
                org_id,
                payment,
                actor_id,
                reason,
                idempotency_suffix,
            )

        # 2. Subledger: hand each invoice its balance back and let the
        #    paid-status owner (ADR-protected) say what that makes it.
        if was_cleared:
            allocations = list(
                db.scalars(
                    select(PaymentAllocation).where(
                        PaymentAllocation.payment_id == pay_id
                    )
                ).all()
            )
            for alloc in allocations:
                invoice = db.get(Invoice, alloc.invoice_id)
                if invoice:
                    invoice.amount_paid -= alloc.allocated_amount
                    apply_payment_status(invoice)

        # 3. Terminal status, and the durable answer to "by whom, why, how
        #    much". Until a Refund aggregate exists (ADR-0008 follow-up) this
        #    audit row plus the refund-marked reversal journal ARE the record.
        old_status = payment.status.value
        payment.status = outcome_status

        fire_audit_event(
            db=db,
            organization_id=org_id,
            table_schema="ar",
            table_name="customer_payment",
            record_id=str(pay_id),
            action=AuditAction.UPDATE,
            old_values={"status": old_status},
            new_values={
                "status": outcome_status.value,
                "refunded_amount": str(refunded_amount),
                "credit_note_id": str(credit_note_id) if credit_note_id else None,
            },
            user_id=actor_id,
            reason=reason,
        )

        db.flush()

        logger.info(
            "Refunded payment %s: %s -> %s (%s)",
            pay_id,
            old_status,
            outcome_status.value,
            reason,
        )
        return payment

    @staticmethod
    def _reverse_payment_journal(
        db: Session,
        org_id: UUID,
        payment: CustomerPayment,
        *,
        actor_id: UUID,
        reason: str,
        idempotency_suffix: str,
    ) -> UUID | None:
        """Reverse a refunded receipt's GL journal, or raise having done nothing.

        The reversal is marked with ``idempotency_suffix`` so a refund reversal
        is distinguishable in the ledger from an FX revaluation or a
        data-health correction — ``ReversalService`` owns HOW a journal is
        reversed and is deliberately not told WHY by anything but this string.
        """
        from app.models.finance.gl.journal_entry import JournalEntry
        from app.services.finance.gl.reversal import ReversalService

        journal_id = payment.journal_entry_id
        if journal_id is None:  # pragma: no cover — guarded by the caller
            return None

        # Already reversed (a retry that got past the status check) is a
        # success, not a second reversal.
        original = db.get(JournalEntry, journal_id)
        existing_reversal: UUID | None = (
            original.reversal_journal_id if original is not None else None
        )
        if existing_reversal:
            logger.info(
                "Journal %s for payment %s was already reversed (%s)",
                journal_id,
                payment.payment_id,
                existing_reversal,
            )
            return existing_reversal

        try:
            result = ReversalService.create_reversal(
                db=db,
                organization_id=org_id,
                original_journal_id=journal_id,
                reversal_date=date.today(),
                created_by_user_id=actor_id,
                reason=reason,
                auto_post=True,
                idempotency_key=(
                    f"{org_id}:AR:PAY:{payment.payment_id}"
                    f":{idempotency_suffix}-reversal:v1"
                ),
            )
        except Exception as exc:
            logger.exception("GL reversal errored for payment %s", payment.payment_id)
            raise RefundReversalError(
                f"Could not reverse the GL journal for payment "
                f"{payment.payment_id}: {exc}"
            ) from exc

        if not getattr(result, "success", False):
            raise RefundReversalError(
                f"Could not reverse the GL journal for payment "
                f"{payment.payment_id}: {getattr(result, 'message', 'unknown')}"
            )

        logger.info(
            "Created GL reversal journal %s for payment %s (%s)",
            result.reversal_journal_id,
            payment.payment_id,
            idempotency_suffix,
        )
        return result.reversal_journal_id

    @staticmethod
    def void_payment(
        db: Session,
        organization_id: UUID,
        payment_id: UUID,
        voided_by_user_id: UUID,
        reason: str,
    ) -> CustomerPayment:
        """Void a payment — a refund decision whose reason is "voided".

        A thin caller of :meth:`refund_payment` (ADR-0008), not a second
        implementation of it. The GL reason and idempotency key it produces are
        byte-identical to the ones this method wrote before the consolidation,
        so no already-reversed production journal changes identity.

        Two behaviours changed on purpose: voiding an already-VOID payment is
        now a no-op rather than a `ValidationError`, and a failed GL reversal
        now refuses the void instead of completing it over a ledger that still
        holds the cash.
        """
        return CustomerPaymentService.refund_payment(
            db,
            organization_id,
            payment_id,
            reason=f"Payment voided: {reason}",
            refunded_by_user_id=voided_by_user_id,
            outcome_status=PaymentStatus.VOID,
            idempotency_suffix="void",
        )

    @staticmethod
    def mark_bounced(
        db: Session,
        organization_id: UUID,
        payment_id: UUID,
        reason: str,
    ) -> CustomerPayment:
        """Mark a payment as bounced — a refund decision whose reason is "bounced".

        A thin caller of :meth:`refund_payment` (ADR-0008). ``allowed_from``
        preserves this method's narrower precondition: a bounce is only
        meaningful for a payment that was pending or cleared.
        """
        return CustomerPaymentService.refund_payment(
            db,
            organization_id,
            payment_id,
            reason=f"Payment bounced: {reason}",
            outcome_status=PaymentStatus.BOUNCED,
            idempotency_suffix="bounce",
            allowed_from=frozenset({PaymentStatus.PENDING, PaymentStatus.CLEARED}),
        )

    @staticmethod
    def update_payment(
        db: Session,
        organization_id: UUID,
        payment_id: UUID,
        input: CustomerPaymentInput,
        updated_by_user_id: UUID,
    ) -> CustomerPayment:
        """
        Update an existing customer payment receipt.

        Only PENDING payments can be updated.

        Args:
            db: Database session
            organization_id: Organization scope
            payment_id: Payment to update
            input: Updated payment data
            updated_by_user_id: User making the update

        Returns:
            Updated CustomerPayment
        """
        org_id = coerce_uuid(organization_id)
        pay_id = coerce_uuid(payment_id)
        coerce_uuid(updated_by_user_id)
        customer_id = coerce_uuid(input.customer_id)

        payment = db.get(CustomerPayment, pay_id)
        if not payment or payment.organization_id != org_id:
            raise NotFoundError("Payment not found")

        if payment.status != PaymentStatus.PENDING:
            raise ValidationError(
                f"Cannot edit payment with status '{payment.status.value}'. Only PENDING payments can be edited."
            )

        # Validate customer
        customer = db.get(Customer, customer_id)
        if not customer or customer.organization_id != org_id:
            raise NotFoundError("Customer not found")

        if not customer.is_active:
            raise ValidationError("Customer is not active")

        # Consolidated (reseller) payment: resolve the account family and require
        # a single shared AR control account, so the AR-control credit posting
        # stays GL-correct when paying sub-account invoices.
        family_ids: set[UUID] = {customer_id}
        if input.consolidated:
            from app.services.finance.ar.customer_family import CustomerFamilyResolver

            family_ids = set(CustomerFamilyResolver(db).family_ids(org_id, customer_id))
            distinct_controls = set(
                db.scalars(
                    select(Customer.ar_control_account_id)
                    .where(Customer.customer_id.in_(family_ids))
                    .distinct()
                ).all()
            )
            if distinct_controls != {customer.ar_control_account_id}:
                raise ValidationError(
                    "Consolidated payment requires the whole account family to "
                    "share one AR control account"
                )

        net_amount, gross_amount, wht_amount = _resolve_receipt_amounts(input)

        # Validate allocations
        if input.allocations:
            allocation_total = sum(a.amount for a in input.allocations)
            if allocation_total > gross_amount:
                raise ValidationError("Allocation total exceeds gross payment amount")

            for alloc in input.allocations:
                # Lock the invoice row to prevent over-allocation race condition.
                # Select only the PK so the FOR UPDATE does not pull in the
                # lazy="joined" customer (Postgres rejects FOR UPDATE on the
                # nullable side of that outer join).
                db.execute(
                    select(Invoice.invoice_id)
                    .where(Invoice.invoice_id == coerce_uuid(alloc.invoice_id))
                    .with_for_update()
                )
                invoice = db.get(Invoice, coerce_uuid(alloc.invoice_id))
                if not invoice or invoice.organization_id != org_id:
                    raise NotFoundError(f"Invoice {alloc.invoice_id} not found")
                if invoice.customer_id not in family_ids:
                    raise ValidationError(
                        f"Invoice {invoice.invoice_number} belongs to different customer"
                    )
                payable_statuses = [
                    InvoiceStatus.POSTED,
                    InvoiceStatus.PARTIALLY_PAID,
                    InvoiceStatus.OVERDUE,
                ]
                if invoice.status not in payable_statuses:
                    raise ValidationError(
                        f"Invoice {invoice.invoice_number} is not payable"
                    )
                if alloc.amount > invoice.balance_due:
                    raise ValidationError(
                        f"Allocation exceeds balance due on {invoice.invoice_number}"
                    )

        # Validate WHT code if provided
        validated_wht_code_id: UUID | None = None
        if input.wht_code_id:
            wht_code = db.get(TaxCode, coerce_uuid(input.wht_code_id))
            if not wht_code or wht_code.organization_id != org_id:
                raise NotFoundError("WHT tax code not found")
            if wht_code.tax_type != TaxType.WITHHOLDING:
                raise ValidationError("Selected tax code is not a WITHHOLDING tax code")
            validated_wht_code_id = wht_code.tax_code_id

        if wht_amount > Decimal("0") and not validated_wht_code_id:
            raise ValidationError(
                "WHT tax code is required when WHT amount is specified"
            )

        # Calculate functional currency amount
        exchange_rate = input.exchange_rate or Decimal("1.0")
        functional_amount = net_amount * exchange_rate

        # Update payment fields
        payment.customer_id = customer_id
        payment.payment_date = input.payment_date
        payment.payment_method = input.payment_method
        payment.currency_code = input.currency_code
        payment.gross_amount = gross_amount
        payment.amount = net_amount
        payment.wht_code_id = validated_wht_code_id
        payment.wht_amount = wht_amount
        payment.wht_certificate_number = input.wht_certificate_number
        payment.exchange_rate = exchange_rate
        payment.functional_currency_amount = functional_amount
        payment.bank_account_id = input.bank_account_id
        payment.reference = input.reference
        payment.description = input.description

        # Delete existing allocations and recreate
        db.execute(
            delete(PaymentAllocation).where(PaymentAllocation.payment_id == pay_id)
        )

        # Create new allocations
        for alloc in input.allocations:
            allocation = PaymentAllocation(
                payment_id=pay_id,
                invoice_id=coerce_uuid(alloc.invoice_id),
                allocated_amount=alloc.amount,
                allocation_date=payment.payment_date,
            )
            db.add(allocation)

        db.flush()

        return payment

    @staticmethod
    def get(
        db: Session,
        payment_id: str,
        organization_id: str | UUID | None = None,
    ) -> CustomerPayment:
        """Get a payment by ID.

        Args:
            db: Database session.
            payment_id: The payment UUID.
            organization_id: Organization UUID for tenant isolation.
        """
        payment = db.get(CustomerPayment, coerce_uuid(payment_id))
        if not payment:
            raise NotFoundError("Payment not found")
        if organization_id is not None and str(payment.organization_id) != str(
            coerce_uuid(organization_id)
        ):
            raise NotFoundError("Payment not found")
        return payment

    @staticmethod
    def get_payment_allocations(
        db: Session,
        organization_id: UUID,
        payment_id: UUID,
    ) -> list[PaymentAllocation]:
        """Get allocations for a payment."""
        org_id = coerce_uuid(organization_id)
        pay_id = coerce_uuid(payment_id)

        payment = db.get(CustomerPayment, pay_id)
        if not payment or payment.organization_id != org_id:
            raise NotFoundError("Payment not found")

        return list(
            db.scalars(
                select(PaymentAllocation).where(PaymentAllocation.payment_id == pay_id)
            ).all()
        )

    @staticmethod
    def delete_receipt(
        db: Session,
        organization_id: UUID,
        receipt_id: UUID,
    ) -> None:
        """Delete a receipt (PENDING only)."""
        org_id = coerce_uuid(organization_id)
        pay_id = coerce_uuid(receipt_id)

        payment = db.get(CustomerPayment, pay_id)
        if not payment or payment.organization_id != org_id:
            raise NotFoundError("Receipt not found")

        if payment.status != PaymentStatus.PENDING:
            raise ValidationError(
                f"Cannot delete receipt with status '{payment.status.value}'. "
                "Only draft receipts can be deleted."
            )

        db.execute(
            delete(PaymentAllocation).where(PaymentAllocation.payment_id == pay_id)
        )
        db.delete(payment)
        db.flush()

    @staticmethod
    def list(
        db: Session,
        organization_id: str,
        customer_id: str | None = None,
        status: PaymentStatus | None = None,
        payment_method: PaymentMethod | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CustomerPayment]:
        """List payments with optional filters."""
        stmt = select(CustomerPayment).where(
            CustomerPayment.organization_id == coerce_uuid(organization_id)
        )

        if customer_id:
            stmt = stmt.where(CustomerPayment.customer_id == coerce_uuid(customer_id))

        if status:
            stmt = stmt.where(CustomerPayment.status == status)

        if payment_method:
            stmt = stmt.where(CustomerPayment.payment_method == payment_method)

        if from_date:
            stmt = stmt.where(CustomerPayment.payment_date >= from_date)

        if to_date:
            stmt = stmt.where(CustomerPayment.payment_date <= to_date)

        stmt = stmt.order_by(CustomerPayment.payment_date.desc())
        stmt = stmt.limit(limit).offset(offset)
        return list(db.scalars(stmt).all())


# Module-level singleton instance
customer_payment_service = CustomerPaymentService()
