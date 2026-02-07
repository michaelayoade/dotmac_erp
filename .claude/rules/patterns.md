# Clean Code Patterns

## Model Field Names
ALWAYS read the model file before referencing fields. Each model has its own PK naming:
```python
# WRONG
claim.id          # ExpenseClaim uses claim_id
invoice.id        # SupplierInvoice uses invoice_id

# CORRECT
claim.claim_id
invoice.invoice_id
payment.payment_id
```

## Cross-Module Integration
Use dispatcher/handler pattern:
```python
class RemitaSourceHandler:
    def handle_rrr_paid(self, rrr: RemitaRRR) -> None:
        handler_map = {
            "ap_invoice": self._handle_ap_invoice_paid,
            "payroll_run": self._handle_payroll_run_paid,
        }
        handler = handler_map.get(rrr.source_type)
        if handler:
            handler(rrr)
```

## Generic Source Linking
```python
source_type: Mapped[Optional[str]] = mapped_column(String(50))
source_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
```

## Side Effect Error Handling
Side effects must never break the main operation:
```python
try:
    self._notify_source_paid(rrr)
except Exception as e:
    logger.exception(f"Failed to notify source: {e}")
    # Continue - main operation succeeded
```

## Circular Import Avoidance
Import inside the function for cross-module deps:
```python
def _handle_paid(self, rrr):
    from app.services.remita.source_handler import get_source_handler
    handler = get_source_handler(self.db)
    handler.handle_rrr_paid(rrr)
```

## Web Service Context Pattern
Build template context in web service methods:
```python
class ModuleWebService:
    def detail_context(self, org_id: UUID, entity_id: UUID) -> dict:
        entity = self._get_or_404(entity_id)
        return {"entity": entity, "can_edit": ..., "can_delete": ...}
```

## Web Service Dependency Exception
- `app/services/*/web.py` and `app/services/*/web/*.py` MAY import from `app.web.deps`
- Pure business services (`*_service.py`) must NEVER import from `app.web.*`

## Reusable Template Partials
For UI components used across pages, create Jinja2 macros in separate files:
```html
{% from "finance/remita/_generate_modal.html" import rrr_modal %}
{{ rrr_modal(invoice.amount, "ap_invoice", invoice.invoice_id) }}
```

## Payer/Org Defaults
Import Organization inside the method to get org defaults for forms.
