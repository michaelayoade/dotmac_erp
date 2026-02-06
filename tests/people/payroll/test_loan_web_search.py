from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.people.payroll.web import loan_web


class _StubStmt:
    def __init__(self) -> None:
        self.join_calls = []
        self.where_calls = 0

    def options(self, *args, **kwargs):
        return self

    def where(self, *args, **kwargs):
        self.where_calls += 1
        return self

    def order_by(self, *args, **kwargs):
        return self

    def join(self, *args, **kwargs):
        self.join_calls.append(args)
        return self

    def subquery(self):
        return self

    def select_from(self, *args, **kwargs):
        return self

    def offset(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self


def _scalars_result(rows):
    return SimpleNamespace(unique=lambda: SimpleNamespace(all=lambda: rows))


def test_loan_search_adds_person_joins():
    stubs = []

    def _select(*_args, **_kwargs):
        stmt = _StubStmt()
        stubs.append(stmt)
        return stmt

    service = loan_web.LoanWebService()
    db = MagicMock()
    db.scalar.return_value = 0
    db.scalars.return_value = _scalars_result([])

    auth = SimpleNamespace(organization_id="00000000-0000-0000-0000-000000000001")

    with (
        patch.object(loan_web, "select", _select),
        patch.object(loan_web, "base_context", lambda *args, **kwargs: {}),
        patch.object(
            loan_web.templates, "TemplateResponse", lambda *_args, **_kwargs: None
        ),
    ):
        service.list_loans_response(MagicMock(), auth, db, search="Ada")

    assert stubs, "expected select() to be called"
    assert len(stubs[0].join_calls) == 2


def test_loan_list_without_search_skips_person_joins():
    stubs = []

    def _select(*_args, **_kwargs):
        stmt = _StubStmt()
        stubs.append(stmt)
        return stmt

    service = loan_web.LoanWebService()
    db = MagicMock()
    db.scalar.return_value = 0
    db.scalars.return_value = _scalars_result([])

    auth = SimpleNamespace(organization_id="00000000-0000-0000-0000-000000000001")

    with (
        patch.object(loan_web, "select", _select),
        patch.object(loan_web, "base_context", lambda *args, **kwargs: {}),
        patch.object(
            loan_web.templates, "TemplateResponse", lambda *_args, **_kwargs: None
        ),
    ):
        service.list_loans_response(MagicMock(), auth, db)

    assert stubs, "expected select() to be called"
    assert len(stubs[0].join_calls) == 0
