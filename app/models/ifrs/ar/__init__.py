"""
Accounts Receivable Schema - IFRS 15 & IFRS 9.
Customers, contracts, invoices, payments, ECL, quotes, sales orders.
"""
from app.models.ifrs.ar.customer import Customer, CustomerType, RiskCategory
from app.models.ifrs.ar.payment_terms import PaymentTerms
from app.models.ifrs.ar.contract import Contract, ContractType, ContractStatus
from app.models.ifrs.ar.performance_obligation import PerformanceObligation, SatisfactionPattern
from app.models.ifrs.ar.revenue_recognition_event import RevenueRecognitionEvent
from app.models.ifrs.ar.invoice import Invoice, InvoiceType, InvoiceStatus
from app.models.ifrs.ar.invoice_line import InvoiceLine
from app.models.ifrs.ar.invoice_line_tax import InvoiceLineTax
from app.models.ifrs.ar.customer_payment import CustomerPayment, PaymentMethod, PaymentStatus
from app.models.ifrs.ar.payment_allocation import PaymentAllocation
from app.models.ifrs.ar.expected_credit_loss import ExpectedCreditLoss, ECLMethodology, ECLStage
from app.models.ifrs.ar.ar_aging_snapshot import ARAgingSnapshot
from app.models.ifrs.ar.quote import Quote, QuoteLine, QuoteStatus
from app.models.ifrs.ar.sales_order import (
    SalesOrder, SalesOrderLine, SOStatus, FulfillmentStatus,
    Shipment, ShipmentLine,
)

__all__ = [
    "Customer",
    "CustomerType",
    "RiskCategory",
    "PaymentTerms",
    "Contract",
    "ContractType",
    "ContractStatus",
    "PerformanceObligation",
    "SatisfactionPattern",
    "RevenueRecognitionEvent",
    "Invoice",
    "InvoiceType",
    "InvoiceStatus",
    "InvoiceLine",
    "InvoiceLineTax",
    "CustomerPayment",
    "PaymentMethod",
    "PaymentStatus",
    "PaymentAllocation",
    "ExpectedCreditLoss",
    "ECLMethodology",
    "ECLStage",
    "ARAgingSnapshot",
    # Quotes
    "Quote",
    "QuoteLine",
    "QuoteStatus",
    # Sales Orders
    "SalesOrder",
    "SalesOrderLine",
    "SOStatus",
    "FulfillmentStatus",
    "Shipment",
    "ShipmentLine",
]
