"""
IFRS Data Models - SQLAlchemy tables across multiple PostgreSQL schemas.

This module exports all IFRS-compliant SQLAlchemy models organized by schema.

Note: Authentication and RBAC models use the existing starter template
models in the public schema (Person, Role, Permission, Session, AuditEvent, etc.).
Audit logging and approvals live in the IFRS audit schema.

Part 1 - Core Infrastructure (33 tables, 6 schemas):
- platform: Event-driven architecture (3 tables)
- audit: Audit trail and approvals (4 tables)
- core_org: Organization structure (6 tables)
- core_fx: Foreign exchange (4 tables)
- core_config: System configuration (2 tables)
- gl: General Ledger (11 tables + 3 views)

Part 2 - Business Modules (schemas below):
- ar: Accounts Receivable - IFRS 9, IFRS 15 (11 tables)
- ap: Accounts Payable (11 tables)
- banking: Banking & cash management (4 tables)
- fa: Fixed Assets - IAS 16, IAS 36, IAS 38 (9 tables)
- lease: Leases - IFRS 16 (5 tables)
- inv: Inventory - IAS 2 (9 tables)
- tax: Tax - IAS 12 (6 tables)
- cons: Consolidation - IFRS 10 (6 tables)
- rpt: Reporting (5 tables)
"""

# Part 1 - Core Infrastructure

# Platform Schema
# AP Schema
from app.models.finance.ap import (
    APAgingSnapshot,
    APBatchStatus,
    APPaymentAllocation,
    APPaymentBatch,
    APPaymentMethod,
    APPaymentStatus,
    GoodsReceipt,
    GoodsReceiptLine,
    POStatus,
    PurchaseOrder,
    PurchaseOrderLine,
    ReceiptStatus,
    Supplier,
    SupplierInvoice,
    SupplierInvoiceLine,
    SupplierInvoiceStatus,
    SupplierInvoiceType,
    SupplierPayment,
    SupplierType,
)

# Part 2 - Business Modules
# AR Schema
from app.models.finance.ar import (
    ARAgingSnapshot,
    Contract,
    ContractStatus,
    ContractType,
    Customer,
    CustomerPayment,
    CustomerType,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    InvoiceType,
    PaymentAllocation,
    PaymentMethod,
    PaymentTerms,
    PerformanceObligation,
    RevenueRecognitionEvent,
    RiskCategory,
    SatisfactionPattern,
)
from app.models.finance.ar import (
    PaymentStatus as ARPaymentStatus,
)

# Audit Schema
from app.models.finance.audit import (
    ApprovalDecision,
    ApprovalDecisionAction,
    ApprovalRequest,
    ApprovalRequestStatus,
    ApprovalWorkflow,
    AuditAction,
    AuditLog,
)

# Automation Schema
from app.models.finance.automation import (
    ActionType,
    CustomFieldDefinition,
    CustomFieldEntityType,
    CustomFieldType,
    DocumentTemplate,
    ExecutionStatus,
    RecurringEntityType,
    RecurringFrequency,
    RecurringLog,
    RecurringLogStatus,
    RecurringStatus,
    RecurringTemplate,
    TemplateType,
    TriggerEvent,
    WorkflowEntityType,
    WorkflowExecution,
    WorkflowRule,
)

# Banking Schema
from app.models.finance.banking import (
    BankAccount,
    BankAccountStatus,
    BankAccountType,
    BankReconciliation,
    BankReconciliationLine,
    BankStatement,
    BankStatementLine,
    BankStatementStatus,
    ReconciliationMatchType,
    ReconciliationStatus,
    StatementLineType,
)

# Common Schema
from app.models.finance.common import (
    Attachment,
    AttachmentCategory,
)

# Consolidation Schema
from app.models.finance.cons import (
    ConsolidatedBalance,
    ConsolidationRun,
    ConsolidationStatus,
    EliminationEntry,
    EliminationType,
    EntityType,
    IntercompanyBalance,
    LegalEntity,
    OwnershipInterest,
)
from app.models.finance.cons import (
    ConsolidationMethod as ConsConsolidationMethod,
)

# Core Config Schema
from app.models.finance.core_config import (
    ConfigType,
    NumberingSequence,
    SequenceType,
    SystemConfiguration,
)

# Core FX Schema
from app.models.finance.core_fx import (
    CTAAdjustmentType,
    Currency,
    CurrencyTranslationAdjustment,
    ExchangeRate,
    ExchangeRateSource,
    ExchangeRateType,
)

# Core Org Schema
from app.models.finance.core_org import (
    BusinessUnit,
    BusinessUnitType,
    ConsolidationMethod,
    CostCenter,
    Location,
    LocationType,
    Organization,
    Project,
    ProjectStatus,
    ReportingSegment,
    SegmentType,
)

# Expense Schema
from app.models.finance.exp import (
    ExpenseEntry,
    ExpenseStatus,
)
from app.models.finance.exp import (
    PaymentMethod as ExpensePaymentMethod,
)

# GL Schema
from app.models.finance.gl import (
    Account,
    AccountBalance,
    AccountCategory,
    AccountType,
    BalanceType,
    BatchStatus,
    Budget,
    BudgetLine,
    BudgetStatus,
    FiscalPeriod,
    FiscalYear,
    IFRSCategory,
    JournalEntry,
    JournalEntryLine,
    JournalStatus,
    JournalType,
    NormalBalance,
    PeriodStatus,
    PostedLedgerLine,
    PostingBatch,
)

# IPSAS Fund Accounting (public sector)
from app.models.finance.ipsas import (  # noqa: F401
    Allotment,
    AllotmentStatus,
    Appropriation,
    AppropriationStatus,
    AppropriationType,
    CoASegmentDefinition,
    CoASegmentType,
    CoASegmentValue,
    Commitment,
    CommitmentLine,
    CommitmentStatus,
    CommitmentType,
    Fund,
    FundStatus,
    FundType,
    Virement,
    VirementStatus,
)

# Lease Schema
from app.models.finance.lease import (
    LeaseAsset,
    LeaseClassification,
    LeaseContract,
    LeaseLiability,
    LeaseModification,
    LeasePaymentSchedule,
    LeaseStatus,
    ModificationType,
)
from app.models.finance.lease import (
    PaymentStatus as LeasePaymentStatus,
)

# Payments Schema
from app.models.finance.payments import (
    PaymentIntent,
    PaymentIntentStatus,
    PaymentWebhook,
    WebhookStatus,
)
from app.models.finance.platform import (
    CheckpointStatus,
    EventHandlerCheckpoint,
    EventOutbox,
    EventStatus,
    IdempotencyRecord,
)

# Remita Integration
from app.models.finance.remita import (
    RemitaRRR,
    RRRStatus,
)

# Reporting Schema
from app.models.finance.rpt import (
    DisclosureChecklist,
    DisclosureStatus,
    FinancialStatementLine,
    ReportDefinition,
    ReportInstance,
    ReportSchedule,
    ReportStatus,
    ReportType,
    ScheduleFrequency,
    StatementType,
)

# Tax Schema
from app.models.finance.tax import (
    DeferredTaxBasis,
    DeferredTaxMovement,
    DifferenceType,
    TaxCode,
    TaxJurisdiction,
    TaxPeriod,
    TaxPeriodFrequency,
    TaxPeriodStatus,
    TaxReconciliation,
    TaxReturn,
    TaxReturnStatus,
    TaxReturnType,
    TaxTransaction,
    TaxTransactionType,
    TaxType,
)

# FA Schema (standalone module)
from app.models.fixed_assets import (
    Asset,
    AssetCategory,
    AssetComponent,
    AssetDisposal,
    AssetImpairment,
    AssetRevaluation,
    AssetStatus,
    CashGeneratingUnit,
    DepreciationRun,
    DepreciationRunStatus,
    DepreciationSchedule,
    DisposalType,
)

# Inventory Schema (standalone module)
from app.models.inventory import (
    CostingMethod,
    CountStatus,
    InventoryCount,
    InventoryCountLine,
    InventoryLot,
    InventoryTransaction,
    InventoryValuation,
    Item,
    ItemCategory,
    ItemType,
    TransactionType,
    Warehouse,
    WarehouseLocation,
)

__all__ = [
    # Platform
    "EventOutbox",
    "EventStatus",
    "EventHandlerCheckpoint",
    "CheckpointStatus",
    "IdempotencyRecord",
    # Audit
    "AuditLog",
    "AuditAction",
    "ApprovalWorkflow",
    "ApprovalRequest",
    "ApprovalRequestStatus",
    "ApprovalDecision",
    "ApprovalDecisionAction",
    # Core Org
    "Organization",
    "ConsolidationMethod",
    "BusinessUnit",
    "BusinessUnitType",
    "ReportingSegment",
    "SegmentType",
    "CostCenter",
    "Project",
    "ProjectStatus",
    "Location",
    "LocationType",
    # Core FX
    "Currency",
    "ExchangeRateType",
    "ExchangeRate",
    "ExchangeRateSource",
    "CurrencyTranslationAdjustment",
    "CTAAdjustmentType",
    # Core Config
    "NumberingSequence",
    "SequenceType",
    "SystemConfiguration",
    "ConfigType",
    # GL
    "AccountCategory",
    "IFRSCategory",
    "Account",
    "AccountType",
    "NormalBalance",
    "FiscalYear",
    "FiscalPeriod",
    "PeriodStatus",
    "JournalEntry",
    "JournalType",
    "JournalStatus",
    "JournalEntryLine",
    "PostingBatch",
    "BatchStatus",
    "PostedLedgerLine",
    "AccountBalance",
    "BalanceType",
    "Budget",
    "BudgetStatus",
    "BudgetLine",
    # AR
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
    "CustomerPayment",
    "PaymentMethod",
    "ARPaymentStatus",
    "PaymentAllocation",
    "ARAgingSnapshot",
    # AP
    "Supplier",
    "SupplierType",
    "PurchaseOrder",
    "POStatus",
    "PurchaseOrderLine",
    "GoodsReceipt",
    "ReceiptStatus",
    "GoodsReceiptLine",
    "SupplierInvoice",
    "SupplierInvoiceType",
    "SupplierInvoiceStatus",
    "SupplierInvoiceLine",
    "SupplierPayment",
    "APPaymentMethod",
    "APPaymentStatus",
    "APPaymentAllocation",
    "APPaymentBatch",
    "APBatchStatus",
    "APAgingSnapshot",
    # Banking
    "BankAccount",
    "BankAccountStatus",
    "BankAccountType",
    "BankReconciliation",
    "BankReconciliationLine",
    "BankStatement",
    "BankStatementLine",
    "BankStatementStatus",
    "ReconciliationMatchType",
    "ReconciliationStatus",
    "StatementLineType",
    # FA
    "AssetCategory",
    "Asset",
    "AssetStatus",
    "AssetComponent",
    "DepreciationRun",
    "DepreciationRunStatus",
    "DepreciationSchedule",
    "AssetRevaluation",
    "CashGeneratingUnit",
    "AssetImpairment",
    "AssetDisposal",
    "DisposalType",
    # Lease
    "LeaseContract",
    "LeaseClassification",
    "LeaseStatus",
    "LeaseAsset",
    "LeaseLiability",
    "LeasePaymentSchedule",
    "LeasePaymentStatus",
    "LeaseModification",
    "ModificationType",
    # Inventory
    "ItemCategory",
    "Item",
    "ItemType",
    "CostingMethod",
    "Warehouse",
    "WarehouseLocation",
    "InventoryLot",
    "InventoryTransaction",
    "TransactionType",
    "InventoryValuation",
    "InventoryCount",
    "CountStatus",
    "InventoryCountLine",
    # Tax
    "TaxJurisdiction",
    "TaxCode",
    "TaxType",
    "TaxPeriod",
    "TaxPeriodFrequency",
    "TaxPeriodStatus",
    "TaxTransaction",
    "TaxTransactionType",
    "DeferredTaxBasis",
    "DifferenceType",
    "DeferredTaxMovement",
    "TaxReconciliation",
    "TaxReturn",
    "TaxReturnStatus",
    "TaxReturnType",
    # Consolidation
    "LegalEntity",
    "EntityType",
    "ConsConsolidationMethod",
    "OwnershipInterest",
    "IntercompanyBalance",
    "EliminationEntry",
    "EliminationType",
    "ConsolidationRun",
    "ConsolidationStatus",
    "ConsolidatedBalance",
    # Reporting
    "ReportDefinition",
    "ReportType",
    "ReportSchedule",
    "ScheduleFrequency",
    "ReportInstance",
    "ReportStatus",
    "FinancialStatementLine",
    "StatementType",
    "DisclosureChecklist",
    "DisclosureStatus",
    # Expense
    "ExpenseEntry",
    "ExpenseStatus",
    "ExpensePaymentMethod",
    # Common
    "Attachment",
    "AttachmentCategory",
    # Automation
    "RecurringTemplate",
    "RecurringEntityType",
    "RecurringFrequency",
    "RecurringStatus",
    "RecurringLog",
    "RecurringLogStatus",
    "WorkflowRule",
    "WorkflowEntityType",
    "TriggerEvent",
    "ActionType",
    "WorkflowExecution",
    "ExecutionStatus",
    "CustomFieldDefinition",
    "CustomFieldEntityType",
    "CustomFieldType",
    "DocumentTemplate",
    "TemplateType",
    # Payments
    "PaymentIntent",
    "PaymentIntentStatus",
    "PaymentWebhook",
    "WebhookStatus",
    # Remita
    "RemitaRRR",
    "RRRStatus",
]
