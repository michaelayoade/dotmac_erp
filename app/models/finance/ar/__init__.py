"""
Accounts Receivable Schema - IFRS 15 & IFRS 9.
Customers, contracts, invoices, payments, ECL, quotes, sales orders.
"""
from app.models.finance.ar.customer import Customer, CustomerType, RiskCategory
from app.models.finance.ar.payment_terms import PaymentTerms
from app.models.finance.ar.contract import Contract, ContractType, ContractStatus
from app.models.finance.ar.performance_obligation import PerformanceObligation, SatisfactionPattern
from app.models.finance.ar.revenue_recognition_event import RevenueRecognitionEvent
from app.models.finance.ar.invoice import Invoice, InvoiceType, InvoiceStatus
from app.models.finance.ar.invoice_line import InvoiceLine
from app.models.finance.ar.invoice_line_tax import InvoiceLineTax
from app.models.finance.ar.customer_payment import CustomerPayment, PaymentMethod, PaymentStatus
from app.models.finance.ar.payment_allocation import PaymentAllocation
from app.models.finance.ar.ar_aging_snapshot import ARAgingSnapshot
from app.models.finance.ar.quote import Quote, QuoteLine, QuoteStatus
from app.models.finance.ar.sales_order import (
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
