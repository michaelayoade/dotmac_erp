# Phase 3: Analytics & Inventory — Pivot Tables & Automated Valuation

**Timeline**: ~26 days | **Depends on**: Phase 1 (Aggregate Auto-Refresh, Fiscal Positions)

---

## Overview

Phase 3 adds user-facing analytics and tightens inventory-accounting integration:

1. **Pivot Table / Ad-Hoc Reporting Engine** (16 days) — Self-service BI with drill-down
2. **Automated Inventory Valuation + WAC** (10 days) — Atomic GL posting on stock moves + weighted average cost method

---

## 1. Pivot Table / Ad-Hoc Reporting Engine

### Problem

Finance teams live in pivot tables. Today DotMac offers pre-built reports (trial balance, financial statements, dashboards with stat cards) but users **cannot ask** "show me revenue by customer by month" or "compare AP aging by supplier category across quarters." Every new analysis requires a developer building a new page.

This is the **#1 user-facing feature gap** versus Odoo, which has built-in pivot and graph views on any model.

### Odoo Pattern Being Adapted

Odoo creates SQL VIEW models (`_auto = False`) with `init()` methods that define materialized aggregations. These are exposed as pivot views where users drag dimensions and measures interactively.

### Architecture

```
┌──────────────────────────────────────────────────┐
│  User Interface (Alpine.js pivot component)      │
│  - Drag dimensions (rows, columns)               │
│  - Select measures (sum, count, avg)             │
│  - Drill-down on cells → filtered list page      │
│  - Export to CSV/Excel                           │
├──────────────────────────────────────────────────┤
│  API Layer (/api/v1/analysis/{cube})             │
│  - Accepts: dimensions[], measures[], filters[]  │
│  - Returns: aggregated data as JSON              │
├──────────────────────────────────────────────────┤
│  Analysis Engine (Python)                        │
│  - AnalysisCubeService: query builder            │
│  - Validates dimensions/measures against cube def│
│  - Generates SQL with GROUP BY + aggregation     │
├──────────────────────────────────────────────────┤
│  Analysis Cubes (PostgreSQL materialized views)  │
│  - sales_analysis_mv                             │
│  - purchase_analysis_mv                          │
│  - gl_analysis_mv                                │
│  - inventory_analysis_mv                         │
└──────────────────────────────────────────────────┘
```

### Data Model

#### `AnalysisCube` (configuration, not a SQL view — stores cube metadata)

```python
# app/models/finance/rpt/analysis_cube.py
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AnalysisCube(Base):
    """Metadata for an analysis cube (materialized view + dimension/measure definitions)."""
    __tablename__ = "analysis_cube"

    cube_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    # e.g., "sales", "purchases", "gl", "inventory", "aging"
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_view: Mapped[str] = mapped_column(String(80), nullable=False)
    # Materialized view name: "sales_analysis_mv"

    # Available dimensions (fields users can group by)
    dimensions: Mapped[list[dict]] = mapped_column(JSONB, nullable=False)
    # [
    #   {"field": "customer_name", "label": "Customer", "type": "text"},
    #   {"field": "period_label", "label": "Period", "type": "text"},
    #   {"field": "account_code", "label": "Account", "type": "text"},
    #   {"field": "invoice_date", "label": "Date", "type": "date",
    #    "intervals": ["day", "week", "month", "quarter", "year"]},
    # ]

    # Available measures (fields users can aggregate)
    measures: Mapped[list[dict]] = mapped_column(JSONB, nullable=False)
    # [
    #   {"field": "amount_total", "label": "Total Amount", "type": "currency", "agg": "sum"},
    #   {"field": "line_count", "label": "Line Count", "type": "integer", "agg": "sum"},
    #   {"field": "record_count", "label": "Records", "type": "integer", "agg": "count"},
    # ]

    # Default configuration
    default_rows: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    default_columns: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    default_measures: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)

    # Drill-down configuration
    drill_down_url_template: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # e.g., "/finance/ar/invoices?customer_id={customer_id}&period={period}"

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    refresh_interval_minutes: Mapped[int] = mapped_column(default=60)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
```

#### `SavedAnalysis` (user-saved pivot configurations)

```python
class SavedAnalysis(Base):
    """User-saved pivot table configurations."""
    __tablename__ = "saved_analysis"

    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    cube_code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )

    # Saved configuration
    row_dimensions: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    column_dimensions: Mapped[list[str]] = mapped_column(JSONB, default=list)
    measures: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    filters: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)
    # [{"field": "status", "operator": "=", "value": "POSTED"}]

    is_shared: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
```

### PostgreSQL Materialized Views

#### Sales Analysis View

```sql
-- Migration: create_sales_analysis_mv.py
CREATE MATERIALIZED VIEW IF NOT EXISTS sales_analysis_mv AS
SELECT
    i.organization_id,
    i.invoice_id,
    i.invoice_number,
    i.invoice_date,
    DATE_TRUNC('month', i.invoice_date)::date AS period_date,
    TO_CHAR(i.invoice_date, 'Mon YYYY') AS period_label,
    EXTRACT(YEAR FROM i.invoice_date)::int AS year,
    EXTRACT(QUARTER FROM i.invoice_date)::int AS quarter,
    i.customer_id,
    c.legal_name AS customer_name,
    c.customer_type,
    i.status,
    i.currency_code,
    COALESCE(i.amount_subtotal, 0) AS amount_subtotal,
    COALESCE(i.amount_tax, 0) AS amount_tax,
    COALESCE(i.amount_total, 0) AS amount_total,
    COALESCE(i.amount_paid, 0) AS amount_paid,
    COALESCE(i.amount_total, 0) - COALESCE(i.amount_paid, 0) AS amount_outstanding,
    1 AS record_count
FROM ar_invoice i
LEFT JOIN ar_customer c ON i.customer_id = c.customer_id
WHERE i.status NOT IN ('DRAFT', 'VOIDED')
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS sales_analysis_mv_pk ON sales_analysis_mv (invoice_id);
CREATE INDEX IF NOT EXISTS sales_analysis_mv_org ON sales_analysis_mv (organization_id);
CREATE INDEX IF NOT EXISTS sales_analysis_mv_date ON sales_analysis_mv (organization_id, period_date);
```

#### GL Analysis View

```sql
CREATE MATERIALIZED VIEW IF NOT EXISTS gl_analysis_mv AS
SELECT
    pll.organization_id,
    pll.posted_ledger_line_id AS line_id,
    pll.posting_date,
    DATE_TRUNC('month', pll.posting_date)::date AS period_date,
    TO_CHAR(pll.posting_date, 'Mon YYYY') AS period_label,
    a.account_code,
    a.account_name,
    a.account_type,
    a.normal_balance,
    pll.debit_amount,
    pll.credit_amount,
    pll.debit_amount - pll.credit_amount AS net_amount,
    je.journal_type,
    je.source_module,
    pll.business_unit_id,
    pll.cost_center_id,
    pll.project_id,
    1 AS record_count
FROM posted_ledger_line pll
JOIN account a ON pll.account_id = a.account_id
JOIN journal_entry je ON pll.journal_entry_id = je.journal_entry_id
WITH DATA;
```

#### Purchase Analysis View

```sql
CREATE MATERIALIZED VIEW IF NOT EXISTS purchase_analysis_mv AS
SELECT
    si.organization_id,
    si.invoice_id,
    si.invoice_number,
    si.invoice_date,
    DATE_TRUNC('month', si.invoice_date)::date AS period_date,
    TO_CHAR(si.invoice_date, 'Mon YYYY') AS period_label,
    si.supplier_id,
    s.supplier_name,
    s.supplier_type,
    si.status,
    si.currency_code,
    COALESCE(si.amount_total, 0) AS amount_total,
    COALESCE(si.amount_paid, 0) AS amount_paid,
    COALESCE(si.amount_total, 0) - COALESCE(si.amount_paid, 0) AS amount_outstanding,
    1 AS record_count
FROM ap_supplier_invoice si
LEFT JOIN ap_supplier s ON si.supplier_id = s.supplier_id
WHERE si.status NOT IN ('DRAFT', 'VOIDED')
WITH DATA;
```

#### Inventory Analysis View

```sql
CREATE MATERIALIZED VIEW IF NOT EXISTS inventory_analysis_mv AS
SELECT
    it.organization_id,
    it.transaction_id,
    it.transaction_date,
    DATE_TRUNC('month', it.transaction_date)::date AS period_date,
    it.item_id,
    i.item_code,
    i.item_name,
    ic.category_name,
    it.warehouse_id,
    w.warehouse_name,
    it.transaction_type,
    it.quantity,
    it.unit_cost,
    it.quantity * it.unit_cost AS total_value,
    1 AS record_count
FROM inventory_transaction it
JOIN item i ON it.item_id = i.item_id
LEFT JOIN item_category ic ON i.category_id = ic.category_id
LEFT JOIN warehouse w ON it.warehouse_id = w.warehouse_id
WITH DATA;
```

### Service Layer

#### `AnalysisCubeService`

```python
# app/services/finance/rpt/analysis_cube_service.py

class AnalysisCubeService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def query(
        self,
        organization_id: UUID,
        cube_code: str,
        *,
        row_dimensions: list[str],
        column_dimensions: list[str] | None = None,
        measures: list[str],
        filters: list[dict] | None = None,
        order_by: str | None = None,
        limit: int = 1000,
    ) -> AnalysisResult:
        """Execute a pivot query against a materialized view.

        Returns: AnalysisResult with rows, column headers, and totals.
        """
        cube = self._get_cube(cube_code)
        self._validate_fields(cube, row_dimensions, column_dimensions, measures)

        # Build SQL dynamically
        view_name = cube.source_view
        select_cols: list[str] = []
        group_cols: list[str] = []

        # Row dimensions
        for dim in row_dimensions:
            dim_def = self._get_dimension_def(cube, dim)
            col = self._apply_interval(dim, dim_def)
            select_cols.append(f"{col} AS {dim}")
            group_cols.append(col)

        # Measures (aggregated)
        for measure in measures:
            measure_def = self._get_measure_def(cube, measure)
            agg = measure_def.get("agg", "sum")
            select_cols.append(f"{agg}({measure_def['field']}) AS {measure}")

        # Build query
        where_clauses = [f"organization_id = :org_id"]
        params: dict = {"org_id": organization_id}

        if filters:
            for i, f in enumerate(filters):
                clause, p = self._build_filter_clause(f, i)
                where_clauses.append(clause)
                params.update(p)

        sql = f"""
            SELECT {', '.join(select_cols)}
            FROM {view_name}
            WHERE {' AND '.join(where_clauses)}
            GROUP BY {', '.join(group_cols)}
            ORDER BY {order_by or group_cols[0]}
            LIMIT :limit
        """
        params["limit"] = limit

        rows = self.db.execute(text(sql), params).fetchall()

        # Calculate totals
        totals = self._calculate_totals(rows, measures)

        return AnalysisResult(
            cube_code=cube_code,
            row_dimensions=row_dimensions,
            column_dimensions=column_dimensions or [],
            measures=measures,
            rows=[dict(row._mapping) for row in rows],
            totals=totals,
            row_count=len(rows),
        )

    def refresh_view(self, cube_code: str) -> None:
        """Refresh a materialized view."""
        cube = self._get_cube(cube_code)
        self.db.execute(text(
            f"REFRESH MATERIALIZED VIEW CONCURRENTLY {cube.source_view}"
        ))
        cube.last_refreshed_at = datetime.utcnow()
        self.db.flush()


@dataclass
class AnalysisResult:
    cube_code: str
    row_dimensions: list[str]
    column_dimensions: list[str]
    measures: list[str]
    rows: list[dict]
    totals: dict[str, Decimal]
    row_count: int
```

### Celery Task: View Refresh

```python
# app/tasks/finance.py

@shared_task
def refresh_analysis_views() -> dict:
    """Refresh materialized views for analysis cubes. Runs hourly."""
    with SessionLocal() as db:
        from app.services.finance.rpt.analysis_cube_service import AnalysisCubeService
        service = AnalysisCubeService(db)

        cubes = db.scalars(
            select(AnalysisCube).where(AnalysisCube.is_active == True)
        ).all()

        results = {"refreshed": 0, "errors": []}
        for cube in cubes:
            try:
                service.refresh_view(cube.code)
                results["refreshed"] += 1
            except Exception as e:
                logger.exception("Failed to refresh %s", cube.code)
                results["errors"].append(str(e))

        db.commit()
    return results
```

### API Routes

```python
# app/api/analysis.py

@router.get("/analysis/{cube_code}")
def query_analysis(
    cube_code: str,
    rows: str = Query(..., description="Comma-separated row dimensions"),
    measures: str = Query(..., description="Comma-separated measures"),
    columns: str | None = Query(None),
    filters: str | None = Query(None, description="JSON-encoded filter array"),
    auth=Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict:
    service = AnalysisCubeService(db)
    result = service.query(
        organization_id=auth.organization_id,
        cube_code=cube_code,
        row_dimensions=rows.split(","),
        column_dimensions=columns.split(",") if columns else None,
        measures=measures.split(","),
        filters=json.loads(filters) if filters else None,
    )
    return result.__dict__
```

### Web Routes & UI

#### Analysis Page: `/finance/reports/analysis/{cube_code}`

```python
# app/web/finance/reports.py

@router.get("/analysis/{cube_code}")
def analysis_view(
    cube_code: str, request: Request, auth=Depends(require_auth),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    service = AnalysisCubeService(db)
    cube = service._get_cube(cube_code)

    context = base_context(request, auth, f"{cube.name} Analysis", "reports", db=db)
    context["cube"] = cube
    context["saved_analyses"] = service.list_saved(auth.organization_id, cube_code)
    return templates.TemplateResponse(request, "finance/reports/analysis.html", context)
```

#### Frontend Pivot Component

The pivot table UI built with Alpine.js:

```html
<!-- templates/finance/reports/analysis.html -->
<div x-data='pivotTable({{ cube | tojson }})'>
  <!-- Dimension/Measure selector -->
  <div class="card p-4 mb-4">
    <div class="grid grid-cols-3 gap-4">
      <div>
        <label class="form-label">Rows</label>
        <template x-for="dim in cube.dimensions" :key="dim.field">
          <label class="flex items-center gap-2 py-1">
            <input type="checkbox" :value="dim.field" x-model="config.rows">
            <span x-text="dim.label" class="text-sm"></span>
          </label>
        </template>
      </div>
      <div>
        <label class="form-label">Columns</label>
        <!-- Same pattern for column dimensions -->
      </div>
      <div>
        <label class="form-label">Measures</label>
        <template x-for="m in cube.measures" :key="m.field">
          <label class="flex items-center gap-2 py-1">
            <input type="checkbox" :value="m.field" x-model="config.measures">
            <span x-text="m.label" class="text-sm"></span>
          </label>
        </template>
      </div>
    </div>
    <div class="flex gap-3 mt-4">
      <button @click="runQuery()" class="btn btn-primary btn-sm">Run Analysis</button>
      <button @click="exportCsv()" class="btn btn-secondary btn-sm">Export CSV</button>
    </div>
  </div>

  <!-- Pivot table results -->
  <div class="table-container" x-show="results">
    <table class="table">
      <thead>
        <tr>
          <template x-for="dim in config.rows" :key="dim">
            <th scope="col" x-text="getDimensionLabel(dim)"></th>
          </template>
          <template x-for="m in config.measures" :key="m">
            <th scope="col" class="text-right" x-text="getMeasureLabel(m)"></th>
          </template>
        </tr>
      </thead>
      <tbody>
        <template x-for="row in results.rows" :key="JSON.stringify(row)">
          <tr class="group cursor-pointer" @click="drillDown(row)">
            <template x-for="dim in config.rows" :key="dim">
              <td x-text="row[dim]"></td>
            </template>
            <template x-for="m in config.measures" :key="m">
              <td class="text-right font-mono tabular-nums"
                  x-text="formatMeasure(row[m], m)"></td>
            </template>
          </tr>
        </template>
      </tbody>
      <tfoot>
        <tr class="font-semibold border-t-2 border-slate-300 dark:border-slate-600">
          <td :colspan="config.rows.length">Total</td>
          <template x-for="m in config.measures" :key="m">
            <td class="text-right font-mono tabular-nums"
                x-text="formatMeasure(results.totals[m], m)"></td>
          </template>
        </tr>
      </tfoot>
    </table>
  </div>
</div>
```

### Seed Data: Pre-installed Cubes

```python
CUBES = [
    {
        "code": "sales",
        "name": "Sales Analysis",
        "source_view": "sales_analysis_mv",
        "dimensions": [
            {"field": "customer_name", "label": "Customer", "type": "text"},
            {"field": "customer_type", "label": "Customer Type", "type": "text"},
            {"field": "period_label", "label": "Period", "type": "text"},
            {"field": "year", "label": "Year", "type": "integer"},
            {"field": "quarter", "label": "Quarter", "type": "integer"},
            {"field": "status", "label": "Status", "type": "text"},
            {"field": "currency_code", "label": "Currency", "type": "text"},
        ],
        "measures": [
            {"field": "amount_total", "label": "Total Amount", "type": "currency", "agg": "sum"},
            {"field": "amount_paid", "label": "Paid Amount", "type": "currency", "agg": "sum"},
            {"field": "amount_outstanding", "label": "Outstanding", "type": "currency", "agg": "sum"},
            {"field": "record_count", "label": "Invoice Count", "type": "integer", "agg": "sum"},
        ],
        "default_rows": ["customer_name"],
        "default_measures": ["amount_total", "record_count"],
        "drill_down_url_template": "/finance/ar/invoices?customer_id={customer_id}",
    },
    # ... gl, purchases, inventory cubes similarly
]
```

### Deliverables

| Item | File | Type |
|------|------|------|
| Model: AnalysisCube, SavedAnalysis | `app/models/finance/rpt/analysis_cube.py` | New |
| Service: AnalysisCubeService | `app/services/finance/rpt/analysis_cube_service.py` | New |
| API route | `app/api/analysis.py` | New |
| Web route | `app/web/finance/reports.py` | Edit |
| Template: analysis page | `templates/finance/reports/analysis.html` | New |
| JS: pivot component | `static/src/js/pivot-table.js` | New |
| Migration: materialized views | `alembic/versions/XXXX_create_analysis_views.py` | New |
| Migration: cube metadata | `alembic/versions/XXXX_seed_analysis_cubes.py` | New |
| Celery task | `app/tasks/finance.py` | Edit |
| Tests | `tests/ifrs/rpt/test_analysis_cube.py` | New |

---

## 2. Automated Inventory Valuation + WAC

### Problem

Two gaps in current inventory-accounting integration:

1. **Non-atomic posting**: Inventory transactions and GL journals are created in separate steps. If the GL posting fails, inventory and accounting diverge.
2. **FIFO only**: The `CostingMethod` enum includes `WEIGHTED_AVERAGE` but it's not implemented. WAC is the most common method for African businesses with fungible goods.

### Odoo Pattern Being Adapted

Odoo's `property_valuation = 'real_time'` mode automatically creates GL journal entries whenever stock moves are validated. The GL entry and stock move share the same database transaction — they can never be out of sync.

### Implementation: Atomic Valuation Posting

#### Valuation Mode Setting

Add to `settings_spec.py`:

```python
# INVENTORY domain settings
SettingSpec(
    domain=SettingDomain.inventory,
    key="valuation_mode",
    env_var=None,
    value_type=SettingValueType.string,
    default="manual",  # "manual" or "real_time"
    allowed={"manual", "real_time"},
    label="Inventory Valuation Mode",
    description="'manual' = GL posting triggered separately. 'real_time' = GL journal created atomically with inventory transaction.",
),
```

#### Enhanced Inventory Transaction Service

```python
# In app/services/inventory/transaction.py

def post_transaction(
    self, transaction: InventoryTransaction, posted_by_user_id: UUID,
) -> PostingResult | None:
    """Post an inventory transaction. In real_time mode, creates GL journal atomically."""
    valuation_mode = resolve_value(
        self.db, SettingDomain.inventory, "valuation_mode"
    ) or "manual"

    # Calculate valuation based on costing method
    item = self.db.get(Item, transaction.item_id)
    if item.costing_method == CostingMethod.FIFO:
        valuation = self._calculate_fifo_valuation(transaction)
    elif item.costing_method == CostingMethod.WEIGHTED_AVERAGE:
        valuation = self._calculate_wac_valuation(transaction)
    else:
        valuation = self._calculate_standard_valuation(transaction)

    # Store valuation on transaction
    transaction.unit_cost = valuation.unit_cost
    transaction.total_cost = valuation.total_cost
    transaction.status = "POSTED"

    if valuation_mode == "real_time":
        # Create GL journal in the SAME flush — atomic
        from app.services.inventory.posting.router import route_posting
        posting_result = route_posting(
            self.db,
            organization_id=transaction.organization_id,
            transaction=transaction,
            valuation=valuation,
            posted_by_user_id=posted_by_user_id,
        )
        if not posting_result.success:
            raise RuntimeError(f"GL posting failed: {posting_result.message}")

        self.db.flush()  # Both transaction + GL journal committed together
        return posting_result

    self.db.flush()  # Transaction only — GL posted separately (manual mode)
    return None
```

### Weighted Average Cost Implementation

#### WAC Calculation Service

```python
# app/services/inventory/wac_valuation.py

class WACValuationService:
    """Weighted Average Cost valuation for inventory transactions."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def calculate_receipt_cost(
        self, item_id: UUID, warehouse_id: UUID,
        receipt_qty: Decimal, receipt_unit_cost: Decimal,
    ) -> WACResult:
        """Calculate new WAC after a receipt.

        WAC formula:
            new_wac = (existing_qty * existing_wac + receipt_qty * receipt_cost)
                      / (existing_qty + receipt_qty)
        """
        current = self._get_current_balance(item_id, warehouse_id)

        if current.quantity + receipt_qty == 0:
            new_wac = Decimal("0")
        else:
            total_value = (current.quantity * current.wac) + (receipt_qty * receipt_unit_cost)
            new_qty = current.quantity + receipt_qty
            new_wac = (total_value / new_qty).quantize(Decimal("0.000001"))

        return WACResult(
            previous_wac=current.wac,
            new_wac=new_wac,
            unit_cost=receipt_unit_cost,  # Actual cost for this receipt
            total_cost=receipt_qty * receipt_unit_cost,
            new_balance_qty=current.quantity + receipt_qty,
            new_balance_value=(current.quantity + receipt_qty) * new_wac,
        )

    def calculate_issue_cost(
        self, item_id: UUID, warehouse_id: UUID, issue_qty: Decimal,
    ) -> WACResult:
        """Calculate cost for an issue using current WAC.

        Issues always use the current WAC — no layer tracking needed.
        """
        current = self._get_current_balance(item_id, warehouse_id)

        if current.quantity < issue_qty:
            raise ValueError(
                f"Insufficient stock: {current.quantity} available, {issue_qty} requested"
            )

        return WACResult(
            previous_wac=current.wac,
            new_wac=current.wac,  # WAC doesn't change on issue
            unit_cost=current.wac,
            total_cost=issue_qty * current.wac,
            new_balance_qty=current.quantity - issue_qty,
            new_balance_value=(current.quantity - issue_qty) * current.wac,
        )

    def _get_current_balance(
        self, item_id: UUID, warehouse_id: UUID,
    ) -> CurrentBalance:
        """Get current quantity and WAC for an item at a warehouse."""
        # Query from inventory_balance or calculate from transactions
        ...


@dataclass
class WACResult:
    previous_wac: Decimal
    new_wac: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    new_balance_qty: Decimal
    new_balance_value: Decimal


@dataclass
class CurrentBalance:
    quantity: Decimal
    wac: Decimal  # Current weighted average cost
```

#### WAC Balance Tracking

Add WAC tracking to the inventory balance model:

```python
# Add to InventoryBalance or create new WACLedger:
class ItemWACLedger(Base):
    """Tracks weighted average cost per item per warehouse."""
    __tablename__ = "item_wac_ledger"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    current_wac: Mapped[Decimal] = mapped_column(Numeric(20, 6), default=Decimal("0"))
    quantity_on_hand: Mapped[Decimal] = mapped_column(Numeric(20, 6), default=Decimal("0"))
    total_value: Mapped[Decimal] = mapped_column(Numeric(20, 6), default=Decimal("0"))

    last_transaction_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    last_updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("organization_id", "item_id", "warehouse_id", name="uq_item_wac"),
    )
```

### Valuation Reconciliation Report

New report: "Inventory Valuation vs GL" — compares:
- Sum of `item_wac_ledger.total_value` per item (inventory side)
- `AccountBalance` for inventory asset accounts (GL side)
- Highlights discrepancies

```python
# app/services/inventory/valuation_reconciliation.py

class ValuationReconciliationService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def reconcile(self, organization_id: UUID) -> list[ValuationDiscrepancy]:
        """Compare inventory valuation to GL balances."""
        # Inventory side: sum of WAC ledger values
        inv_stmt = (
            select(
                ItemWACLedger.item_id,
                func.sum(ItemWACLedger.total_value).label("inv_value"),
            )
            .where(ItemWACLedger.organization_id == organization_id)
            .group_by(ItemWACLedger.item_id)
        )

        # GL side: inventory asset account balances
        # (requires mapping item → GL account via posting adapter config)
        ...

        # Compare and return discrepancies
```

### Testing

```python
# tests/ifrs/inventory/test_wac_valuation.py

def test_wac_receipt_calculation(db, org_id):
    """WAC recalculates correctly after receipt."""
    # Starting: 100 units @ ₦10 = ₦1,000
    setup_balance(db, org_id, item_id, qty=100, wac=Decimal("10"))

    # Receipt: 50 units @ ₦16 = ₦800
    service = WACValuationService(db)
    result = service.calculate_receipt_cost(item_id, wh_id, Decimal("50"), Decimal("16"))

    # New WAC: (100*10 + 50*16) / 150 = 1800/150 = ₦12
    assert result.new_wac == Decimal("12.000000")
    assert result.new_balance_qty == Decimal("150")

def test_wac_issue_uses_current_wac(db, org_id):
    """Issues use current WAC, don't change it."""
    setup_balance(db, org_id, item_id, qty=150, wac=Decimal("12"))

    service = WACValuationService(db)
    result = service.calculate_issue_cost(item_id, wh_id, Decimal("30"))

    assert result.unit_cost == Decimal("12")  # Uses current WAC
    assert result.new_wac == Decimal("12")    # WAC unchanged
    assert result.total_cost == Decimal("360")  # 30 * 12

def test_wac_insufficient_stock_raises(db, org_id):
    """Issuing more than available raises ValueError."""
    setup_balance(db, org_id, item_id, qty=10, wac=Decimal("100"))

    service = WACValuationService(db)
    with pytest.raises(ValueError, match="Insufficient stock"):
        service.calculate_issue_cost(item_id, wh_id, Decimal("20"))

def test_atomic_posting_creates_gl_journal(db, org_id):
    """In real_time mode, GL journal is created with inventory transaction."""
    set_setting(db, "inventory", "valuation_mode", "real_time")

    service = InventoryTransactionService(db)
    result = service.post_transaction(receipt_txn, user_id)

    assert result is not None
    assert result.success is True
    assert result.journal_entry_id is not None

    # Verify GL journal exists
    je = db.get(JournalEntry, result.journal_entry_id)
    assert je is not None
    assert je.status == "POSTED"
```

### Deliverables

| Item | File | Type |
|------|------|------|
| Service: WACValuationService | `app/services/inventory/wac_valuation.py` | New |
| Model: ItemWACLedger | `app/models/inventory/item_wac_ledger.py` | New |
| Service: ValuationReconciliation | `app/services/inventory/valuation_reconciliation.py` | New |
| Edit: InventoryTransactionService | `app/services/inventory/transaction.py` | Edit |
| Setting: valuation_mode | `app/services/settings_spec.py` | Edit |
| Migration | `alembic/versions/XXXX_add_wac_ledger.py` | New |
| Tests | `tests/ifrs/inventory/test_wac_valuation.py` | New |

---

## Phase 3 Summary

| Feature | New Files | Edited Files | Days |
|---------|-----------|-------------|------|
| Pivot Table Engine | 6 | 3 | 16 |
| Inventory Valuation + WAC | 4 | 3 | 10 |
| **Total** | **10** | **6** | **26** |

### Dependencies

```
Phase 1 (Aggregate Auto-Refresh) → Pivot views refresh pattern
Phase 1 (Fiscal Positions) → Inventory account mapping in posting adapters
Pivot Tables ←→ Inventory Valuation (independent, can parallelize)
```

### Verification Checklist

- [ ] Materialized views create successfully on PostgreSQL
- [ ] `REFRESH MATERIALIZED VIEW CONCURRENTLY` works without locks
- [ ] Pivot queries enforce organization_id (multi-tenancy)
- [ ] WAC calculations match hand-computed examples
- [ ] Atomic posting: GL journal and inventory transaction in same flush
- [ ] Valuation reconciliation catches intentional discrepancies in test
- [ ] All existing inventory tests pass (backward compatibility)
