# IFRS-Based Accounting Software — Data Model Specification

## Part 1: Platform Infrastructure, Core Modules & General Ledger

**Document 14a — Data Models (Architecture-Aligned)**

This document defines the foundational data models aligned with Documents 07–13.

---

## Table of Contents

1. [Design Principles](#1-design-principles)
2. [Schema Organization](#2-schema-organization)
3. [Platform Infrastructure](#3-platform-infrastructure)
4. [Core Identity & Access](#4-core-identity--access)
5. [Core Organization & Dimensions](#5-core-organization--dimensions)
6. [Core FX & Currency](#6-core-fx--currency)
7. [Core Configuration](#7-core-configuration)
8. [Audit & Compliance](#8-audit--compliance)
9. [General Ledger & Posting](#9-general-ledger--posting)

---

## 1. Design Principles

### 1.1 From Document 07 (Ledger & Posting Architecture)

| Principle | Implementation |
|-----------|----------------|
| Document layer is not the ledger | Separate `journal_entry` (mutable) from `posted_ledger_line` (immutable) |
| Posted facts are immutable | `posted_ledger_line` is append-only with DB triggers blocking UPDATE/DELETE |
| Idempotent posting | Unique constraint on `(organization_id, idempotency_key)` |
| Period controls at ledger boundary | `PeriodGuard` enforces status before any posting |
| Balances are derived | `account_balance` is a cache rebuilt from posted facts |

### 1.2 From Document 12 (Data Architecture)

| Rule | Implementation |
|------|----------------|
| One-writer rule | Only owning module writes to its tables |
| UUID primary keys | All PKs use `UUID DEFAULT gen_random_uuid()` |
| Money precision | `NUMERIC(20,6)` for amounts |
| Timestamps | `TIMESTAMPTZ` for all temporal data |
| Effective dating | Master data uses `effective_from`/`effective_to` |
| Append-only audit | `audit_log` blocks UPDATE/DELETE via trigger |

### 1.3 From Document 13 (Security & RBAC)

| Requirement | Implementation |
|-------------|----------------|
| SoD tracking | All documents track `created_by`, `submitted_by`, `approved_by`, `posted_by` |
| Dual control | `period_reopen_session` requires `requested_by` ≠ `approved_by` |
| MFA for sensitive ops | `approval_decision.mfa_verified` flag |
| Data scope | `data_access_policy` with dimension arrays |

---

## 2. Schema Organization

Tables are organized into PostgreSQL schemas by module ownership:

```
┌─────────────────┬─────────────────────────────────────────────────┐
│ Schema          │ Purpose                                         │
├─────────────────┼─────────────────────────────────────────────────┤
│ platform        │ Outbox, idempotency, jobs (infrastructure)      │
│ core_identity   │ Users, roles, permissions, sessions             │
│ core_org        │ Organizations, BUs, segments, dimensions        │
│ core_fx         │ Currencies, exchange rates                      │
│ core_config     │ Sequences, settings, feature flags              │
│ audit           │ Audit logs, approvals, locks, evidence          │
│ gl              │ Journals, postings, balances                    │
│ ar              │ Customers, contracts, invoices, receipts        │
│ ap              │ Suppliers, POs, invoices, payments              │
│ fa              │ Assets, depreciation, impairment                │
│ lease           │ IFRS 16 contracts, schedules                    │
│ inv             │ Items, warehouses, transactions                 │
│ fin_inst        │ IFRS 9 instruments, valuations                  │
│ tax             │ VAT/WHT, deferred tax                           │
│ cons            │ Group structure, eliminations                   │
│ rpt             │ Statements, disclosures                         │
└─────────────────┴─────────────────────────────────────────────────┘
```

**Ownership Rule**: Only the owning module may INSERT/UPDATE/DELETE its tables.

---

## 3. Platform Infrastructure

### 3.1 platform.event_outbox

Transactional outbox for reliable event delivery (Document 10).

```sql
CREATE TABLE platform.event_outbox (
    -- Identity
    event_id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Timing
    occurred_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at            TIMESTAMPTZ,
    
    -- Event metadata
    event_name              VARCHAR(200) NOT NULL,
        -- Format: <domain>.<aggregate>.<action>
        -- e.g., ledger.posting.completed, period.close.hard_completed
    event_version           INTEGER NOT NULL DEFAULT 1,
    producer_module         VARCHAR(50) NOT NULL,
    aggregate_type          VARCHAR(100) NOT NULL,
    aggregate_id            VARCHAR(100) NOT NULL,
    
    -- Correlation
    correlation_id          VARCHAR(100) NOT NULL,
    causation_id            UUID REFERENCES platform.event_outbox(event_id),
    
    -- Idempotency
    idempotency_key         VARCHAR(200) NOT NULL,
    
    -- Payload
    payload                 JSONB NOT NULL,
    headers                 JSONB NOT NULL,
        -- Required: organization_id, user_id, request_id, ip_address, source
    
    -- Processing state
    status                  VARCHAR(20) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'PUBLISHED', 'FAILED', 'DEAD')),
    retry_count             INTEGER NOT NULL DEFAULT 0,
    next_retry_at           TIMESTAMPTZ,
    last_error              TEXT,
    
    -- Metadata
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    -- Constraints
    CONSTRAINT uq_outbox_idempotency 
        UNIQUE ((headers->>'organization_id'), idempotency_key)
);

-- Indexes for relay worker
CREATE INDEX idx_outbox_pending 
    ON platform.event_outbox (status, next_retry_at) 
    WHERE status IN ('PENDING', 'FAILED');
CREATE INDEX idx_outbox_aggregate 
    ON platform.event_outbox (aggregate_type, aggregate_id);
CREATE INDEX idx_outbox_correlation 
    ON platform.event_outbox (correlation_id);
```

### 3.2 platform.event_handler_checkpoint

Tracks handler processing for idempotency (Document 10).

```sql
CREATE TABLE platform.event_handler_checkpoint (
    checkpoint_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id                UUID NOT NULL REFERENCES platform.event_outbox(event_id),
    handler_name            VARCHAR(200) NOT NULL,
    processed_at            TIMESTAMPTZ,
    status                  VARCHAR(20) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'SUCCESS', 'FAILED')),
    attempts                INTEGER NOT NULL DEFAULT 0,
    last_error              TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_checkpoint_event_handler UNIQUE (event_id, handler_name)
);
```

### 3.3 platform.idempotency_record

API idempotency tracking (Document 11).

```sql
CREATE TABLE platform.idempotency_record (
    record_id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    idempotency_key         VARCHAR(200) NOT NULL,
    endpoint                VARCHAR(200) NOT NULL,
    request_hash            VARCHAR(64) NOT NULL,
    response_status         INTEGER NOT NULL,
    response_body           JSONB,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at              TIMESTAMPTZ NOT NULL,
    
    CONSTRAINT uq_idempotency UNIQUE (organization_id, endpoint, idempotency_key)
);

CREATE INDEX idx_idempotency_expires ON platform.idempotency_record (expires_at);
```

---

## 4. Core Identity & Access

### 4.1 core_identity.user

```sql
CREATE TABLE core_identity.user (
    user_id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    
    -- Credentials
    username                VARCHAR(100) NOT NULL,
    email                   VARCHAR(255) NOT NULL,
    password_hash           VARCHAR(255) NOT NULL,
    
    -- Profile
    first_name              VARCHAR(100),
    last_name               VARCHAR(100),
    display_name            VARCHAR(200) GENERATED ALWAYS AS (
        COALESCE(first_name || ' ' || last_name, username)
    ) STORED,
    department_id           UUID,
    
    -- Status
    is_active               BOOLEAN NOT NULL DEFAULT true,
    is_locked               BOOLEAN NOT NULL DEFAULT false,
    locked_at               TIMESTAMPTZ,
    locked_reason           TEXT,
    failed_login_attempts   INTEGER NOT NULL DEFAULT 0,
    
    -- Security
    last_login_at           TIMESTAMPTZ,
    last_login_ip           INET,
    password_changed_at     TIMESTAMPTZ,
    must_change_password    BOOLEAN NOT NULL DEFAULT false,
    
    -- Metadata
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_user_username UNIQUE (organization_id, username),
    CONSTRAINT uq_user_email UNIQUE (organization_id, email)
);
```

### 4.2 core_identity.role

```sql
CREATE TABLE core_identity.role (
    role_id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    role_code               VARCHAR(50) NOT NULL,
    role_name               VARCHAR(100) NOT NULL,
    description             TEXT,
    
    -- System roles cannot be deleted
    is_system_role          BOOLEAN NOT NULL DEFAULT false,
    is_active               BOOLEAN NOT NULL DEFAULT true,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_role_code UNIQUE (organization_id, role_code)
);

-- Default roles (per Document 13)
COMMENT ON TABLE core_identity.role IS 
'Standard roles: SYSTEM_ADMIN, FINANCE_ADMIN, CONTROLLER, ACCOUNTANT, 
AP_CLERK, AR_CLERK, TREASURY_MANAGER, FA_MANAGER, AUDITOR, VIEWER';
```

### 4.3 core_identity.permission

```sql
CREATE TABLE core_identity.permission (
    permission_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Permission identifier
    permission_code         VARCHAR(100) NOT NULL UNIQUE,
        -- Format: <module>.<resource>.<action>
        -- e.g., gl.journal.post, period.hard_close, audit.lock.enable
    permission_name         VARCHAR(200) NOT NULL,
    description             TEXT,
    
    -- Categorization
    module                  VARCHAR(50) NOT NULL,
    resource                VARCHAR(50) NOT NULL,
    action                  VARCHAR(50) NOT NULL,
    
    -- Security flags
    is_sensitive            BOOLEAN NOT NULL DEFAULT false,
        -- true for: posting, reversals, period controls, audit locks
    requires_mfa            BOOLEAN NOT NULL DEFAULT false,
    requires_dual_control   BOOLEAN NOT NULL DEFAULT false,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Core permission examples
COMMENT ON TABLE core_identity.permission IS 
'Key permissions: gl.journal.post, gl.journal.reverse, period.soft_close, 
period.hard_close, period.lock, period.reopen, audit.lock.enable, 
audit.lock.disable, master.coa.manage, master.fx_rate.manage';
```

### 4.4 core_identity.role_permission

```sql
CREATE TABLE core_identity.role_permission (
    role_id                 UUID NOT NULL REFERENCES core_identity.role(role_id),
    permission_id           UUID NOT NULL REFERENCES core_identity.permission(permission_id),
    granted_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    granted_by_user_id      UUID REFERENCES core_identity.user(user_id),
    
    PRIMARY KEY (role_id, permission_id)
);
```

### 4.5 core_identity.user_role

```sql
CREATE TABLE core_identity.user_role (
    user_id                 UUID NOT NULL REFERENCES core_identity.user(user_id),
    role_id                 UUID NOT NULL REFERENCES core_identity.role(role_id),
    assigned_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    assigned_by_user_id     UUID REFERENCES core_identity.user(user_id),
    valid_from              TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_to                TIMESTAMPTZ,
    
    PRIMARY KEY (user_id, role_id)
);
```

### 4.6 core_identity.data_access_policy

Per Document 13: scope-based authorization.

```sql
CREATE TABLE core_identity.data_access_policy (
    policy_id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    role_id                 UUID NOT NULL REFERENCES core_identity.role(role_id),
    
    -- What this policy applies to
    entity_type             VARCHAR(50) NOT NULL,
        -- INVOICE, JOURNAL, PAYMENT, REPORT, MASTER_DATA, ALL
    access_level            VARCHAR(20) NOT NULL DEFAULT 'NONE'
        CHECK (access_level IN ('NONE', 'READ', 'WRITE', 'FULL')),
    
    -- Dimension restrictions (NULL = no restriction)
    business_unit_ids       UUID[],
    segment_ids             UUID[],
    cost_center_ids         UUID[],
    project_ids             UUID[],
    account_ids             UUID[],
    
    -- Amount limits
    amount_limit            NUMERIC(20,6),
    amount_currency_code    CHAR(3),
    
    -- Special permissions
    can_view_pii            BOOLEAN NOT NULL DEFAULT false,
    can_export_data         BOOLEAN NOT NULL DEFAULT false,
    
    is_active               BOOLEAN NOT NULL DEFAULT true,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_policy_role ON core_identity.data_access_policy (role_id);
```

### 4.7 core_identity.mfa_enrollment

```sql
CREATE TABLE core_identity.mfa_enrollment (
    enrollment_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID NOT NULL REFERENCES core_identity.user(user_id),
    mfa_type                VARCHAR(20) NOT NULL
        CHECK (mfa_type IN ('TOTP', 'SMS', 'EMAIL', 'HARDWARE_KEY')),
    
    -- Secrets (encrypted at rest)
    secret_encrypted        BYTEA,
    phone_number            VARCHAR(20),
    
    -- Status
    is_verified             BOOLEAN NOT NULL DEFAULT false,
    is_primary              BOOLEAN NOT NULL DEFAULT false,
    backup_codes_hash       VARCHAR(255)[],
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    verified_at             TIMESTAMPTZ
);

CREATE INDEX idx_mfa_user ON core_identity.mfa_enrollment (user_id);
```

### 4.8 core_identity.user_session

```sql
CREATE TABLE core_identity.user_session (
    session_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID NOT NULL REFERENCES core_identity.user(user_id),
    organization_id         UUID NOT NULL,
    
    -- Token
    token_hash              VARCHAR(64) NOT NULL UNIQUE,
    
    -- Context
    ip_address              INET,
    user_agent              TEXT,
    device_fingerprint      VARCHAR(64),
    
    -- Lifecycle
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at              TIMESTAMPTZ NOT NULL,
    last_activity_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    -- Revocation
    is_revoked              BOOLEAN NOT NULL DEFAULT false,
    revoked_at              TIMESTAMPTZ,
    revoked_reason          TEXT
);

CREATE INDEX idx_session_user ON core_identity.user_session (user_id);
CREATE INDEX idx_session_expires ON core_identity.user_session (expires_at) 
    WHERE NOT is_revoked;
```

---

## 5. Core Organization & Dimensions

### 5.1 core_org.organization

```sql
CREATE TABLE core_org.organization (
    organization_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_code       VARCHAR(20) NOT NULL UNIQUE,
    
    -- Legal identity
    legal_name              VARCHAR(255) NOT NULL,
    trading_name            VARCHAR(255),
    registration_number     VARCHAR(50),
    tax_identification_number VARCHAR(50),
    incorporation_date      DATE,
    jurisdiction_country_code CHAR(2),
    
    -- Currency settings
    functional_currency_code CHAR(3) NOT NULL,
    presentation_currency_code CHAR(3) NOT NULL,
    
    -- Fiscal year settings
    fiscal_year_end_month   INTEGER NOT NULL CHECK (fiscal_year_end_month BETWEEN 1 AND 12),
    fiscal_year_end_day     INTEGER NOT NULL CHECK (fiscal_year_end_day BETWEEN 1 AND 31),
    
    -- Group structure
    parent_organization_id  UUID REFERENCES core_org.organization(organization_id),
    consolidation_method    VARCHAR(20)
        CHECK (consolidation_method IN ('FULL', 'PROPORTIONAL', 'EQUITY', 'NONE')),
    ownership_percentage    NUMERIC(5,2),
    
    -- Status
    is_active               BOOLEAN NOT NULL DEFAULT true,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 5.2 core_org.business_unit

```sql
CREATE TABLE core_org.business_unit (
    business_unit_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL REFERENCES core_org.organization(organization_id),
    
    unit_code               VARCHAR(20) NOT NULL,
    unit_name               VARCHAR(100) NOT NULL,
    unit_type               VARCHAR(30) NOT NULL
        CHECK (unit_type IN ('BRANCH', 'DIVISION', 'DEPARTMENT', 'COST_CENTER', 'PROFIT_CENTER')),
    
    -- Hierarchy
    parent_unit_id          UUID REFERENCES core_org.business_unit(business_unit_id),
    hierarchy_level         INTEGER NOT NULL DEFAULT 1,
    hierarchy_path          TEXT,  -- Materialized path: /root/parent/child/
    
    -- Management
    manager_user_id         UUID,
    
    -- Effective dating
    is_active               BOOLEAN NOT NULL DEFAULT true,
    effective_from          DATE NOT NULL DEFAULT CURRENT_DATE,
    effective_to            DATE,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_bu_code UNIQUE (organization_id, unit_code)
);

CREATE INDEX idx_bu_parent ON core_org.business_unit (parent_unit_id);
CREATE INDEX idx_bu_path ON core_org.business_unit (hierarchy_path);
```

### 5.3 core_org.reporting_segment

IFRS 8 Operating Segments.

```sql
CREATE TABLE core_org.reporting_segment (
    segment_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL REFERENCES core_org.organization(organization_id),
    
    segment_code            VARCHAR(20) NOT NULL,
    segment_name            VARCHAR(100) NOT NULL,
    segment_type            VARCHAR(30) NOT NULL
        CHECK (segment_type IN ('OPERATING', 'GEOGRAPHICAL', 'REPORTABLE')),
    
    -- IFRS 8 requirements
    chief_operating_decision_maker VARCHAR(100),
    is_reportable           BOOLEAN NOT NULL DEFAULT false,
    aggregation_criteria    TEXT,
    
    -- Effective dating
    is_active               BOOLEAN NOT NULL DEFAULT true,
    effective_from          DATE NOT NULL DEFAULT CURRENT_DATE,
    effective_to            DATE,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_segment_code UNIQUE (organization_id, segment_code)
);
```

### 5.4 core_org.cost_center

```sql
CREATE TABLE core_org.cost_center (
    cost_center_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL REFERENCES core_org.organization(organization_id),
    
    cost_center_code        VARCHAR(20) NOT NULL,
    cost_center_name        VARCHAR(100) NOT NULL,
    
    -- Relationships
    business_unit_id        UUID REFERENCES core_org.business_unit(business_unit_id),
    manager_user_id         UUID,
    
    -- Effective dating
    is_active               BOOLEAN NOT NULL DEFAULT true,
    effective_from          DATE NOT NULL DEFAULT CURRENT_DATE,
    effective_to            DATE,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_cc_code UNIQUE (organization_id, cost_center_code)
);
```

### 5.5 core_org.project

```sql
CREATE TABLE core_org.project (
    project_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL REFERENCES core_org.organization(organization_id),
    
    project_code            VARCHAR(20) NOT NULL,
    project_name            VARCHAR(200) NOT NULL,
    description             TEXT,
    
    -- Relationships
    business_unit_id        UUID REFERENCES core_org.business_unit(business_unit_id),
    segment_id              UUID REFERENCES core_org.reporting_segment(segment_id),
    project_manager_user_id UUID,
    
    -- Timeline
    start_date              DATE,
    end_date                DATE,
    
    -- Budget
    budget_amount           NUMERIC(20,6),
    budget_currency_code    CHAR(3),
    
    -- Status
    status                  VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('PLANNING', 'ACTIVE', 'ON_HOLD', 'COMPLETED', 'CANCELLED')),
    
    -- Accounting
    is_capitalizable        BOOLEAN NOT NULL DEFAULT false,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_project_code UNIQUE (organization_id, project_code)
);
```

### 5.6 core_org.location

```sql
CREATE TABLE core_org.location (
    location_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL REFERENCES core_org.organization(organization_id),
    
    location_code           VARCHAR(20) NOT NULL,
    location_name           VARCHAR(100) NOT NULL,
    location_type           VARCHAR(30)
        CHECK (location_type IN ('HEAD_OFFICE', 'BRANCH', 'WAREHOUSE', 'PLANT', 'REMOTE')),
    
    -- Address
    address_line_1          VARCHAR(255),
    address_line_2          VARCHAR(255),
    city                    VARCHAR(100),
    state_province          VARCHAR(100),
    postal_code             VARCHAR(20),
    country_code            CHAR(2),
    
    is_active               BOOLEAN NOT NULL DEFAULT true,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_location_code UNIQUE (organization_id, location_code)
);
```

---

## 6. Core FX & Currency

### 6.1 core_fx.currency

```sql
CREATE TABLE core_fx.currency (
    currency_code           CHAR(3) PRIMARY KEY,  -- ISO 4217
    currency_name           VARCHAR(100) NOT NULL,
    symbol                  VARCHAR(10),
    decimal_places          INTEGER NOT NULL DEFAULT 2,
    is_active               BOOLEAN NOT NULL DEFAULT true,
    is_crypto               BOOLEAN NOT NULL DEFAULT false
);

-- Seed common currencies
INSERT INTO core_fx.currency (currency_code, currency_name, symbol, decimal_places) VALUES
('USD', 'US Dollar', '$', 2),
('EUR', 'Euro', '€', 2),
('GBP', 'British Pound', '£', 2),
('NGN', 'Nigerian Naira', '₦', 2),
('JPY', 'Japanese Yen', '¥', 0);
```

### 6.2 core_fx.exchange_rate_type

```sql
CREATE TABLE core_fx.exchange_rate_type (
    rate_type_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL REFERENCES core_org.organization(organization_id),
    
    type_code               VARCHAR(20) NOT NULL,
    type_name               VARCHAR(50) NOT NULL,
    description             TEXT,
    is_default              BOOLEAN NOT NULL DEFAULT false,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_rate_type UNIQUE (organization_id, type_code)
);

-- Standard rate types: SPOT, AVERAGE, CLOSING, HISTORICAL, BUDGET
```

### 6.3 core_fx.exchange_rate

```sql
CREATE TABLE core_fx.exchange_rate (
    exchange_rate_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL REFERENCES core_org.organization(organization_id),
    
    from_currency_code      CHAR(3) NOT NULL REFERENCES core_fx.currency(currency_code),
    to_currency_code        CHAR(3) NOT NULL REFERENCES core_fx.currency(currency_code),
    rate_type_id            UUID NOT NULL REFERENCES core_fx.exchange_rate_type(rate_type_id),
    
    effective_date          DATE NOT NULL,
    exchange_rate           NUMERIC(20,10) NOT NULL CHECK (exchange_rate > 0),
    inverse_rate            NUMERIC(20,10) GENERATED ALWAYS AS (1.0 / exchange_rate) STORED,
    
    source                  VARCHAR(30)
        CHECK (source IN ('MANUAL', 'ECB', 'REUTERS', 'BLOOMBERG', 'API')),
    
    created_by_user_id      UUID,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_exchange_rate UNIQUE (
        organization_id, from_currency_code, to_currency_code, rate_type_id, effective_date
    )
);

CREATE INDEX idx_fx_rate_lookup ON core_fx.exchange_rate (
    organization_id, from_currency_code, to_currency_code, rate_type_id, effective_date DESC
);
```

### 6.4 core_fx.currency_translation_adjustment

IAS 21 – Foreign Operations CTA.

```sql
CREATE TABLE core_fx.currency_translation_adjustment (
    adjustment_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL REFERENCES core_org.organization(organization_id),
    foreign_operation_id    UUID NOT NULL REFERENCES core_org.organization(organization_id),
    fiscal_period_id        UUID NOT NULL,
    
    adjustment_type         VARCHAR(30) NOT NULL
        CHECK (adjustment_type IN ('TRANSLATION', 'HYPERINFLATION', 'DISPOSAL')),
    
    functional_currency_code CHAR(3) NOT NULL,
    presentation_currency_code CHAR(3) NOT NULL,
    
    -- Amounts
    net_investment_amount   NUMERIC(20,6),
    translation_difference  NUMERIC(20,6) NOT NULL,
    recycled_to_pnl         NUMERIC(20,6) NOT NULL DEFAULT 0,
    oci_balance             NUMERIC(20,6) NOT NULL,
    
    journal_entry_id        UUID,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 7. Core Configuration

### 7.1 core_config.numbering_sequence

```sql
CREATE TABLE core_config.numbering_sequence (
    sequence_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL REFERENCES core_org.organization(organization_id),
    
    sequence_type           VARCHAR(30) NOT NULL
        CHECK (sequence_type IN (
            'INVOICE', 'CREDIT_NOTE', 'PAYMENT', 'RECEIPT',
            'JOURNAL', 'PURCHASE_ORDER', 'SUPPLIER_INVOICE',
            'ASSET', 'LEASE', 'GOODS_RECEIPT'
        )),
    
    prefix                  VARCHAR(10),
    suffix                  VARCHAR(10),
    current_number          BIGINT NOT NULL DEFAULT 0,
    min_digits              INTEGER NOT NULL DEFAULT 6,
    
    -- Reset behavior
    fiscal_year_reset       BOOLEAN NOT NULL DEFAULT false,
    fiscal_year_id          UUID,
    
    last_used_at            TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_sequence UNIQUE (organization_id, sequence_type, fiscal_year_id)
);

-- Function to get next number atomically
CREATE OR REPLACE FUNCTION core_config.get_next_sequence_number(
    p_org_id UUID,
    p_sequence_type VARCHAR(30),
    p_fiscal_year_id UUID DEFAULT NULL
) RETURNS VARCHAR AS $$
DECLARE
    v_record RECORD;
    v_number BIGINT;
    v_result VARCHAR;
BEGIN
    UPDATE core_config.numbering_sequence
    SET current_number = current_number + 1,
        last_used_at = now()
    WHERE organization_id = p_org_id
      AND sequence_type = p_sequence_type
      AND (fiscal_year_id = p_fiscal_year_id OR (fiscal_year_id IS NULL AND p_fiscal_year_id IS NULL))
    RETURNING prefix, suffix, current_number, min_digits
    INTO v_record;
    
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Sequence not found: % / %', p_sequence_type, p_org_id;
    END IF;
    
    v_result := COALESCE(v_record.prefix, '') 
                || LPAD(v_record.current_number::TEXT, v_record.min_digits, '0')
                || COALESCE(v_record.suffix, '');
    
    RETURN v_result;
END;
$$ LANGUAGE plpgsql;
```

### 7.2 core_config.system_configuration

```sql
CREATE TABLE core_config.system_configuration (
    config_id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID REFERENCES core_org.organization(organization_id),
        -- NULL = system-wide default
    
    config_key              VARCHAR(100) NOT NULL,
    config_value            TEXT NOT NULL,
    config_type             VARCHAR(20) NOT NULL DEFAULT 'STRING'
        CHECK (config_type IN ('STRING', 'NUMBER', 'BOOLEAN', 'JSON')),
    
    description             TEXT,
    is_encrypted            BOOLEAN NOT NULL DEFAULT false,
    
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by_user_id      UUID,
    
    CONSTRAINT uq_config UNIQUE (organization_id, config_key)
);
```

---

## 8. Audit & Compliance

### 8.1 audit.audit_log

Append-only audit trail (Document 12).

```sql
CREATE TABLE audit.audit_log (
    audit_id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    
    -- Target
    table_schema            VARCHAR(50) NOT NULL,
    table_name              VARCHAR(100) NOT NULL,
    record_id               VARCHAR(100) NOT NULL,
    
    -- Change
    action                  VARCHAR(10) NOT NULL
        CHECK (action IN ('INSERT', 'UPDATE', 'DELETE')),
    old_values              JSONB,
    new_values              JSONB,
    changed_fields          TEXT[],
    
    -- Actor
    user_id                 UUID,
    ip_address              INET,
    user_agent              TEXT,
    session_id              UUID,
    
    -- Context
    correlation_id          VARCHAR(100),
    reason                  TEXT,
    
    -- Timestamp
    occurred_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    -- Tamper detection (optional)
    hash_chain              VARCHAR(64)
        -- SHA256(prev_hash + record_payload)
);

-- CRITICAL: This table is APPEND-ONLY
-- Block UPDATE and DELETE via trigger
CREATE OR REPLACE FUNCTION audit.prevent_audit_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Audit log is append-only. Modifications are not allowed.';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_audit_log_immutable
    BEFORE UPDATE OR DELETE ON audit.audit_log
    FOR EACH ROW EXECUTE FUNCTION audit.prevent_audit_modification();

-- Indexes
CREATE INDEX idx_audit_org_table ON audit.audit_log (organization_id, table_schema, table_name);
CREATE INDEX idx_audit_record ON audit.audit_log (table_schema, table_name, record_id);
CREATE INDEX idx_audit_user ON audit.audit_log (user_id);
CREATE INDEX idx_audit_correlation ON audit.audit_log (correlation_id);
CREATE INDEX idx_audit_occurred ON audit.audit_log (occurred_at);
```

### 8.2 audit.approval_workflow

```sql
CREATE TABLE audit.approval_workflow (
    workflow_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL REFERENCES core_org.organization(organization_id),
    
    workflow_code           VARCHAR(50) NOT NULL,
    workflow_name           VARCHAR(100) NOT NULL,
    description             TEXT,
    
    -- Scope
    document_type           VARCHAR(50) NOT NULL,
        -- INVOICE, JOURNAL, PAYMENT, PO, ADJUSTMENT, PERIOD_REOPEN, AUDIT_LOCK
    
    -- Thresholds
    threshold_amount        NUMERIC(20,6),
    threshold_currency_code CHAR(3),
    
    -- Levels (JSONB array)
    approval_levels         JSONB NOT NULL,
        /*
        [
            {
                "level": 1,
                "approver_type": "ROLE" | "USER" | "DEPARTMENT_HEAD" | "COST_CENTER_OWNER",
                "approver_id": "uuid",
                "can_delegate": true,
                "required_count": 1,
                "sod_rule": "CANNOT_BE_CREATOR" | "CANNOT_BE_PREVIOUS_APPROVER" | null
            }
        ]
        */
    
    is_active               BOOLEAN NOT NULL DEFAULT true,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_workflow_code UNIQUE (organization_id, workflow_code)
);
```

### 8.3 audit.approval_request

```sql
CREATE TABLE audit.approval_request (
    request_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    workflow_id             UUID NOT NULL REFERENCES audit.approval_workflow(workflow_id),
    
    -- Document reference
    document_type           VARCHAR(50) NOT NULL,
    document_id             UUID NOT NULL,
    document_reference      VARCHAR(100),
    document_amount         NUMERIC(20,6),
    document_currency_code  CHAR(3),
    
    -- Requester
    requested_by_user_id    UUID NOT NULL,
    requested_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    -- Progress
    current_level           INTEGER NOT NULL DEFAULT 1,
    status                  VARCHAR(20) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'CANCELLED', 'ESCALATED')),
    
    -- Completion
    completed_at            TIMESTAMPTZ,
    final_approver_user_id  UUID,
    notes                   TEXT,
    
    correlation_id          VARCHAR(100)
);

CREATE INDEX idx_approval_document ON audit.approval_request (document_type, document_id);
CREATE INDEX idx_approval_status ON audit.approval_request (organization_id, status);
```

### 8.4 audit.approval_decision

```sql
CREATE TABLE audit.approval_decision (
    decision_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id              UUID NOT NULL REFERENCES audit.approval_request(request_id),
    
    level                   INTEGER NOT NULL,
    approver_user_id        UUID NOT NULL,
    delegated_from_user_id  UUID,
    
    action                  VARCHAR(20) NOT NULL
        CHECK (action IN ('APPROVE', 'REJECT', 'DELEGATE', 'ESCALATE', 'REQUEST_INFO')),
    comments                TEXT,
    
    decided_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    ip_address              INET,
    mfa_verified            BOOLEAN NOT NULL DEFAULT false
);

CREATE INDEX idx_decision_request ON audit.approval_decision (request_id);
```

### 8.5 audit.audit_lock

Per Document 08 – Audit season control.

```sql
CREATE TABLE audit.audit_lock (
    lock_id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL REFERENCES core_org.organization(organization_id),
    
    -- Scope
    scope_type              VARCHAR(30) NOT NULL
        CHECK (scope_type IN ('PERIOD_RANGE', 'FISCAL_YEAR', 'STATEMENT_INSTANCE')),
    start_period_id         UUID NOT NULL,
    end_period_id           UUID NOT NULL,
    statement_instance_id   UUID,
    
    -- Permissions during lock
    allowed_actions         JSONB NOT NULL DEFAULT '[]',
        -- e.g., ["AUDIT_ADJUSTMENT_POSTING"]
    restricted_entities     JSONB NOT NULL DEFAULT '["COA", "FX_RATE", "TAX_CODE", "CONSOLIDATION_MAPPING"]',
    
    -- Reason and evidence
    reason                  TEXT NOT NULL,
    evidence_attachment_ids UUID[],
    
    -- Enable/disable tracking (dual control per Document 13)
    enabled_by_user_id      UUID NOT NULL,
    enabled_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_by_user_id     UUID,
        -- Must be different from enabled_by_user_id (dual control)
    
    disabled_by_user_id     UUID,
    disabled_at             TIMESTAMPTZ,
    
    status                  VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'DISABLED')),
    
    correlation_id          VARCHAR(100)
);

CREATE INDEX idx_audit_lock_org ON audit.audit_lock (organization_id, status);
CREATE INDEX idx_audit_lock_periods ON audit.audit_lock (start_period_id, end_period_id);
```

### 8.6 audit.period_reopen_session

Per Document 08 – Controlled period reopening.

```sql
CREATE TABLE audit.period_reopen_session (
    session_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL REFERENCES core_org.organization(organization_id),
    fiscal_period_id        UUID NOT NULL,
    
    -- Previous state
    previous_status         VARCHAR(20) NOT NULL,
    
    -- Reopen scope (per Document 08)
    reopen_scope            VARCHAR(30) NOT NULL
        CHECK (reopen_scope IN (
            'ADJUSTMENTS_ONLY', 'REVERSALS_ONLY', 
            'POSTING_ALLOWED', 'MODULE_LIMITED'
        )),
    allowed_modules         TEXT[],  -- For MODULE_LIMITED: ['GL', 'AR']
    
    -- Time window
    time_window_start       TIMESTAMPTZ NOT NULL DEFAULT now(),
    time_window_end         TIMESTAMPTZ,
    
    -- Reason and evidence (required per Document 13)
    reason_code             VARCHAR(50) NOT NULL,
    reason_notes            TEXT NOT NULL,
    evidence_attachment_ids UUID[],
    
    -- Dual control (requestor ≠ approver per Document 13)
    requested_by_user_id    UUID NOT NULL,
    approved_by_user_id     UUID NOT NULL,
        -- CONSTRAINT: approved_by_user_id != requested_by_user_id
    
    -- Lifecycle
    opened_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at               TIMESTAMPTZ,
    auto_closed             BOOLEAN NOT NULL DEFAULT false,
    
    correlation_id          VARCHAR(100),
    
    CONSTRAINT chk_dual_control CHECK (approved_by_user_id != requested_by_user_id)
);
```

### 8.7 audit.document_attachment

```sql
CREATE TABLE audit.document_attachment (
    attachment_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    
    -- Reference
    document_type           VARCHAR(50) NOT NULL,
    document_id             UUID NOT NULL,
    
    -- File info
    file_name               VARCHAR(255) NOT NULL,
    file_type               VARCHAR(50) NOT NULL,
    file_size_bytes         BIGINT NOT NULL,
    storage_path            VARCHAR(500) NOT NULL,
    storage_provider        VARCHAR(30) NOT NULL DEFAULT 'LOCAL',
    checksum_sha256         VARCHAR(64),
    
    -- Metadata
    description             TEXT,
    attachment_category     VARCHAR(30)
        CHECK (attachment_category IN (
            'SUPPORTING_DOC', 'EVIDENCE', 'APPROVAL', 
            'CONTRACT', 'INVOICE_IMAGE', 'OTHER'
        )),
    
    -- Lifecycle
    uploaded_by_user_id     UUID NOT NULL,
    uploaded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_active               BOOLEAN NOT NULL DEFAULT true,
    deleted_at              TIMESTAMPTZ,
    deleted_by_user_id      UUID
);

CREATE INDEX idx_attachment_doc ON audit.document_attachment (document_type, document_id);
```

---

## 9. General Ledger & Posting

### 9.1 gl.fiscal_year

```sql
CREATE TABLE gl.fiscal_year (
    fiscal_year_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL REFERENCES core_org.organization(organization_id),
    
    year_code               VARCHAR(10) NOT NULL,
    year_name               VARCHAR(50) NOT NULL,
    start_date              DATE NOT NULL,
    end_date                DATE NOT NULL,
    
    status                  VARCHAR(20) NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'CLOSED', 'LOCKED')),
    
    is_adjustment_year      BOOLEAN NOT NULL DEFAULT false,
    
    closed_by_user_id       UUID,
    closed_at               TIMESTAMPTZ,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_fiscal_year UNIQUE (organization_id, year_code),
    CONSTRAINT chk_fiscal_dates CHECK (start_date < end_date)
);
```

### 9.2 gl.fiscal_period

```sql
CREATE TABLE gl.fiscal_period (
    fiscal_period_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fiscal_year_id          UUID NOT NULL REFERENCES gl.fiscal_year(fiscal_year_id),
    organization_id         UUID NOT NULL,
    
    period_number           INTEGER NOT NULL,
    period_name             VARCHAR(50) NOT NULL,
    start_date              DATE NOT NULL,
    end_date                DATE NOT NULL,
    
    -- Status (per Document 08)
    status                  VARCHAR(20) NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'SOFT_CLOSED', 'HARD_CLOSED', 'LOCKED')),
    
    -- Special periods
    is_adjustment_period    BOOLEAN NOT NULL DEFAULT false,
        -- Period 13 for audit adjustments
    is_closing_period       BOOLEAN NOT NULL DEFAULT false,
        -- Period 14 for year-end closing entries
    
    -- Close tracking
    soft_closed_by_user_id  UUID,
    soft_closed_at          TIMESTAMPTZ,
    hard_closed_by_user_id  UUID,
    hard_closed_at          TIMESTAMPTZ,
    locked_by_user_id       UUID,
    locked_at               TIMESTAMPTZ,
    
    -- Reopen tracking
    reopened_count          INTEGER NOT NULL DEFAULT 0,
    
    -- Checklist
    checklist_id            UUID,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_fiscal_period UNIQUE (fiscal_year_id, period_number),
    CONSTRAINT chk_period_dates CHECK (start_date <= end_date)
);

CREATE INDEX idx_period_org_status ON gl.fiscal_period (organization_id, status);
CREATE INDEX idx_period_dates ON gl.fiscal_period (organization_id, start_date, end_date);
```

### 9.3 gl.period_close_checklist

```sql
CREATE TABLE gl.period_close_checklist (
    checklist_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    fiscal_period_id        UUID NOT NULL REFERENCES gl.fiscal_period(fiscal_period_id),
    
    status                  VARCHAR(20) NOT NULL DEFAULT 'NOT_STARTED'
        CHECK (status IN ('NOT_STARTED', 'IN_PROGRESS', 'COMPLETED')),
    
    started_at              TIMESTAMPTZ,
    started_by_user_id      UUID,
    completed_at            TIMESTAMPTZ,
    completed_by_user_id    UUID,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_checklist_period UNIQUE (fiscal_period_id)
);
```

### 9.4 gl.period_close_checklist_item

```sql
CREATE TABLE gl.period_close_checklist_item (
    item_id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    checklist_id            UUID NOT NULL REFERENCES gl.period_close_checklist(checklist_id),
    
    item_code               VARCHAR(50) NOT NULL,
    item_description        TEXT NOT NULL,
    
    -- Category (per Document 08)
    category                VARCHAR(30) NOT NULL
        CHECK (category IN (
            'BANK', 'AR', 'AP', 'FA', 'INVENTORY', 
            'LEASES', 'TAX', 'INTERCOMPANY', 'GL', 'REPORTING'
        )),
    
    sequence_order          INTEGER NOT NULL,
    is_mandatory            BOOLEAN NOT NULL DEFAULT true,
    
    -- Automation
    is_automated            BOOLEAN NOT NULL DEFAULT false,
    automation_job_name     VARCHAR(100),
    
    -- Status
    status                  VARCHAR(20) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'IN_PROGRESS', 'COMPLETED', 'SKIPPED', 'FAILED')),
    
    completed_by_user_id    UUID,
    completed_at            TIMESTAMPTZ,
    notes                   TEXT,
    evidence_attachment_ids UUID[],
    failure_reason          TEXT,
    
    CONSTRAINT uq_checklist_item UNIQUE (checklist_id, item_code)
);
```

### 9.5 gl.account_category

```sql
CREATE TABLE gl.account_category (
    category_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL REFERENCES core_org.organization(organization_id),
    
    category_code           VARCHAR(20) NOT NULL,
    category_name           VARCHAR(100) NOT NULL,
    
    -- Classification
    account_type            VARCHAR(20) NOT NULL
        CHECK (account_type IN ('ASSET', 'LIABILITY', 'EQUITY', 'REVENUE', 'EXPENSE')),
    normal_balance          VARCHAR(10) NOT NULL
        CHECK (normal_balance IN ('DEBIT', 'CREDIT')),
    
    -- IFRS mapping
    ifrs_statement          VARCHAR(30) NOT NULL
        CHECK (ifrs_statement IN (
            'FINANCIAL_POSITION', 'COMPREHENSIVE_INCOME', 
            'CASH_FLOWS', 'EQUITY_CHANGES'
        )),
    ifrs_classification     VARCHAR(30)
        CHECK (ifrs_classification IN (
            'CURRENT', 'NON_CURRENT', 'OPERATING', 
            'INVESTING', 'FINANCING'
        )),
    ifrs_line_item_mapping  VARCHAR(100),
    
    -- Hierarchy
    parent_category_id      UUID REFERENCES gl.account_category(category_id),
    display_order           INTEGER NOT NULL DEFAULT 0,
    
    is_active               BOOLEAN NOT NULL DEFAULT true,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_category_code UNIQUE (organization_id, category_code)
);
```

### 9.6 gl.account

```sql
CREATE TABLE gl.account (
    account_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL REFERENCES core_org.organization(organization_id),
    
    account_number          VARCHAR(20) NOT NULL,
    account_name            VARCHAR(200) NOT NULL,
    description             TEXT,
    
    -- Classification
    category_id             UUID NOT NULL REFERENCES gl.account_category(category_id),
    account_type            VARCHAR(20) NOT NULL
        CHECK (account_type IN ('ASSET', 'LIABILITY', 'EQUITY', 'REVENUE', 'EXPENSE')),
    account_subtype         VARCHAR(50),
    normal_balance          VARCHAR(10) NOT NULL
        CHECK (normal_balance IN ('DEBIT', 'CREDIT')),
    
    -- Control account settings
    is_control_account      BOOLEAN NOT NULL DEFAULT false,
    control_account_type    VARCHAR(20)
        CHECK (control_account_type IN (
            'AR', 'AP', 'INVENTORY', 'FIXED_ASSET', 
            'BANK', 'LEASE_LIABILITY', 'ROU_ASSET', 'NONE'
        )),
    
    -- Bank account
    is_bank_account         BOOLEAN NOT NULL DEFAULT false,
    bank_account_details    JSONB,
        -- { bank_name, account_number_masked, routing_number, swift_code, iban }
    
    -- Currency
    currency_code           CHAR(3),
    is_multi_currency       BOOLEAN NOT NULL DEFAULT false,
    
    -- Dimension requirements
    requires_cost_center    BOOLEAN NOT NULL DEFAULT false,
    requires_project        BOOLEAN NOT NULL DEFAULT false,
    requires_segment        BOOLEAN NOT NULL DEFAULT false,
    requires_intercompany   BOOLEAN NOT NULL DEFAULT false,
    
    -- Tax
    default_tax_code_id     UUID,
    
    -- Cash flow
    cash_flow_category      VARCHAR(20)
        CHECK (cash_flow_category IN ('OPERATING', 'INVESTING', 'FINANCING', 'NONE')),
    
    -- Reconciliation
    is_reconcilable         BOOLEAN NOT NULL DEFAULT false,
    
    -- Hierarchy
    parent_account_id       UUID REFERENCES gl.account(account_id),
    hierarchy_level         INTEGER NOT NULL DEFAULT 1,
    
    -- Posting
    is_posting_allowed      BOOLEAN NOT NULL DEFAULT true,
    
    -- Opening balance
    opening_balance_date    DATE,
    opening_balance_amount  NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    -- Effective dating
    is_active               BOOLEAN NOT NULL DEFAULT true,
    effective_from          DATE NOT NULL DEFAULT CURRENT_DATE,
    effective_to            DATE,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_account_number UNIQUE (organization_id, account_number)
);

CREATE INDEX idx_account_category ON gl.account (category_id);
CREATE INDEX idx_account_type ON gl.account (organization_id, account_type);
CREATE INDEX idx_account_control ON gl.account (organization_id, control_account_type) 
    WHERE is_control_account;
```

### 9.7 gl.journal_type

```sql
CREATE TABLE gl.journal_type (
    journal_type_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL REFERENCES core_org.organization(organization_id),
    
    type_code               VARCHAR(20) NOT NULL,
    type_name               VARCHAR(100) NOT NULL,
    description             TEXT,
    
    -- Source module (per Document 07)
    source_module           VARCHAR(30) NOT NULL
        CHECK (source_module IN (
            'GL', 'AR', 'AP', 'FA', 'INVENTORY', 'LEASE',
            'PAYROLL', 'BANK', 'CONSOLIDATION', 'REVALUATION', 
            'TAX', 'ADJUSTMENT', 'MANUAL'
        )),
    
    auto_numbering_prefix   VARCHAR(10),
    
    -- Approval
    requires_approval       BOOLEAN NOT NULL DEFAULT false,
    approval_workflow_id    UUID REFERENCES audit.approval_workflow(workflow_id),
    
    -- Reversal
    default_reversal_period VARCHAR(20)
        CHECK (default_reversal_period IN ('SAME_PERIOD', 'NEXT_PERIOD', 'MANUAL')),
    
    -- Flags
    is_system_generated     BOOLEAN NOT NULL DEFAULT false,
    is_audit_adjustment     BOOLEAN NOT NULL DEFAULT false,
        -- true for audit adjustment journals only
    
    is_active               BOOLEAN NOT NULL DEFAULT true,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_journal_type UNIQUE (organization_id, type_code)
);
```

### 9.8 gl.journal_entry

Document layer – mutable until posted (Document 07).

```sql
CREATE TABLE gl.journal_entry (
    journal_entry_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL REFERENCES core_org.organization(organization_id),
    journal_type_id         UUID NOT NULL REFERENCES gl.journal_type(journal_type_id),
    
    entry_number            VARCHAR(30) NOT NULL,
    fiscal_period_id        UUID NOT NULL REFERENCES gl.fiscal_period(fiscal_period_id),
    
    -- Dates
    entry_date              DATE NOT NULL,
    posting_date            DATE,
    
    -- Description
    description             TEXT NOT NULL,
    reference               VARCHAR(100),
    
    -- Source document
    source_document_type    VARCHAR(50),
        -- INVOICE, PAYMENT, DEPRECIATION_RUN, LEASE_RUN, etc.
    source_document_id      UUID,
    
    -- Currency
    currency_code           CHAR(3) NOT NULL,
    exchange_rate           NUMERIC(20,10),
    exchange_rate_type_id   UUID,
    
    -- Totals
    total_debit             NUMERIC(20,6) NOT NULL DEFAULT 0,
    total_credit            NUMERIC(20,6) NOT NULL DEFAULT 0,
    total_debit_functional  NUMERIC(20,6) NOT NULL DEFAULT 0,
    total_credit_functional NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    -- Status
    status                  VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN (
            'DRAFT', 'SUBMITTED', 'PENDING_APPROVAL',
            'APPROVED', 'POSTED', 'REVERSED', 'REJECTED'
        )),
    
    -- Reversal tracking
    is_reversing_entry      BOOLEAN NOT NULL DEFAULT false,
    reversed_entry_id       UUID REFERENCES gl.journal_entry(journal_entry_id),
        -- Points to the entry this reverses (if is_reversing_entry = true)
    original_entry_id       UUID REFERENCES gl.journal_entry(journal_entry_id),
        -- Back-pointer from original to reversal
    reversal_date           DATE,
    reversal_reason_code    VARCHAR(50),
    
    -- Recurring
    is_recurring            BOOLEAN NOT NULL DEFAULT false,
    recurring_template_id   UUID,
    
    -- Intercompany
    is_intercompany         BOOLEAN NOT NULL DEFAULT false,
    intercompany_batch_id   UUID,
    
    -- Posting control (Document 07)
    posting_version         INTEGER NOT NULL DEFAULT 0,
        -- Incremented each time posted (for idempotency)
    
    -- SoD tracking (Document 13)
    created_by_user_id      UUID NOT NULL,
    submitted_by_user_id    UUID,
    submitted_at            TIMESTAMPTZ,
    approved_by_user_id     UUID,
    approved_at             TIMESTAMPTZ,
    posted_by_user_id       UUID,
    posted_at               TIMESTAMPTZ,
    reversed_by_user_id     UUID,
    reversed_at             TIMESTAMPTZ,
    
    approval_request_id     UUID REFERENCES audit.approval_request(request_id),
    correlation_id          VARCHAR(100),
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_journal_entry UNIQUE (organization_id, entry_number)
);

CREATE INDEX idx_je_period ON gl.journal_entry (organization_id, fiscal_period_id);
CREATE INDEX idx_je_status ON gl.journal_entry (organization_id, status);
CREATE INDEX idx_je_source ON gl.journal_entry (source_document_type, source_document_id);
CREATE INDEX idx_je_correlation ON gl.journal_entry (correlation_id);
```

### 9.9 gl.journal_entry_line

```sql
CREATE TABLE gl.journal_entry_line (
    line_id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    journal_entry_id        UUID NOT NULL REFERENCES gl.journal_entry(journal_entry_id),
    line_number             INTEGER NOT NULL,
    
    -- Account
    account_id              UUID NOT NULL REFERENCES gl.account(account_id),
    description             TEXT,
    
    -- Amounts (exactly one must be > 0)
    debit_amount            NUMERIC(20,6) NOT NULL DEFAULT 0,
    credit_amount           NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    -- Currency
    currency_code           CHAR(3) NOT NULL,
    exchange_rate           NUMERIC(20,10),
    debit_amount_functional NUMERIC(20,6) NOT NULL DEFAULT 0,
    credit_amount_functional NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    -- Dimensions
    business_unit_id        UUID,
    cost_center_id          UUID,
    project_id              UUID,
    segment_id              UUID,
    intercompany_org_id     UUID,
    
    -- Tax
    tax_code_id             UUID,
    tax_amount              NUMERIC(20,6),
    
    -- Subledger pointers (Document 07)
    subledger_type          VARCHAR(20)
        CHECK (subledger_type IN ('AR', 'AP', 'FA', 'INVENTORY', 'LEASE', 'NONE')),
    subledger_id            UUID,
    
    -- Additional
    quantity                NUMERIC(20,6),
    unit_of_measure         VARCHAR(20),
    statistical_amount      NUMERIC(20,6),
    reference_1             VARCHAR(100),
    reference_2             VARCHAR(100),
    user_defined_fields     JSONB,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_je_line UNIQUE (journal_entry_id, line_number),
    
    -- Exactly one of debit or credit must be > 0
    CONSTRAINT chk_debit_or_credit CHECK (
        (debit_amount > 0 AND credit_amount = 0) OR 
        (credit_amount > 0 AND debit_amount = 0)
    ),
    CONSTRAINT chk_amounts_positive CHECK (debit_amount >= 0 AND credit_amount >= 0)
);

CREATE INDEX idx_jel_account ON gl.journal_entry_line (account_id);
CREATE INDEX idx_jel_dimensions ON gl.journal_entry_line (business_unit_id, cost_center_id, segment_id);
```

### 9.10 gl.posting_batch

Groups lines from one posting action (Document 07).

```sql
CREATE TABLE gl.posting_batch (
    posting_batch_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    
    batch_number            VARCHAR(50) NOT NULL,
    posting_date            DATE NOT NULL,
    fiscal_period_id        UUID NOT NULL REFERENCES gl.fiscal_period(fiscal_period_id),
    
    -- Posting mode (Document 07)
    posting_mode            VARCHAR(30) NOT NULL
        CHECK (posting_mode IN (
            'NORMAL', 'REVALUATION', 'DEPRECIATION_RUN',
            'LEASE_RUN', 'INVENTORY_VALUATION', 'CONSOLIDATION',
            'ADJUSTMENT', 'REVERSAL'
        )),
    
    -- Source
    source_document_type    VARCHAR(50),
    source_document_id      UUID,
    journal_entry_id        UUID NOT NULL REFERENCES gl.journal_entry(journal_entry_id),
    
    -- Totals
    line_count              INTEGER NOT NULL,
    total_debit_functional  NUMERIC(20,6) NOT NULL,
    total_credit_functional NUMERIC(20,6) NOT NULL,
    
    -- Actor
    posted_by_user_id       UUID,
    posted_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    -- Idempotency (Document 07)
    correlation_id          VARCHAR(100),
    idempotency_key         VARCHAR(200) NOT NULL,
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    -- Idempotency: only ONE posting batch per idempotency_key
    CONSTRAINT uq_posting_batch UNIQUE (organization_id, idempotency_key),
    -- Prevent duplicate re-posts under retries
    CONSTRAINT uq_posting_batch_version UNIQUE (organization_id, journal_entry_id, posting_version),
    CONSTRAINT chk_batch_balance CHECK (total_debit_functional = total_credit_functional)
);
```

### 9.11 gl.posted_ledger_line

**Canonical ledger – APPEND-ONLY, IMMUTABLE** (Document 07).

```sql
CREATE TABLE gl.posted_ledger_line (
    posting_line_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    
    -- Batch reference
    posting_batch_id        UUID NOT NULL REFERENCES gl.posting_batch(posting_batch_id),
    
    -- Timing
    posting_date            DATE NOT NULL,
    fiscal_period_id        UUID NOT NULL REFERENCES gl.fiscal_period(fiscal_period_id),
    
    -- Source
    journal_entry_id        UUID NOT NULL REFERENCES gl.journal_entry(journal_entry_id),
    journal_entry_line_id   UUID NOT NULL REFERENCES gl.journal_entry_line(line_id),
    account_id              UUID NOT NULL REFERENCES gl.account(account_id),
    
    -- Transaction currency amounts
    debit_amount_txn        NUMERIC(20,6) NOT NULL DEFAULT 0,
    credit_amount_txn       NUMERIC(20,6) NOT NULL DEFAULT 0,
    currency_code_txn       CHAR(3) NOT NULL,
    exchange_rate           NUMERIC(20,10) NOT NULL,
    exchange_rate_type_id   UUID,
    
    -- Functional currency amounts (MANDATORY)
    debit_amount_functional NUMERIC(20,6) NOT NULL DEFAULT 0,
    credit_amount_functional NUMERIC(20,6) NOT NULL DEFAULT 0,
    functional_currency_code CHAR(3) NOT NULL,
    
    -- Presentation currency amounts (optional – can compute at report time)
    debit_amount_presentation NUMERIC(20,6),
    credit_amount_presentation NUMERIC(20,6),
    presentation_currency_code CHAR(3),
    
    -- Dimensions
    business_unit_id        UUID,
    cost_center_id          UUID,
    project_id              UUID,
    segment_id              UUID,
    intercompany_org_id     UUID,
    
    -- Tax
    tax_code_id             UUID,
    tax_amount_txn          NUMERIC(20,6),
    
    -- Subledger pointers (Document 07)
    source_document_type    VARCHAR(50),
    source_document_id      UUID,
    subledger_type          VARCHAR(20),
    subledger_id            UUID,
    
    -- Control fields (Document 07)
    posting_version         INTEGER NOT NULL DEFAULT 1,
    idempotency_key         VARCHAR(200) NOT NULL,
        -- References posting_batch.idempotency_key (NOT unique here - batch has many lines)
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
    
    -- NOTE: Idempotency is enforced at posting_batch level, NOT here.
    -- A single posting batch produces multiple ledger lines sharing the same idempotency_key.
) PARTITION BY RANGE (posting_date);

-- CRITICAL: This table is APPEND-ONLY
-- Block UPDATE and DELETE via trigger
CREATE OR REPLACE FUNCTION gl.prevent_ledger_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Posted ledger is append-only. Use reversal entries for corrections.';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ledger_immutable
    BEFORE UPDATE OR DELETE ON gl.posted_ledger_line
    FOR EACH ROW EXECUTE FUNCTION gl.prevent_ledger_modification();

-- Create partitions by year
CREATE TABLE gl.posted_ledger_line_y2024 
    PARTITION OF gl.posted_ledger_line
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');

CREATE TABLE gl.posted_ledger_line_y2025 
    PARTITION OF gl.posted_ledger_line
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');

CREATE TABLE gl.posted_ledger_line_y2026 
    PARTITION OF gl.posted_ledger_line
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');

-- REQUIRED INDEXES (per partition) - Document 12
CREATE INDEX idx_pll_org_period_account 
    ON gl.posted_ledger_line (organization_id, fiscal_period_id, account_id);
CREATE INDEX idx_pll_org_posting_date 
    ON gl.posted_ledger_line (organization_id, posting_date);
CREATE INDEX idx_pll_doc_ref 
    ON gl.posted_ledger_line (organization_id, source_document_type, source_document_id);
CREATE INDEX idx_pll_journal 
    ON gl.posted_ledger_line (journal_entry_id, journal_entry_line_id);
CREATE INDEX idx_pll_batch 
    ON gl.posted_ledger_line (posting_batch_id);
```

### 9.12 gl.account_balance

Derived cache – rebuilt from posted_ledger_line (Document 07).

```sql
CREATE TABLE gl.account_balance (
    balance_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    account_id              UUID NOT NULL REFERENCES gl.account(account_id),
    fiscal_period_id        UUID NOT NULL REFERENCES gl.fiscal_period(fiscal_period_id),
    
    -- Dimensions (NULL = aggregate across dimension)
    business_unit_id        UUID,
    cost_center_id          UUID,
    segment_id              UUID,
    
    -- Functional currency (mandatory)
    opening_debit_functional  NUMERIC(20,6) NOT NULL DEFAULT 0,
    opening_credit_functional NUMERIC(20,6) NOT NULL DEFAULT 0,
    period_debit_functional   NUMERIC(20,6) NOT NULL DEFAULT 0,
    period_credit_functional  NUMERIC(20,6) NOT NULL DEFAULT 0,
    closing_debit_functional  NUMERIC(20,6) GENERATED ALWAYS AS 
        (opening_debit_functional + period_debit_functional) STORED,
    closing_credit_functional NUMERIC(20,6) GENERATED ALWAYS AS 
        (opening_credit_functional + period_credit_functional) STORED,
    ytd_debit_functional      NUMERIC(20,6) NOT NULL DEFAULT 0,
    ytd_credit_functional     NUMERIC(20,6) NOT NULL DEFAULT 0,
    
    -- Transaction currency (for foreign currency accounts)
    currency_code           CHAR(3),
    period_debit_txn        NUMERIC(20,6),
    period_credit_txn       NUMERIC(20,6),
    
    -- Tracking
    last_posting_date       DATE,
    last_updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    rebuild_required        BOOLEAN NOT NULL DEFAULT false,
    
    -- Uniqueness across all dimension combinations
    CONSTRAINT uq_account_balance UNIQUE (
        organization_id, account_id, fiscal_period_id,
        COALESCE(business_unit_id, '00000000-0000-0000-0000-000000000000'),
        COALESCE(cost_center_id, '00000000-0000-0000-0000-000000000000'),
        COALESCE(segment_id, '00000000-0000-0000-0000-000000000000')
    )
);

CREATE INDEX idx_balance_account ON gl.account_balance (account_id, fiscal_period_id);
CREATE INDEX idx_balance_rebuild ON gl.account_balance (organization_id, rebuild_required) 
    WHERE rebuild_required;
```

### 9.13 gl.recurring_journal_template

```sql
CREATE TABLE gl.recurring_journal_template (
    template_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL REFERENCES core_org.organization(organization_id),
    
    template_code           VARCHAR(30) NOT NULL,
    template_name           VARCHAR(100) NOT NULL,
    journal_type_id         UUID NOT NULL REFERENCES gl.journal_type(journal_type_id),
    description             TEXT,
    
    -- Schedule
    frequency               VARCHAR(20) NOT NULL
        CHECK (frequency IN ('DAILY', 'WEEKLY', 'MONTHLY', 'QUARTERLY', 'ANNUALLY')),
    day_of_month            INTEGER,  -- For MONTHLY
    month_of_year           INTEGER,  -- For ANNUALLY
    
    start_date              DATE NOT NULL,
    end_date                DATE,
    next_run_date           DATE,
    
    -- Occurrence limits
    total_occurrences       INTEGER,
    completed_occurrences   INTEGER NOT NULL DEFAULT 0,
    
    -- Amount handling
    amount_type             VARCHAR(20) NOT NULL DEFAULT 'FIXED'
        CHECK (amount_type IN ('FIXED', 'VARIABLE', 'CALCULATED')),
    calculation_formula     TEXT,
    
    -- Posting options
    auto_post               BOOLEAN NOT NULL DEFAULT false,
    auto_reverse            BOOLEAN NOT NULL DEFAULT false,
    reversal_period_offset  INTEGER,  -- 0 = same period, 1 = next period
    
    -- Status
    status                  VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'PAUSED', 'COMPLETED', 'CANCELLED')),
    
    last_generated_date     DATE,
    
    -- Line templates (JSONB)
    template_lines          JSONB NOT NULL,
        /*
        [
            {
                "line_number": 1,
                "account_id": "uuid",
                "description": "...",
                "debit_amount": 1000.00,
                "credit_amount": 0,
                "business_unit_id": "uuid",
                "cost_center_id": "uuid"
            }
        ]
        */
    
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    CONSTRAINT uq_recurring_template UNIQUE (organization_id, template_code)
);
```

### 9.14 gl.reconciliation_issue

Operational tooling for ledger integrity (Document 12).

```sql
CREATE TABLE gl.reconciliation_issue (
    issue_id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL,
    
    issue_type              VARCHAR(50) NOT NULL
        CHECK (issue_type IN (
            'BALANCE_MISMATCH', 'CONTROL_ACCOUNT_DIFF',
            'SUBLEDGER_MISMATCH', 'POSTING_IMBALANCE',
            'ORPHAN_POSTING', 'MISSING_DIMENSION'
        )),
    severity                VARCHAR(20) NOT NULL
        CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    
    detected_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    -- Context
    fiscal_period_id        UUID,
    account_id              UUID,
    posting_batch_id        UUID,
    
    -- Details
    expected_value          NUMERIC(20,6),
    actual_value            NUMERIC(20,6),
    difference              NUMERIC(20,6),
    evidence                JSONB NOT NULL,
    
    -- Resolution
    status                  VARCHAR(20) NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'INVESTIGATING', 'RESOLVED', 'ACCEPTED')),
    resolution_notes        TEXT,
    resolved_by_user_id     UUID,
    resolved_at             TIMESTAMPTZ
);

CREATE INDEX idx_recon_issue_status ON gl.reconciliation_issue (organization_id, status);
```

---

## Summary

Part 1 covers the foundational infrastructure:

| Schema | Tables | Purpose |
|--------|--------|---------|
| `platform` | 3 | Event outbox, idempotency |
| `core_identity` | 8 | Users, roles, permissions, sessions |
| `core_org` | 6 | Organizations, dimensions |
| `core_fx` | 4 | Currency, exchange rates |
| `core_config` | 2 | Sequences, configuration |
| `audit` | 7 | Audit logs, approvals, locks |
| `gl` | 14 | Chart of accounts, journals, ledger |

**Total: 44 tables**

---

## Appendix A: MVP Feature Configuration

The full schema is deployed, but workflows are gated by feature flags.

### A.1 MVP-Enabled Features

| Module | Feature | Status |
|--------|---------|--------|
| GL | Journal entry creation | ✅ Enabled |
| GL | Journal posting | ✅ Enabled |
| GL | Reversal entries | ✅ Enabled |
| GL | Period soft/hard close | ✅ Enabled |
| GL | Period lock | ✅ Enabled |
| Period | Reopen sessions | ✅ Enabled |
| Audit | Audit logging | ✅ Enabled |
| Audit | Approval workflows | ✅ Enabled |
| FX | Exchange rate management | ✅ Enabled |
| FX | Multi-currency postings | ✅ Enabled |

### A.2 MVP-Deferred Features

| Module | Feature | Status | Reason |
|--------|---------|--------|--------|
| GL | Recurring journal templates | 🔒 Deferred | Convenience, not core |
| GL | Reconciliation issues | 🔒 Deferred | Operational tooling |
| Audit | Audit lock overlay | 🔒 Deferred | Year-end only |
| FX | CTA automation | 🔒 Deferred | Manual OK for MVP |

### A.3 Idempotency Architecture (Critical)

```
┌─────────────────────────────────────────────────────────────────┐
│                    IDEMPOTENCY ENFORCEMENT                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  posting_batch.idempotency_key  ──> UNIQUE (org, key)          │
│       │                                                         │
│       └──> posted_ledger_line.idempotency_key (NOT unique)     │
│            (Many lines share same key from one batch)          │
│                                                                 │
│  posting_batch ──> UNIQUE (org, journal_entry_id, version)     │
│       │                                                         │
│       └──> Prevents duplicate re-posts under retries           │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### A.4 Functional Currency Rule

**CRITICAL**: Every posting-producing operation MUST compute functional currency amounts BEFORE calling the posting service.

| Source | Functional Currency Field | Enforcement |
|--------|--------------------------|-------------|
| `gl.journal_entry_line` | `*_amount_functional` | Application layer |
| `ar.invoice` | `functional_currency_amount` | Application layer |
| `ap.supplier_invoice` | `functional_currency_amount` | Application layer |
| `fa.depreciation_run` | Via journal lines | Application layer |
| `lease.lease_run` | Via journal lines | Application layer |
| `inv.inventory_transaction` | `total_cost` in functional | Application layer |

**Posting service rejects any batch where functional amounts are NULL or zero.**

---

**Continue to Part 2** for:
- Accounts Receivable (IFRS 15, IFRS 9 ECL)
- Accounts Payable
- Fixed Assets (IAS 16, IAS 36, IAS 38)
- Leases (IFRS 16)
- Inventory (IAS 2)
- Financial Instruments (IFRS 9)
- Tax Management (IAS 12)
- Consolidation
- Financial Reporting
