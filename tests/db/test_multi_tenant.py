"""Tests for the multi-tenant primitives (exception, deny-list, detection)."""

from __future__ import annotations


class TestMissingOrgContextError:
    def test_inherits_from_runtime_error(self):
        from app.db.multi_tenant import MissingOrgContextError

        assert issubclass(MissingOrgContextError, RuntimeError)

    def test_message_names_the_model(self):
        from app.db.multi_tenant import MissingOrgContextError

        exc = MissingOrgContextError("Invoice")
        assert "Invoice" in str(exc)


class TestIsOrgScoped:
    def test_returns_true_for_class_with_organization_id_column(self):
        from app.db.multi_tenant import is_org_scoped
        from app.models.finance.ar.invoice import Invoice

        assert is_org_scoped(Invoice) is True

    def test_returns_false_for_organization_class_itself(self):
        """Organization's own org_id IS its PK — no parent to scope by."""
        from app.db.multi_tenant import is_org_scoped
        from app.models.finance.core_org.organization import Organization

        assert is_org_scoped(Organization) is False

    def test_returns_false_for_genuinely_shared_models(self):
        """Currency / Country / TaxJurisdiction have no organization_id;
        the column heuristic skips them automatically."""
        from app.db.multi_tenant import is_org_scoped
        from app.models.finance.core_fx.currency import Currency

        assert is_org_scoped(Currency) is False

    def test_returns_false_for_none(self):
        from app.db.multi_tenant import is_org_scoped

        assert is_org_scoped(None) is False


class TestIsOrgScopedWithAliases:
    """Aliased org-scoped models must still be detected as org-scoped,
    or the listener (Task 5) would silently skip filtering on aliased
    queries — a cross-tenant leak path."""

    def test_aliased_org_scoped_model_returns_true(self):
        from sqlalchemy.orm import aliased

        from app.db.multi_tenant import is_org_scoped
        from app.models.finance.ar.invoice import Invoice

        InvoiceAlias = aliased(Invoice)
        assert is_org_scoped(InvoiceAlias) is True

    def test_aliased_organization_returns_false(self):
        """Organization is on the deny-list; aliasing must not bypass it."""
        from sqlalchemy.orm import aliased

        from app.db.multi_tenant import is_org_scoped
        from app.models.finance.core_org.organization import Organization

        OrgAlias = aliased(Organization)
        assert is_org_scoped(OrgAlias) is False
