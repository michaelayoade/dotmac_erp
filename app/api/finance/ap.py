"""
AP API Router.

Accounts Payable API endpoints for suppliers, invoices, and payments.
"""

import logging
from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id, require_tenant_auth
from app.api.finance.utils import parse_enum
from app.db import SessionLocal
from app.models.finance.ap.purchase_order import POStatus
from app.models.finance.ap.supplier import SupplierType
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoiceStatus,
    SupplierInvoiceType,
)
from app.models.finance.ap.supplier_payment import APPaymentMethod, APPaymentStatus
from app.schemas.finance.ap import (
    APAgingReportRead,
    APInvoiceCreate,
    APInvoiceRead,
    APPaymentCreate,
    APPaymentRead,
    BankFileResultRead,
    GRCreate,
    GRRead,
    PaymentBatchCreate,
    PaymentBatchRead,
    POCreate,
    PORead,
    SupplierCreate,
    SupplierRead,
    SupplierUpdate,
)
from app.schemas.finance.common import ListResponse, PostingResultSchema
from app.services.auth_dependencies import require_tenant_permission
from app.services.finance.ap import (
    InvoiceLineInput,
    PaymentAllocationInput,
    SupplierInput,
    SupplierInvoiceInput,
    SupplierPaymentInput,
    ap_aging_service,
    ap_posting_adapter,
    supplier_invoice_service,
    supplier_payment_service,
    supplier_service,
)

router = APIRouter(
    prefix="/ap",
    tags=["accounts-payable"],
    dependencies=[Depends(require_tenant_auth)],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =============================================================================
# Suppliers
# =============================================================================


@router.post(
    "/suppliers", response_model=SupplierRead, status_code=status.HTTP_201_CREATED
)
def create_supplier(
    payload: SupplierCreate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:suppliers:create")),
    db: Session = Depends(get_db),
):
    """Create a new supplier."""
    # Thin wrapper: pass template-friendly names directly to service
    # Service handles field mapping internally
    input_data = SupplierInput(
        supplier_code=payload.supplier_code,
        supplier_type=parse_enum(SupplierType, payload.supplier_type)
        or SupplierType.VENDOR,
        supplier_name=payload.supplier_name,  # Template-friendly name
        trading_name=payload.trading_name,
        tax_id=payload.tax_id,  # Template-friendly name
        payment_terms_days=payload.payment_terms_days,
        currency_code=payload.currency_code,
        default_expense_account_id=payload.default_expense_account_id,
        default_payable_account_id=payload.default_payable_account_id,  # Template-friendly name
    )
    return supplier_service.create_supplier(db, organization_id, input_data)


@router.get("/suppliers/{supplier_id}", response_model=SupplierRead)
def get_supplier(
    supplier_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:suppliers:read")),
    db: Session = Depends(get_db),
):
    """Get a supplier by ID."""
    return supplier_service.get(db, organization_id, str(supplier_id))


@router.get("/suppliers", response_model=ListResponse[SupplierRead])
def list_suppliers(
    organization_id: UUID = Depends(require_organization_id),
    is_active: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("ap:suppliers:read")),
    db: Session = Depends(get_db),
):
    """List suppliers with filters."""
    suppliers = supplier_service.list(
        db=db,
        organization_id=str(organization_id),
        is_active=is_active,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=suppliers,
        count=len(suppliers),
        limit=limit,
        offset=offset,
    )


@router.patch("/suppliers/{supplier_id}", response_model=SupplierRead)
def update_supplier(
    supplier_id: UUID,
    payload: SupplierUpdate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:suppliers:update")),
    db: Session = Depends(get_db),
):
    """Update a supplier (partial update)."""
    # Convert payload to dict, excluding unset fields
    update_data = payload.model_dump(exclude_unset=True)
    return supplier_service.partial_update_supplier(
        db=db,
        organization_id=organization_id,
        supplier_id=supplier_id,
        update_data=update_data,
    )


# =============================================================================
# AP Invoices
# =============================================================================


@router.post(
    "/invoices", response_model=APInvoiceRead, status_code=status.HTTP_201_CREATED
)
def create_ap_invoice(
    payload: APInvoiceCreate,
    organization_id: UUID = Depends(require_organization_id),
    created_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("ap:invoices:create")),
    db: Session = Depends(get_db),
):
    """Create a new AP invoice."""
    lines = [
        InvoiceLineInput(
            expense_account_id=line.expense_account_id,
            description=line.description,
            quantity=line.quantity,
            unit_price=line.unit_price,
            tax_code_id=line.tax_code_id,
            cost_center_id=line.cost_center_id,
            project_id=line.project_id,
        )
        for line in payload.lines
    ]

    input_data = SupplierInvoiceInput(
        supplier_id=payload.supplier_id,
        invoice_type=parse_enum(SupplierInvoiceType, payload.invoice_type)
        or SupplierInvoiceType.STANDARD,
        supplier_invoice_number=payload.invoice_number,
        invoice_date=payload.invoice_date,
        received_date=payload.received_date or payload.invoice_date,
        due_date=payload.due_date,
        currency_code=payload.currency_code,
        lines=lines,
    )

    return supplier_invoice_service.create_invoice(
        db=db,
        organization_id=organization_id,
        input=input_data,
        created_by_user_id=created_by_user_id,
    )


@router.get("/invoices/{invoice_id}", response_model=APInvoiceRead)
def get_ap_invoice(
    invoice_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:invoices:read")),
    db: Session = Depends(get_db),
):
    """Get an AP invoice by ID."""
    return supplier_invoice_service.get(db, str(invoice_id), organization_id)


@router.get("/invoices", response_model=ListResponse[APInvoiceRead])
def list_ap_invoices(
    organization_id: UUID = Depends(require_organization_id),
    supplier_id: UUID | None = None,
    status: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("ap:invoices:read")),
    db: Session = Depends(get_db),
):
    """List AP invoices with filters."""
    status_value = None
    if status:
        try:
            status_value = SupplierInvoiceStatus(status)
        except ValueError:
            status_value = None
    invoices = supplier_invoice_service.list(
        db=db,
        organization_id=str(organization_id),
        supplier_id=str(supplier_id) if supplier_id else None,
        status=status_value,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=invoices,
        count=len(invoices),
        limit=limit,
        offset=offset,
    )


@router.post("/invoices/{invoice_id}/submit", response_model=APInvoiceRead)
def submit_ap_invoice(
    invoice_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    submitted_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("ap:invoices:submit")),
    db: Session = Depends(get_db),
):
    """Submit an AP invoice for approval."""
    return supplier_invoice_service.submit_invoice(
        db=db,
        organization_id=organization_id,
        invoice_id=invoice_id,
        submitted_by_user_id=submitted_by_user_id,
    )


@router.post("/invoices/{invoice_id}/approve", response_model=APInvoiceRead)
def approve_ap_invoice(
    invoice_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    approved_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("ap:invoices:approve")),
    db: Session = Depends(get_db),
):
    """Approve an AP invoice."""
    return supplier_invoice_service.approve_invoice(
        db=db,
        organization_id=organization_id,
        invoice_id=invoice_id,
        approved_by_user_id=approved_by_user_id,
    )


@router.post("/invoices/{invoice_id}/post", response_model=PostingResultSchema)
def post_ap_invoice(
    invoice_id: UUID,
    posting_date: date = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    posted_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("ap:invoices:post")),
    db: Session = Depends(get_db),
):
    """Post an AP invoice to the GL."""
    result = ap_posting_adapter.post_invoice(
        db=db,
        organization_id=organization_id,
        invoice_id=invoice_id,
        posting_date=posting_date,
        posted_by_user_id=posted_by_user_id,
    )
    return PostingResultSchema(
        success=result.success,
        journal_entry_id=result.journal_entry_id,
        entry_number=None,
        message=result.message,
    )


# =============================================================================
# AP Payments
# =============================================================================


@router.post(
    "/payments", response_model=APPaymentRead, status_code=status.HTTP_201_CREATED
)
def create_ap_payment(
    payload: APPaymentCreate,
    organization_id: UUID = Depends(require_organization_id),
    created_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("ap:payments:create")),
    db: Session = Depends(get_db),
):
    """Create a new AP payment."""
    allocations = [
        PaymentAllocationInput(
            invoice_id=alloc.invoice_id,
            amount=alloc.amount,
        )
        for alloc in payload.allocations
    ]
    total_amount = sum((alloc.amount for alloc in allocations), Decimal("0"))

    try:
        payment_method = APPaymentMethod(payload.payment_method)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid payment method") from exc

    input_data = SupplierPaymentInput(
        supplier_id=payload.supplier_id,
        payment_date=payload.payment_date,
        payment_method=payment_method,
        bank_account_id=payload.bank_account_id,
        currency_code=payload.currency_code,
        amount=total_amount,
        reference=payload.reference_number,
        allocations=allocations,
    )

    return supplier_payment_service.create_payment(
        db=db,
        organization_id=organization_id,
        input=input_data,
        created_by_user_id=created_by_user_id,
    )


@router.get("/payments/{payment_id}", response_model=APPaymentRead)
def get_ap_payment(
    payment_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:payments:read")),
    db: Session = Depends(get_db),
):
    """Get an AP payment by ID."""
    return supplier_payment_service.get(db, str(payment_id), organization_id)


@router.get("/payments", response_model=ListResponse[APPaymentRead])
def list_ap_payments(
    organization_id: UUID = Depends(require_organization_id),
    supplier_id: UUID | None = None,
    status: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("ap:payments:read")),
    db: Session = Depends(get_db),
):
    """List AP payments with filters."""
    status_value = None
    if status:
        try:
            status_value = APPaymentStatus(status)
        except ValueError:
            status_value = None
    payments = supplier_payment_service.list(
        db=db,
        organization_id=str(organization_id),
        supplier_id=str(supplier_id) if supplier_id else None,
        status=status_value,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=payments,
        count=len(payments),
        limit=limit,
        offset=offset,
    )


@router.post("/payments/{payment_id}/post", response_model=PostingResultSchema)
def post_ap_payment(
    payment_id: UUID,
    posting_date: date = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    posted_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("ap:payments:post")),
    db: Session = Depends(get_db),
):
    """Post an AP payment to the GL."""
    result = ap_posting_adapter.post_payment(
        db=db,
        organization_id=organization_id,
        payment_id=payment_id,
        posting_date=posting_date,
        posted_by_user_id=posted_by_user_id,
    )
    return PostingResultSchema(
        success=result.success,
        journal_entry_id=result.journal_entry_id,
        entry_number=None,
        message=result.message,
    )


# =============================================================================
# AP Aging
# =============================================================================


@router.get("/aging", response_model=APAgingReportRead)
def get_ap_aging(
    organization_id: UUID = Depends(require_organization_id),
    as_of_date: date = Query(...),
    supplier_id: UUID | None = None,
    auth: dict = Depends(require_tenant_permission("ap:aging:read")),
    db: Session = Depends(get_db),
):
    """Get AP aging report."""
    org_summary = ap_aging_service.calculate_organization_aging(
        db=db,
        organization_id=organization_id,
        as_of_date=as_of_date,
    )

    if supplier_id:
        supplier_summaries = [
            ap_aging_service.calculate_supplier_aging(
                db=db,
                organization_id=organization_id,
                supplier_id=supplier_id,
                as_of_date=as_of_date,
            )
        ]
    else:
        supplier_summaries = ap_aging_service.get_aging_by_supplier(
            db=db,
            organization_id=organization_id,
            as_of_date=as_of_date,
        )

    buckets = [
        {
            "supplier_id": summary.supplier_id,
            "supplier_code": summary.supplier_code,
            "supplier_name": summary.supplier_name,
            "current": summary.current,
            "days_1_30": summary.current,
            "days_31_60": summary.days_31_60,
            "days_61_90": summary.days_61_90,
            "over_90": summary.over_90,
            "total": summary.total_outstanding,
        }
        for summary in supplier_summaries
    ]

    totals = {
        "supplier_id": UUID(int=0),
        "supplier_code": "TOTAL",
        "supplier_name": "Total",
        "current": org_summary.current,
        "days_1_30": org_summary.current,
        "days_31_60": org_summary.days_31_60,
        "days_61_90": org_summary.days_61_90,
        "over_90": org_summary.over_90,
        "total": org_summary.total_outstanding,
    }

    return {
        "as_of_date": org_summary.as_of_date,
        "currency_code": org_summary.currency_code,
        "buckets": buckets,
        "totals": totals,
    }


# =============================================================================
# Purchase Orders
# =============================================================================

from app.services.finance.ap import (  # noqa: E402
    POLineInput,
    PurchaseOrderInput,
    purchase_order_service,
)


@router.post(
    "/purchase-orders", response_model=PORead, status_code=status.HTTP_201_CREATED
)
def create_purchase_order(
    payload: POCreate,
    organization_id: UUID = Depends(require_organization_id),
    created_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("ap:purchase_orders:create")),
    db: Session = Depends(get_db),
):
    """Create a new purchase order."""
    lines = [
        POLineInput(
            item_id=line.item_id,
            description=line.description,
            quantity_ordered=line.quantity,
            unit_price=line.unit_price,
            expense_account_id=line.expense_account_id,
        )
        for line in payload.lines
    ]
    input_data = PurchaseOrderInput(
        supplier_id=payload.supplier_id,
        po_date=payload.po_date,
        expected_delivery_date=payload.expected_delivery_date,
        currency_code=payload.currency_code,
        terms_and_conditions=payload.description,
        lines=lines,
    )
    return purchase_order_service.create_po(
        db, organization_id, input_data, created_by_user_id
    )


@router.get("/purchase-orders/{po_id}", response_model=PORead)
def get_purchase_order(
    po_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:purchase_orders:read")),
    db: Session = Depends(get_db),
):
    """Get a purchase order by ID."""
    return purchase_order_service.get(db, str(po_id), organization_id)


@router.get("/purchase-orders", response_model=ListResponse[PORead])
def list_purchase_orders(
    organization_id: UUID = Depends(require_organization_id),
    supplier_id: UUID | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("ap:purchase_orders:read")),
    db: Session = Depends(get_db),
):
    """List purchase orders with filters."""
    status_value = None
    if status:
        try:
            status_value = POStatus(status)
        except ValueError:
            status_value = None
    pos = purchase_order_service.list(
        db=db,
        organization_id=str(organization_id),
        supplier_id=str(supplier_id) if supplier_id else None,
        status=status_value,
        limit=limit,
        offset=offset,
    )
    return ListResponse(items=pos, count=len(pos), limit=limit, offset=offset)


@router.post("/purchase-orders/{po_id}/submit", response_model=PORead)
def submit_po_for_approval(
    po_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    submitted_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("ap:purchase_orders:submit")),
    db: Session = Depends(get_db),
):
    """Submit PO for approval."""
    return purchase_order_service.submit_for_approval(
        db, organization_id, po_id, submitted_by_user_id
    )


@router.post("/purchase-orders/{po_id}/approve", response_model=PORead)
def approve_purchase_order(
    po_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    approved_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("ap:purchase_orders:approve")),
    db: Session = Depends(get_db),
):
    """Approve a purchase order."""
    return purchase_order_service.approve_po(
        db, organization_id, po_id, approved_by_user_id
    )


@router.post("/purchase-orders/{po_id}/cancel", response_model=PORead)
def cancel_purchase_order(
    po_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:purchase_orders:void")),
    db: Session = Depends(get_db),
):
    """Cancel a purchase order."""
    return purchase_order_service.cancel_po(db, organization_id, po_id)


# =============================================================================
# Goods Receipts
# =============================================================================

from app.models.finance.ap.goods_receipt import ReceiptStatus  # noqa: E402
from app.models.finance.ap.payment_batch import (  # noqa: E402  # pragma: allowlist secret
    APBatchStatus,
)
from app.services.finance.ap import (  # noqa: E402
    GoodsReceiptInput,
    GRLineInput,
    goods_receipt_service,
)


@router.post(
    "/goods-receipts", response_model=GRRead, status_code=status.HTTP_201_CREATED
)
def create_goods_receipt(
    payload: GRCreate,
    organization_id: UUID = Depends(require_organization_id),
    received_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("ap:goods_receipts:create")),
    db: Session = Depends(get_db),
):
    """Create a goods receipt against a PO."""
    lines: list[GRLineInput] = []
    for line in payload.lines:
        if not line.po_line_id:
            raise HTTPException(
                status_code=400, detail="po_line_id required for each line"
            )
        lines.append(
            GRLineInput(
                po_line_id=line.po_line_id,
                quantity_received=line.quantity_received,
                location_id=line.warehouse_id,
            )
        )
    input_data = GoodsReceiptInput(
        po_id=payload.po_id,
        receipt_date=payload.receipt_date,
        notes=payload.notes,
        lines=lines,
    )
    return goods_receipt_service.create_receipt(
        db, organization_id, input_data, received_by_user_id
    )


@router.get("/goods-receipts/{receipt_id}", response_model=GRRead)
def get_goods_receipt(
    receipt_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:goods_receipts:read")),
    db: Session = Depends(get_db),
):
    """Get a goods receipt by ID."""
    return goods_receipt_service.get(db, str(receipt_id), organization_id)


@router.get("/goods-receipts", response_model=ListResponse[GRRead])
def list_goods_receipts(
    organization_id: UUID = Depends(require_organization_id),
    po_id: UUID | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("ap:goods_receipts:read")),
    db: Session = Depends(get_db),
):
    """List goods receipts with filters."""
    status_value = None
    if status:
        try:
            status_value = ReceiptStatus(status)
        except ValueError:
            status_value = None
    receipts = goods_receipt_service.list(
        db=db,
        organization_id=str(organization_id),
        po_id=str(po_id) if po_id else None,
        status=status_value,
        limit=limit,
        offset=offset,
    )
    return ListResponse(items=receipts, count=len(receipts), limit=limit, offset=offset)


@router.post("/goods-receipts/{receipt_id}/inspect", response_model=GRRead)
def start_gr_inspection(
    receipt_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:goods_receipts:update")),
    db: Session = Depends(get_db),
):
    """Start inspection for a goods receipt."""
    return goods_receipt_service.start_inspection(db, organization_id, receipt_id)


@router.post("/goods-receipts/{receipt_id}/accept", response_model=GRRead)
def accept_goods_receipt(
    receipt_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:goods_receipts:approve")),
    db: Session = Depends(get_db),
):
    """Accept all items in a goods receipt."""
    return goods_receipt_service.accept_all(db, organization_id, receipt_id)


# =============================================================================
# Payment Batches
# =============================================================================

from app.services.finance.ap import (  # noqa: E402
    PaymentBatchInput,
    payment_batch_service,
)

logger = logging.getLogger(__name__)


@router.post(
    "/payment-batches",
    response_model=PaymentBatchRead,
    status_code=status.HTTP_201_CREATED,
)
def create_payment_batch(
    payload: PaymentBatchCreate,
    organization_id: UUID = Depends(require_organization_id),
    created_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("ap:payment_batches:create")),
    db: Session = Depends(get_db),
):
    """Create a new payment batch."""
    input_data = PaymentBatchInput(
        batch_date=payload.payment_date,
        bank_account_id=payload.bank_account_id,
        payment_method=payload.payment_method,
        currency_code=None,  # Resolved by service from bank account
        payments=[],
    )
    return payment_batch_service.create_batch(
        db, organization_id, input_data, created_by_user_id
    )


@router.get("/payment-batches/{batch_id}", response_model=PaymentBatchRead)
def get_payment_batch(
    batch_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:payment_batches:read")),
    db: Session = Depends(get_db),
):
    """Get a payment batch by ID."""
    return payment_batch_service.get(db, str(batch_id), organization_id)


@router.get("/payment-batches", response_model=ListResponse[PaymentBatchRead])
def list_payment_batches(
    organization_id: UUID = Depends(require_organization_id),
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("ap:payment_batches:read")),
    db: Session = Depends(get_db),
):
    """List payment batches with filters."""
    status_value = None
    if status:
        try:
            status_value = APBatchStatus(status)
        except ValueError:
            status_value = None
    batches = payment_batch_service.list(
        db=db,
        organization_id=str(organization_id),
        status=status_value,
        limit=limit,
        offset=offset,
    )
    return ListResponse(items=batches, count=len(batches), limit=limit, offset=offset)


@router.post(
    "/payment-batches/{batch_id}/add-payment/{payment_id}",
    response_model=PaymentBatchRead,
)
def add_payment_to_batch(
    batch_id: UUID,
    payment_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:payment_batches:update")),
    db: Session = Depends(get_db),
):
    """Add a payment to a batch."""
    return payment_batch_service.add_payment_to_batch(
        db, organization_id, batch_id, payment_id
    )


@router.post("/payment-batches/{batch_id}/approve", response_model=PaymentBatchRead)
def approve_payment_batch(
    batch_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    approved_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("ap:payment_batches:approve")),
    db: Session = Depends(get_db),
):
    """Approve a payment batch."""
    return payment_batch_service.approve_batch(
        db, organization_id, batch_id, approved_by_user_id
    )


@router.post("/payment-batches/{batch_id}/process", response_model=PaymentBatchRead)
def process_payment_batch(
    batch_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    processed_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("ap:payment_batches:process")),
    db: Session = Depends(get_db),
):
    """Process an approved payment batch."""
    return payment_batch_service.process_batch(
        db, organization_id, batch_id, processed_by_user_id
    )


@router.post(
    "/payment-batches/{batch_id}/generate-bank-file", response_model=BankFileResultRead
)
def generate_bank_file(
    batch_id: UUID,
    file_format: str = Query(default="NACHA"),
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("ap:payment_batches:export")),
    db: Session = Depends(get_db),
):
    """Generate bank file for a payment batch."""
    return payment_batch_service.generate_bank_file(
        db, organization_id, batch_id, file_format
    )
