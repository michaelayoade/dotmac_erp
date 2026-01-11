"""
IFRS Web Routes.

HTML template routes for the IFRS Accounting modules.
"""

from fastapi import APIRouter

from app.web.ifrs.dashboard import router as dashboard_router
from app.web.ifrs.gl import router as gl_router
from app.web.ifrs.ap import router as ap_router
from app.web.ifrs.ar import router as ar_router
from app.web.ifrs.inv import router as inv_router
from app.web.ifrs.fa import router as fa_router
from app.web.ifrs.fin_inst import router as fin_inst_router
from app.web.ifrs.banking import router as banking_router
from app.web.ifrs.lease import router as lease_router
from app.web.ifrs.tax import router as tax_router
from app.web.ifrs.reports import router as reports_router
from app.web.ifrs.exp import router as exp_router
from app.web.ifrs.quote import router as quote_router
from app.web.ifrs.sales_order import router as sales_order_router
from app.web.ifrs.settings import router as settings_router
from app.web.ifrs.automation import router as automation_router

router = APIRouter(tags=["ifrs-web"])

router.include_router(dashboard_router)
router.include_router(gl_router)
router.include_router(ap_router)
router.include_router(ar_router)
router.include_router(inv_router)
router.include_router(fa_router)
router.include_router(fin_inst_router)
router.include_router(banking_router)
router.include_router(lease_router)
router.include_router(tax_router)
router.include_router(reports_router)
router.include_router(exp_router)
router.include_router(quote_router)
router.include_router(sales_order_router)
router.include_router(settings_router)
router.include_router(automation_router)
