# DotMac CRM Integration Guide

This document describes how to integrate DotMac CRM with the ERP system for syncing projects, tickets, work orders, expenses, and inventory.

## Overview

The integration enables:
- **CRM → ERP**: Sync projects, tickets, and work orders
- **ERP → CRM**: Fetch expense totals and inventory stock levels (pull)
- **ERP → CRM**: Push inventory updates to CRM (push)

```
┌─────────────┐                        ┌─────────────┐
│   DotMac    │  ←── Inventory Pull ── │    ERP      │
│    CRM      │  ←── Inventory Push ── │   System    │
│             │  ←── Expense Totals ── │             │
│             │  ─── Entities ───────> │             │
└─────────────┘                        └─────────────┘
```

## 1. Authentication Setup

### Generate API Key (ERP Admin UI)

1. Navigate to **Admin → Sync → DotMac CRM** (`/admin/sync/crm`)
2. Go to **Configuration** tab (`/admin/sync/crm/config`)
3. Click **Generate API Key**
4. **Copy the key immediately** - it's only shown once!

### Using the API Key

Include the API key in all requests as a header:

```
X-API-Key: <your-api-key>
```

Example:
```bash
curl -H "X-API-Key: abc123xyz..." https://erp.example.com/api/v1/sync/crm/inventory
```

## 2. API Base URL

```
https://<erp-domain>/api/v1/sync/crm
```

## 3. Endpoints Reference

### Quick Reference

| Action | Method | Endpoint |
|--------|--------|----------|
| Sync entities | POST | `/bulk` |
| Get expense totals | POST | `/expense-totals` |
| List inventory | GET | `/inventory` |
| Get item detail | GET | `/inventory/{item_id}` |
| List categories | GET | `/inventory/meta/categories` |
| List warehouses | GET | `/inventory/meta/warehouses` |

---

## 4. Sync Entities (CRM → ERP)

### POST `/bulk` - Bulk Sync Projects, Tickets, Work Orders

Sync CRM entities to ERP in a single request. Idempotent - safe to retry.

**Request:**
```json
{
  "projects": [
    {
      "crm_id": "550e8400-e29b-41d4-a716-446655440000",
      "name": "Fiber Installation - Lagos Zone A",
      "code": "FIB-2024-001",
      "project_type": "fiber",
      "status": "active",
      "region": "Lagos",
      "description": "Fiber rollout for Zone A",
      "start_at": "2024-01-15T00:00:00Z",
      "due_at": "2024-06-30T00:00:00Z",
      "customer_name": "ABC Corp",
      "customer_crm_id": "customer-uuid-123",
      "metadata": {"custom_field": "value"}
    }
  ],
  "tickets": [
    {
      "crm_id": "ticket-uuid-123",
      "subject": "Internet connectivity issue",
      "ticket_number": "TKT-2024-0042",
      "ticket_type": "support",
      "status": "active",
      "priority": "high",
      "customer_name": "XYZ Ltd",
      "customer_crm_id": "customer-uuid-456"
    }
  ],
  "work_orders": [
    {
      "crm_id": "wo-uuid-789",
      "title": "Router Installation at Site B",
      "work_type": "installation",
      "status": "scheduled",
      "priority": "medium",
      "project_crm_id": "550e8400-e29b-41d4-a716-446655440000",
      "ticket_crm_id": "ticket-uuid-123",
      "assigned_employee_email": "technician@company.com",
      "scheduled_start": "2024-02-01T09:00:00Z",
      "scheduled_end": "2024-02-01T17:00:00Z"
    }
  ]
}
```

**Response:**
```json
{
  "projects_synced": 1,
  "tickets_synced": 1,
  "work_orders_synced": 1,
  "errors": []
}
```

### Status Values

| Entity | Valid Statuses |
|--------|----------------|
| Project | `planned`, `active`, `on_hold`, `completed`, `cancelled` |
| Ticket | `open`, `active`, `in_progress`, `resolved`, `closed` |
| Work Order | `draft`, `scheduled`, `active`, `in_progress`, `completed`, `cancelled` |

### Project Types

| CRM Type | ERP Mapping |
|----------|-------------|
| `internal` | Internal Project |
| `client` | Client Project |
| `fiber` | Fiber Optics Installation |
| `airfiber` | Air Fiber Installation |

---

## 5. Expense Totals (ERP → CRM)

### POST `/expense-totals` - Get expense totals for CRM entities

Retrieve expense claim totals grouped by status for display on CRM entity pages.

**Request:**
```json
{
  "project_crm_ids": ["550e8400-e29b-41d4-a716-446655440000"],
  "ticket_crm_ids": ["ticket-uuid-123"],
  "work_order_crm_ids": ["wo-uuid-789"]
}
```

**Response:**
```json
{
  "totals": {
    "550e8400-e29b-41d4-a716-446655440000": {
      "draft": "5000.00",
      "submitted": "12500.00",
      "approved": "8000.00",
      "paid": "45000.00",
      "currency": "NGN"
    },
    "ticket-uuid-123": {
      "draft": "0.00",
      "submitted": "2500.00",
      "approved": "0.00",
      "paid": "0.00",
      "currency": "NGN"
    }
  }
}
```

---

## 6. Inventory Sync (ERP → CRM)

### GET `/inventory` - List inventory items with stock levels

Retrieve available inventory for installation assignments.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `search` | string | - | Search by item code, name, or barcode |
| `category_code` | string | - | Filter by category code |
| `warehouse_id` | UUID | - | Filter by specific warehouse |
| `include_zero_stock` | boolean | `false` | Include items with zero available stock |
| `only_below_reorder` | boolean | `false` | Only show items below reorder point |
| `limit` | integer | `100` | Max items per page (max: 500) |
| `offset` | integer | `0` | Pagination offset |

**Example Request:**
```bash
curl -H "X-API-Key: <key>" \
  "https://erp.example.com/api/v1/sync/crm/inventory?search=router&limit=50"
```

**Response:**
```json
{
  "items": [
    {
      "item_id": "item-uuid-001",
      "item_code": "RTR-WIFI-001",
      "item_name": "Wireless Router AC1200",
      "description": "Dual-band wireless router",
      "category_code": "NETWORK",
      "category_name": "Network Equipment",
      "base_uom": "UNIT",
      "quantity_on_hand": "150.000000",
      "quantity_reserved": "25.000000",
      "quantity_available": "125.000000",
      "reorder_point": "50.000000",
      "list_price": "45000.00",
      "currency_code": "NGN",
      "barcode": "1234567890123",
      "is_below_reorder": false
    }
  ],
  "total_count": 1,
  "has_more": false
}
```

### GET `/inventory/{item_id}` - Get detailed item with warehouse breakdown

**Response:**
```json
{
  "item_id": "item-uuid-001",
  "item_code": "RTR-WIFI-001",
  "item_name": "Wireless Router AC1200",
  "description": "Dual-band wireless router",
  "category_code": "NETWORK",
  "category_name": "Network Equipment",
  "base_uom": "UNIT",
  "total_on_hand": "150.000000",
  "total_reserved": "25.000000",
  "total_available": "125.000000",
  "reorder_point": "50.000000",
  "list_price": "45000.00",
  "currency_code": "NGN",
  "barcode": "1234567890123",
  "warehouses": [
    {
      "warehouse_id": "wh-uuid-001",
      "warehouse_code": "WH-MAIN",
      "warehouse_name": "Main Warehouse",
      "quantity_on_hand": "100.000000",
      "quantity_reserved": "20.000000",
      "quantity_available": "80.000000"
    },
    {
      "warehouse_id": "wh-uuid-002",
      "warehouse_code": "WH-FIELD",
      "warehouse_name": "Field Stock",
      "quantity_on_hand": "50.000000",
      "quantity_reserved": "5.000000",
      "quantity_available": "45.000000"
    }
  ]
}
```

### GET `/inventory/meta/categories` - List item categories

**Response:**
```json
[
  {"code": "NETWORK", "name": "Network Equipment"},
  {"code": "CABLES", "name": "Cables and Wiring"},
  {"code": "TOOLS", "name": "Installation Tools"}
]
```

### GET `/inventory/meta/warehouses` - List warehouses

**Response:**
```json
[
  {"warehouse_id": "wh-uuid-001", "code": "WH-MAIN", "name": "Main Warehouse"},
  {"warehouse_id": "wh-uuid-002", "code": "WH-FIELD", "name": "Field Stock"}
]
```

---

## 7. Inventory Push (ERP → CRM)

In addition to CRM pulling inventory via GET endpoints, ERP can actively push inventory updates to CRM.

### Configuration

Set these environment variables on the ERP server:

```env
# CRM API token for authentication
CRM_API_TOKEN=your-crm-bearer-token

# CRM webhook endpoint to receive inventory data
CRM_INVENTORY_WEBHOOK_URL=https://crm.dotmac.io/api/v1/inventory/sync

# Push threshold - push when stock changes by this percentage (optional)
CRM_INVENTORY_PUSH_THRESHOLD_PERCENT=10
```

### Admin UI

Access the inventory push UI at:
```
https://<erp-domain>/admin/sync/crm/inventory
```

Features:
- **Test Connection** - Verify CRM webhook is accessible
- **Full Inventory Push** - Push all items with stock levels
- **Low Stock Alerts** - Push only items below reorder point

### Webhook Payload Format

ERP sends POST requests to `CRM_INVENTORY_WEBHOOK_URL` with:

**Headers:**
```
Authorization: Bearer <CRM_API_TOKEN>
Content-Type: application/json
```

**Full Sync Payload:**
```json
{
  "sync_type": "full",
  "organization_id": "org-uuid",
  "timestamp": "2024-02-01T10:30:00Z",
  "items": [
    {
      "item_id": "item-uuid",
      "item_code": "RTR-001",
      "item_name": "Wireless Router",
      "description": "Dual-band router",
      "category_code": "NETWORK",
      "category_name": "Network Equipment",
      "base_uom": "UNIT",
      "quantity_on_hand": "100.000000",
      "quantity_reserved": "15.000000",
      "quantity_available": "85.000000",
      "reorder_point": "20.000000",
      "list_price": "45000.00",
      "currency_code": "NGN",
      "barcode": "1234567890",
      "is_below_reorder": false
    }
  ]
}
```

**Low Stock Alert Payload:**
```json
{
  "sync_type": "low_stock_alert",
  "organization_id": "org-uuid",
  "timestamp": "2024-02-01T10:30:00Z",
  "items": [
    {
      "item_id": "item-uuid",
      "item_code": "CBL-001",
      "item_name": "Ethernet Cable",
      "quantity_on_hand": "10.000000",
      "quantity_available": "8.000000",
      "reorder_point": "50.000000",
      "reorder_quantity": "100.000000",
      "suggested_order_qty": "90.000000",
      "is_below_reorder": true
    }
  ]
}
```

### Celery Tasks

Push can be triggered programmatically:

```python
from app.tasks.crm import (
    push_inventory_to_crm,
    push_low_stock_alerts_to_crm,
    push_specific_items_to_crm,
)

# Full inventory push
push_inventory_to_crm.delay(organization_id="org-uuid", include_zero_stock=False)

# Low stock alerts only
push_low_stock_alerts_to_crm.delay(organization_id="org-uuid")

# Push specific items (after stock changes)
push_specific_items_to_crm.delay(
    organization_id="org-uuid",
    item_ids=["item-uuid-1", "item-uuid-2"]
)
```

### CRM Webhook Implementation

CRM should implement a POST endpoint that:

1. Validates the Bearer token
2. Processes the `sync_type` field:
   - `full` - Replace/update all inventory cache
   - `incremental` - Update specific items only
   - `low_stock_alert` - Trigger low stock notifications
   - `health_check` - Return 200 OK (for connectivity tests)
3. Returns appropriate status:
   - `200/201` - Success
   - `401` - Invalid token
   - `429` - Rate limited (include `Retry-After` header)

**Example CRM handler (Python/FastAPI):**
```python
@router.post("/inventory/sync")
def receive_inventory(
    payload: dict,
    authorization: str = Header(...),
):
    # Validate token
    if authorization != f"Bearer {settings.ERP_PUSH_TOKEN}":
        raise HTTPException(401, "Invalid token")

    sync_type = payload.get("sync_type")
    items = payload.get("items", [])

    if sync_type == "health_check":
        return {"status": "ok"}

    if sync_type == "full":
        inventory_cache.replace_all(items)
    elif sync_type == "incremental":
        inventory_cache.update(items)
    elif sync_type == "low_stock_alert":
        send_low_stock_notifications(items)

    return {"status": "ok", "items_received": len(items)}
```

---

## 8. Error Handling

### HTTP Status Codes

| Code | Meaning |
|------|---------|
| `200` | Success |
| `401` | Invalid or missing API key |
| `403` | API key not associated with organization |
| `404` | Resource not found |
| `422` | Validation error (check request body) |
| `500` | Server error |

### Error Response Format

```json
{
  "detail": "Error message here"
}
```

### Bulk Sync Partial Errors

Bulk sync continues on individual entity errors:
```json
{
  "projects_synced": 2,
  "tickets_synced": 0,
  "work_orders_synced": 1,
  "errors": [
    {
      "entity_type": "ticket",
      "crm_id": "bad-ticket-uuid",
      "error": "Invalid status value: unknown_status"
    }
  ]
}
```

---

## 9. Recommended Sync Strategy

### Entity Sync (CRM → ERP)

**When to sync:**
- On entity create/update in CRM (real-time)
- Batch sync every 15-30 minutes for bulk changes
- Full re-sync daily (overnight) for data integrity

**Order of operations:**
1. Projects first (work orders may reference them)
2. Tickets
3. Work orders last (references projects/tickets)

### Inventory Fetch (ERP → CRM)

**When to fetch:**
- When user opens work order / installation form
- Cache for 5-10 minutes to reduce API calls
- Refresh on page reload

---

## 9. Sample Integration Code

### Python

```python
import requests
from typing import Optional

class ERPClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip('/')
        self.headers = {"X-API-Key": api_key}

    def sync_entities(self, projects=None, tickets=None, work_orders=None):
        """Sync CRM entities to ERP."""
        response = requests.post(
            f"{self.base_url}/api/v1/sync/crm/bulk",
            headers=self.headers,
            json={
                "projects": projects or [],
                "tickets": tickets or [],
                "work_orders": work_orders or [],
            }
        )
        response.raise_for_status()
        return response.json()

    def get_inventory(
        self,
        search: Optional[str] = None,
        category_code: Optional[str] = None,
        limit: int = 100,
    ):
        """Get available inventory items."""
        params = {"limit": limit}
        if search:
            params["search"] = search
        if category_code:
            params["category_code"] = category_code

        response = requests.get(
            f"{self.base_url}/api/v1/sync/crm/inventory",
            headers=self.headers,
            params=params,
        )
        response.raise_for_status()
        return response.json()

    def get_expense_totals(self, project_ids=None, ticket_ids=None, work_order_ids=None):
        """Get expense totals for CRM entities."""
        response = requests.post(
            f"{self.base_url}/api/v1/sync/crm/expense-totals",
            headers=self.headers,
            json={
                "project_crm_ids": project_ids or [],
                "ticket_crm_ids": ticket_ids or [],
                "work_order_crm_ids": work_order_ids or [],
            }
        )
        response.raise_for_status()
        return response.json()


# Usage
client = ERPClient("https://erp.example.com", "your-api-key-here")

# Sync a project
result = client.sync_entities(projects=[{
    "crm_id": "my-project-uuid",
    "name": "New Installation Project",
    "status": "active",
}])

# Get routers in stock
inventory = client.get_inventory(search="router", category_code="NETWORK")
for item in inventory["items"]:
    print(f"{item['item_code']}: {item['quantity_available']} available")
```

### JavaScript/TypeScript

```typescript
class ERPClient {
  private baseUrl: string;
  private headers: Record<string, string>;

  constructor(baseUrl: string, apiKey: string) {
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.headers = {
      'X-API-Key': apiKey,
      'Content-Type': 'application/json',
    };
  }

  async syncEntities(data: {
    projects?: any[];
    tickets?: any[];
    work_orders?: any[];
  }) {
    const response = await fetch(`${this.baseUrl}/api/v1/sync/crm/bulk`, {
      method: 'POST',
      headers: this.headers,
      body: JSON.stringify({
        projects: data.projects || [],
        tickets: data.tickets || [],
        work_orders: data.work_orders || [],
      }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  async getInventory(params: {
    search?: string;
    category_code?: string;
    limit?: number;
  } = {}) {
    const searchParams = new URLSearchParams();
    if (params.search) searchParams.set('search', params.search);
    if (params.category_code) searchParams.set('category_code', params.category_code);
    if (params.limit) searchParams.set('limit', String(params.limit));

    const response = await fetch(
      `${this.baseUrl}/api/v1/sync/crm/inventory?${searchParams}`,
      { headers: this.headers }
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  async getExpenseTotals(data: {
    project_crm_ids?: string[];
    ticket_crm_ids?: string[];
    work_order_crm_ids?: string[];
  }) {
    const response = await fetch(`${this.baseUrl}/api/v1/sync/crm/expense-totals`, {
      method: 'POST',
      headers: this.headers,
      body: JSON.stringify({
        project_crm_ids: data.project_crm_ids || [],
        ticket_crm_ids: data.ticket_crm_ids || [],
        work_order_crm_ids: data.work_order_crm_ids || [],
      }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }
}

// Usage
const client = new ERPClient('https://erp.example.com', 'your-api-key-here');

// Get inventory for installation form
const inventory = await client.getInventory({ search: 'router' });
inventory.items.forEach(item => {
  console.log(`${item.item_code}: ${item.quantity_available} available`);
});
```

---

## 10. Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| `401 Unauthorized` | Check API key is correct and not expired |
| `403 Forbidden` | API key user has no organization access |
| Work order sync fails | Ensure project is synced first |
| Employee not found | Verify email matches ERP employee record |
| Zero inventory shown | Set `include_zero_stock=true` or check warehouse filter |

### Debug Checklist

1. Verify API key is active in ERP Admin UI
2. Check request headers include `X-API-Key`
3. Validate JSON payload format
4. Check CRM IDs are valid UUIDs
5. Review ERP logs for detailed errors

### Support

For integration issues, check:
- ERP Admin → Sync → DotMac CRM → Recent Activity
- Server logs for detailed error messages
