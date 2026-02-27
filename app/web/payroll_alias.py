"""
Payroll alias routes.

Provides /payroll/* redirects to the People payroll module.
"""

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter(prefix="/payroll", tags=["payroll-alias"])


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(url=f"/people/payroll{path}", status_code=302)


@router.get("")
def payroll_root() -> RedirectResponse:
    return _redirect("/slips")


@router.get("/")
def payroll_root_slash() -> RedirectResponse:
    return _redirect("/slips")


@router.get("/components")
def payroll_components() -> RedirectResponse:
    return _redirect("/components")


@router.get("/components/new")
def payroll_components_new() -> RedirectResponse:
    return _redirect("/components/new")


@router.get("/structures")
def payroll_structures() -> RedirectResponse:
    return _redirect("/structures")


@router.get("/structures/new")
def payroll_structures_new() -> RedirectResponse:
    return _redirect("/structures/new")


@router.get("/assignments")
def payroll_assignments() -> RedirectResponse:
    return _redirect("/assignments")


@router.get("/assignments/new")
def payroll_assignments_new() -> RedirectResponse:
    return _redirect("/assignments/new")


@router.get("/loans")
def payroll_loans() -> RedirectResponse:
    return _redirect("/loans")


@router.get("/loan")
def payroll_loan_singular() -> RedirectResponse:
    return _redirect("/loans")


@router.get("/loans/")
def payroll_loans_slash() -> RedirectResponse:
    return _redirect("/loans")


@router.get("/loans/new")
def payroll_loans_new() -> RedirectResponse:
    return _redirect("/loans/new")


@router.get("/loans/types")
def payroll_loan_types() -> RedirectResponse:
    return _redirect("/loans/types")


@router.get("/loans/types/")
def payroll_loan_types_slash() -> RedirectResponse:
    return _redirect("/loans/types")
