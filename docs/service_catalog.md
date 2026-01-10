# IFRS Accounting System — Service Catalog

## Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2025-01-09 | Architecture | Initial MVP service catalog |

---

## 1. Design Principles

| Principle | Description |
|-----------|-------------|
| **Single Writer** | Only `LedgerPostingService` writes to `posted_ledger_line` |
| **Central Gate** | All write operations flow through `PeriodGuardService` |
| **Document + Adapter** | Subledgers have document services + posting adapters |
| **Event-Driven Derivation** | Balances and snapshots are derived via events |
| **Idempotent Commands** | All financial writes are replay-safe |

---

## 2. Service Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           PLATFORM SERVICES                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│  AuthService          │ AuthorizationService  │ AuditLogService             │
│  IdempotencyService   │ ApprovalWorkflowSvc   │ FeatureFlagService          │
│  SequenceService      │ FXService             │                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         ACCOUNTING SPINE                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────────┐                                                       │
│  │ PeriodGuardSvc   │◄──────────────────────────────────────────────────┐   │
│  └────────┬─────────┘                                                   │   │
│           │                                                             │   │
│           ▼                                                             │   │
│  ┌──────────────────┐     ┌─────────────────────┐     ┌──────────────┐ │   │
│  │  JournalService  │────►│ LedgerPostingService│────►│BalanceService│ │   │
│  └──────────────────┘     └──────────┬──────────┘     └──────────────┘ │   │
│           │                          │                                  │   │
│           ▼                          ▼                                  │   │
│  ┌──────────────────┐     ┌─────────────────────┐                      │   │
│  │ ReversalService  │     │   OutboxPublisher   │                      │   │
│  └──────────────────┘     └─────────────────────┘                      │   │
│                                                                         │   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         SUBLEDGER SERVICES                                   │
├──────────────────┬──────────────────┬──────────────────┬────────────────────┤
│       AR         │        AP        │        FA        │     INVENTORY      │
├──────────────────┼──────────────────┼──────────────────┼────────────────────┤
│ InvoiceService   │ InvoiceService   │ AssetService     │ ItemService        │
│ PaymentService   │ PaymentService   │ DepreciationSvc  │ TransactionService │
│ PostingAdapter   │ PostingAdapter   │ DisposalService  │ PostingAdapter     │
│ AgingService     │ AgingService     │ PostingAdapter   │ ValuationService   │
├──────────────────┴──────────────────┴──────────────────┴────────────────────┤
│       LEASE            │        TAX           │      REPORTING              │
├────────────────────────┼──────────────────────┼─────────────────────────────┤
│ ContractService        │ CalculationService   │ TrialBalanceService         │
│ LeaseRunService        │ TransactionService   │ StatementService            │
│ PostingAdapter         │ PostingAdapter       │ ReconciliationService       │
└────────────────────────┴──────────────────────┴─────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                       GOVERNANCE SERVICES                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│  PeriodService  │  PeriodReopenService  │  AuditLockService                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                       BACKGROUND SERVICES                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│  OutboxPublisher  │  LedgerEventHandler  │  SnapshotScheduler               │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Platform Services

| Service | Purpose | MVP | Key Responsibilities |
|---------|---------|-----|---------------------|
| **AuthService** | Authentication & sessions | ✅ | Login/logout, session validation, MFA challenges |
| **AuthorizationService** | RBAC & data scope | ✅ | Permission checks, data access policies, SoD validation |
| **AuditLogService** | Immutable audit trail | ✅ | Who/what/when/why, old/new values, hash chain |
| **IdempotencyService** | Safe retries | ✅ | Key validation, replay detection, response caching |
| **ApprovalWorkflowService** | Approvals & SoD | ✅ | Submit/approve/reject, threshold evaluation, dual control |
| **FeatureFlagService** | MVP feature gating | ✅ | Enable/disable features per org, gradual rollout |
| **SequenceService** | Document numbering | ✅ | Atomic sequence generation, fiscal year reset |
| **FXService** | Currency conversion | ✅ | Rate resolution, convert to functional, batch conversion |

---

## 4. Accounting Spine Services

| Service | Purpose | MVP | Key Responsibilities |
|---------|---------|-----|---------------------|
| **PeriodGuardService** ⭐ | Central write gate | ✅ | Period status checks, audit lock enforcement, reopen session validation |
| **LedgerPostingService** ⭐ | Single ledger writer | ✅ | Validate entries, enforce balance, write posted_ledger_line, emit events |
| **JournalService** | Journal lifecycle | ✅ | Create/edit drafts, submit/approve, request posting |
| **ReversalService** | Controlled reversals | ✅ | Create reversal entries, enforce rules, link original ↔ reversal |
| **AccountBalanceService** | Balance cache | ✅ | Update on posting events, rebuild on demand |

### 4.1 LedgerPostingService — Critical Rules

```
┌─────────────────────────────────────────────────────────────────┐
│                 LEDGER POSTING SERVICE RULES                    │
├─────────────────────────────────────────────────────────────────┤
│ 1. ONLY this service writes to posted_ledger_line              │
│ 2. Functional currency amounts MUST NOT be null/zero           │
│ 3. Debit = Credit MUST balance                                 │
│ 4. Idempotency key MUST be provided                            │
│ 5. Period MUST be open (via PeriodGuardService)                │
│ 6. All postings emit events via outbox                         │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 Posting Flow

```
Subledger Document
       │
       ▼
┌──────────────────┐
│ Posting Adapter  │  ← Translates document → accounting entries
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ PeriodGuardSvc   │  ← Validates period is open
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ LedgerPostingSvc │  ← Creates journal_entry + posting_batch + posted_ledger_line
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ OutboxPublisher  │  ← Emits ledger.posting.completed event
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│LedgerEventHandler│  ← Updates balances, subledger posting_status
└──────────────────┘
```

---

## 5. Subledger Services

Each subledger follows a consistent pattern:

```
┌─────────────────────────────────────────────────────────────────┐
│                    SUBLEDGER PATTERN                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  DocumentService         ← Manages document lifecycle           │
│       │                    (create, submit, approve)            │
│       ▼                                                         │
│  PostingAdapter          ← Translates document → GL entries     │
│       │                                                         │
│       ▼                                                         │
│  LedgerPostingService    ← Single writer (shared)               │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 5.1 Accounts Receivable (AR)

| Service | Purpose | MVP |
|---------|---------|-----|
| **ARInvoiceService** | Invoice lifecycle (create, submit, approve, void) | ✅ |
| **ARPaymentService** | Receipt recording, allocation to invoices | ✅ |
| **ARPostingAdapter** | Invoice/payment → GL entries | ✅ |
| **ARAgingService** | Generate aging snapshots at period end | ✅ |
| **ARContractService** | IFRS 15 contracts & performance obligations | 🔒 Deferred |
| **ECLService** | IFRS 9 expected credit loss provisioning | 🔒 Deferred |

### 5.2 Accounts Payable (AP)

| Service | Purpose | MVP |
|---------|---------|-----|
| **APInvoiceService** | Supplier invoice lifecycle | ✅ |
| **APPaymentService** | Supplier payments, allocation | ✅ |
| **APPostingAdapter** | Invoice/payment → GL entries | ✅ |
| **APAgingService** | Generate aging snapshots | ✅ |
| **PurchaseOrderService** | PO creation and tracking | ✅ |
| **GoodsReceiptService** | Receipt recording | ✅ |
| **ThreeWayMatchService** | PO-GR-Invoice matching | 🔒 Deferred |
| **PaymentBatchService** | Bulk payment processing | 🔒 Deferred |

### 5.3 Fixed Assets (FA)

| Service | Purpose | MVP |
|---------|---------|-----|
| **AssetService** | Asset master data, capitalization | ✅ |
| **DepreciationRunService** | Calculate & post depreciation (straight-line) | ✅ |
| **AssetDisposalService** | Disposal with gain/loss | ✅ |
| **FAPostingAdapter** | Depreciation/disposal → GL entries | ✅ |
| **ComponentService** | Asset componentization | 🔒 Deferred |
| **RevaluationService** | IAS 16 revaluation model | 🔒 Deferred |
| **ImpairmentService** | IAS 36 impairment testing | 🔒 Deferred |

### 5.4 Inventory

| Service | Purpose | MVP |
|---------|---------|-----|
| **InventoryItemService** | Item master, warehouse/location setup | ✅ |
| **InventoryTransactionService** | Receipts, issues, adjustments, transfers | ✅ |
| **InventoryPostingAdapter** | Movements → GL entries | ✅ |
| **InventoryBalanceService** | On-hand, reserved, available tracking | ✅ |
| **CostLayerService** | FIFO cost layer management | 🔒 Deferred |
| **NRVValuationService** | IAS 2 NRV assessment | 🔒 Deferred |
| **LotTrackingService** | Lot/serial tracking | 🔒 Deferred |

### 5.5 Leases (IFRS 16)

| Service | Purpose | MVP |
|---------|---------|-----|
| **LeaseContractService** | Lease setup, payment schedule generation | ✅ |
| **LeaseRunService** | Calculate & post interest + depreciation | ✅ |
| **LeasePostingAdapter** | Lease amounts → GL entries | ✅ |
| **LeaseModificationService** | Lease modifications & reassessments | 🔒 Deferred |

### 5.6 Tax

| Service | Purpose | MVP |
|---------|---------|-----|
| **TaxCalculationService** | Calculate VAT/GST on AR/AP lines | ✅ |
| **TaxTransactionService** | Create tax_transaction records | ✅ |
| **TaxPeriodService** | Tax period management | ✅ |
| **TaxReturnService** | Generate tax return summaries | ✅ |
| **TaxPostingAdapter** | Tax adjustments → GL entries | ✅ |
| **DeferredTaxService** | IAS 12 deferred tax | 🔒 Deferred |

---

## 6. Governance Services

| Service | Purpose | MVP |
|---------|---------|-----|
| **PeriodService** | Create fiscal years/periods, soft/hard close, lock | ✅ |
| **PeriodReopenService** | Reopen sessions with dual control, auto-close | ✅ |
| **AuditLockService** | Enable/disable audit locks (stub in MVP) | 🔒 Stub |
| **CloseChecklistService** | Period close checklist validation | ✅ |

### 6.1 Period Status Flow

```
OPEN ──────► SOFT_CLOSED ──────► HARD_CLOSED ──────► LOCKED
  │              │                    │
  │              ▼                    ▼
  │         [Reopen Session]    [Reopen Session]
  │          (adjustments)       (reversals only)
  │              │                    │
  └──────────────┴────────────────────┘
```

---

## 7. Reporting Services

| Service | Purpose | MVP |
|---------|---------|-----|
| **TrialBalanceService** | Generate TB from account_balance | ✅ |
| **FinancialStatementService** | Generate BS/P&L, apply templates | ✅ |
| **ReconciliationService** | Detect control account mismatches | 🔒 Stub |
| **CashFlowStatementService** | IAS 7 cash flow statement | 🔒 Deferred |

---

## 8. Background Services

| Service | Purpose | MVP |
|---------|---------|-----|
| **OutboxPublisher** | Publish pending events, retry failed | ✅ |
| **LedgerEventHandler** | React to posting events, update balances | ✅ |
| **SnapshotScheduler** | Period-end aging snapshots, statement generation | ✅ |
| **IdempotencyCleanup** | Purge expired idempotency records | ✅ |

### 8.1 Event Flow

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│LedgerPostingSvc │────►│  OutboxPublisher│────►│LedgerEventHandler│
└─────────────────┘     └─────────────────┘     └────────┬────────┘
                                                         │
                        ┌────────────────────────────────┼────────────────────────────────┐
                        │                                │                                │
                        ▼                                ▼                                ▼
              ┌─────────────────┐            ┌─────────────────┐            ┌─────────────────┐
              │BalanceService   │            │ Subledger       │            │ Snapshot        │
              │(update balances)│            │ (update status) │            │ (if period end) │
              └─────────────────┘            └─────────────────┘            └─────────────────┘
```

---

## 9. Transaction Boundaries

| Operation | Transaction Scope |
|-----------|-------------------|
| Invoice creation | Invoice + lines (single tx) |
| Journal posting | journal_entry + posting_batch + posted_ledger_line + outbox event (single tx) |
| Payment allocation | Payment + allocations + invoice balance update (single tx) |
| Depreciation run | All depreciation schedules + journal + posting (single tx) |
| Period close | Status update + checklist validation (single tx) |
| Reversal | Original status update + reversal journal + posting (single tx) |

---

## 10. Service Dependencies

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         DEPENDENCY GRAPH                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  AuthService (foundational - no deps)                                       │
│       │                                                                      │
│       ▼                                                                      │
│  AuthorizationService ◄─────────────────────────────────────────────────┐   │
│       │                                                                  │   │
│       ▼                                                                  │   │
│  PeriodGuardService ◄───────────────────────────────────────────────┐   │   │
│       │                                                              │   │   │
│       ▼                                                              │   │   │
│  LedgerPostingService                                                │   │   │
│       │                                                              │   │   │
│       ├──► IdempotencyService                                        │   │   │
│       ├──► FXService                                                 │   │   │
│       ├──► SequenceService                                           │   │   │
│       ├──► AuditLogService                                           │   │   │
│       │                                                              │   │   │
│       ▼                                                              │   │   │
│  [All Posting Adapters] ─────────────────────────────────────────────┘   │   │
│       │                                                                  │   │
│       ├── ARPostingAdapter                                               │   │
│       ├── APPostingAdapter                                               │   │
│       ├── FAPostingAdapter                                               │   │
│       ├── InventoryPostingAdapter                                        │   │
│       ├── LeasePostingAdapter                                            │   │
│       └── TaxPostingAdapter                                              │   │
│                                                                          │   │
│  [All Document Services] ────────────────────────────────────────────────┘   │
│       │                                                                      │
│       ├── ARInvoiceService ──► ApprovalWorkflowService                      │
│       ├── APInvoiceService ──► ApprovalWorkflowService                      │
│       ├── JournalService ────► ApprovalWorkflowService                      │
│       └── ...                                                                │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 11. MVP Scope Matrix

### 11.1 Platform Services

| Service | MVP Status |
|---------|------------|
| AuthService | ✅ Full |
| AuthorizationService | ✅ Full |
| AuditLogService | ✅ Full |
| IdempotencyService | ✅ Full |
| ApprovalWorkflowService | ✅ Full |
| FeatureFlagService | ✅ Full |
| SequenceService | ✅ Full |
| FXService | ✅ Full |

### 11.2 Accounting Spine

| Service | MVP Status |
|---------|------------|
| PeriodGuardService | ✅ Full |
| LedgerPostingService | ✅ Full |
| JournalService | ✅ Full |
| ReversalService | ✅ Full |
| AccountBalanceService | ✅ Full |

### 11.3 Subledgers

| Module | MVP Services | Deferred Services |
|--------|--------------|-------------------|
| **AR** | Invoice, Payment, Posting, Aging | Contracts (IFRS 15), ECL |
| **AP** | Invoice, Payment, Posting, Aging, PO, GR | 3-way match, Payment batches |
| **FA** | Asset, Depreciation (SL), Disposal | Components, Revaluation, Impairment |
| **Inventory** | Item, Transaction, Posting, Balance | FIFO layers, NRV, Lot/Serial |
| **Lease** | Contract, LeaseRun, Posting | Modifications, Variable payments |
| **Tax** | Calculation, Transaction, Period, Return | Deferred tax, Loss carryforward |

### 11.4 Governance & Reporting

| Service | MVP Status |
|---------|------------|
| PeriodService | ✅ Full |
| PeriodReopenService | ✅ Full |
| AuditLockService | 🔒 Stub |
| TrialBalanceService | ✅ Full |
| FinancialStatementService | ✅ Full |
| ReconciliationService | 🔒 Stub |

---

## 12. Implementation Priority

### Phase 1: Foundation (Weeks 1-2)

```
1. AuthService
2. AuthorizationService
3. AuditLogService
4. IdempotencyService
5. SequenceService
6. FXService
7. FeatureFlagService
```

### Phase 2: Accounting Spine (Weeks 3-4)

```
1. PeriodGuardService
2. LedgerPostingService
3. JournalService
4. ReversalService
5. AccountBalanceService
6. OutboxPublisher
7. LedgerEventHandler
```

### Phase 3: Core Subledgers (Weeks 5-8)

```
1. AR (Invoice → Payment → Posting → Aging)
2. AP (Invoice → Payment → Posting → Aging)
3. Tax (Calculation → Transaction)
```

### Phase 4: Extended Subledgers (Weeks 9-12)

```
1. FA (Asset → Depreciation → Disposal)
2. Inventory (Item → Transaction → Balance)
3. Lease (Contract → LeaseRun)
```

### Phase 5: Governance & Reporting (Weeks 13-14)

```
1. PeriodService (close workflows)
2. PeriodReopenService
3. TrialBalanceService
4. FinancialStatementService
5. SnapshotScheduler
```

---

## 13. Critical Implementation Notes

### 13.1 LedgerPostingService Contract

```
┌─────────────────────────────────────────────────────────────────┐
│ POSTING SERVICE CONTRACT — NON-NEGOTIABLE                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│ 1. Functional currency amounts are MANDATORY                   │
│    - Reject any line where functional amount is NULL or 0      │
│    - Caller MUST compute functional amounts before calling     │
│                                                                 │
│ 2. Debit = Credit MUST balance (functional currency)           │
│    - Reject entire batch if imbalanced                         │
│                                                                 │
│ 3. Idempotency key REQUIRED                                    │
│    - Reject if missing                                         │
│    - Return cached result on replay                            │
│                                                                 │
│ 4. Period validation via PeriodGuardService                    │
│    - Reject if period not open                                 │
│    - Check audit locks                                         │
│    - Check reopen session scope                                │
│                                                                 │
│ 5. All writes in single transaction                            │
│    - journal_entry                                              │
│    - posting_batch                                              │
│    - posted_ledger_line (all lines)                            │
│    - event_outbox                                               │
│                                                                 │
│ 6. Emit event on success                                       │
│    - ledger.posting.completed                                   │
│    - Include journal_entry_id, posting_batch_id                │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 13.2 Posting Adapter Pattern

Every subledger posting adapter MUST:

```typescript
// Pseudocode pattern
async function postDocument(document, userId, correlationId) {
  // 1. Build accounting entries
  const lines = buildAccountingEntries(document);
  
  // 2. Convert to functional currency (CRITICAL)
  for (const line of lines) {
    const converted = await fxService.convertToFunctional(
      { amount: line.amount, currencyCode: line.currency },
      document.organizationId,
      document.postingDate
    );
    line.debitAmountFunctional = converted.debitAmount;
    line.creditAmountFunctional = converted.creditAmount;
  }
  
  // 3. Call posting service
  const result = await ledgerPostingService.post({
    organizationId: document.organizationId,
    correlationId,
    idempotencyKey: `${document.type}-${document.id}-v1`,
    sourceModule: 'AR', // or AP, FA, etc.
    sourceDocumentType: document.type,
    sourceDocumentId: document.id,
    lines,
    postedByUserId: userId,
  });
  
  // 4. Update document status
  await updateDocumentPostingStatus(document.id, 'POSTED', result);
  
  return result;
}
```

### 13.3 Event Handler Pattern

```typescript
// LedgerEventHandler
async function handlePostingCompleted(event: PostingCompletedEvent) {
  // 1. Update account balances
  await accountBalanceService.updateFromPosting(event);
  
  // 2. Update subledger document status
  if (event.sourceDocumentType && event.sourceDocumentId) {
    await updateSubledgerStatus(
      event.sourceModule,
      event.sourceDocumentId,
      'POSTED'
    );
  }
  
  // 3. Trigger snapshots if period-end
  if (await isPeriodEndPosting(event)) {
    await snapshotScheduler.triggerSnapshots(event.fiscalPeriodId);
  }
}
```

---

## 14. Feature Flag Codes

```typescript
enum FeatureCode {
  // AR
  AR_IFRS15_CONTRACTS = 'AR_IFRS15_CONTRACTS',
  AR_ECL_PROVISIONING = 'AR_ECL_PROVISIONING',
  
  // AP
  AP_THREE_WAY_MATCH = 'AP_THREE_WAY_MATCH',
  AP_PAYMENT_BATCHES = 'AP_PAYMENT_BATCHES',
  AP_WITHHOLDING_TAX = 'AP_WITHHOLDING_TAX',
  
  // FA
  FA_COMPONENTIZATION = 'FA_COMPONENTIZATION',
  FA_REVALUATION = 'FA_REVALUATION',
  FA_IMPAIRMENT = 'FA_IMPAIRMENT',
  FA_DECLINING_BALANCE = 'FA_DECLINING_BALANCE',
  
  // Inventory
  INV_FIFO_COSTING = 'INV_FIFO_COSTING',
  INV_LOT_TRACKING = 'INV_LOT_TRACKING',
  INV_SERIAL_TRACKING = 'INV_SERIAL_TRACKING',
  INV_NRV_ASSESSMENT = 'INV_NRV_ASSESSMENT',
  
  // Lease
  LEASE_MODIFICATIONS = 'LEASE_MODIFICATIONS',
  LEASE_VARIABLE_PAYMENTS = 'LEASE_VARIABLE_PAYMENTS',
  
  // Tax
  TAX_DEFERRED = 'TAX_DEFERRED',
  TAX_LOSS_CARRYFORWARD = 'TAX_LOSS_CARRYFORWARD',
  
  // Consolidation
  CONSOLIDATION_ENABLED = 'CONSOLIDATION_ENABLED',
  
  // Reporting
  REPORT_CASH_FLOW = 'REPORT_CASH_FLOW',
  REPORT_XBRL = 'REPORT_XBRL',
}
```

---

## Summary

| Layer | Services | MVP Ready |
|-------|----------|-----------|
| **Platform** | 8 services | ✅ All |
| **Accounting Spine** | 5 services | ✅ All |
| **AR** | 4 MVP + 2 deferred | ✅ Core |
| **AP** | 6 MVP + 2 deferred | ✅ Core |
| **FA** | 4 MVP + 3 deferred | ✅ Core |
| **Inventory** | 4 MVP + 3 deferred | ✅ Core |
| **Lease** | 3 MVP + 1 deferred | ✅ Core |
| **Tax** | 5 MVP + 2 deferred | ✅ Core |
| **Governance** | 3 services (1 stub) | ✅ Core |
| **Reporting** | 2 MVP + 2 deferred | ✅ Core |
| **Background** | 4 services | ✅ All |

**Total: 44 MVP services, 15 deferred**
