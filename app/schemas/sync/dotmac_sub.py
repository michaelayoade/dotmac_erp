"""Sub-native ERP sync contracts."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SubPurchaseInvoiceStatusResponse(BaseModel):
    """ERP-owned AP settlement observation for a Sub purchase invoice."""

    model_config = ConfigDict(extra="forbid")

    source_invoice_id: UUID
    purchase_invoice_id: UUID
    invoice_number: str
    status: str
    currency: str
    total_amount: Decimal
    amount_paid: Decimal
    balance_due: Decimal
    source_updated_at: datetime | None
