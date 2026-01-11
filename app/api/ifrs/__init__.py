"""
IFRS API Routers.

FastAPI routers for IFRS accounting system.
"""

from app.api.ifrs.gl import router as gl_router
from app.api.ifrs.ap import router as ap_router
from app.api.ifrs.ar import router as ar_router
from app.api.ifrs.fa import router as fa_router
from app.api.ifrs.inv import router as inv_router
from app.api.ifrs.lease import router as lease_router
from app.api.ifrs.fin_inst import router as fin_inst_router
from app.api.ifrs.tax import router as tax_router
from app.api.ifrs.cons import router as cons_router
from app.api.ifrs.rpt import router as rpt_router
from app.api.ifrs.banking import router as banking_router
from app.api.ifrs.import_export import router as import_export_router

__all__ = [
    "gl_router",
    "ap_router",
    "ar_router",
    "fa_router",
    "inv_router",
    "lease_router",
    "fin_inst_router",
    "tax_router",
    "cons_router",
    "rpt_router",
    "banking_router",
    "import_export_router",
]
