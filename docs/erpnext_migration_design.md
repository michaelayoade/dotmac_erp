# ERPNext Migration Sync System Design

## Overview

Single-direction sync system to migrate books, assets, and inventory data from ERPNext into Dotmac ERP.

**Direction**: ERPNext → Dotmac ERP (one-way import only)

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│    ERPNext      │────▶│  Migration       │────▶│  Dotmac ERP   │
│    (Source)     │     │  Service         │     │  (Target)       │
└─────────────────┘     └──────────────────┘     └─────────────────┘
        │                       │                        │
        │                       ▼                        │
        │               ┌──────────────────┐             │
        │               │  Sync State      │             │
        │               │  Tracking        │             │
        │               └──────────────────┘             │
        │                       │                        │
        ▼                       ▼                        ▼
   Frappe REST API      sync_entity table         Finance Models
   (read-only)          sync_history table        (fa, inv, gl)
```

## Components

### 1. ERPNext API Client (`app/services/erpnext/client.py`)

Read-only client for ERPNext/Frappe REST API.

**Authentication Methods**:
- API Key + Secret (recommended for server-to-server)
- OAuth 2.0 Bearer Token

**Core Operations**:
- `list_documents(doctype, filters, fields, limit, offset)` - Paginated listing
- `get_document(doctype, name)` - Single document fetch
- `get_count(doctype, filters)` - Count for progress tracking

### 2. Sync State Models (`app/models/sync/`)

Track migration state to enable:
- Incremental syncs (only new/modified records)
- Audit trail of what was imported
- Error tracking and retry

**Tables**:

```sql
-- Track individual entity sync status
CREATE TABLE sync.sync_entity (
    sync_id UUID PRIMARY KEY,
    organization_id UUID NOT NULL,
    source_system VARCHAR(50) NOT NULL,  -- 'erpnext'
    source_doctype VARCHAR(100) NOT NULL, -- 'Item', 'Asset', etc.
    source_name VARCHAR(255) NOT NULL,    -- ERPNext document name
    target_table VARCHAR(100) NOT NULL,   -- 'inv.item', 'fa.asset'
    target_id UUID,                        -- Dotmac ERP entity ID
    sync_status VARCHAR(20) NOT NULL,     -- pending/synced/failed/skipped
    source_modified TIMESTAMP,            -- ERPNext modified timestamp
    synced_at TIMESTAMP,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(organization_id, source_system, source_doctype, source_name)
);

-- Track sync job runs
CREATE TABLE sync.sync_history (
    history_id UUID PRIMARY KEY,
    organization_id UUID NOT NULL,
    source_system VARCHAR(50) NOT NULL,
    sync_type VARCHAR(50) NOT NULL,       -- 'full', 'incremental'
    entity_types JSONB,                   -- ['items', 'assets', 'accounts']
    status VARCHAR(20) NOT NULL,          -- running/completed/failed
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    total_records INTEGER DEFAULT 0,
    synced_count INTEGER DEFAULT 0,
    skipped_count INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    errors JSONB,                         -- [{doctype, name, error}]
    created_by_user_id UUID NOT NULL
);
```

### 3. Field Mappings (`app/services/erpnext/mappings/`)

Configuration files mapping ERPNext DocTypes to Dotmac ERP models.

## ERPNext DocType → Dotmac ERP Mapping

### Chart of Accounts

| ERPNext Field | Dotmac ERP Field | Notes |
|---------------|-------------------|-------|
| `name` | `account_code` | ERPNext uses name as identifier |
| `account_name` | `account_name` | |
| `root_type` | `account_category` | Asset/Liability/Equity/Income/Expense |
| `account_type` | `subledger_type` | Bank/Receivable/Payable/Stock/Fixed Asset |
| `is_group` | `is_header` | Parent account flag |
| `parent_account` | `parent_account_id` | Lookup by source_name |
| `account_currency` | `currency_code` | |
| `disabled` | `is_active` | Inverted |

### Items (Inventory)

| ERPNext Field | Dotmac ERP Field | Notes |
|---------------|-------------------|-------|
| `item_code` | `item_code` | |
| `item_name` | `item_name` | |
| `description` | `description` | |
| `item_group` | `category_id` | Create/lookup category |
| `stock_uom` | `base_uom` | |
| `is_stock_item` | `track_inventory` | |
| `has_batch_no` | `track_lots` | |
| `has_serial_no` | `track_serial_numbers` | |
| `valuation_method` | `costing_method` | FIFO/Moving Average |
| `standard_rate` | `standard_cost` | |
| `last_purchase_rate` | `last_purchase_cost` | |
| `disabled` | `is_active` | Inverted |
| `is_purchase_item` | `is_purchaseable` | |
| `is_sales_item` | `is_saleable` | |

### Item Groups → Item Categories

| ERPNext Field | Dotmac ERP Field | Notes |
|---------------|-------------------|-------|
| `name` | `category_code` | |
| `item_group_name` | `category_name` | |
| `parent_item_group` | `parent_category_id` | Lookup |
| `is_group` | - | For hierarchy |

### Assets (Fixed Assets)

| ERPNext Field | Dotmac ERP Field | Notes |
|---------------|-------------------|-------|
| `name` | `asset_number` | |
| `asset_name` | `asset_name` | |
| `item_code` | - | Link to item if needed |
| `asset_category` | `category_id` | Create/lookup |
| `company` | `organization_id` | Map to org |
| `location` | `location_id` | Create/lookup |
| `purchase_date` | `acquisition_date` | |
| `available_for_use_date` | `in_service_date` | |
| `gross_purchase_amount` | `acquisition_cost` | |
| `asset_quantity` | - | DotMac tracks individual |
| `depreciation_method` | `depreciation_method` | Map values |
| `total_number_of_depreciations` | `useful_life_months` | Convert |
| `frequency_of_depreciation` | - | Used with total for months |
| `expected_value_after_useful_life` | `residual_value` | |
| `opening_accumulated_depreciation` | `accumulated_depreciation` | |
| `status` | `status` | Map: Draft/Submitted/Scrapped |
| `serial_no` | `serial_number` | |
| `custodian` | `custodian_user_id` | Lookup user |
| `disposal_date` | `disposal_date` | |
| `value_after_depreciation` | `net_book_value` | Calculated |

### Asset Categories

| ERPNext Field | Dotmac ERP Field | Notes |
|---------------|-------------------|-------|
| `name` | `category_code` | |
| `asset_category_name` | `category_name` | |
| `depreciation_method` | `depreciation_method` | Default |
| `total_number_of_depreciations` | `useful_life_months` | Default |
| `frequency_of_depreciation` | - | Monthly=12x |
| `expected_value_after_useful_life` | `residual_value_percent` | As % |

### Warehouses

| ERPNext Field | Dotmac ERP Field | Notes |
|---------------|-------------------|-------|
| `name` | `warehouse_code` | |
| `warehouse_name` | `warehouse_name` | |
| `is_group` | - | For hierarchy |
| `parent_warehouse` | - | Flatten or map |
| `disabled` | `is_active` | Inverted |
| `address` | `address` | JSONB |
| `company` | `organization_id` | |

### Stock Ledger Entry → Inventory Transactions

| ERPNext Field | Dotmac ERP Field | Notes |
|---------------|-------------------|-------|
| `item_code` | `item_id` | Lookup |
| `warehouse` | `warehouse_id` | Lookup |
| `posting_date` | `transaction_date` | |
| `actual_qty` | `quantity` | +ve=receipt, -ve=issue |
| `valuation_rate` | `unit_cost` | |
| `stock_value_difference` | `total_cost` | |
| `voucher_type` | `source_document_type` | |
| `voucher_no` | `source_document_id` | Lookup |
| `batch_no` | `lot_id` | Lookup |

### Customers

| ERPNext Field | Dotmac ERP Field | Notes |
|---------------|-------------------|-------|
| `name` | - | Internal ID |
| `customer_name` | `legal_name` | |
| `customer_type` | `customer_type` | Company/Individual |
| `customer_group` | - | Optional categorization |
| `territory` | - | |
| `default_currency` | `currency_code` | |
| `default_price_list` | - | |
| `tax_id` | `tax_id` | |
| `disabled` | `is_active` | Inverted |

### Suppliers

| ERPNext Field | Dotmac ERP Field | Notes |
|---------------|-------------------|-------|
| `name` | - | Internal ID |
| `supplier_name` | `legal_name` | |
| `supplier_type` | `supplier_type` | Company/Individual |
| `supplier_group` | - | |
| `country` | - | In address |
| `default_currency` | `currency_code` | |
| `tax_id` | `tax_id` | |
| `disabled` | `is_active` | Inverted |

### Sales Invoices

| ERPNext Field | Dotmac ERP Field | Notes |
|---------------|-------------------|-------|
| `name` | `invoice_number` | |
| `customer` | `customer_id` | Lookup |
| `posting_date` | `invoice_date` | |
| `due_date` | `due_date` | |
| `currency` | `currency_code` | |
| `grand_total` | `total_amount` | |
| `outstanding_amount` | - | Calculate |
| `status` | `status` | Map values |
| `items` | Lines | Iterate |

### Purchase Invoices

| ERPNext Field | Dotmac ERP Field | Notes |
|---------------|-------------------|-------|
| `name` | `invoice_number` | |
| `supplier` | `supplier_id` | Lookup |
| `posting_date` | `invoice_date` | |
| `due_date` | `due_date` | |
| `currency` | `currency_code` | |
| `grand_total` | `total_amount` | |
| `outstanding_amount` | - | Calculate |
| `status` | `status` | Map values |
| `items` | Lines | Iterate |

## Migration Order (Dependencies)

```
Phase 1: Foundation (no dependencies)
├── Chart of Accounts
├── Item Groups → Item Categories
├── Asset Categories
└── Warehouses

Phase 2: Master Data (requires Phase 1)
├── Customers (requires AR account)
├── Suppliers (requires AP account)
├── Items (requires categories, accounts)
└── Assets (requires categories, accounts)

Phase 3: Transactions (requires Phase 2)
├── Stock Ledger → Inventory Transactions
├── Sales Invoices (requires customers, items)
├── Purchase Invoices (requires suppliers, items)
└── Journal Entries
```

## Sync Modes

### Full Sync
- Fetches all records from ERPNext
- Compares with existing sync_entity records
- Creates/updates Dotmac ERP entities
- Used for initial migration

### Incremental Sync
- Fetches records modified since last sync
- Uses ERPNext `modified` timestamp
- More efficient for ongoing maintenance

## Error Handling

1. **Connection Errors**: Retry with exponential backoff (3 attempts)
2. **Validation Errors**: Log to sync_entity.error_message, continue with next
3. **Duplicate Detection**: Match by source_name, update if modified
4. **Missing Dependencies**: Queue for retry after dependencies sync
5. **Transaction Rollback**: Batch commits with rollback on failure

## Configuration

```python
# Environment variables
ERPNEXT_URL=https://erp.example.com
ERPNEXT_API_KEY=your_api_key
ERPNEXT_API_SECRET=your_api_secret
ERPNEXT_COMPANY=Your Company Name  # For filtering

# Per-organization settings (stored in DB)
{
    "erpnext_url": "https://erp.example.com",
    "erpnext_company": "Company Name",
    "default_warehouse": "Stores - COMP",
    "ar_account": "1100 - Accounts Receivable",
    "ap_account": "2100 - Accounts Payable",
    "sync_batch_size": 100,
    "sync_interval_hours": 24
}
```

## File Structure

```
app/
├── models/
│   └── sync/
│       ├── __init__.py
│       ├── sync_entity.py      # Sync state tracking
│       └── sync_history.py     # Sync job history
├── services/
│   └── erpnext/
│       ├── __init__.py
│       ├── client.py           # ERPNext API client
│       ├── mappings/
│       │   ├── __init__.py
│       │   ├── accounts.py     # Account field mapping
│       │   ├── items.py        # Item field mapping
│       │   ├── assets.py       # Asset field mapping
│       │   └── contacts.py     # Customer/Supplier mapping
│       └── sync/
│           ├── __init__.py
│           ├── base.py         # Base sync service
│           ├── accounts.py     # Account sync
│           ├── items.py        # Item sync
│           ├── assets.py       # Asset sync
│           ├── contacts.py     # Customer/Supplier sync
│           ├── transactions.py # Invoice/Stock sync
│           └── orchestrator.py # Migration orchestration
└── web/
    └── erpnext/
        ├── __init__.py
        └── sync.py             # Web routes for sync UI
```

## API Endpoints

```
POST /api/erpnext/test-connection     # Test ERPNext connection
POST /api/erpnext/sync/preview        # Preview what will be synced
POST /api/erpnext/sync/start          # Start migration job
GET  /api/erpnext/sync/status/{job_id} # Get sync job status
POST /api/erpnext/sync/retry/{entity_id} # Retry failed entity
GET  /api/erpnext/sync/history        # List sync history
```

## Security Considerations

1. **API Credentials**: Stored encrypted in organization settings
2. **Read-Only Access**: ERPNext user should have read-only permissions
3. **Rate Limiting**: Respect ERPNext API rate limits (configurable delay)
4. **Audit Trail**: All sync operations logged with user context
5. **Data Validation**: Validate all incoming data before insert
