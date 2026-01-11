"""
AP API Router.

Accounts Payable API endpoints for suppliers, invoices, and payments.
"""

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.schemas.ifrs.ap import (
    SupplierCreate,
    SupplierUpdate,
    SupplierRead,
    APInvoiceCreate,
    APInvoiceRead,
    APPaymentCreate,
    APPaymentRead,
    APAgingReportRead,
)
from app.schemas.ifrs.common import ListResponse, PostingResultSchema
from app.services.ifrs.ap import (
    supplier_service,
    supplier_invoice_service,
    supplier_payment_service,
    ap_posting_adapter,
    SupplierInput,
    SupplierInvoiceInput,
    InvoiceLineInput,
    SupplierPaymentInput,
    PaymentAllocationInput,
)


router = APIRouter(prefix="/ap", tags=["accounts-payable"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =============================================================================
# Suppliers
# =============================================================================

@router.post("/suppliers", response_model=SupplierRead, status_code=status.HTTP_201_CREATED)
def create_supplier(
    payload: SupplierCreate,
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Create a new supplier."""
    input_data = SupplierInput(
        supplier_code=payload.supplier_code,
        supplier_name=payload.supplier_name,
        tax_id=payload.tax_id,
        payment_terms_days=payload.payment_terms_days,
        currency_code=payload.currency_code,
        default_expense_account_id=payload.default_expense_account_id,
        default_payable_account_id=payload.default_payable_account_id,
    )
    return supplier_service.create_supplier(db, organization_id, input_data)


@router.get("/suppliers/{supplier_id}", response_model=SupplierRead)
def get_supplier(
    supplier_id: UUID,
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Get a supplier by ID."""
    return supplier_service.get(db, organization_id, str(supplier_id))


@router.get("/suppliers", response_model=ListResponse[SupplierRead])
def list_suppliers(
    organization_id: UUID = Query(...),
    is_active: Optional[bool] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
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
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Update a supplier."""
    return supplier_service.update_supplier(
        db=db,
        organization_id=organization_id,
        supplier_id=supplier_id,
        supplier_name=payload.supplier_name,
        payment_terms_days=payload.payment_terms_days,
        is_active=payload.is_active,
    )


# =============================================================================
# AP Invoices
# =============================================================================

@router.post("/invoices", response_model=APInvoiceRead, status_code=status.HTTP_201_CREATED)
def create_ap_invoice(
    payload: APInvoiceCreate,
    organization_id: UUID = Query(...),
    created_by_user_id: UUID = Query(...),
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
        invoice_number=payload.invoice_number,
        invoice_date=payload.invoice_date,
        due_date=payload.due_date,
        currency_code=payload.currency_code,
        description=payload.description,
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
    db: Session = Depends(get_db),
):
    """Get an AP invoice by ID."""
    return supplier_invoice_service.get(db, str(invoice_id))


@router.get("/invoices", response_model=ListResponse[APInvoiceRead])
def list_ap_invoices(
    organization_id: UUID = Query(...),
    supplier_id: Optional[UUID] = None,
    status: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List AP invoices with filters."""
    invoices = supplier_invoice_service.list(
        db=db,
        organization_id=str(organization_id),
        supplier_id=str(supplier_id) if supplier_id else None,
        status=status,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=invoices,
        count=len(invoices),
        limit=limit,
        offset=offset,
    )


@router.post("/invoices/{invoice_id}/approve", response_model=APInvoiceRead)
def approve_ap_invoice(
    invoice_id: UUID,
    organization_id: UUID = Query(...),
    approved_by_user_id: UUID = Query(...),
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
    organization_id: UUID = Query(...),
    posted_by_user_id: UUID = Query(...),
    fiscal_period_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Post an AP invoice to the GL."""
    result = ap_posting_adapter.post_invoice(
        db=db,
        organization_id=organization_id,
        invoice_id=invoice_id,
        fiscal_period_id=fiscal_period_id,
        posting_date=posting_date,
        posted_by_user_id=posted_by_user_id,
    )
    return PostingResultSchema(
        success=result.success,
        journal_entry_id=result.journal_entry_id,
        entry_number=result.entry_number,
        message=result.message,
    )


# =============================================================================
# AP Payments
# =============================================================================

@router.post("/payments", response_model=APPaymentRead, status_code=status.HTTP_201_CREATED)
def create_ap_payment(
    payload: APPaymentCreate,
    organization_id: UUID = Query(...),
    created_by_user_id: UUID = Query(...),
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

    input_data = SupplierPaymentInput(
        supplier_id=payload.supplier_id,
        payment_date=payload.payment_date,
        payment_method=payload.payment_method,
        bank_account_id=payload.bank_account_id,
        currency_code=payload.currency_code,
        reference_number=payload.reference_number,
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
    db: Session = Depends(get_db),
):
    """Get an AP payment by ID."""
    return supplier_payment_service.get(db, str(payment_id))


@router.get("/payments", response_model=ListResponse[APPaymentRead])
def list_ap_payments(
    organization_id: UUID = Query(...),
    supplier_id: Optional[UUID] = None,
    status: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List AP payments with filters."""
    payments = supplier_payment_service.list(
        db=db,
        organization_id=str(organization_id),
        supplier_id=str(supplier_id) if supplier_id else None,
        status=status,
        start_date=start_date,
        end_date=end_date,
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
    organization_id: UUID = Query(...),
    posted_by_user_id: UUID = Query(...),
    fiscal_period_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Post an AP payment to the GL."""
    result = ap_posting_adapter.post_payment(
        db=db,
        organization_id=organization_id,
        payment_id=payment_id,
        fiscal_period_id=fiscal_period_id,
        posting_date=posting_date,
        posted_by_user_id=posted_by_user_id,
    )
    return PostingResultSchema(
        success=result.success,
        journal_entry_id=result.journal_entry_id,
        entry_number=result.entry_number,
        message=result.message,
    )


# =============================================================================
# AP Aging
# =============================================================================

@router.get("/aging", response_model=APAgingReportRead)
def get_ap_aging(
    organization_id: UUID = Query(...),
    as_of_date: date = Query(...),
    supplier_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
):
    """Get AP aging report."""
    return supplier_invoice_service.get_aging_report(
        db=db,
        organization_id=str(organization_id),
        as_of_date=as_of_date,
        supplier_id=str(supplier_id) if supplier_id else None,
    )


# =============================================================================
# Purchase Orders
# =============================================================================

from pydantic import BaseModel, ConfigDict, Field
from decimal import Decimal
from app.services.ifrs.ap import purchase_order_service, PurchaseOrderInput, POLineInput


class POLineCreate(BaseModel):
    """PO line input."""
    item_id: Optional[UUID] = None
    description: str
    quantity: Decimal
    unit_price: Decimal
    expense_account_id: Optional[UUID] = None


class POCreate(BaseModel):
    """Create PO request."""
    supplier_id: UUID
    po_date: date
    expected_delivery_date: Optional[date] = None
    currency_code: str = Field(max_length=3)
    description: Optional[str] = None
    lines: list[POLineCreate]


class PORead(BaseModel):
    """PO response."""
    model_config = ConfigDict(from_attributes=True)
    po_id: UUID
    organization_id: UUID
    supplier_id: UUID
    po_number: str
    po_date: date
    status: str
    currency_code: str
    total_amount: Decimal


@router.post("/purchase-orders", response_model=PORead, status_code=status.HTTP_201_CREATED)
def create_purchase_order(
    payload: POCreate,
    organization_id: UUID = Query(...),
    created_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Create a new purchase order."""
    lines = [
        POLineInput(
            item_id=line.item_id,
            description=line.description,
            quantity=line.quantity,
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
        description=payload.description,
        lines=lines,
    )
    return purchase_order_service.create_po(db, organization_id, input_data, created_by_user_id)


@router.get("/purchase-orders/{po_id}", response_model=PORead)
def get_purchase_order(po_id: UUID, db: Session = Depends(get_db)):
    """Get a purchase order by ID."""
    return purchase_order_service.get(db, str(po_id))


@router.get("/purchase-orders", response_model=ListResponse[PORead])
def list_purchase_orders(
    organization_id: UUID = Query(...),
    supplier_id: Optional[UUID] = None,
    status: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List purchase orders with filters."""
    pos = purchase_order_service.list(
        db=db,
        organization_id=str(organization_id),
        supplier_id=str(supplier_id) if supplier_id else None,
        status=status,
        limit=limit,
        offset=offset,
    )
    return ListResponse(items=pos, count=len(pos), limit=limit, offset=offset)


@router.post("/purchase-orders/{po_id}/submit", response_model=PORead)
def submit_po_for_approval(
    po_id: UUID,
    organization_id: UUID = Query(...),
    submitted_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Submit PO for approval."""
    return purchase_order_service.submit_for_approval(db, organization_id, po_id, submitted_by_user_id)


@router.post("/purchase-orders/{po_id}/approve", response_model=PORead)
def approve_purchase_order(
    po_id: UUID,
    organization_id: UUID = Query(...),
    approved_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Approve a purchase order."""
    return purchase_order_service.approve_po(db, organization_id, po_id, approved_by_user_id)


@router.post("/purchase-orders/{po_id}/cancel", response_model=PORead)
def cancel_purchase_order(
    po_id: UUID,
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Cancel a purchase order."""
    return purchase_order_service.cancel_po(db, organization_id, po_id)


# =============================================================================
# Goods Receipts
# =============================================================================

from app.services.ifrs.ap import goods_receipt_service, GoodsReceiptInput, GRLineInput


class GRLineCreate(BaseModel):
    """Goods receipt line input."""
    po_line_id: Optional[UUID] = None
    item_id: UUID
    quantity_received: Decimal
    unit_cost: Decimal
    warehouse_id: Optional[UUID] = None


class GRCreate(BaseModel):
    """Create goods receipt request."""
    po_id: UUID
    receipt_date: date
    notes: Optional[str] = None
    lines: list[GRLineCreate]


class GRRead(BaseModel):
    """Goods receipt response."""
    model_config = ConfigDict(from_attributes=True)
    receipt_id: UUID
    organization_id: UUID
    po_id: UUID
    receipt_number: str
    receipt_date: date
    status: str
    total_amount: Decimal


@router.post("/goods-receipts", response_model=GRRead, status_code=status.HTTP_201_CREATED)
def create_goods_receipt(
    payload: GRCreate,
    organization_id: UUID = Query(...),
    received_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Create a goods receipt against a PO."""
    lines = [
        GRLineInput(
            po_line_id=line.po_line_id,
            item_id=line.item_id,
            quantity_received=line.quantity_received,
            unit_cost=line.unit_cost,
            warehouse_id=line.warehouse_id,
        )
        for line in payload.lines
    ]
    input_data = GoodsReceiptInput(
        po_id=payload.po_id,
        receipt_date=payload.receipt_date,
        notes=payload.notes,
        lines=lines,
    )
    return goods_receipt_service.create_receipt(db, organization_id, input_data, received_by_user_id)


@router.get("/goods-receipts/{receipt_id}", response_model=GRRead)
def get_goods_receipt(receipt_id: UUID, db: Session = Depends(get_db)):
    """Get a goods receipt by ID."""
    return goods_receipt_service.get(db, str(receipt_id))


@router.get("/goods-receipts", response_model=ListResponse[GRRead])
def list_goods_receipts(
    organization_id: UUID = Query(...),
    po_id: Optional[UUID] = None,
    status: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List goods receipts with filters."""
    receipts = goods_receipt_service.list(
        db=db,
        organization_id=str(organization_id),
        po_id=str(po_id) if po_id else None,
        status=status,
        limit=limit,
        offset=offset,
    )
    return ListResponse(items=receipts, count=len(receipts), limit=limit, offset=offset)


@router.post("/goods-receipts/{receipt_id}/inspect", response_model=GRRead)
def start_gr_inspection(
    receipt_id: UUID,
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Start inspection for a goods receipt."""
    return goods_receipt_service.start_inspection(db, organization_id, receipt_id)


@router.post("/goods-receipts/{receipt_id}/accept", response_model=GRRead)
def accept_goods_receipt(
    receipt_id: UUID,
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Accept all items in a goods receipt."""
    return goods_receipt_service.accept_all(db, organization_id, receipt_id)


# =============================================================================
# Payment Batches
# =============================================================================

from app.services.ifrs.ap import payment_batch_service, PaymentBatchInput


class PaymentBatchCreate(BaseModel):
    """Create payment batch request."""
    batch_name: str = Field(max_length=100)
    payment_date: date
    bank_account_id: UUID
    payment_method: str = "EFT"
    description: Optional[str] = None


class PaymentBatchRead(BaseModel):
    """Payment batch response."""
    model_config = ConfigDict(from_attributes=True)
    batch_id: UUID
    organization_id: UUID
    batch_number: str
    batch_name: str
    payment_date: date
    status: str
    total_amount: Decimal
    payment_count: int


class BankFileResultRead(BaseModel):
    """Bank file generation result."""
    success: bool
    file_format: str
    file_content: Optional[str] = None
    payment_count: int
    total_amount: str


@router.post("/payment-batches", response_model=PaymentBatchRead, status_code=status.HTTP_201_CREATED)
def create_payment_batch(
    payload: PaymentBatchCreate,
    organization_id: UUID = Query(...),
    created_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Create a new payment batch."""
    input_data = PaymentBatchInput(
        batch_name=payload.batch_name,
        payment_date=payload.payment_date,
        bank_account_id=payload.bank_account_id,
        payment_method=payload.payment_method,
        description=payload.description,
    )
    return payment_batch_service.create_batch(db, organization_id, input_data, created_by_user_id)


@router.get("/payment-batches/{batch_id}", response_model=PaymentBatchRead)
def get_payment_batch(batch_id: UUID, db: Session = Depends(get_db)):
    """Get a payment batch by ID."""
    return payment_batch_service.get(db, str(batch_id))


@router.get("/payment-batches", response_model=ListResponse[PaymentBatchRead])
def list_payment_batches(
    organization_id: UUID = Query(...),
    status: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List payment batches with filters."""
    batches = payment_batch_service.list(
        db=db,
        organization_id=str(organization_id),
        status=status,
        limit=limit,
        offset=offset,
    )
    return ListResponse(items=batches, count=len(batches), limit=limit, offset=offset)


@router.post("/payment-batches/{batch_id}/add-payment/{payment_id}", response_model=PaymentBatchRead)
def add_payment_to_batch(
    batch_id: UUID,
    payment_id: UUID,
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Add a payment to a batch."""
    return payment_batch_service.add_payment_to_batch(db, organization_id, batch_id, payment_id)


@router.post("/payment-batches/{batch_id}/approve", response_model=PaymentBatchRead)
def approve_payment_batch(
    batch_id: UUID,
    organization_id: UUID = Query(...),
    approved_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Approve a payment batch."""
    return payment_batch_service.approve_batch(db, organization_id, batch_id, approved_by_user_id)


@router.post("/payment-batches/{batch_id}/process", response_model=PaymentBatchRead)
def process_payment_batch(
    batch_id: UUID,
    organization_id: UUID = Query(...),
    processed_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Process an approved payment batch."""
    return payment_batch_service.process_batch(db, organization_id, batch_id, processed_by_user_id)


@router.post("/payment-batches/{batch_id}/generate-bank-file", response_model=BankFileResultRead)
def generate_bank_file(
    batch_id: UUID,
    file_format: str = Query(default="NACHA"),
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Generate bank file for a payment batch."""
    return payment_batch_service.generate_bank_file(db, organization_id, batch_id, file_format)
