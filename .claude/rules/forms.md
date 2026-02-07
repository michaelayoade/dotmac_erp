# Form Design Standards

## Context Query Parameters
Forms that create entities linked to a parent MUST accept the parent ID as a query parameter:

| Parameter | Used By |
|-----------|---------|
| `customer_id` | Receipts, invoices, quotes, sales orders |
| `supplier_id` | AP payments, purchase orders |
| `invoice_id` | Receipts (AR), payments (AP) |
| `account_id` | Journal entries, transfers |
| `project_id` | Expenses, time entries, tasks |
| `po_id` | Goods received notes |
| `so_id` | Delivery notes, invoices |
| `quote_id` | Sales orders |
| `employee_id` | Leave requests, payslips, expense claims |

## Form Context Method Pattern
Every form MUST have a dedicated `*_form_context()` method on the web service that returns all dropdown data, defaults, and pre-selections:

```python
@staticmethod
def receipt_form_context(
    db: Session,
    organization_id: str,
    *,
    invoice_id: Optional[str] = None,
    customer_id: Optional[str] = None,
    receipt_id: Optional[str] = None,
) -> dict:
    context = {"customers": [...], "accounts": [...], "payment_methods": [...]}
    if invoice_id:
        invoice = service.get_by_id(UUID(invoice_id))
        context["selected_invoice"] = invoice
        context["selected_customer_id"] = str(invoice.customer_id)
        context["locked_customer"] = True
    return context
```

## Data Precedence Rules
1. **Edit mode** (existing record) — overrides everything
2. **Related entity from query param** — e.g., `?invoice_id=` sets customer and amount
3. **Organization defaults** — today's date, org currency, default accounts
4. **Empty/blank** — field left for user input

## Locked Field UI
When a field is auto-selected from a query param, display it read-only:
```html
<div x-show="lockedCustomer" class="form-input bg-slate-50 dark:bg-slate-700 cursor-not-allowed">
    <span x-text="selectedCustomerName"></span>
    <span class="text-xs text-slate-400 ml-2">(from invoice)</span>
</div>
<select x-show="!lockedCustomer" name="customer_id" class="form-select">...</select>
```

## Context Banner
When prefilled from a parent entity, show an info banner at the top of the form.

## Navigation Continuity
"New X" links from detail pages MUST include the parent entity ID:
```html
<a href="/finance/ar/receipts/new?invoice_id={{ invoice.invoice_id }}">Record Payment</a>
```

## Post-Submit Redirect
If form was opened from a parent entity, redirect back to it. Otherwise redirect to the list.

## Error Re-Population
On validation failure, re-render the form with `context["error"]` and `context["form_data"]`.

## Form Template Section Order
1. Context banner (if prefilled)
2. Error summary
3. Header details (dates, reference numbers)
4. Primary entity (customer/supplier selector)
5. Amounts and allocations
6. Notes and attachments
7. Form actions — Cancel (left), Save (right)

## Build Input Methods
Parse form data in a dedicated `build_*_input()` static method — never in the route.

## Cross-Entity Validation
Validate ownership on submit (invoice belongs to customer, entity belongs to org).
