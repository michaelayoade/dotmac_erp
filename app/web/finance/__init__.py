"""
Finance Web Routes.

HTML template routes for the Finance/Accounting modules.
"""

from fastapi import APIRouter

from app.web.finance.ap import router as ap_router
from app.web.finance.ar import router as ar_router
from app.web.finance.automation import router as automation_router
from app.web.finance.banking import router as banking_router
from app.web.finance.dashboard import router as dashboard_router

# Independent routers (exported for use in main.py)
from app.web.finance.exp import router as expense_router
from app.web.finance.gl import router as gl_router
from app.web.finance.help import router as help_router
from app.web.finance.import_export import router as import_export_router
from app.web.finance.ipsas import router as ipsas_router
from app.web.finance.opening_balance import router as opening_balance_router
from app.web.finance.payments import router as payments_router
from app.web.finance.quote import router as quote_router
from app.web.finance.remita import router as remita_router
from app.web.finance.reports import router as reports_router
from app.web.finance.sales_order import router as sales_order_router
from app.web.finance.settings import router as settings_router
from app.web.finance.tax import router as tax_router

router = APIRouter(tags=["finance-web"])

router.include_router(dashboard_router)
router.include_router(gl_router)
router.include_router(ap_router)
router.include_router(ar_router)
router.include_router(automation_router)
router.include_router(banking_router)
router.include_router(ipsas_router)
router.include_router(tax_router)
router.include_router(reports_router)
router.include_router(quote_router)
router.include_router(sales_order_router)
router.include_router(expense_router)
router.include_router(import_export_router)
router.include_router(opening_balance_router)
router.include_router(help_router)
router.include_router(payments_router)
router.include_router(remita_router)
router.include_router(settings_router)
