"""
CRM Integration Services.

Provides sync services for integrating with crm.dotmac.io:
- Ticket sync (CRM → ERP)
- Project sync (CRM → ERP)
- Webhook handlers for real-time updates
"""

from .client import CRMClient, CRMConfig, CRMError

__all__ = [
    "CRMClient",
    "CRMConfig",
    "CRMError",
]
