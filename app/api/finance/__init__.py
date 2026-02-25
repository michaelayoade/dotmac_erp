"""
IFRS API Routers.

FastAPI routers for IFRS accounting system.
"""

from app.api.finance.analysis import router as analysis_router
from app.api.finance.ap import router as ap_router
from app.api.finance.ar import router as ar_router
from app.api.finance.banking import router as banking_router
from app.api.finance.cons import router as cons_router
from app.api.finance.fx import router as fx_router
from app.api.finance.gl import router as gl_router
from app.api.finance.import_export import router as import_export_router
from app.api.finance.ipsas import router as ipsas_router
from app.api.finance.lease import router as lease_router
from app.api.finance.opening_balance import router as opening_balance_router
from app.api.finance.payments import router as payments_router
from app.api.finance.payments import webhook_router as payments_webhook_router
from app.api.finance.rpt import router as rpt_router
from app.api.finance.search import router as search_router
from app.api.finance.tax import router as tax_router

__all__ = [
    "gl_router",
    "ap_router",
    "ar_router",
    "lease_router",
    "tax_router",
    "cons_router",
    "rpt_router",
    "banking_router",
    "import_export_router",
    "opening_balance_router",
    "search_router",
    "payments_router",
    "payments_webhook_router",
    "ipsas_router",
    "fx_router",
    "analysis_router",
]
