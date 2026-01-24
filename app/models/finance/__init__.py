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
- fin_inst: Financial Instruments - IFRS 9 (5 tables)
- tax: Tax - IAS 12 (6 tables)
- cons: Consolidation - IFRS 10 (6 tables)
- rpt: Reporting (5 tables)
"""

# Part 1 - Core Infrastructure

# Platform Schema
from app.models.finance.platform import (
    EventOutbox,
    EventStatus,
    EventHandlerCheckpoint,
    CheckpointStatus,
    IdempotencyRecord,
)

# Audit Schema
from app.models.finance.audit import (
    AuditLog,
    AuditAction,
    ApprovalWorkflow,
    ApprovalRequest,
    ApprovalRequestStatus,
    ApprovalDecision,
    ApprovalDecisionAction,
)

# Core Org Schema
from app.models.finance.core_org import (
    Organization,
    ConsolidationMethod,
    BusinessUnit,
    BusinessUnitType,
    ReportingSegment,
    SegmentType,
    CostCenter,
    Project,
    ProjectStatus,
    Location,
    LocationType,
)

# Core FX Schema
from app.models.finance.core_fx import (
    Currency,
    ExchangeRateType,
    ExchangeRate,
    ExchangeRateSource,
    CurrencyTranslationAdjustment,
    CTAAdjustmentType,
)

# Core Config Schema
from app.models.finance.core_config import (
    NumberingSequence,
    SequenceType,
    SystemConfiguration,
    ConfigType,
)

# GL Schema
from app.models.finance.gl import (
    AccountCategory,
    IFRSCategory,
    Account,
    AccountType,
    NormalBalance,
    FiscalYear,
    FiscalPeriod,
    PeriodStatus,
    JournalEntry,
    JournalType,
    JournalStatus,
    JournalEntryLine,
    PostingBatch,
    BatchStatus,
    PostedLedgerLine,
    AccountBalance,
    BalanceType,
    Budget,
    BudgetStatus,
    BudgetLine,
)

# Part 2 - Business Modules

# AR Schema
from app.models.finance.ar import (
    Customer,
    CustomerType,
    RiskCategory,
    PaymentTerms,
    Contract,
    ContractType,
    ContractStatus,
    PerformanceObligation,
    SatisfactionPattern,
    RevenueRecognitionEvent,
    Invoice,
    InvoiceType,
    InvoiceStatus,
    InvoiceLine,
    CustomerPayment,
    PaymentMethod,
    PaymentStatus as ARPaymentStatus,
    PaymentAllocation,
    ExpectedCreditLoss,
    ECLMethodology,
    ECLStage,
    ARAgingSnapshot,
)

# AP Schema
from app.models.finance.ap import (
    Supplier,
    SupplierType,
    PurchaseOrder,
    POStatus,
    PurchaseOrderLine,
    GoodsReceipt,
    ReceiptStatus,
    GoodsReceiptLine,
    SupplierInvoice,
    SupplierInvoiceType,
    SupplierInvoiceStatus,
    SupplierInvoiceLine,
    SupplierPayment,
    APPaymentMethod,
    APPaymentStatus,
    APPaymentAllocation,
    APPaymentBatch,
    APBatchStatus,
    APAgingSnapshot,
)

# FA Schema
from app.models.finance.fa import (
    AssetCategory,
    Asset,
    AssetStatus,
    AssetComponent,
    DepreciationRun,
    DepreciationRunStatus,
    DepreciationSchedule,
    AssetRevaluation,
    CashGeneratingUnit,
    AssetImpairment,
    AssetDisposal,
    DisposalType,
)

# Lease Schema
from app.models.finance.lease import (
    LeaseContract,
    LeaseClassification,
    LeaseStatus,
    LeaseAsset,
    LeaseLiability,
    LeasePaymentSchedule,
    PaymentStatus as LeasePaymentStatus,
    LeaseModification,
    ModificationType,
)

# Inventory Schema
from app.models.finance.inv import (
    ItemCategory,
    Item,
    ItemType,
    CostingMethod,
    Warehouse,
    WarehouseLocation,
    InventoryLot,
    InventoryTransaction,
    TransactionType,
    InventoryValuation,
    InventoryCount,
    CountStatus,
    InventoryCountLine,
)

# Banking Schema
from app.models.finance.banking import (
    BankAccount,
    BankAccountStatus,
    BankAccountType,
    BankStatement,
    BankStatementLine,
    BankStatementStatus,
    StatementLineType,
    BankReconciliation,
    BankReconciliationLine,
    ReconciliationStatus,
    ReconciliationMatchType,
)

# Financial Instruments Schema
from app.models.finance.fin_inst import (
    FinancialInstrument,
    InstrumentType,
    InstrumentClassification,
    InstrumentStatus,
    InstrumentValuation,
    InterestAccrual,
    HedgeRelationship,
    HedgeType,
    HedgeStatus,
    HedgeEffectiveness,
)

# Tax Schema
from app.models.finance.tax import (
    TaxJurisdiction,
    TaxCode,
    TaxType,
    TaxPeriod,
    TaxPeriodFrequency,
    TaxPeriodStatus,
    TaxReturn,
    TaxReturnStatus,
    TaxReturnType,
    TaxTransaction,
    TaxTransactionType,
    DeferredTaxBasis,
    DifferenceType,
    DeferredTaxMovement,
    TaxReconciliation,
)

# Consolidation Schema
from app.models.finance.cons import (
    LegalEntity,
    EntityType,
    ConsolidationMethod as ConsConsolidationMethod,
    OwnershipInterest,
    IntercompanyBalance,
    EliminationEntry,
    EliminationType,
    ConsolidationRun,
    ConsolidationStatus,
    ConsolidatedBalance,
)

# Reporting Schema
from app.models.finance.rpt import (
    ReportDefinition,
    ReportType,
    ReportSchedule,
    ScheduleFrequency,
    ReportInstance,
    ReportStatus,
    FinancialStatementLine,
    StatementType,
    DisclosureChecklist,
    DisclosureStatus,
)

# Expense Schema
from app.models.finance.exp import (
    ExpenseEntry,
    ExpenseStatus,
    PaymentMethod as ExpensePaymentMethod,
)

# Common Schema
from app.models.finance.common import (
    Attachment,
    AttachmentCategory,
)

# Automation Schema
from app.models.finance.automation import (
    RecurringTemplate,
    RecurringEntityType,
    RecurringFrequency,
    RecurringStatus,
    RecurringLog,
    RecurringLogStatus,
    WorkflowRule,
    WorkflowEntityType,
    TriggerEvent,
    ActionType,
    WorkflowExecution,
    ExecutionStatus,
    CustomFieldDefinition,
    CustomFieldEntityType,
    CustomFieldType,
    DocumentTemplate,
    TemplateType,
)

# Payments Schema
from app.models.finance.payments import (
    PaymentIntent,
    PaymentIntentStatus,
    PaymentWebhook,
    WebhookStatus,
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
    "ExpectedCreditLoss",
    "ECLMethodology",
    "ECLStage",
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
    # Financial Instruments
    "FinancialInstrument",
    "InstrumentType",
    "InstrumentClassification",
    "InstrumentStatus",
    "InstrumentValuation",
    "InterestAccrual",
    "HedgeRelationship",
    "HedgeType",
    "HedgeStatus",
    "HedgeEffectiveness",
    # Tax
    "TaxJurisdiction",
    "TaxCode",
    "TaxType",
    "TaxTransaction",
    "TaxTransactionType",
    "DeferredTaxBasis",
    "DifferenceType",
    "DeferredTaxMovement",
    "TaxReconciliation",
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
]
