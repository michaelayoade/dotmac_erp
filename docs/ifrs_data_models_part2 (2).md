# IFRS-Based Accounting Software — Data Model Specification

## Part 2: Subledgers, Financial Instruments, Tax & Reporting

**Document 14b — Data Models (Architecture-Aligned)**

This document defines the business domain data models for subledgers and reporting.

---

## Table of Contents

1. [Accounts Receivable (IFRS 15 & IFRS 9)](#1-accounts-receivable-ifrs-15--ifrs-9)
2. [Accounts Payable](#2-accounts-payable)
3. [Fixed Assets (IAS 16, IAS 36, IAS 38)](#3-fixed-assets-ias-16-ias-36-ias-38)
4. [Leases (IFRS 16)](#4-leases-ifrs-16)
5. [Inventory (IAS 2)](#5-inventory-ias-2)
6. [Financial Instruments (IFRS 9)](#6-financial-instruments-ifrs-9)
7. [Tax Management (IAS 12)](#7-tax-management-ias-12)
8. [Consolidation & Group Reporting](#8-consolidation--group-reporting)
9. [Financial Reporting](#9-financial-reporting)
10. [Summary & Cross-References](#10-summary--cross-references)

---

## 1. Accounts Receivable (IFRS 15 & IFRS 9)

### 1.1 ar.customer

```sql
CREATE TABLE ar.customer (
    customer_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    
    customer_code           VARCHAR(30) NOT NULL,
    customer_type           VARCHAR(20) NOT NULL
        CHECK (customer_type IN ('INDIVIDUAL', 'COMPANY', 'GOVERNMENT', 'RELATED_PARTY')),
    
    -- Identity
    legal_name              VARCHAR(255) NOT NULL,
    trading_name            VARCHAR(255),
    tax_identification_number VARCHAR(50),
    registration_number     VARCHAR(50),
    
    -- Credit
    credit_limit            NUMERIC(20,6),
    credit_terms_days       INTEGER NOT NULL DEFAULT 30,
    payment_terms_id        UUID,
    
    -- Defaults
    currency_code           CHAR(3) NOT NULL DEFAULT 'USD',
    price_list_id           UUID,
    ar_control_account_id   UUID NOT NULL,
    default_revenue_account_id UUID,
    
    -- Relationships
    sales_rep_user_id       UUID,
    customer_group_id       UUID,
    
    -- Risk (for IFRS 9 ECL)
    risk_category           VARCHAR(20) NOT NULL DEFAULT 'MEDIUM'
        CHECK (risk_category IN ('LOW', 'MEDIUM', 'HIGH', 'WATCH')),
    
    -- Related party
    is_related_party        BOOLEAN NOT NULL DEFAULT false,
    related_party_type      VARCHAR(50),
    related_party_relationship TEXT,
    
    -- Contact & Address (JSONB for flexibility)
    billing_address         JSONB,
    shipping_address        JSONB,
    primary_contact         JSONB,
    bank_details            JSONB,  -- Encrypted/masked
    
    is_active               BOOLEAN NOT NULL DEFAULT true,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_customer_code UNIQUE (organization_id, customer_code)
);

CREATE INDEX idx_customer_org ON ar.customer (organization_id, is_active);
CREATE INDEX idx_customer_risk ON ar.customer (organization_id, risk_category);
```

### 1.2 ar.payment_terms

```sql
CREATE TABLE ar.payment_terms (
    payment_terms_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    
    terms_code              VARCHAR(20) NOT NULL,
    terms_name              VARCHAR(100) NOT NULL,
    due_days                INTEGER NOT NULL,
    discount_days           INTEGER,
    discount_percentage     NUMERIC(5,2),
    
    is_active               BOOLEAN NOT NULL DEFAULT true,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_payment_terms UNIQUE (organization_id, terms_code)
);
```

### 1.3 ar.contract (IFRS 15)

```sql
CREATE TABLE ar.contract (
    contract_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    customer_id             UUID NOT NULL REFERENCES ar.customer(customer_id),
    
    contract_number         VARCHAR(50) NOT NULL,
    contract_name           VARCHAR(200) NOT NULL,
    contract_type           VARCHAR(30) NOT NULL
        CHECK (contract_type IN ('STANDARD', 'FRAMEWORK', 'SUBSCRIPTION', 'PROJECT')),
    
    -- Timeline
    start_date              DATE NOT NULL,
    end_date                DATE,
    
    -- Value
    total_contract_value    NUMERIC(20,6),
    currency_code           CHAR(3) NOT NULL,
    
    -- Status
    status                  VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'ACTIVE', 'COMPLETED', 'TERMINATED', 'SUSPENDED')),
    approval_status         VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    
    -- IFRS 15 criteria
    is_enforceable          BOOLEAN NOT NULL DEFAULT true,
    has_commercial_substance BOOLEAN NOT NULL DEFAULT true,
    collectability_assessment VARCHAR(20) NOT NULL DEFAULT 'PROBABLE'
        CHECK (collectability_assessment IN ('PROBABLE', 'NOT_PROBABLE', 'REASSESS')),
    
    -- Contract modifications (JSONB for history)
    modification_history    JSONB,
    
    -- Variable consideration
    variable_consideration  JSONB,
        /*
        {
            "type": "DISCOUNT" | "REBATE" | "REFUND" | "BONUS" | "PENALTY",
            "estimation_method": "EXPECTED_VALUE" | "MOST_LIKELY",
            "estimated_amount": 5000.00,
            "constraint_applied": true
        }
        */
    
    -- Financing component
    significant_financing   BOOLEAN NOT NULL DEFAULT false,
    financing_rate          NUMERIC(8,5),
    
    -- Non-cash consideration
    noncash_consideration   JSONB,
    consideration_payable   JSONB,
    
    terms_and_conditions    JSONB,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_contract_number UNIQUE (organization_id, contract_number)
);
```

### 1.4 ar.performance_obligation (IFRS 15)

```sql
CREATE TABLE ar.performance_obligation (
    obligation_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_id             UUID NOT NULL REFERENCES ar.contract(contract_id),
    organization_id         UUID NOT NULL,
    
    obligation_number       INTEGER NOT NULL,
    description             TEXT NOT NULL,
    
    -- Distinctness
    is_distinct             BOOLEAN NOT NULL DEFAULT true,
    
    -- Satisfaction pattern
    satisfaction_pattern    VARCHAR(20) NOT NULL
        CHECK (satisfaction_pattern IN ('POINT_IN_TIME', 'OVER_TIME')),
    over_time_method        VARCHAR(20)
        CHECK (over_time_method IN ('OUTPUT', 'INPUT', 'STRAIGHT_LINE')),
    progress_measure        TEXT,
    
    -- Pricing
    standalone_selling_price NUMERIC(20,6) NOT NULL,
    ssp_determination_method VARCHAR(30) NOT NULL
        CHECK (ssp_determination_method IN (
            'OBSERVABLE', 'ADJUSTED_MARKET', 'EXPECTED_COST_PLUS', 'RESIDUAL'
        )),
    allocated_transaction_price NUMERIC(20,6) NOT NULL,
    
    -- Progress
    total_satisfied_amount  NUMERIC(20,6) NOT NULL DEFAULT 0,
    satisfaction_percentage NUMERIC(5,2) NOT NULL DEFAULT 0,
    
    -- Timeline
    expected_completion_date DATE,
    actual_completion_date  DATE,
    
    -- Status
    status                  VARCHAR(20) NOT NULL DEFAULT 'NOT_STARTED'
        CHECK (status IN ('NOT_STARTED', 'IN_PROGRESS', 'SATISFIED', 'CANCELLED')),
    
    -- Account mappings
    revenue_account_id      UUID NOT NULL,
    contract_asset_account_id UUID,
    contract_liability_account_id UUID,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_obligation UNIQUE (contract_id, obligation_number)
);
```

### 1.5 ar.revenue_recognition_event

```sql
CREATE TABLE ar.revenue_recognition_event (
    event_id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    obligation_id           UUID NOT NULL REFERENCES ar.performance_obligation(obligation_id),
    organization_id         UUID NOT NULL,
    
    event_date              DATE NOT NULL,
    event_type              VARCHAR(30) NOT NULL
        CHECK (event_type IN ('SATISFACTION', 'PROGRESS_UPDATE', 'MODIFICATION', 'IMPAIRMENT')),
    
    progress_percentage     NUMERIC(5,2),
    amount_recognized       NUMERIC(20,6) NOT NULL,
    cumulative_recognized   NUMERIC(20,6) NOT NULL,
    
    measurement_details     JSONB,
    
    journal_entry_id        UUID,
    posting_batch_id        UUID,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 1.6 ar.invoice

```sql
CREATE TABLE ar.invoice (
    invoice_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    customer_id             UUID NOT NULL REFERENCES ar.customer(customer_id),
    contract_id             UUID REFERENCES ar.contract(contract_id),
    
    invoice_number          VARCHAR(30) NOT NULL,
    invoice_type            VARCHAR(20) NOT NULL
        CHECK (invoice_type IN ('STANDARD', 'CREDIT_NOTE', 'DEBIT_NOTE', 'PROFORMA')),
    
    -- Dates
    invoice_date            DATE NOT NULL,
    due_date                DATE NOT NULL,
    
    -- Currency
    currency_code           CHAR(3) NOT NULL,
    exchange_rate           NUMERIC(20,10),
    exchange_rate_type_id   UUID,
    
    -- Amounts
    subtotal                NUMERIC(20,6) NOT NULL,
    tax_amount              NUMERIC(20,6) NOT NULL DEFAULT 0,
    total_amount            NUMERIC(20,6) NOT NULL,
    amount_paid             NUMERIC(20,6) NOT NULL DEFAULT 0,
    balance_due             NUMERIC(20,6) GENERATED ALWAYS AS (total_amount - amount_paid) STORED,
    functional_currency_amount NUMERIC(20,6) NOT NULL,
    
    -- Status
    status                  VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN (
            'DRAFT', 'SUBMITTED', 'APPROVED', 'POSTED',
            'PARTIALLY_PAID', 'PAID', 'OVERDUE', 'VOID', 'DISPUTED'
        )),
    
    -- References
    payment_terms_id        UUID,
    billing_address         JSONB,
    shipping_address        JSONB,
    notes                   TEXT,
    internal_notes          TEXT,
    
    -- Accounting
    ar_control_account_id   UUID NOT NULL,
    journal_entry_id        UUID,
    posting_batch_id        UUID,
    
    -- Posting status (explicit for retries/support)
    posting_status          VARCHAR(20) NOT NULL DEFAULT 'NOT_POSTED'
        CHECK (posting_status IN ('NOT_POSTED', 'POSTING', 'POSTED', 'FAILED')),
    
    -- IFRS 9 ECL
    ecl_provision_amount    NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    -- Intercompany
    is_intercompany         BOOLEAN NOT NULL DEFAULT false,
    intercompany_org_id     UUID,
    
    -- Source
    source_document_type    VARCHAR(50),
    source_document_id      UUID,
    
    -- SoD tracking
    created_by_user_id      UUID NOT NULL,
    submitted_by_user_id    UUID,
    submitted_at            TIMESTAMPTZ,
    approved_by_user_id     UUID,
    approved_at             TIMESTAMPTZ,
    posted_by_user_id       UUID,
    posted_at               TIMESTAMPTZ,
    voided_by_user_id       UUID,
    voided_at               TIMESTAMPTZ,
    void_reason             TEXT,
    
    approval_request_id     UUID,
    correlation_id          VARCHAR(100),
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_invoice_number UNIQUE (organization_id, invoice_number)
);

CREATE INDEX idx_invoice_customer ON ar.invoice (customer_id);
CREATE INDEX idx_invoice_status ON ar.invoice (organization_id, status);
CREATE INDEX idx_invoice_due_date ON ar.invoice (organization_id, due_date) WHERE status NOT IN ('PAID', 'VOID');
CREATE INDEX idx_invoice_correlation ON ar.invoice (correlation_id) WHERE correlation_id IS NOT NULL;
```

### 1.7 ar.invoice_line

```sql
CREATE TABLE ar.invoice_line (
    line_id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id              UUID NOT NULL REFERENCES ar.invoice(invoice_id),
    line_number             INTEGER NOT NULL,
    
    -- IFRS 15 link
    obligation_id           UUID REFERENCES ar.performance_obligation(obligation_id),
    
    -- Item
    item_id                 UUID,
    description             TEXT NOT NULL,
    
    -- Quantity & Price
    quantity                NUMERIC(20,6) NOT NULL DEFAULT 1,
    unit_price              NUMERIC(20,6) NOT NULL,
    discount_percentage     NUMERIC(5,2),
    discount_amount         NUMERIC(20,6) NOT NULL DEFAULT 0,
    line_amount             NUMERIC(20,6) NOT NULL,
    
    -- Tax
    tax_code_id             UUID,
    tax_amount              NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    -- Accounting
    revenue_account_id      UUID NOT NULL,
    
    -- Dimensions
    cost_center_id          UUID,
    project_id              UUID,
    segment_id              UUID,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_invoice_line UNIQUE (invoice_id, line_number)
);
```

### 1.8 ar.customer_payment

```sql
CREATE TABLE ar.customer_payment (
    payment_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    customer_id             UUID NOT NULL REFERENCES ar.customer(customer_id),
    
    payment_number          VARCHAR(30) NOT NULL,
    payment_date            DATE NOT NULL,
    
    payment_method          VARCHAR(30) NOT NULL
        CHECK (payment_method IN (
            'CASH', 'CHECK', 'BANK_TRANSFER', 'CARD', 
            'DIRECT_DEBIT', 'MOBILE_MONEY'
        )),
    
    -- Amounts
    currency_code           CHAR(3) NOT NULL,
    amount                  NUMERIC(20,6) NOT NULL,
    exchange_rate           NUMERIC(20,10),
    functional_currency_amount NUMERIC(20,6) NOT NULL,
    
    -- Bank
    bank_account_id         UUID,
    reference               VARCHAR(100),
    description             TEXT,
    
    -- Status
    status                  VARCHAR(20) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'CLEARED', 'BOUNCED', 'REVERSED', 'VOID')),
    
    -- Accounting
    journal_entry_id        UUID,
    posting_batch_id        UUID,
    bank_reconciliation_id  UUID,
    
    -- SoD tracking
    created_by_user_id      UUID NOT NULL,
    posted_by_user_id       UUID,
    posted_at               TIMESTAMPTZ,
    
    correlation_id          VARCHAR(100),
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_payment_number UNIQUE (organization_id, payment_number)
);
```

### 1.9 ar.payment_allocation

```sql
CREATE TABLE ar.payment_allocation (
    allocation_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    payment_id              UUID NOT NULL REFERENCES ar.customer_payment(payment_id),
    invoice_id              UUID NOT NULL REFERENCES ar.invoice(invoice_id),
    
    allocated_amount        NUMERIC(20,6) NOT NULL,
    discount_taken          NUMERIC(20,6) NOT NULL DEFAULT 0,
    write_off_amount        NUMERIC(20,6) NOT NULL DEFAULT 0,
    exchange_difference     NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    allocation_date         DATE NOT NULL,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_allocation UNIQUE (payment_id, invoice_id)
);
```

### 1.10 ar.expected_credit_loss (IFRS 9)

```sql
CREATE TABLE ar.expected_credit_loss (
    ecl_id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    
    calculation_date        DATE NOT NULL,
    fiscal_period_id        UUID NOT NULL,
    
    -- Methodology
    methodology             VARCHAR(30) NOT NULL DEFAULT 'SIMPLIFIED'
        CHECK (methodology IN ('SIMPLIFIED', 'GENERAL', 'PURCHASED_CREDIT_IMPAIRED')),
    
    -- Scope (NULL customer_id = portfolio approach)
    customer_id             UUID REFERENCES ar.customer(customer_id),
    portfolio_segment       VARCHAR(50),
    
    -- Aging bucket
    aging_bucket            VARCHAR(30)
        CHECK (aging_bucket IN (
            'CURRENT', '1_30_DAYS', '31_60_DAYS', '61_90_DAYS',
            '91_180_DAYS', '181_365_DAYS', 'OVER_365_DAYS'
        )),
    
    -- ECL inputs
    gross_carrying_amount   NUMERIC(20,6) NOT NULL,
    historical_loss_rate    NUMERIC(8,5),
    forward_looking_adjustment NUMERIC(8,5),
    
    -- PD/LGD/EAD model (for general approach)
    probability_of_default  NUMERIC(8,5),
    loss_given_default      NUMERIC(8,5),
    exposure_at_default     NUMERIC(20,6),
    
    -- ECL output
    ecl_12_month            NUMERIC(20,6),
    ecl_lifetime            NUMERIC(20,6),
    ecl_stage               VARCHAR(10) NOT NULL DEFAULT 'STAGE_1'
        CHECK (ecl_stage IN ('STAGE_1', 'STAGE_2', 'STAGE_3')),
    
    -- SICR
    credit_risk_rating      VARCHAR(20),
    significant_increase_indicator BOOLEAN NOT NULL DEFAULT false,
    
    -- Provision
    provision_amount        NUMERIC(20,6) NOT NULL,
    provision_movement      NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    -- Accounting
    journal_entry_id        UUID,
    posting_batch_id        UUID,
    
    calculation_details     JSONB,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_ecl_org_period ON ar.expected_credit_loss (organization_id, fiscal_period_id);
CREATE INDEX idx_ecl_customer ON ar.expected_credit_loss (customer_id);
```

### 1.11 ar.ar_aging_snapshot

Point-in-time aging for audit evidence (Document 07).

```sql
CREATE TABLE ar.ar_aging_snapshot (
    snapshot_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    fiscal_period_id        UUID NOT NULL,
    snapshot_date           DATE NOT NULL,
    
    customer_id             UUID NOT NULL REFERENCES ar.customer(customer_id),
    aging_bucket            VARCHAR(30) NOT NULL,
    
    amount_functional       NUMERIC(20,6) NOT NULL,
    invoice_count           INTEGER NOT NULL,
    
    currency_code           CHAR(3),
    amount_original_currency NUMERIC(20,6),
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_ar_aging UNIQUE (fiscal_period_id, customer_id, aging_bucket)
);
```

---

## 2. Accounts Payable

### 2.1 ap.supplier

```sql
CREATE TABLE ap.supplier (
    supplier_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    
    supplier_code           VARCHAR(30) NOT NULL,
    supplier_type           VARCHAR(30) NOT NULL
        CHECK (supplier_type IN (
            'VENDOR', 'CONTRACTOR', 'SERVICE_PROVIDER',
            'UTILITY', 'GOVERNMENT', 'RELATED_PARTY'
        )),
    
    -- Identity
    legal_name              VARCHAR(255) NOT NULL,
    trading_name            VARCHAR(255),
    tax_identification_number VARCHAR(50),
    registration_number     VARCHAR(50),
    
    -- Payment terms
    payment_terms_days      INTEGER NOT NULL DEFAULT 30,
    currency_code           CHAR(3) NOT NULL DEFAULT 'USD',
    
    -- Defaults
    default_expense_account_id UUID,
    ap_control_account_id   UUID NOT NULL,
    supplier_group_id       UUID,
    
    -- Related party
    is_related_party        BOOLEAN NOT NULL DEFAULT false,
    related_party_relationship TEXT,
    
    -- Withholding tax
    withholding_tax_applicable BOOLEAN NOT NULL DEFAULT false,
    withholding_tax_code_id UUID,
    
    -- Contact & Address
    billing_address         JSONB,
    remittance_address      JSONB,
    primary_contact         JSONB,
    bank_details            JSONB,  -- Encrypted/masked
    
    is_active               BOOLEAN NOT NULL DEFAULT true,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_supplier_code UNIQUE (organization_id, supplier_code)
);
```

### 2.2 ap.purchase_order

```sql
CREATE TABLE ap.purchase_order (
    po_id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    supplier_id             UUID NOT NULL REFERENCES ap.supplier(supplier_id),
    
    po_number               VARCHAR(30) NOT NULL,
    po_date                 DATE NOT NULL,
    expected_delivery_date  DATE,
    
    -- Currency
    currency_code           CHAR(3) NOT NULL,
    exchange_rate           NUMERIC(20,10),
    
    -- Amounts
    subtotal                NUMERIC(20,6) NOT NULL,
    tax_amount              NUMERIC(20,6) NOT NULL DEFAULT 0,
    total_amount            NUMERIC(20,6) NOT NULL,
    amount_invoiced         NUMERIC(20,6) NOT NULL DEFAULT 0,
    amount_received         NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    -- Status
    status                  VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN (
            'DRAFT', 'PENDING_APPROVAL', 'APPROVED',
            'PARTIALLY_RECEIVED', 'RECEIVED', 'CANCELLED', 'CLOSED'
        )),
    
    shipping_address        JSONB,
    terms_and_conditions    TEXT,
    
    -- Budget / Encumbrance
    budget_id               UUID,
    commitment_journal_entry_id UUID,
    
    -- SoD tracking
    created_by_user_id      UUID NOT NULL,
    approved_by_user_id     UUID,
    approved_at             TIMESTAMPTZ,
    approval_request_id     UUID,
    
    correlation_id          VARCHAR(100),
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_po_number UNIQUE (organization_id, po_number)
);
```

### 2.3 ap.purchase_order_line

```sql
CREATE TABLE ap.purchase_order_line (
    line_id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    po_id                   UUID NOT NULL REFERENCES ap.purchase_order(po_id),
    line_number             INTEGER NOT NULL,
    
    item_id                 UUID,
    description             TEXT NOT NULL,
    
    -- Quantities
    quantity_ordered        NUMERIC(20,6) NOT NULL,
    quantity_received       NUMERIC(20,6) NOT NULL DEFAULT 0,
    quantity_invoiced       NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    -- Pricing
    unit_price              NUMERIC(20,6) NOT NULL,
    line_amount             NUMERIC(20,6) NOT NULL,
    
    -- Tax
    tax_code_id             UUID,
    tax_amount              NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    -- Accounting
    expense_account_id      UUID,
    asset_account_id        UUID,  -- For capitalized items
    
    -- Dimensions
    cost_center_id          UUID,
    project_id              UUID,
    segment_id              UUID,
    
    delivery_date           DATE,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_po_line UNIQUE (po_id, line_number)
);
```

### 2.4 ap.goods_receipt

```sql
CREATE TABLE ap.goods_receipt (
    receipt_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    supplier_id             UUID NOT NULL REFERENCES ap.supplier(supplier_id),
    po_id                   UUID NOT NULL REFERENCES ap.purchase_order(po_id),
    
    receipt_number          VARCHAR(30) NOT NULL,
    receipt_date            DATE NOT NULL,
    
    status                  VARCHAR(20) NOT NULL DEFAULT 'RECEIVED'
        CHECK (status IN ('RECEIVED', 'INSPECTING', 'ACCEPTED', 'REJECTED', 'PARTIAL')),
    
    received_by_user_id     UUID,
    warehouse_id            UUID,
    notes                   TEXT,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_receipt_number UNIQUE (organization_id, receipt_number)
);
```

### 2.5 ap.goods_receipt_line

```sql
CREATE TABLE ap.goods_receipt_line (
    line_id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    receipt_id              UUID NOT NULL REFERENCES ap.goods_receipt(receipt_id),
    po_line_id              UUID NOT NULL REFERENCES ap.purchase_order_line(line_id),
    line_number             INTEGER NOT NULL,
    
    quantity_received       NUMERIC(20,6) NOT NULL,
    quantity_accepted       NUMERIC(20,6) NOT NULL DEFAULT 0,
    quantity_rejected       NUMERIC(20,6) NOT NULL DEFAULT 0,
    rejection_reason        TEXT,
    
    location_id             UUID,
    lot_number              VARCHAR(50),
    serial_numbers          TEXT[],
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_receipt_line UNIQUE (receipt_id, line_number)
);
```

### 2.6 ap.supplier_invoice

```sql
CREATE TABLE ap.supplier_invoice (
    invoice_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    supplier_id             UUID NOT NULL REFERENCES ap.supplier(supplier_id),
    
    invoice_number          VARCHAR(30) NOT NULL,
    supplier_invoice_number VARCHAR(100),
    invoice_type            VARCHAR(20) NOT NULL
        CHECK (invoice_type IN ('STANDARD', 'CREDIT_NOTE', 'DEBIT_NOTE')),
    
    -- Dates
    invoice_date            DATE NOT NULL,
    received_date           DATE NOT NULL DEFAULT CURRENT_DATE,
    due_date                DATE NOT NULL,
    
    -- Currency
    currency_code           CHAR(3) NOT NULL,
    exchange_rate           NUMERIC(20,10),
    exchange_rate_type_id   UUID,
    
    -- Amounts
    subtotal                NUMERIC(20,6) NOT NULL,
    tax_amount              NUMERIC(20,6) NOT NULL DEFAULT 0,
    total_amount            NUMERIC(20,6) NOT NULL,
    amount_paid             NUMERIC(20,6) NOT NULL DEFAULT 0,
    balance_due             NUMERIC(20,6) GENERATED ALWAYS AS (total_amount - amount_paid) STORED,
    functional_currency_amount NUMERIC(20,6) NOT NULL,
    
    -- Status
    status                  VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN (
            'DRAFT', 'SUBMITTED', 'PENDING_APPROVAL', 'APPROVED',
            'POSTED', 'PARTIALLY_PAID', 'PAID', 'ON_HOLD', 'VOID', 'DISPUTED'
        )),
    
    -- Accounting
    ap_control_account_id   UUID NOT NULL,
    journal_entry_id        UUID,
    posting_batch_id        UUID,
    
    -- Posting status (explicit for retries/support)
    posting_status          VARCHAR(20) NOT NULL DEFAULT 'NOT_POSTED'
        CHECK (posting_status IN ('NOT_POSTED', 'POSTING', 'POSTED', 'FAILED')),
    
    -- Three-way match
    three_way_match_status  VARCHAR(20) NOT NULL DEFAULT 'PENDING'
        CHECK (three_way_match_status IN ('PENDING', 'MATCHED', 'UNMATCHED', 'EXCEPTION')),
    
    -- Withholding
    withholding_tax_amount  NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    -- Prepayment
    is_prepayment           BOOLEAN NOT NULL DEFAULT false,
    prepayment_applied      NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    -- Intercompany
    is_intercompany         BOOLEAN NOT NULL DEFAULT false,
    intercompany_org_id     UUID,
    
    -- SoD tracking
    created_by_user_id      UUID NOT NULL,
    submitted_by_user_id    UUID,
    submitted_at            TIMESTAMPTZ,
    approved_by_user_id     UUID,
    approved_at             TIMESTAMPTZ,
    posted_by_user_id       UUID,
    posted_at               TIMESTAMPTZ,
    
    approval_request_id     UUID,
    correlation_id          VARCHAR(100),
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_supplier_invoice UNIQUE (organization_id, invoice_number)
);

CREATE INDEX idx_supplier_invoice_supplier ON ap.supplier_invoice (supplier_id);
CREATE INDEX idx_supplier_invoice_status ON ap.supplier_invoice (organization_id, status);
CREATE INDEX idx_supplier_invoice_due_date ON ap.supplier_invoice (organization_id, due_date) WHERE status NOT IN ('PAID', 'VOID');
CREATE INDEX idx_supplier_invoice_correlation ON ap.supplier_invoice (correlation_id) WHERE correlation_id IS NOT NULL;
```

### 2.7 ap.supplier_invoice_line

```sql
CREATE TABLE ap.supplier_invoice_line (
    line_id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id              UUID NOT NULL REFERENCES ap.supplier_invoice(invoice_id),
    line_number             INTEGER NOT NULL,
    
    -- Matching
    po_line_id              UUID REFERENCES ap.purchase_order_line(line_id),
    goods_receipt_line_id   UUID REFERENCES ap.goods_receipt_line(line_id),
    
    item_id                 UUID,
    description             TEXT NOT NULL,
    
    -- Quantity & Price
    quantity                NUMERIC(20,6) NOT NULL DEFAULT 1,
    unit_price              NUMERIC(20,6) NOT NULL,
    line_amount             NUMERIC(20,6) NOT NULL,
    
    -- Tax
    tax_code_id             UUID,
    tax_amount              NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    -- Accounting
    expense_account_id      UUID,
    asset_account_id        UUID,
    
    -- Dimensions
    cost_center_id          UUID,
    project_id              UUID,
    segment_id              UUID,
    
    -- Capitalization
    capitalize_flag         BOOLEAN NOT NULL DEFAULT false,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_supplier_invoice_line UNIQUE (invoice_id, line_number)
);
```

### 2.8 ap.supplier_payment

```sql
CREATE TABLE ap.supplier_payment (
    payment_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    supplier_id             UUID NOT NULL REFERENCES ap.supplier(supplier_id),
    
    payment_batch_id        UUID,
    payment_number          VARCHAR(30) NOT NULL,
    payment_date            DATE NOT NULL,
    
    payment_method          VARCHAR(30) NOT NULL
        CHECK (payment_method IN ('CHECK', 'BANK_TRANSFER', 'WIRE', 'ACH', 'CARD')),
    
    -- Amounts
    currency_code           CHAR(3) NOT NULL,
    amount                  NUMERIC(20,6) NOT NULL,
    exchange_rate           NUMERIC(20,10),
    functional_currency_amount NUMERIC(20,6) NOT NULL,
    
    -- Bank
    bank_account_id         UUID NOT NULL,
    reference               VARCHAR(100),
    
    -- Status
    status                  VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'PENDING', 'APPROVED', 'SENT', 'CLEARED', 'VOID', 'REJECTED')),
    
    -- Accounting
    journal_entry_id        UUID,
    posting_batch_id        UUID,
    bank_reconciliation_id  UUID,
    
    -- Withholding
    withholding_tax_amount  NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    -- Remittance
    remittance_advice_sent  BOOLEAN NOT NULL DEFAULT false,
    remittance_sent_at      TIMESTAMPTZ,
    
    -- SoD tracking
    created_by_user_id      UUID NOT NULL,
    approved_by_user_id     UUID,
    approved_at             TIMESTAMPTZ,
    posted_by_user_id       UUID,
    posted_at               TIMESTAMPTZ,
    
    approval_request_id     UUID,
    correlation_id          VARCHAR(100),
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_supplier_payment UNIQUE (organization_id, payment_number)
);
```

### 2.9 ap.payment_allocation

```sql
CREATE TABLE ap.payment_allocation (
    allocation_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    payment_id              UUID NOT NULL REFERENCES ap.supplier_payment(payment_id),
    invoice_id              UUID NOT NULL REFERENCES ap.supplier_invoice(invoice_id),
    
    allocated_amount        NUMERIC(20,6) NOT NULL,
    discount_taken          NUMERIC(20,6) NOT NULL DEFAULT 0,
    exchange_difference     NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    allocation_date         DATE NOT NULL,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_ap_allocation UNIQUE (payment_id, invoice_id)
);
```

### 2.10 ap.payment_batch

```sql
CREATE TABLE ap.payment_batch (
    batch_id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    
    batch_number            VARCHAR(30) NOT NULL,
    batch_date              DATE NOT NULL,
    payment_method          VARCHAR(30) NOT NULL,
    
    bank_account_id         UUID NOT NULL,
    currency_code           CHAR(3) NOT NULL,
    
    total_payments          INTEGER NOT NULL DEFAULT 0,
    total_amount            NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    status                  VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'APPROVED', 'PROCESSING', 'COMPLETED', 'FAILED')),
    
    -- Bank file generation
    bank_file_generated     BOOLEAN NOT NULL DEFAULT false,
    bank_file_reference     VARCHAR(100),
    bank_file_generated_at  TIMESTAMPTZ,
    
    created_by_user_id      UUID NOT NULL,
    approved_by_user_id     UUID,
    approved_at             TIMESTAMPTZ,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_payment_batch UNIQUE (organization_id, batch_number)
);
```

### 2.11 ap.ap_aging_snapshot

```sql
CREATE TABLE ap.ap_aging_snapshot (
    snapshot_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    fiscal_period_id        UUID NOT NULL,
    snapshot_date           DATE NOT NULL,
    
    supplier_id             UUID NOT NULL REFERENCES ap.supplier(supplier_id),
    aging_bucket            VARCHAR(30) NOT NULL,
    
    amount_functional       NUMERIC(20,6) NOT NULL,
    invoice_count           INTEGER NOT NULL,
    
    currency_code           CHAR(3),
    amount_original_currency NUMERIC(20,6),
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_ap_aging UNIQUE (fiscal_period_id, supplier_id, aging_bucket)
);
```

---

## 3. Fixed Assets (IAS 16, IAS 36, IAS 38)

### 3.1 fa.asset_category

```sql
CREATE TABLE fa.asset_category (
    category_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    
    category_code           VARCHAR(20) NOT NULL,
    category_name           VARCHAR(100) NOT NULL,
    
    -- Type
    asset_type              VARCHAR(30) NOT NULL
        CHECK (asset_type IN ('TANGIBLE', 'INTANGIBLE', 'INVESTMENT_PROPERTY', 'ROU_ASSET')),
    ifrs_classification     VARCHAR(50),
    
    -- Defaults
    default_useful_life_months INTEGER NOT NULL,
    default_depreciation_method VARCHAR(30) NOT NULL DEFAULT 'STRAIGHT_LINE'
        CHECK (default_depreciation_method IN (
            'STRAIGHT_LINE', 'DECLINING_BALANCE', 'UNITS_OF_PRODUCTION', 'SUM_OF_YEARS'
        )),
    default_residual_value_pct NUMERIC(5,2) NOT NULL DEFAULT 0,
    
    -- Account mappings
    default_asset_account_id UUID,
    default_accum_depr_account_id UUID,
    default_depr_expense_account_id UUID,
    default_impairment_account_id UUID,
    default_gain_loss_account_id UUID,
    default_reval_reserve_account_id UUID,
    
    -- Options
    revaluation_model_allowed BOOLEAN NOT NULL DEFAULT false,
    componentization_required BOOLEAN NOT NULL DEFAULT false,
    
    is_active               BOOLEAN NOT NULL DEFAULT true,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_asset_category UNIQUE (organization_id, category_code)
);
```

### 3.2 fa.asset

```sql
CREATE TABLE fa.asset (
    asset_id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    
    asset_number            VARCHAR(30) NOT NULL,
    asset_name              VARCHAR(200) NOT NULL,
    description             TEXT,
    
    -- Classification
    category_id             UUID NOT NULL REFERENCES fa.asset_category(category_id),
    asset_type              VARCHAR(30) NOT NULL,
    
    -- Componentization
    parent_asset_id         UUID REFERENCES fa.asset(asset_id),
    
    -- Physical details
    serial_number           VARCHAR(100),
    model_number            VARCHAR(100),
    manufacturer            VARCHAR(100),
    location_id             UUID,
    custodian_user_id       UUID,
    
    -- Dimensions
    business_unit_id        UUID,
    cost_center_id          UUID,
    segment_id              UUID,
    project_id              UUID,
    
    -- Acquisition
    acquisition_date        DATE NOT NULL,
    in_service_date         DATE NOT NULL,
    acquisition_cost        NUMERIC(20,6) NOT NULL,
    acquisition_currency_code CHAR(3) NOT NULL,
    acquisition_exchange_rate NUMERIC(20,10),
    functional_currency_cost NUMERIC(20,6) NOT NULL,
    capitalized_costs       NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    -- Depreciation
    residual_value          NUMERIC(20,6) NOT NULL DEFAULT 0,
    useful_life_months      INTEGER NOT NULL,
    remaining_life_months   INTEGER NOT NULL,
    depreciation_method     VARCHAR(30) NOT NULL,
    depreciation_convention VARCHAR(20) NOT NULL DEFAULT 'FULL_MONTH'
        CHECK (depreciation_convention IN ('FULL_MONTH', 'HALF_MONTH', 'MID_QUARTER', 'HALF_YEAR')),
    depreciation_start_date DATE,
    
    -- Measurement model (IAS 16)
    measurement_model       VARCHAR(20) NOT NULL DEFAULT 'COST'
        CHECK (measurement_model IN ('COST', 'REVALUATION', 'FAIR_VALUE')),
    fair_value_level        VARCHAR(10)
        CHECK (fair_value_level IN ('LEVEL_1', 'LEVEL_2', 'LEVEL_3')),
    last_revaluation_date   DATE,
    revalued_amount         NUMERIC(20,6),
    revaluation_surplus     NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    -- Current values
    accumulated_depreciation NUMERIC(20,6) NOT NULL DEFAULT 0,
    accumulated_impairment  NUMERIC(20,6) NOT NULL DEFAULT 0,
    net_book_value          NUMERIC(20,6) GENERATED ALWAYS AS (
        functional_currency_cost + capitalized_costs 
        - accumulated_depreciation - accumulated_impairment 
        + COALESCE(revaluation_surplus, 0)
    ) STORED,
    
    -- Impairment (IAS 36)
    impairment_indicator_check_date DATE,
    recoverable_amount      NUMERIC(20,6),
    value_in_use            NUMERIC(20,6),
    fair_value_less_costs   NUMERIC(20,6),
    cgu_id                  UUID,
    
    -- Status
    status                  VARCHAR(30) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN (
            'UNDER_CONSTRUCTION', 'ACTIVE', 'FULLY_DEPRECIATED',
            'IMPAIRED', 'HELD_FOR_SALE', 'DISPOSED'
        )),
    
    -- Disposal
    disposal_date           DATE,
    disposal_proceeds       NUMERIC(20,6),
    disposal_costs          NUMERIC(20,6),
    disposal_gain_loss      NUMERIC(20,6),
    
    -- Source
    supplier_id             UUID,
    purchase_invoice_id     UUID,
    
    -- Insurance & warranty
    warranty_expiry_date    DATE,
    insurance_policy_number VARCHAR(50),
    insurance_value         NUMERIC(20,6),
    
    -- Tax depreciation
    tax_depreciation_method VARCHAR(30),
    tax_useful_life_months  INTEGER,
    tax_written_down_value  NUMERIC(20,6),
    
    user_defined_fields     JSONB,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_asset_number UNIQUE (organization_id, asset_number)
);

CREATE INDEX idx_asset_category ON fa.asset (category_id);
CREATE INDEX idx_asset_status ON fa.asset (organization_id, status);
CREATE INDEX idx_asset_location ON fa.asset (location_id);
```

### 3.3 fa.asset_component (IAS 16 Componentization)

```sql
CREATE TABLE fa.asset_component (
    component_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_asset_id         UUID NOT NULL REFERENCES fa.asset(asset_id),
    
    component_name          VARCHAR(100) NOT NULL,
    description             TEXT,
    
    cost                    NUMERIC(20,6) NOT NULL,
    residual_value          NUMERIC(20,6) NOT NULL DEFAULT 0,
    useful_life_months      INTEGER NOT NULL,
    depreciation_method     VARCHAR(30) NOT NULL,
    
    accumulated_depreciation NUMERIC(20,6) NOT NULL DEFAULT 0,
    net_book_value          NUMERIC(20,6) GENERATED ALWAYS AS (cost - accumulated_depreciation) STORED,
    
    replacement_date        DATE,
    replacement_cost        NUMERIC(20,6),
    
    is_active               BOOLEAN NOT NULL DEFAULT true,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 3.4 fa.depreciation_run

```sql
CREATE TABLE fa.depreciation_run (
    run_id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    fiscal_period_id        UUID NOT NULL,
    
    run_date                DATE NOT NULL,
    run_number              INTEGER NOT NULL,
    
    status                  VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'CALCULATED', 'APPROVED', 'POSTED', 'CANCELLED')),
    
    asset_count             INTEGER NOT NULL DEFAULT 0,
    total_depreciation      NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    journal_entry_id        UUID,
    posting_batch_id        UUID,
    
    -- Posting status (explicit for retries/support)
    posting_status          VARCHAR(20) NOT NULL DEFAULT 'NOT_POSTED'
        CHECK (posting_status IN ('NOT_POSTED', 'POSTING', 'POSTED', 'FAILED')),
    
    -- SoD tracking
    calculated_by_user_id   UUID,
    calculated_at           TIMESTAMPTZ,
    approved_by_user_id     UUID,
    approved_at             TIMESTAMPTZ,
    posted_by_user_id       UUID,
    posted_at               TIMESTAMPTZ,
    
    correlation_id          VARCHAR(100),
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_depreciation_run UNIQUE (organization_id, fiscal_period_id, run_number)
);

CREATE INDEX idx_depreciation_run_status ON fa.depreciation_run (organization_id, status);
CREATE INDEX idx_depreciation_run_correlation ON fa.depreciation_run (correlation_id) WHERE correlation_id IS NOT NULL;
```

### 3.5 fa.depreciation_schedule

```sql
CREATE TABLE fa.depreciation_schedule (
    schedule_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id                UUID NOT NULL REFERENCES fa.asset(asset_id),
    component_id            UUID REFERENCES fa.asset_component(component_id),
    depreciation_run_id     UUID REFERENCES fa.depreciation_run(run_id),
    fiscal_period_id        UUID NOT NULL,
    
    depreciation_date       DATE NOT NULL,
    depreciation_amount     NUMERIC(20,6) NOT NULL,
    accumulated_depreciation NUMERIC(20,6) NOT NULL,
    net_book_value          NUMERIC(20,6) NOT NULL,
    calculation_basis       NUMERIC(20,6) NOT NULL,
    days_in_period          INTEGER NOT NULL,
    
    is_posted               BOOLEAN NOT NULL DEFAULT false,
    journal_entry_line_id   UUID,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_depr_schedule_asset ON fa.depreciation_schedule (asset_id, fiscal_period_id);
```

### 3.6 fa.asset_revaluation

```sql
CREATE TABLE fa.asset_revaluation (
    revaluation_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id                UUID NOT NULL REFERENCES fa.asset(asset_id),
    organization_id         UUID NOT NULL,
    
    revaluation_date        DATE NOT NULL,
    
    valuer_name             VARCHAR(100),
    valuation_method        VARCHAR(50) NOT NULL,
    
    previous_carrying_amount NUMERIC(20,6) NOT NULL,
    fair_value              NUMERIC(20,6) NOT NULL,
    
    revaluation_increase    NUMERIC(20,6) NOT NULL DEFAULT 0,
    revaluation_decrease    NUMERIC(20,6) NOT NULL DEFAULT 0,
    revaluation_surplus_movement NUMERIC(20,6) NOT NULL DEFAULT 0,
    retained_earnings_impact NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    journal_entry_id        UUID,
    posting_batch_id        UUID,
    
    supporting_documentation JSONB,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 3.7 fa.cash_generating_unit (IAS 36)

```sql
CREATE TABLE fa.cash_generating_unit (
    cgu_id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    
    cgu_code                VARCHAR(20) NOT NULL,
    cgu_name                VARCHAR(100) NOT NULL,
    description             TEXT,
    
    segment_id              UUID,
    
    allocated_goodwill      NUMERIC(20,6) NOT NULL DEFAULT 0,
    allocated_corporate_assets NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    last_impairment_test_date DATE,
    
    is_active               BOOLEAN NOT NULL DEFAULT true,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_cgu_code UNIQUE (organization_id, cgu_code)
);
```

### 3.8 fa.asset_impairment (IAS 36)

```sql
CREATE TABLE fa.asset_impairment (
    impairment_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    
    asset_id                UUID REFERENCES fa.asset(asset_id),
    cgu_id                  UUID REFERENCES fa.cash_generating_unit(cgu_id),
    
    impairment_date         DATE NOT NULL,
    fiscal_period_id        UUID NOT NULL,
    
    impairment_indicator    TEXT NOT NULL,
    
    carrying_amount_before  NUMERIC(20,6) NOT NULL,
    recoverable_amount      NUMERIC(20,6) NOT NULL,
    value_in_use            NUMERIC(20,6),
    fair_value_less_costs   NUMERIC(20,6),
    discount_rate_used      NUMERIC(8,5),
    
    impairment_loss         NUMERIC(20,6) NOT NULL,
    allocation_to_goodwill  NUMERIC(20,6) NOT NULL DEFAULT 0,
    allocation_to_assets    JSONB,  -- { asset_id: amount }
    
    is_reversal             BOOLEAN NOT NULL DEFAULT false,
    reversal_amount         NUMERIC(20,6),
    
    journal_entry_id        UUID,
    posting_batch_id        UUID,
    
    assessment_documentation JSONB,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 3.9 fa.asset_disposal

```sql
CREATE TABLE fa.asset_disposal (
    disposal_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id                UUID NOT NULL REFERENCES fa.asset(asset_id),
    organization_id         UUID NOT NULL,
    
    disposal_date           DATE NOT NULL,
    fiscal_period_id        UUID NOT NULL,
    
    disposal_type           VARCHAR(20) NOT NULL
        CHECK (disposal_type IN ('SALE', 'SCRAP', 'TRADE_IN', 'DONATION', 'WRITE_OFF')),
    disposal_reason         TEXT,
    
    carrying_amount         NUMERIC(20,6) NOT NULL,
    accumulated_depreciation NUMERIC(20,6) NOT NULL,
    disposal_proceeds       NUMERIC(20,6) NOT NULL DEFAULT 0,
    disposal_costs          NUMERIC(20,6) NOT NULL DEFAULT 0,
    gain_loss               NUMERIC(20,6) NOT NULL,
    
    customer_id             UUID,
    invoice_id              UUID,
    
    journal_entry_id        UUID,
    posting_batch_id        UUID,
    
    approval_status         VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    approved_by_user_id     UUID,
    approved_at             TIMESTAMPTZ,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 4. Leases (IFRS 16)

### 4.1 lease.lease_contract

```sql
CREATE TABLE lease.lease_contract (
    lease_id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    
    lease_number            VARCHAR(30) NOT NULL,
    lease_name              VARCHAR(200) NOT NULL,
    
    -- Counterparty
    counterparty_id         UUID NOT NULL,
    counterparty_type       VARCHAR(20) NOT NULL
        CHECK (counterparty_type IN ('CUSTOMER', 'SUPPLIER')),
    
    -- Type
    lease_type              VARCHAR(20) NOT NULL
        CHECK (lease_type IN ('LESSEE', 'LESSOR')),
    
    underlying_asset_type   VARCHAR(30) NOT NULL
        CHECK (underlying_asset_type IN (
            'PROPERTY', 'VEHICLE', 'EQUIPMENT', 'IT_EQUIPMENT', 
            'AIRCRAFT', 'VESSEL', 'OTHER'
        )),
    underlying_asset_description TEXT NOT NULL,
    
    -- Timeline
    commencement_date       DATE NOT NULL,
    end_date                DATE NOT NULL,
    lease_term_months       INTEGER NOT NULL,
    
    -- Currency
    currency_code           CHAR(3) NOT NULL,
    
    -- Classification
    classification          VARCHAR(20) NOT NULL
        CHECK (classification IN ('FINANCE', 'OPERATING', 'SHORT_TERM', 'LOW_VALUE')),
    is_short_term_exemption BOOLEAN NOT NULL DEFAULT false,
    is_low_value_exemption  BOOLEAN NOT NULL DEFAULT false,
    
    -- Options
    has_purchase_option     BOOLEAN NOT NULL DEFAULT false,
    purchase_option_amount  NUMERIC(20,6),
    purchase_option_reasonably_certain BOOLEAN NOT NULL DEFAULT false,
    has_extension_option    BOOLEAN NOT NULL DEFAULT false,
    extension_periods       JSONB,
    extension_reasonably_certain BOOLEAN NOT NULL DEFAULT false,
    has_termination_option  BOOLEAN NOT NULL DEFAULT false,
    termination_option_details TEXT,
    has_variable_payments   BOOLEAN NOT NULL DEFAULT false,
    variable_payment_terms  JSONB,
    
    -- Guarantees
    residual_value_guarantee NUMERIC(20,6),
    
    -- Rates
    incremental_borrowing_rate NUMERIC(8,5) NOT NULL,
    implicit_rate           NUMERIC(8,5),
    discount_rate_used      NUMERIC(8,5) NOT NULL,
    
    -- Initial measurement
    initial_rou_asset_amount NUMERIC(20,6) NOT NULL,
    initial_lease_liability_amount NUMERIC(20,6) NOT NULL,
    initial_direct_costs    NUMERIC(20,6) NOT NULL DEFAULT 0,
    lease_incentives_received NUMERIC(20,6) NOT NULL DEFAULT 0,
    prepaid_lease_payments  NUMERIC(20,6) NOT NULL DEFAULT 0,
    restoration_obligation  NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    -- Account mappings
    rou_asset_account_id    UUID,
    lease_liability_account_id UUID,
    interest_expense_account_id UUID,
    depreciation_expense_account_id UUID,
    
    -- Status
    status                  VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'ACTIVE', 'MODIFIED', 'TERMINATED', 'EXPIRED')),
    
    modification_history    JSONB,
    
    -- Dimensions
    business_unit_id        UUID,
    cost_center_id          UUID,
    segment_id              UUID,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_lease_number UNIQUE (organization_id, lease_number)
);
```

### 4.2 lease.lease_payment_schedule

```sql
CREATE TABLE lease.lease_payment_schedule (
    schedule_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lease_id                UUID NOT NULL REFERENCES lease.lease_contract(lease_id),
    
    payment_number          INTEGER NOT NULL,
    payment_date            DATE NOT NULL,
    
    -- Amounts
    payment_amount          NUMERIC(20,6) NOT NULL,
    fixed_payment           NUMERIC(20,6) NOT NULL,
    variable_payment        NUMERIC(20,6) NOT NULL DEFAULT 0,
    is_variable_included_in_liability BOOLEAN NOT NULL DEFAULT false,
    
    payment_type            VARCHAR(30) NOT NULL DEFAULT 'BASE_RENT'
        CHECK (payment_type IN ('BASE_RENT', 'CAM', 'INSURANCE', 'TAXES', 'OTHER')),
    
    -- Amortization
    principal_portion       NUMERIC(20,6) NOT NULL,
    interest_portion        NUMERIC(20,6) NOT NULL,
    opening_liability_balance NUMERIC(20,6) NOT NULL,
    closing_liability_balance NUMERIC(20,6) NOT NULL,
    
    -- Payment tracking
    is_paid                 BOOLEAN NOT NULL DEFAULT false,
    actual_payment_date     DATE,
    actual_payment_amount   NUMERIC(20,6),
    variance_amount         NUMERIC(20,6),
    
    journal_entry_id        UUID,
    posting_batch_id        UUID,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_lease_payment UNIQUE (lease_id, payment_number)
);
```

### 4.3 lease.rou_asset

```sql
CREATE TABLE lease.rou_asset (
    rou_asset_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lease_id                UUID NOT NULL REFERENCES lease.lease_contract(lease_id),
    asset_id                UUID,  -- Link to FA module
    
    initial_measurement     NUMERIC(20,6) NOT NULL,
    depreciation_method     VARCHAR(30) NOT NULL DEFAULT 'STRAIGHT_LINE',
    useful_life_months      INTEGER NOT NULL,
    
    accumulated_depreciation NUMERIC(20,6) NOT NULL DEFAULT 0,
    impairment_loss         NUMERIC(20,6) NOT NULL DEFAULT 0,
    revaluation_adjustment  NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    carrying_amount         NUMERIC(20,6) GENERATED ALWAYS AS (
        initial_measurement - accumulated_depreciation - impairment_loss + revaluation_adjustment
    ) STORED,
    
    last_depreciation_date  DATE,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_rou_lease UNIQUE (lease_id)
);
```

### 4.4 lease.lease_run

```sql
CREATE TABLE lease.lease_run (
    run_id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    fiscal_period_id        UUID NOT NULL,
    
    run_date                DATE NOT NULL,
    run_number              INTEGER NOT NULL,
    
    status                  VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'CALCULATED', 'APPROVED', 'POSTED', 'CANCELLED')),
    
    lease_count             INTEGER NOT NULL DEFAULT 0,
    total_interest_expense  NUMERIC(20,6) NOT NULL DEFAULT 0,
    total_depreciation_expense NUMERIC(20,6) NOT NULL DEFAULT 0,
    total_liability_reduction NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    journal_entry_id        UUID,
    posting_batch_id        UUID,
    
    -- Posting status (explicit for retries/support)
    posting_status          VARCHAR(20) NOT NULL DEFAULT 'NOT_POSTED'
        CHECK (posting_status IN ('NOT_POSTED', 'POSTING', 'POSTED', 'FAILED')),
    
    calculated_by_user_id   UUID,
    calculated_at           TIMESTAMPTZ,
    approved_by_user_id     UUID,
    approved_at             TIMESTAMPTZ,
    posted_by_user_id       UUID,
    posted_at               TIMESTAMPTZ,
    
    correlation_id          VARCHAR(100),
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_lease_run UNIQUE (organization_id, fiscal_period_id, run_number)
);

CREATE INDEX idx_lease_run_status ON lease.lease_run (organization_id, status);
CREATE INDEX idx_lease_run_correlation ON lease.lease_run (correlation_id) WHERE correlation_id IS NOT NULL;
```

### 4.5 lease.lease_modification

```sql
CREATE TABLE lease.lease_modification (
    modification_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lease_id                UUID NOT NULL REFERENCES lease.lease_contract(lease_id),
    
    modification_date       DATE NOT NULL,
    modification_type       VARCHAR(30) NOT NULL
        CHECK (modification_type IN (
            'SCOPE_INCREASE', 'SCOPE_DECREASE', 'TERM_CHANGE', 
            'CONSIDERATION_CHANGE', 'REASSESSMENT'
        )),
    
    is_separate_lease       BOOLEAN NOT NULL DEFAULT false,
    
    previous_lease_liability NUMERIC(20,6) NOT NULL,
    previous_rou_asset      NUMERIC(20,6) NOT NULL,
    revised_lease_liability NUMERIC(20,6) NOT NULL,
    revised_rou_asset       NUMERIC(20,6) NOT NULL,
    gain_loss_recognized    NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    revised_discount_rate   NUMERIC(8,5),
    effective_date          DATE NOT NULL,
    
    journal_entry_id        UUID,
    posting_batch_id        UUID,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 5. Inventory (IAS 2)

### 5.1 inv.item_category

```sql
CREATE TABLE inv.item_category (
    category_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    
    category_code           VARCHAR(20) NOT NULL,
    category_name           VARCHAR(100) NOT NULL,
    parent_category_id      UUID REFERENCES inv.item_category(category_id),
    
    is_active               BOOLEAN NOT NULL DEFAULT true,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_item_category UNIQUE (organization_id, category_code)
);
```

### 5.2 inv.item

```sql
CREATE TABLE inv.item (
    item_id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    
    item_code               VARCHAR(50) NOT NULL,
    item_name               VARCHAR(200) NOT NULL,
    description             TEXT,
    
    item_type               VARCHAR(20) NOT NULL
        CHECK (item_type IN ('INVENTORY', 'SERVICE', 'NON_INVENTORY', 'KIT', 'PHANTOM')),
    category_id             UUID REFERENCES inv.item_category(category_id),
    unit_of_measure         VARCHAR(20) NOT NULL,
    
    -- Flags
    is_purchasable          BOOLEAN NOT NULL DEFAULT true,
    is_sellable             BOOLEAN NOT NULL DEFAULT true,
    is_stockable            BOOLEAN NOT NULL DEFAULT true,
    
    -- Costing
    costing_method          VARCHAR(30) NOT NULL DEFAULT 'WEIGHTED_AVERAGE'
        CHECK (costing_method IN ('FIFO', 'WEIGHTED_AVERAGE', 'SPECIFIC_ID', 'STANDARD')),
    standard_cost           NUMERIC(20,6),
    last_purchase_cost      NUMERIC(20,6),
    average_cost            NUMERIC(20,6),
    list_price              NUMERIC(20,6),
    
    -- Inventory levels
    minimum_stock_level     NUMERIC(20,6),
    reorder_point           NUMERIC(20,6),
    reorder_quantity        NUMERIC(20,6),
    lead_time_days          INTEGER,
    
    -- Account mappings
    inventory_account_id    UUID,
    cogs_account_id         UUID,
    revenue_account_id      UUID,
    purchase_account_id     UUID,
    inventory_adjustment_account_id UUID,
    nrv_provision_account_id UUID,
    
    -- Tax
    tax_code_purchase_id    UUID,
    tax_code_sales_id       UUID,
    
    -- Physical
    weight                  NUMERIC(10,4),
    weight_unit             VARCHAR(10),
    dimensions              JSONB,
    
    -- Tracking
    is_lot_tracked          BOOLEAN NOT NULL DEFAULT false,
    is_serial_tracked       BOOLEAN NOT NULL DEFAULT false,
    shelf_life_days         INTEGER,
    
    is_active               BOOLEAN NOT NULL DEFAULT true,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_item_code UNIQUE (organization_id, item_code)
);
```

### 5.3 inv.warehouse

```sql
CREATE TABLE inv.warehouse (
    warehouse_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    
    warehouse_code          VARCHAR(20) NOT NULL,
    warehouse_name          VARCHAR(100) NOT NULL,
    location_id             UUID,
    
    is_default              BOOLEAN NOT NULL DEFAULT false,
    is_active               BOOLEAN NOT NULL DEFAULT true,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_warehouse UNIQUE (organization_id, warehouse_code)
);
```

### 5.4 inv.warehouse_location

```sql
CREATE TABLE inv.warehouse_location (
    location_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    warehouse_id            UUID NOT NULL REFERENCES inv.warehouse(warehouse_id),
    
    location_code           VARCHAR(30) NOT NULL,
    location_name           VARCHAR(100) NOT NULL,
    location_type           VARCHAR(20) NOT NULL
        CHECK (location_type IN ('BULK', 'PICKING', 'RECEIVING', 'SHIPPING', 'QUARANTINE')),
    capacity                NUMERIC(20,6),
    
    is_active               BOOLEAN NOT NULL DEFAULT true,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_warehouse_location UNIQUE (warehouse_id, location_code)
);
```

### 5.5 inv.inventory_balance

```sql
CREATE TABLE inv.inventory_balance (
    balance_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    item_id                 UUID NOT NULL REFERENCES inv.item(item_id),
    warehouse_id            UUID NOT NULL REFERENCES inv.warehouse(warehouse_id),
    location_id             UUID REFERENCES inv.warehouse_location(location_id),
    
    lot_number              VARCHAR(50),
    serial_number           VARCHAR(100),
    
    quantity_on_hand        NUMERIC(20,6) NOT NULL DEFAULT 0,
    quantity_reserved       NUMERIC(20,6) NOT NULL DEFAULT 0,
    quantity_available      NUMERIC(20,6) GENERATED ALWAYS AS (quantity_on_hand - quantity_reserved) STORED,
    quantity_on_order       NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    unit_cost               NUMERIC(20,6) NOT NULL,
    total_cost              NUMERIC(20,6) GENERATED ALWAYS AS (quantity_on_hand * unit_cost) STORED,
    
    last_count_date         DATE,
    last_movement_date      DATE,
    expiry_date             DATE,
    
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_inv_balance_item ON inv.inventory_balance (item_id, warehouse_id);
```

### 5.6 inv.inventory_transaction

```sql
CREATE TABLE inv.inventory_transaction (
    transaction_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    
    transaction_type        VARCHAR(20) NOT NULL
        CHECK (transaction_type IN ('RECEIPT', 'ISSUE', 'TRANSFER', 'ADJUSTMENT', 'COUNT', 'RETURN')),
    transaction_date        DATE NOT NULL,
    
    item_id                 UUID NOT NULL REFERENCES inv.item(item_id),
    warehouse_id            UUID NOT NULL REFERENCES inv.warehouse(warehouse_id),
    from_location_id        UUID REFERENCES inv.warehouse_location(location_id),
    to_location_id          UUID REFERENCES inv.warehouse_location(location_id),
    
    quantity                NUMERIC(20,6) NOT NULL,
    unit_cost               NUMERIC(20,6) NOT NULL,
    total_cost              NUMERIC(20,6) NOT NULL,
    
    lot_number              VARCHAR(50),
    serial_number           VARCHAR(100),
    
    reference_type          VARCHAR(30)
        CHECK (reference_type IN ('PO', 'SO', 'PRODUCTION', 'ADJUSTMENT', 'TRANSFER')),
    reference_id            UUID,
    reason_code             VARCHAR(30),
    notes                   TEXT,
    
    journal_entry_id        UUID,
    posting_batch_id        UUID,
    
    created_by_user_id      UUID NOT NULL,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_inv_txn_item ON inv.inventory_transaction (item_id, transaction_date);
```

### 5.7 inv.cost_layer (FIFO)

```sql
CREATE TABLE inv.cost_layer (
    layer_id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    item_id                 UUID NOT NULL REFERENCES inv.item(item_id),
    warehouse_id            UUID NOT NULL REFERENCES inv.warehouse(warehouse_id),
    
    receipt_date            DATE NOT NULL,
    receipt_reference       VARCHAR(100),
    receipt_transaction_id  UUID REFERENCES inv.inventory_transaction(transaction_id),
    
    original_quantity       NUMERIC(20,6) NOT NULL,
    remaining_quantity      NUMERIC(20,6) NOT NULL,
    unit_cost               NUMERIC(20,6) NOT NULL,
    
    lot_number              VARCHAR(50),
    expiry_date             DATE,
    
    is_depleted             BOOLEAN NOT NULL DEFAULT false,
    depleted_at             TIMESTAMPTZ,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_cost_layer_active ON inv.cost_layer (item_id, warehouse_id, is_depleted, receipt_date);
```

### 5.8 inv.inventory_valuation_run (IAS 2 NRV)

```sql
CREATE TABLE inv.inventory_valuation_run (
    run_id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    fiscal_period_id        UUID NOT NULL,
    
    run_date                DATE NOT NULL,
    run_number              INTEGER NOT NULL,
    
    status                  VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'CALCULATED', 'APPROVED', 'POSTED', 'CANCELLED')),
    
    item_count              INTEGER NOT NULL DEFAULT 0,
    total_carrying_amount   NUMERIC(20,6) NOT NULL DEFAULT 0,
    total_nrv               NUMERIC(20,6) NOT NULL DEFAULT 0,
    total_write_down        NUMERIC(20,6) NOT NULL DEFAULT 0,
    total_reversal          NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    journal_entry_id        UUID,
    posting_batch_id        UUID,
    
    -- Posting status (explicit for retries/support)
    posting_status          VARCHAR(20) NOT NULL DEFAULT 'NOT_POSTED'
        CHECK (posting_status IN ('NOT_POSTED', 'POSTING', 'POSTED', 'FAILED')),
    
    calculated_by_user_id   UUID,
    calculated_at           TIMESTAMPTZ,
    approved_by_user_id     UUID,
    approved_at             TIMESTAMPTZ,
    posted_by_user_id       UUID,
    posted_at               TIMESTAMPTZ,
    
    correlation_id          VARCHAR(100),
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_inv_valuation_run UNIQUE (organization_id, fiscal_period_id, run_number)
);

CREATE INDEX idx_inv_valuation_run_status ON inv.inventory_valuation_run (organization_id, status);
CREATE INDEX idx_inv_valuation_run_correlation ON inv.inventory_valuation_run (correlation_id) WHERE correlation_id IS NOT NULL;
```

### 5.9 inv.nrv_assessment

```sql
CREATE TABLE inv.nrv_assessment (
    assessment_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    valuation_run_id        UUID NOT NULL REFERENCES inv.inventory_valuation_run(run_id),
    organization_id         UUID NOT NULL,
    
    item_id                 UUID NOT NULL REFERENCES inv.item(item_id),
    warehouse_id            UUID REFERENCES inv.warehouse(warehouse_id),
    
    quantity_assessed       NUMERIC(20,6) NOT NULL,
    carrying_amount         NUMERIC(20,6) NOT NULL,
    
    estimated_selling_price NUMERIC(20,6) NOT NULL,
    estimated_costs_to_complete NUMERIC(20,6) NOT NULL DEFAULT 0,
    estimated_selling_costs NUMERIC(20,6) NOT NULL DEFAULT 0,
    net_realizable_value    NUMERIC(20,6) NOT NULL,
    
    write_down_required     BOOLEAN NOT NULL DEFAULT false,
    write_down_amount       NUMERIC(20,6) NOT NULL DEFAULT 0,
    reversal_amount         NUMERIC(20,6) NOT NULL DEFAULT 0,
    cumulative_provision    NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    assessment_notes        TEXT,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 6. Financial Instruments (IFRS 9)

### 6.1 fin_inst.financial_instrument

```sql
CREATE TABLE fin_inst.financial_instrument (
    instrument_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    
    instrument_code         VARCHAR(50) NOT NULL,
    instrument_name         VARCHAR(200) NOT NULL,
    
    -- Classification
    instrument_type         VARCHAR(20) NOT NULL
        CHECK (instrument_type IN ('DEBT', 'EQUITY', 'DERIVATIVE', 'HYBRID')),
    instrument_subtype      VARCHAR(30) NOT NULL
        CHECK (instrument_subtype IN (
            'BOND', 'LOAN', 'SHARE', 'OPTION', 'FORWARD', 
            'SWAP', 'CONVERTIBLE', 'FUND', 'DEPOSIT'
        )),
    
    -- IFRS 9 classification
    classification          VARCHAR(30) NOT NULL
        CHECK (classification IN ('AMORTISED_COST', 'FVOCI', 'FVPL', 'FVOCI_NO_RECYCLING')),
    business_model          VARCHAR(30)
        CHECK (business_model IN ('HOLD_TO_COLLECT', 'HOLD_TO_COLLECT_AND_SELL', 'OTHER')),
    sppi_test_passed        BOOLEAN,
    
    -- Counterparty
    counterparty_id         UUID,
    counterparty_type       VARCHAR(20)
        CHECK (counterparty_type IN ('BANK', 'CORPORATE', 'GOVERNMENT', 'INDIVIDUAL', 'FUND')),
    
    -- Timeline
    issue_date              DATE NOT NULL,
    maturity_date           DATE,
    
    -- Currency
    currency_code           CHAR(3) NOT NULL,
    
    -- Values
    face_value              NUMERIC(20,6) NOT NULL,
    issue_price             NUMERIC(20,6),
    acquisition_cost        NUMERIC(20,6) NOT NULL,
    transaction_costs       NUMERIC(20,6) NOT NULL DEFAULT 0,
    initial_fair_value      NUMERIC(20,6) NOT NULL,
    current_fair_value      NUMERIC(20,6) NOT NULL,
    fair_value_level        VARCHAR(10)
        CHECK (fair_value_level IN ('LEVEL_1', 'LEVEL_2', 'LEVEL_3')),
    amortised_cost          NUMERIC(20,6),
    
    -- Interest
    effective_interest_rate NUMERIC(10,6),
    stated_interest_rate    NUMERIC(10,6),
    interest_payment_frequency VARCHAR(20)
        CHECK (interest_payment_frequency IN (
            'MONTHLY', 'QUARTERLY', 'SEMI_ANNUAL', 'ANNUAL', 'AT_MATURITY'
        )),
    day_count_convention    VARCHAR(20),
    
    -- Hedge accounting
    is_hedging_instrument   BOOLEAN NOT NULL DEFAULT false,
    hedge_relationship_id   UUID,
    embedded_derivative_separated BOOLEAN NOT NULL DEFAULT false,
    
    -- Credit risk (IFRS 9)
    ecl_stage               VARCHAR(10) NOT NULL DEFAULT 'STAGE_1'
        CHECK (ecl_stage IN ('STAGE_1', 'STAGE_2', 'STAGE_3')),
    credit_risk_rating      VARCHAR(20),
    is_credit_impaired      BOOLEAN NOT NULL DEFAULT false,
    
    -- Details
    collateral_details      JSONB,
    covenants               JSONB,
    
    -- Status
    status                  VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'MATURED', 'DEFAULTED', 'DERECOGNISED')),
    
    -- Account mappings
    asset_account_id        UUID,
    liability_account_id    UUID,
    interest_income_account_id UUID,
    interest_expense_account_id UUID,
    fvoci_reserve_account_id UUID,
    fvpl_gain_loss_account_id UUID,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_instrument_code UNIQUE (organization_id, instrument_code)
);
```

### 6.2 fin_inst.instrument_cash_flow

```sql
CREATE TABLE fin_inst.instrument_cash_flow (
    cash_flow_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    instrument_id           UUID NOT NULL REFERENCES fin_inst.financial_instrument(instrument_id),
    
    cash_flow_date          DATE NOT NULL,
    cash_flow_type          VARCHAR(20) NOT NULL
        CHECK (cash_flow_type IN ('PRINCIPAL', 'INTEREST', 'FEE', 'DIVIDEND', 'PREMIUM')),
    
    scheduled_amount        NUMERIC(20,6) NOT NULL,
    actual_amount           NUMERIC(20,6),
    
    is_received_paid        BOOLEAN NOT NULL DEFAULT false,
    received_paid_date      DATE,
    
    journal_entry_id        UUID,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 6.3 fin_inst.instrument_valuation

```sql
CREATE TABLE fin_inst.instrument_valuation (
    valuation_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    instrument_id           UUID NOT NULL REFERENCES fin_inst.financial_instrument(instrument_id),
    organization_id         UUID NOT NULL,
    
    valuation_date          DATE NOT NULL,
    fiscal_period_id        UUID NOT NULL,
    
    valuation_method        VARCHAR(30) NOT NULL
        CHECK (valuation_method IN ('MARKET_PRICE', 'DCF', 'COMPARABLE', 'MODEL')),
    
    fair_value              NUMERIC(20,6) NOT NULL,
    fair_value_level        VARCHAR(10) NOT NULL,
    amortised_cost          NUMERIC(20,6),
    carrying_amount         NUMERIC(20,6) NOT NULL,
    
    unrealised_gain_loss    NUMERIC(20,6) NOT NULL DEFAULT 0,
    gain_loss_oci           NUMERIC(20,6) NOT NULL DEFAULT 0,
    gain_loss_pnl           NUMERIC(20,6) NOT NULL DEFAULT 0,
    effective_interest_income NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    valuation_inputs        JSONB,
    
    journal_entry_id        UUID,
    posting_batch_id        UUID,
    valuer_name             VARCHAR(100),
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 6.4 fin_inst.ecl_measurement

```sql
CREATE TABLE fin_inst.ecl_measurement (
    measurement_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    instrument_id           UUID NOT NULL REFERENCES fin_inst.financial_instrument(instrument_id),
    organization_id         UUID NOT NULL,
    
    measurement_date        DATE NOT NULL,
    fiscal_period_id        UUID NOT NULL,
    
    ecl_stage               VARCHAR(10) NOT NULL,
    
    -- Model inputs
    probability_of_default_12m NUMERIC(8,6),
    probability_of_default_lifetime NUMERIC(8,6),
    loss_given_default      NUMERIC(8,6),
    exposure_at_default     NUMERIC(20,6),
    discount_rate           NUMERIC(8,6),
    
    -- ECL outputs
    ecl_12_month            NUMERIC(20,6),
    ecl_lifetime            NUMERIC(20,6),
    ecl_amount              NUMERIC(20,6) NOT NULL,
    previous_ecl_amount     NUMERIC(20,6) NOT NULL DEFAULT 0,
    ecl_movement            NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    -- SICR
    significant_increase_credit_risk BOOLEAN NOT NULL DEFAULT false,
    sicr_indicators         JSONB,
    
    -- Forward-looking scenarios
    forward_looking_scenarios JSONB,
    
    journal_entry_id        UUID,
    posting_batch_id        UUID,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 6.5 fin_inst.hedge_relationship

```sql
CREATE TABLE fin_inst.hedge_relationship (
    relationship_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    
    relationship_code       VARCHAR(30) NOT NULL,
    
    hedge_type              VARCHAR(30) NOT NULL
        CHECK (hedge_type IN ('FAIR_VALUE', 'CASH_FLOW', 'NET_INVESTMENT')),
    
    hedging_instrument_id   UUID NOT NULL REFERENCES fin_inst.financial_instrument(instrument_id),
    
    hedged_item_type        VARCHAR(30) NOT NULL
        CHECK (hedged_item_type IN (
            'RECOGNISED_ASSET', 'RECOGNISED_LIABILITY',
            'FORECAST_TRANSACTION', 'FIRM_COMMITMENT', 'NET_INVESTMENT'
        )),
    hedged_item_id          UUID,
    hedged_item_description TEXT NOT NULL,
    
    hedged_risk             VARCHAR(30) NOT NULL
        CHECK (hedged_risk IN ('INTEREST_RATE', 'FX', 'COMMODITY', 'CREDIT', 'EQUITY')),
    
    designation_date        DATE NOT NULL,
    start_date              DATE NOT NULL,
    end_date                DATE,
    
    hedge_ratio             NUMERIC(8,4) NOT NULL DEFAULT 1.0,
    
    -- Documentation
    economic_relationship_documentation TEXT NOT NULL,
    credit_risk_assessment  TEXT,
    
    -- Effectiveness testing
    effectiveness_testing_method VARCHAR(30) NOT NULL
        CHECK (effectiveness_testing_method IN ('DOLLAR_OFFSET', 'REGRESSION', 'CRITICAL_TERMS')),
    prospective_effectiveness NUMERIC(8,4),
    retrospective_effectiveness NUMERIC(8,4),
    is_highly_effective     BOOLEAN NOT NULL DEFAULT true,
    
    ineffectiveness_amount  NUMERIC(20,6) NOT NULL DEFAULT 0,
    cash_flow_hedge_reserve NUMERIC(20,6) NOT NULL DEFAULT 0,
    cost_of_hedging_reserve NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    status                  VARCHAR(20) NOT NULL DEFAULT 'DESIGNATED'
        CHECK (status IN ('DESIGNATED', 'DISCONTINUED', 'EXPIRED', 'REBALANCED')),
    discontinuation_reason  TEXT,
    discontinuation_date    DATE,
    
    documentation           JSONB,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_hedge_relationship UNIQUE (organization_id, relationship_code)
);
```

---

## 7. Tax Management (IAS 12)

### 7.1 tax.tax_code

```sql
CREATE TABLE tax.tax_code (
    tax_code_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    
    tax_code                VARCHAR(20) NOT NULL,
    tax_name                VARCHAR(100) NOT NULL,
    
    tax_type                VARCHAR(30) NOT NULL
        CHECK (tax_type IN (
            'VAT', 'GST', 'SALES_TAX', 'WITHHOLDING',
            'CUSTOMS', 'EXCISE', 'STAMP_DUTY'
        )),
    
    tax_rate                NUMERIC(8,4) NOT NULL,
    
    -- Effective dating
    effective_from          DATE NOT NULL,
    effective_to            DATE,
    
    -- Recovery
    is_recoverable          BOOLEAN NOT NULL DEFAULT true,
    recovery_percentage     NUMERIC(5,2) NOT NULL DEFAULT 100,
    
    -- Accounts
    tax_payable_account_id  UUID,
    tax_receivable_account_id UUID,
    
    -- Special flags
    is_reverse_charge       BOOLEAN NOT NULL DEFAULT false,
    is_exempt               BOOLEAN NOT NULL DEFAULT false,
    exemption_reason        VARCHAR(100),
    
    -- Reporting
    reporting_code          VARCHAR(30),
    jurisdiction_code       VARCHAR(10),
    
    is_active               BOOLEAN NOT NULL DEFAULT true,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_tax_code UNIQUE (organization_id, tax_code, effective_from)
);
```

### 7.2 tax.tax_period

```sql
CREATE TABLE tax.tax_period (
    period_id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    
    tax_type                VARCHAR(30) NOT NULL,
    period_name             VARCHAR(50) NOT NULL,
    
    start_date              DATE NOT NULL,
    end_date                DATE NOT NULL,
    filing_due_date         DATE NOT NULL,
    
    status                  VARCHAR(20) NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'CLOSED', 'FILED', 'PAID')),
    
    total_output_tax        NUMERIC(20,6),
    total_input_tax         NUMERIC(20,6),
    net_tax_payable         NUMERIC(20,6),
    
    tax_return_id           UUID,
    
    closed_by_user_id       UUID,
    closed_at               TIMESTAMPTZ,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_tax_period UNIQUE (organization_id, tax_type, start_date)
);
```

### 7.3 tax.tax_transaction

```sql
CREATE TABLE tax.tax_transaction (
    transaction_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    tax_code_id             UUID NOT NULL REFERENCES tax.tax_code(tax_code_id),
    
    source_document_type    VARCHAR(30) NOT NULL
        CHECK (source_document_type IN ('AR_INVOICE', 'AP_INVOICE', 'JOURNAL')),
    source_document_id      UUID NOT NULL,
    source_line_id          UUID,
    
    transaction_date        DATE NOT NULL,
    tax_period_id           UUID REFERENCES tax.tax_period(period_id),
    
    taxable_amount          NUMERIC(20,6) NOT NULL,
    tax_amount              NUMERIC(20,6) NOT NULL,
    tax_currency_code       CHAR(3) NOT NULL,
    functional_tax_amount   NUMERIC(20,6) NOT NULL,
    
    is_input_tax            BOOLEAN NOT NULL,
    
    is_deferred             BOOLEAN NOT NULL DEFAULT false,
    deferred_tax_date       DATE,
    
    status                  VARCHAR(20) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'FILED', 'PAID', 'VOID')),
    
    tax_return_id           UUID,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_tax_txn_period ON tax.tax_transaction (tax_period_id);
CREATE INDEX idx_tax_txn_source ON tax.tax_transaction (source_document_type, source_document_id);
```

### 7.4 tax.tax_return

```sql
CREATE TABLE tax.tax_return (
    return_id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    tax_period_id           UUID NOT NULL REFERENCES tax.tax_period(period_id),
    
    return_reference        VARCHAR(50),
    filing_date             DATE,
    
    -- Return data
    gross_sales             NUMERIC(20,6),
    exempt_sales            NUMERIC(20,6),
    taxable_sales           NUMERIC(20,6),
    output_tax              NUMERIC(20,6),
    taxable_purchases       NUMERIC(20,6),
    input_tax               NUMERIC(20,6),
    net_tax_due             NUMERIC(20,6),
    penalties               NUMERIC(20,6),
    interest                NUMERIC(20,6),
    total_due               NUMERIC(20,6),
    
    amount_paid             NUMERIC(20,6),
    payment_date            DATE,
    
    status                  VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'SUBMITTED', 'FILED', 'PAID', 'AMENDED')),
    
    acknowledgement_number  VARCHAR(100),
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_tax_return UNIQUE (organization_id, tax_period_id)
);
```

### 7.5 tax.deferred_tax (IAS 12)

```sql
CREATE TABLE tax.deferred_tax (
    deferred_tax_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    fiscal_period_id        UUID NOT NULL,
    
    item_type               VARCHAR(20) NOT NULL
        CHECK (item_type IN ('ASSET', 'LIABILITY')),
    
    source_type             VARCHAR(30) NOT NULL
        CHECK (source_type IN (
            'FIXED_ASSET', 'PROVISION', 'LEASE', 'INVENTORY',
            'RECEIVABLES', 'INTANGIBLE', 'TAX_LOSSES', 'REVENUE', 'OTHER'
        )),
    source_reference        VARCHAR(100),
    source_id               UUID,
    description             TEXT NOT NULL,
    
    -- Temporary difference calculation
    carrying_amount         NUMERIC(20,6) NOT NULL,
    tax_base                NUMERIC(20,6) NOT NULL,
    temporary_difference    NUMERIC(20,6) GENERATED ALWAYS AS (carrying_amount - tax_base) STORED,
    
    applicable_tax_rate     NUMERIC(8,4) NOT NULL,
    deferred_tax_amount     NUMERIC(20,6) NOT NULL,
    
    classification          VARCHAR(20) NOT NULL DEFAULT 'NON_CURRENT'
        CHECK (classification IN ('CURRENT', 'NON_CURRENT')),
    
    -- Recognition
    recognition_status      VARCHAR(20) NOT NULL DEFAULT 'RECOGNISED'
        CHECK (recognition_status IN ('RECOGNISED', 'NOT_RECOGNISED', 'PARTIAL')),
    non_recognition_reason  TEXT,
    
    -- Movements
    movement_current_year   NUMERIC(20,6) NOT NULL DEFAULT 0,
    movement_oci            NUMERIC(20,6) NOT NULL DEFAULT 0,
    movement_equity         NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    journal_entry_id        UUID,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 7.6 tax.tax_loss_carryforward

```sql
CREATE TABLE tax.tax_loss_carryforward (
    loss_id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    
    loss_year               INTEGER NOT NULL,
    loss_type               VARCHAR(30) NOT NULL DEFAULT 'OPERATING'
        CHECK (loss_type IN ('OPERATING', 'CAPITAL', 'FOREIGN')),
    
    loss_amount             NUMERIC(20,6) NOT NULL,
    utilized_amount         NUMERIC(20,6) NOT NULL DEFAULT 0,
    remaining_amount        NUMERIC(20,6) GENERATED ALWAYS AS (loss_amount - utilized_amount) STORED,
    
    expiry_date             DATE,
    
    utilization_probability VARCHAR(20) NOT NULL DEFAULT 'PROBABLE'
        CHECK (utilization_probability IN ('PROBABLE', 'NOT_PROBABLE', 'CERTAIN')),
    
    deferred_tax_recognized NUMERIC(20,6),
    
    status                  VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'UTILIZED', 'EXPIRED')),
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_tax_loss UNIQUE (organization_id, loss_year, loss_type)
);
```

---

## 8. Consolidation & Group Reporting

### 8.1 cons.group_structure

```sql
CREATE TABLE cons.group_structure (
    structure_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_organization_id  UUID NOT NULL,
    subsidiary_organization_id UUID NOT NULL,
    
    ownership_percentage    NUMERIC(5,2) NOT NULL,
    voting_rights_percentage NUMERIC(5,2),
    
    effective_from          DATE NOT NULL,
    effective_to            DATE,
    
    consolidation_method    VARCHAR(20) NOT NULL
        CHECK (consolidation_method IN ('FULL', 'PROPORTIONAL', 'EQUITY', 'NOT_CONSOLIDATED')),
    
    control_type            VARCHAR(30) NOT NULL
        CHECK (control_type IN ('LEGAL', 'DE_FACTO', 'JOINT_CONTROL', 'SIGNIFICANT_INFLUENCE')),
    
    nci_measurement_method  VARCHAR(30) NOT NULL DEFAULT 'PROPORTIONATE_SHARE'
        CHECK (nci_measurement_method IN ('FAIR_VALUE', 'PROPORTIONATE_SHARE')),
    
    functional_currency_code CHAR(3) NOT NULL,
    translation_method      VARCHAR(20) NOT NULL DEFAULT 'CURRENT_RATE'
        CHECK (translation_method IN ('CURRENT_RATE', 'TEMPORAL')),
    
    is_dormant              BOOLEAN NOT NULL DEFAULT false,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_group_structure UNIQUE (parent_organization_id, subsidiary_organization_id, effective_from)
);
```

### 8.2 cons.consolidation_period

```sql
CREATE TABLE cons.consolidation_period (
    consolidation_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_organization_id   UUID NOT NULL,
    fiscal_period_id        UUID NOT NULL,
    
    consolidation_date      DATE NOT NULL,
    
    status                  VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'IN_PROGRESS', 'COMPLETED', 'APPROVED')),
    
    exchange_rate_type_id   UUID,
    
    subsidiary_count        INTEGER NOT NULL DEFAULT 0,
    elimination_count       INTEGER NOT NULL DEFAULT 0,
    
    consolidation_journal_entry_id UUID,
    posting_batch_id        UUID,
    
    approved_by_user_id     UUID,
    approved_at             TIMESTAMPTZ,
    
    correlation_id          VARCHAR(100),
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_consolidation_period UNIQUE (group_organization_id, fiscal_period_id)
);
```

### 8.3 cons.intercompany_transaction

```sql
CREATE TABLE cons.intercompany_transaction (
    ic_transaction_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    counterparty_organization_id UUID NOT NULL,
    
    transaction_type        VARCHAR(30) NOT NULL
        CHECK (transaction_type IN (
            'SALE', 'PURCHASE', 'LOAN', 'DIVIDEND',
            'MANAGEMENT_FEE', 'ROYALTY', 'RECHARGE', 'INTEREST'
        )),
    
    transaction_date        DATE NOT NULL,
    fiscal_period_id        UUID NOT NULL,
    reference               VARCHAR(100),
    
    amount                  NUMERIC(20,6) NOT NULL,
    currency_code           CHAR(3) NOT NULL,
    functional_amount       NUMERIC(20,6) NOT NULL,
    
    status                  VARCHAR(20) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'MATCHED', 'DISPUTED', 'ELIMINATED')),
    
    source_document_type    VARCHAR(30),
    source_document_id      UUID,
    counterparty_document_reference VARCHAR(100),
    
    matching_ic_transaction_id UUID REFERENCES cons.intercompany_transaction(ic_transaction_id),
    elimination_entry_id    UUID,
    
    dispute_reason          TEXT,
    resolution_date         DATE,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_ic_txn_org ON cons.intercompany_transaction (organization_id, fiscal_period_id);
CREATE INDEX idx_ic_txn_counterparty ON cons.intercompany_transaction (counterparty_organization_id);
```

### 8.4 cons.elimination_entry

```sql
CREATE TABLE cons.elimination_entry (
    elimination_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    consolidation_id        UUID NOT NULL REFERENCES cons.consolidation_period(consolidation_id),
    
    elimination_type        VARCHAR(30) NOT NULL
        CHECK (elimination_type IN (
            'INTERCOMPANY_SALES', 'INTERCOMPANY_PROFIT', 'INTERCOMPANY_BALANCES',
            'INTERCOMPANY_DIVIDENDS', 'INVESTMENT_EQUITY', 'NCI'
        )),
    
    description             TEXT,
    
    debit_organization_id   UUID,
    debit_account_id        UUID,
    debit_amount            NUMERIC(20,6) NOT NULL,
    
    credit_organization_id  UUID,
    credit_account_id       UUID,
    credit_amount           NUMERIC(20,6) NOT NULL,
    
    is_recurring            BOOLEAN NOT NULL DEFAULT false,
    
    journal_entry_id        UUID,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 8.5 cons.non_controlling_interest

```sql
CREATE TABLE cons.non_controlling_interest (
    nci_id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    consolidation_id        UUID NOT NULL REFERENCES cons.consolidation_period(consolidation_id),
    subsidiary_organization_id UUID NOT NULL,
    
    nci_percentage          NUMERIC(5,2) NOT NULL,
    
    nci_share_of_equity     NUMERIC(20,6) NOT NULL,
    nci_share_of_profit_loss NUMERIC(20,6) NOT NULL,
    nci_share_of_oci        NUMERIC(20,6) NOT NULL DEFAULT 0,
    dividends_to_nci        NUMERIC(20,6) NOT NULL DEFAULT 0,
    acquisition_goodwill_nci NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    total_nci_balance       NUMERIC(20,6) NOT NULL,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 8.6 cons.goodwill_allocation (IFRS 3, IAS 36)

```sql
CREATE TABLE cons.goodwill_allocation (
    allocation_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    
    acquisition_id          UUID,
    acquisition_date        DATE NOT NULL,
    acquiree_organization_id UUID NOT NULL,
    cgu_id                  UUID,
    
    initial_goodwill_amount NUMERIC(20,6) NOT NULL,
    accumulated_impairment  NUMERIC(20,6) NOT NULL DEFAULT 0,
    carrying_amount         NUMERIC(20,6) GENERATED ALWAYS AS 
        (initial_goodwill_amount - accumulated_impairment) STORED,
    
    currency_code           CHAR(3) NOT NULL,
    
    last_impairment_test_date DATE,
    recoverable_amount_at_test NUMERIC(20,6),
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 9. Financial Reporting

### 9.1 rpt.financial_statement_template

```sql
CREATE TABLE rpt.financial_statement_template (
    template_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    
    template_name           VARCHAR(100) NOT NULL,
    
    statement_type          VARCHAR(30) NOT NULL
        CHECK (statement_type IN (
            'FINANCIAL_POSITION', 'COMPREHENSIVE_INCOME',
            'CASH_FLOWS', 'EQUITY_CHANGES', 'NOTES'
        )),
    
    ifrs_standard           VARCHAR(20) NOT NULL
        CHECK (ifrs_standard IN ('IAS_1', 'IAS_7', 'IAS_1_OCI')),
    
    presentation_format     VARCHAR(20)
        CHECK (presentation_format IN ('NATURE', 'FUNCTION')),
    
    is_default              BOOLEAN NOT NULL DEFAULT false,
    is_active               BOOLEAN NOT NULL DEFAULT true,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 9.2 rpt.financial_statement_line

```sql
CREATE TABLE rpt.financial_statement_line (
    line_id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    template_id             UUID NOT NULL REFERENCES rpt.financial_statement_template(template_id),
    
    line_code               VARCHAR(30) NOT NULL,
    line_description        TEXT NOT NULL,
    
    line_type               VARCHAR(20) NOT NULL
        CHECK (line_type IN ('HEADER', 'DETAIL', 'SUBTOTAL', 'TOTAL', 'BLANK')),
    
    parent_line_id          UUID REFERENCES rpt.financial_statement_line(line_id),
    display_order           INTEGER NOT NULL,
    indent_level            INTEGER NOT NULL DEFAULT 0,
    is_bold                 BOOLEAN NOT NULL DEFAULT false,
    
    sign_convention         VARCHAR(20) NOT NULL DEFAULT 'NORMAL'
        CHECK (sign_convention IN ('NORMAL', 'REVERSED')),
    
    -- Account mapping (JSONB for flexibility)
    account_mapping         JSONB,
        -- { "type": "CATEGORY" | "ACCOUNTS", "ids": [...], "exclude_ids": [...] }
    
    calculation_formula     TEXT,
        -- e.g., "LINE_001 + LINE_002 - LINE_003"
    
    -- IFRS taxonomy
    ifrs_taxonomy_element   VARCHAR(100),
    is_required_disclosure  BOOLEAN NOT NULL DEFAULT false,
    
    comparative_periods     INTEGER NOT NULL DEFAULT 1,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_statement_line UNIQUE (template_id, line_code)
);
```

### 9.3 rpt.financial_statement_instance

```sql
CREATE TABLE rpt.financial_statement_instance (
    instance_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    template_id             UUID NOT NULL REFERENCES rpt.financial_statement_template(template_id),
    fiscal_period_id        UUID NOT NULL,
    
    statement_date          DATE NOT NULL,
    currency_code           CHAR(3) NOT NULL,
    
    status                  VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'REVIEW', 'APPROVED', 'PUBLISHED')),
    
    generated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    -- Snapshot of values at generation time
    line_values             JSONB NOT NULL,
        -- { "LINE_001": { "current": 1000.00, "prior_1": 900.00 } }
    comparative_values      JSONB,
    
    notes_references        JSONB,
    
    approved_by_user_id     UUID,
    approved_at             TIMESTAMPTZ,
    
    -- Restatement tracking
    is_restated             BOOLEAN NOT NULL DEFAULT false,
    restated_from_instance_id UUID REFERENCES rpt.financial_statement_instance(instance_id),
    restatement_reason      TEXT,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 9.4 rpt.disclosure_note

```sql
CREATE TABLE rpt.disclosure_note (
    note_id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    fiscal_period_id        UUID NOT NULL,
    
    note_number             VARCHAR(10) NOT NULL,
    note_title              VARCHAR(200) NOT NULL,
    
    ifrs_requirement        VARCHAR(100),
    
    note_content            TEXT NOT NULL,
    
    -- Supporting data (tables, schedules)
    supporting_data         JSONB,
    
    status                  VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'REVIEW', 'APPROVED')),
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_disclosure_note UNIQUE (organization_id, fiscal_period_id, note_number)
);
```

### 9.5 rpt.cash_flow_statement (IAS 7)

```sql
CREATE TABLE rpt.cash_flow_statement (
    cash_flow_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    fiscal_period_id        UUID NOT NULL,
    
    preparation_method      VARCHAR(20) NOT NULL
        CHECK (preparation_method IN ('DIRECT', 'INDIRECT')),
    
    -- Operating activities
    operating_activities    JSONB NOT NULL,
        /*
        {
            "profit_before_tax": 100000,
            "adjustments": {
                "depreciation": 20000,
                "provisions": 5000,
                "interest_expense": 3000
            },
            "working_capital_changes": {
                "receivables": -10000,
                "payables": 15000,
                "inventory": -5000
            },
            "cash_generated": 128000,
            "taxes_paid": -25000,
            "net_operating": 103000
        }
        */
    
    -- Investing activities
    investing_activities    JSONB NOT NULL,
    
    -- Financing activities
    financing_activities    JSONB NOT NULL,
    
    -- Summary
    opening_cash            NUMERIC(20,6) NOT NULL,
    net_cash_movement       NUMERIC(20,6) NOT NULL,
    fx_effect_on_cash       NUMERIC(20,6) NOT NULL DEFAULT 0,
    closing_cash            NUMERIC(20,6) NOT NULL,
    
    -- Non-cash transactions
    non_cash_transactions   JSONB,
    
    -- Reconciliation
    reconciliation_to_balance_sheet JSONB,
    
    status                  VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'APPROVED')),
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_cash_flow UNIQUE (organization_id, fiscal_period_id)
);
```

---

## 10. Summary & Cross-References

### 10.1 Table Count by Schema

| Schema | Tables | Key Entities |
|--------|--------|--------------|
| `ar` | 11 | Customer, Contract, Invoice, Payment, ECL |
| `ap` | 11 | Supplier, PO, Invoice, Payment, Receipt |
| `fa` | 9 | Asset, Component, Depreciation, Impairment |
| `lease` | 5 | Contract, Schedule, ROU, Modification |
| `inv` | 9 | Item, Warehouse, Balance, Transaction, NRV |
| `fin_inst` | 5 | Instrument, Valuation, ECL, Hedge |
| `tax` | 6 | Tax Code, Period, Transaction, Deferred |
| `cons` | 6 | Structure, IC Transaction, Elimination, NCI |
| `rpt` | 5 | Template, Line, Instance, Disclosure, Cash Flow |

**Part 2 Total: 67 tables**

### 10.2 Combined Total (Part 1 + Part 2)

| Part | Schemas | Tables |
|------|---------|--------|
| Part 1 | 7 | 44 |
| Part 2 | 9 | 67 |
| **Total** | **16** | **111** |

### 10.3 Key Cross-Schema References

```
┌─────────────────────────────────────────────────────────────────────┐
│                        REFERENCE DIAGRAM                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ar.invoice ─────────┬──────────────────────────────────────────┐  │
│  ap.supplier_invoice ─┤                                          │  │
│  fa.depreciation_run ─┤                                          │  │
│  lease.lease_run ─────┤──> gl.journal_entry ──> gl.posted_ledger │  │
│  inv.inventory_txn ───┤                                          │  │
│  fin_inst.valuation ──┤                                          │  │
│  tax.tax_transaction ─┘                                          │  │
│                                                                     │
│  All modules ────> core_org.organization                           │
│  All modules ────> core_identity.user (SoD tracking)               │
│  All modules ────> gl.fiscal_period (period controls)              │
│  All modules ────> audit.approval_workflow (approvals)             │
│                                                                     │
│  gl.posted_ledger_line ────> gl.account_balance (derived)          │
│  ar.invoice ────> ar.ar_aging_snapshot (derived)                   │
│  ap.supplier_invoice ────> ap.ap_aging_snapshot (derived)          │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 10.4 Document Architecture Compliance Checklist

| Requirement | Document | Implementation |
|-------------|----------|----------------|
| Append-only ledger | Doc 07 | `gl.posted_ledger_line` with trigger |
| Idempotent posting | Doc 07 | `idempotency_key` unique constraint |
| Period controls | Doc 08 | `gl.fiscal_period.status` + PeriodGuard |
| Event outbox | Doc 10 | `platform.event_outbox` |
| SoD tracking | Doc 13 | `created_by`, `approved_by`, `posted_by` |
| Dual control | Doc 13 | `period_reopen_session` constraint |
| Data access policy | Doc 13 | `core_identity.data_access_policy` |
| Schema ownership | Doc 12 | Separate schemas per module |
| Effective dating | Doc 12 | `effective_from`/`to` on master data |
| Partitioning | Doc 12 | Yearly partitions on `posted_ledger_line` |

---

**End of Part 2**

---

## Appendix B: MVP Feature Configuration (Subledgers)

### B.1 Accounts Receivable — MVP Scope

| Feature | Status | Notes |
|---------|--------|-------|
| Customer master | ✅ Enabled | Full CRUD |
| Invoice creation & posting | ✅ Enabled | Core workflow |
| Credit notes | ✅ Enabled | Via invoice_type |
| Payment receipt & allocation | ✅ Enabled | Core workflow |
| IFRS 15 contracts | 🔒 Deferred | Simple invoicing only |
| Performance obligations | 🔒 Deferred | Revenue = invoice |
| ECL provisioning | 🔒 Deferred | Manual provision OK |
| Aging snapshots | ✅ Enabled | Period-end job |

### B.2 Accounts Payable — MVP Scope

| Feature | Status | Notes |
|---------|--------|-------|
| Supplier master | ✅ Enabled | Full CRUD |
| Purchase orders | ✅ Enabled | Optional workflow |
| Goods receipts | ✅ Enabled | Optional for 3-way match |
| Supplier invoice & posting | ✅ Enabled | Core workflow |
| 3-way matching | 🔒 Deferred | Manual approval OK |
| Payment processing | ✅ Enabled | Single payments |
| Payment batches | 🔒 Deferred | Bank file generation |
| Withholding tax | 🔒 Deferred | Manual calculation |

### B.3 Fixed Assets — MVP Scope

| Feature | Status | Notes |
|---------|--------|-------|
| Asset master | ✅ Enabled | Full CRUD |
| Asset categories | ✅ Enabled | Setup |
| Depreciation (straight-line) | ✅ Enabled | Monthly run |
| Depreciation (other methods) | 🔒 Deferred | Straight-line only |
| Asset disposal | ✅ Enabled | Gain/loss calculation |
| Componentization | 🔒 Deferred | Schema ready |
| Revaluation model | 🔒 Deferred | Cost model only |
| Impairment testing | 🔒 Deferred | Manual if needed |
| CGU management | 🔒 Deferred | Schema ready |

### B.4 Leases (IFRS 16) — MVP Scope

| Feature | Status | Notes |
|---------|--------|-------|
| Lease contract setup | ✅ Enabled | Lessee only |
| Fixed payment schedules | ✅ Enabled | Auto-generated |
| Monthly lease run | ✅ Enabled | Interest + depreciation |
| ROU asset tracking | ✅ Enabled | Linked to FA |
| Variable payments | 🔒 Deferred | Fixed only |
| Lease modifications | 🔒 Deferred | Schema ready |
| Reassessments | 🔒 Deferred | Schema ready |
| Short-term/low-value exemptions | 🔒 Deferred | Full IFRS 16 only |

### B.5 Inventory — MVP Scope

| Feature | Status | Notes |
|---------|--------|-------|
| Item master | ✅ Enabled | Full CRUD |
| Warehouses & locations | ✅ Enabled | Setup |
| Inventory transactions | ✅ Enabled | Receipt/issue/adjust |
| Weighted average costing | ✅ Enabled | **MVP default** |
| FIFO costing | 🔒 Deferred | Schema ready |
| Lot/serial tracking | 🔒 Deferred | Schema ready |
| NRV assessment | 🔒 Deferred | Manual write-down |
| Cost layers | 🔒 Deferred | For FIFO |

### B.6 Financial Instruments — MVP Scope

| Feature | Status | Notes |
|---------|--------|-------|
| Instrument master | ✅ Enabled | Loans/deposits only |
| Amortised cost classification | ✅ Enabled | Simple debt |
| Interest accrual posting | ✅ Enabled | Monthly |
| FVOCI classification | 🔒 Deferred | Treasury-grade |
| FVPL classification | 🔒 Deferred | Trading only |
| ECL staging | 🔒 Deferred | Stage 1 default |
| Hedge accounting | 🔒 Deferred | Enterprise feature |
| Derivatives | 🔒 Deferred | Enterprise feature |

### B.7 Tax — MVP Scope

| Feature | Status | Notes |
|---------|--------|-------|
| Tax codes | ✅ Enabled | VAT/GST setup |
| Tax on invoices | ✅ Enabled | Auto-calculation |
| Tax periods | ✅ Enabled | Monthly/quarterly |
| Tax transactions | ✅ Enabled | From AR/AP |
| Tax returns | ✅ Enabled | Summary only |
| Withholding tax | 🔒 Deferred | Manual |
| Deferred tax (IAS 12) | 🔒 Deferred | Year-end only |
| Tax loss carryforward | 🔒 Deferred | Year-end only |

### B.8 Consolidation — MVP Scope

| Feature | Status | Notes |
|---------|--------|-------|
| Group structure | 🔒 Deferred | Single entity MVP |
| Intercompany transactions | 🔒 Deferred | Single entity |
| Elimination entries | 🔒 Deferred | Single entity |
| Currency translation | 🔒 Deferred | Single entity |
| NCI calculation | 🔒 Deferred | Single entity |

### B.9 Reporting — MVP Scope

| Feature | Status | Notes |
|---------|--------|-------|
| Trial balance | ✅ Enabled | From account_balance |
| Statement templates | ✅ Enabled | Basic P&L, BS |
| Statement generation | ✅ Enabled | Period-end |
| Disclosure notes | 🔒 Deferred | Manual |
| Cash flow statement | 🔒 Deferred | Indirect method |
| XBRL export | 🔒 Deferred | Enterprise |

---

## Appendix C: Posting Status State Machine

All posting-producing documents follow this state machine:

```
┌─────────────┐     submit      ┌─────────────┐
│ NOT_POSTED  │ ───────────────>│   POSTING   │
└─────────────┘                 └──────┬──────┘
       ^                               │
       │         ┌─────────────────────┴─────────────────────┐
       │         │                                           │
       │         v                                           v
       │  ┌─────────────┐                           ┌─────────────┐
       │  │   POSTED    │                           │   FAILED    │
       │  └─────────────┘                           └──────┬──────┘
       │                                                   │
       └───────────────────────────────────────────────────┘
                              retry
```

**Rules:**
- `NOT_POSTED` → `POSTING`: On submission to posting service
- `POSTING` → `POSTED`: On successful batch commit
- `POSTING` → `FAILED`: On posting error (idempotency violation, period closed, etc.)
- `FAILED` → `NOT_POSTED`: On manual retry trigger
- `POSTED`: Terminal state (corrections via reversal only)
