"""
AP Supplier Web Service - Supplier-related web view methods.

Provides view-focused data and operations for AP supplier web routes.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
)
from app.models.finance.ap.supplier_payment import SupplierPayment
from app.models.finance.common.attachment import AttachmentCategory
from app.models.finance.gl.account_category import IFRSCategory
from app.services.audit_info import get_audit_service
from app.services.common import coerce_uuid
from app.services.finance.ap.supplier import SupplierInput, supplier_service
from app.services.finance.ap.web.base import (
    calculate_supplier_balance_trends,
    format_currency,
    format_date,
    format_file_size,
    get_accounts,
    invoice_status_label,
    logger,
    recent_activity_view,
    supplier_detail_view,
    supplier_form_view,
    supplier_list_view,
)
from app.services.finance.common.attachment import AttachmentInput, attachment_service
from app.services.finance.common.sorting import apply_sort
from app.services.finance.platform.currency_context import get_currency_context
from app.templates import templates
from app.web.deps import WebAuthContext, base_context


class SupplierWebService:
    """Web service methods for AP suppliers."""

    @staticmethod
    def supplier_typeahead(
        db: Session,
        organization_id: str,
        query: str,
        limit: int = 8,
    ) -> dict:
        """Search active suppliers for typeahead/autocomplete fields."""
        org_id = coerce_uuid(organization_id)
        term = query.strip()
        if not term:
            return {"items": []}

        search_term = f"%{term}%"
        stmt = (
            select(Supplier)
            .where(
                Supplier.organization_id == org_id,
                Supplier.is_active.is_(True),
            )
            .where(
                or_(
                    Supplier.supplier_code.ilike(search_term),
                    Supplier.legal_name.ilike(search_term),
                    Supplier.trading_name.ilike(search_term),
                    Supplier.tax_identification_number.ilike(search_term),
                )
            )
            .order_by(Supplier.legal_name.asc())
            .limit(limit)
        )
        suppliers = list(db.scalars(stmt).all())
        items = []
        for supplier in suppliers:
            name = supplier.trading_name or supplier.legal_name or ""
            code = supplier.supplier_code or ""
            label = f"{code} - {name}".strip(" -")
            items.append(
                {
                    "ref": str(supplier.supplier_id),
                    "label": label,
                    "supplier_code": code,
                    "supplier_name": name,
                    "currency_code": supplier.currency_code,
                    "payment_terms_days": supplier.payment_terms_days or 30,
                }
            )
        return {"items": items}

    @staticmethod
    def people_search(
        db: Session,
        organization_id: str,
        query: str,
        limit: int = 25,
    ) -> dict:
        """Search people by name/email for comment @mentions.

        Uses SQLAlchemy 2.0 ``select()`` syntax instead of ``db.query()``.

        Args:
            db: Database session
            organization_id: Organization scope
            query: Search term
            limit: Max results

        Returns:
            Dict with ``items`` list of person dicts.
        """
        from sqlalchemy import or_
        from sqlalchemy import select as sa_select

        from app.models.person import Person

        term = query.strip()
        if not term:
            return {"items": []}

        org_id = coerce_uuid(organization_id)
        search_term = f"%{term}%"

        people = list(
            db.scalars(
                sa_select(Person)
                .where(
                    Person.organization_id == org_id,
                    or_(
                        Person.first_name.ilike(search_term),
                        Person.last_name.ilike(search_term),
                        Person.display_name.ilike(search_term),
                        Person.email.ilike(search_term),
                    ),
                )
                .order_by(Person.first_name.asc(), Person.last_name.asc())
                .limit(limit)
            ).all()
        )

        items = []
        for person in people:
            name = (
                person.display_name or f"{person.first_name} {person.last_name}".strip()
            )
            person_status = "Active" if person.is_active else "Inactive"
            items.append(
                {
                    "ref": str(person.id),
                    "label": f"{name} <{person.email}> ({person_status})",
                    "name": name,
                    "email": person.email,
                }
            )

        return {"items": items}

    @staticmethod
    def build_supplier_input(
        db: Session, form_data: dict, organization_id: UUID
    ) -> SupplierInput:
        """Build SupplierInput from form data."""
        payload = dict(form_data)
        return supplier_service.build_input_from_payload(
            db=db,
            organization_id=organization_id,
            payload=payload,
        )

    @staticmethod
    def list_suppliers_context(
        db: Session,
        organization_id: str,
        search: str | None,
        status: str | None,
        page: int,
        limit: int = 50,
        sort: str | None = None,
        sort_dir: str | None = None,
    ) -> dict:
        """Get context for supplier listing page."""
        logger.debug(
            "list_suppliers_context: org=%s search=%r status=%s page=%d",
            organization_id,
            search,
            status,
            page,
        )
        offset = (page - 1) * limit
        org_id = coerce_uuid(organization_id)
        from app.services.finance.ap.supplier_query import build_supplier_query

        base_stmt = build_supplier_query(
            db=db,
            organization_id=organization_id,
            search=search,
            status=status,
        )

        total_count = (
            db.scalar(select(func.count()).select_from(base_stmt.subquery())) or 0
        )
        supplier_sort_map: dict[str, Any] = {
            "legal_name": Supplier.legal_name,
            "trading_name": Supplier.trading_name,
            "supplier_code": Supplier.supplier_code,
            # UI "Status" column maps to active/inactive flag on Supplier.
            "status": Supplier.is_active,
        }
        sorted_stmt = apply_sort(
            base_stmt,
            sort,
            sort_dir,
            supplier_sort_map,
            default=Supplier.legal_name.asc(),
        )
        suppliers = list(db.scalars(sorted_stmt.limit(limit).offset(offset)).all())

        open_statuses = [
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PARTIALLY_PAID,
        ]
        balances = db.execute(
            select(
                SupplierInvoice.supplier_id,
                func.coalesce(
                    func.sum(
                        SupplierInvoice.total_amount - SupplierInvoice.amount_paid
                    ),
                    0,
                ).label("balance"),
            )
            .where(
                SupplierInvoice.organization_id == org_id,
                SupplierInvoice.status.in_(open_statuses),
            )
            .group_by(SupplierInvoice.supplier_id)
        ).all()
        balance_map = {row.supplier_id: row.balance for row in balances}

        # Use shared audit service for user names
        audit_service = get_audit_service(db)
        creator_ids = [
            supplier.created_by_user_id
            for supplier in suppliers
            if supplier.created_by_user_id
        ]
        creator_names = audit_service.get_user_names_batch(creator_ids)

        # Calculate balance trends for sparkline charts
        supplier_ids = [s.supplier_id for s in suppliers]
        balance_trends = calculate_supplier_balance_trends(db, org_id, supplier_ids)

        suppliers_view = [
            supplier_list_view(
                supplier,
                balance_map.get(supplier.supplier_id, Decimal("0")),
                creator_names.get(supplier.created_by_user_id)
                if supplier.created_by_user_id
                else None,
                balance_trends.get(supplier.supplier_id),
            )
            for supplier in suppliers
        ]

        total_pages = max(1, (total_count + limit - 1) // limit)

        # Calculate stats for template header cards
        total_suppliers = (
            db.scalar(
                select(func.count(Supplier.supplier_id)).where(
                    Supplier.organization_id == org_id
                )
            )
            or 0
        )
        active_count = (
            db.scalar(
                select(func.count(Supplier.supplier_id)).where(
                    Supplier.organization_id == org_id, Supplier.is_active == True
                )
            )
            or 0
        )
        total_payables_raw = db.scalar(
            select(
                func.coalesce(
                    func.sum(
                        SupplierInvoice.total_amount - SupplierInvoice.amount_paid
                    ),
                    0,
                )
            ).where(
                SupplierInvoice.organization_id == org_id,
                SupplierInvoice.status.in_(open_statuses),
            )
        ) or Decimal("0")
        overdue_count = (
            db.scalar(
                select(func.count(SupplierInvoice.invoice_id)).where(
                    SupplierInvoice.organization_id == org_id,
                    SupplierInvoice.status.in_(open_statuses),
                    SupplierInvoice.due_date < date.today(),
                )
            )
            or 0
        )

        logger.debug("list_suppliers_context: found %d suppliers", total_count)

        return {
            "suppliers": suppliers_view,
            "search": search,
            "status": status,
            "sort": sort,
            "sort_dir": sort_dir,
            "page": page,
            "limit": limit,
            "per_page": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
            # Stats for header cards
            "total_suppliers": total_suppliers,
            "active_count": active_count,
            "total_payables": format_currency(total_payables_raw),
            "overdue_count": overdue_count,
        }

    @staticmethod
    def supplier_form_context(
        db: Session,
        organization_id: str,
        supplier_id: str | None = None,
    ) -> dict:
        """Get context for supplier create/edit form."""
        logger.debug(
            "supplier_form_context: org=%s supplier_id=%s", organization_id, supplier_id
        )
        org_id = coerce_uuid(organization_id)
        supplier = None
        if supplier_id:
            try:
                supplier = supplier_service.get(db, org_id, supplier_id)
            except (ValueError, LookupError) as e:
                logger.warning("Failed to load entity: %s", e)
                supplier = None
        supplier_view = supplier_form_view(supplier) if supplier else None

        expense_accounts = get_accounts(db, org_id, IFRSCategory.EXPENSES)
        payable_accounts = get_accounts(db, org_id, IFRSCategory.LIABILITIES, "AP")

        context = {
            "supplier": supplier_view,
            "expense_accounts": expense_accounts,
            "payable_accounts": payable_accounts,
        }
        context.update(get_currency_context(db, organization_id))

        return context

    @staticmethod
    def supplier_detail_context(
        db: Session,
        organization_id: str,
        supplier_id: str,
    ) -> dict:
        """Get context for supplier detail page."""
        logger.debug(
            "supplier_detail_context: org=%s supplier_id=%s",
            organization_id,
            supplier_id,
        )
        org_id = coerce_uuid(organization_id)
        supplier = None
        try:
            supplier = supplier_service.get(db, org_id, supplier_id)
        except (ValueError, LookupError) as e:
            logger.warning("Failed to load entity: %s", e)
            supplier = None

        if not supplier or supplier.organization_id != org_id:
            return {
                "supplier": None,
                "invoices": [],
                "payments": [],
                "purchase_orders": [],
                "goods_receipts": [],
            }

        from app.models.finance.ap.goods_receipt import GoodsReceipt
        from app.models.finance.ap.purchase_order import PurchaseOrder

        open_statuses = [
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PARTIALLY_PAID,
        ]

        balance = db.scalar(
            select(
                func.coalesce(
                    func.sum(
                        SupplierInvoice.total_amount - SupplierInvoice.amount_paid
                    ),
                    0,
                )
            ).where(
                SupplierInvoice.organization_id == org_id,
                SupplierInvoice.supplier_id == supplier.supplier_id,
                SupplierInvoice.status.in_(open_statuses),
            )
        ) or Decimal("0")

        # All invoices (all statuses)
        today = date.today()
        all_invoices_query = db.scalars(
            select(SupplierInvoice)
            .where(
                SupplierInvoice.organization_id == org_id,
                SupplierInvoice.supplier_id == supplier.supplier_id,
            )
            .order_by(SupplierInvoice.invoice_date.desc())
            .limit(20)
        ).all()
        invoices_view: list[dict] = []
        for inv in all_invoices_query:
            balance_due = inv.total_amount - inv.amount_paid
            invoices_view.append(
                {
                    "invoice_id": inv.invoice_id,
                    "invoice_number": inv.invoice_number,
                    "invoice_date": format_date(inv.invoice_date),
                    "due_date": format_date(inv.due_date),
                    "total_amount": format_currency(
                        inv.total_amount, inv.currency_code
                    ),
                    "balance": format_currency(balance_due, inv.currency_code),
                    "status": invoice_status_label(inv.status),
                    "is_overdue": (
                        inv.due_date < today
                        and inv.status
                        not in {SupplierInvoiceStatus.PAID, SupplierInvoiceStatus.VOID}
                    ),
                }
            )

        # Payments
        payments_query = db.scalars(
            select(SupplierPayment)
            .where(
                SupplierPayment.organization_id == org_id,
                SupplierPayment.supplier_id == supplier.supplier_id,
            )
            .order_by(SupplierPayment.payment_date.desc())
            .limit(20)
        ).all()
        payments_view: list[dict] = []
        for p in payments_query:
            payments_view.append(
                {
                    "payment_id": p.payment_id,
                    "payment_number": p.payment_number,
                    "payment_date": format_date(p.payment_date),
                    "amount": format_currency(p.amount, p.currency_code),
                    "payment_method": (
                        p.payment_method.value.replace("_", " ").title()
                        if p.payment_method
                        else "-"
                    ),
                    "reference": p.reference or "-",
                    "status": p.status.value if p.status else "-",
                }
            )

        # Purchase Orders
        pos_query = db.scalars(
            select(PurchaseOrder)
            .where(
                PurchaseOrder.organization_id == org_id,
                PurchaseOrder.supplier_id == supplier.supplier_id,
            )
            .order_by(PurchaseOrder.po_date.desc())
            .limit(20)
        ).all()
        purchase_orders_view: list[dict] = []
        for po in pos_query:
            purchase_orders_view.append(
                {
                    "po_id": po.po_id,
                    "po_number": po.po_number,
                    "po_date": format_date(po.po_date),
                    "total_amount": (
                        format_currency(po.total_amount, po.currency_code)
                        if po.total_amount
                        else "-"
                    ),
                    "status": po.status.value if po.status else "-",
                }
            )

        # Goods Receipts
        receipts_query = db.scalars(
            select(GoodsReceipt)
            .where(
                GoodsReceipt.organization_id == org_id,
                GoodsReceipt.supplier_id == supplier.supplier_id,
            )
            .order_by(GoodsReceipt.receipt_date.desc())
            .limit(20)
        ).all()
        goods_receipts_view: list[dict] = []
        for gr in receipts_query:
            goods_receipts_view.append(
                {
                    "receipt_id": gr.receipt_id,
                    "receipt_number": gr.receipt_number,
                    "receipt_date": format_date(gr.receipt_date),
                    "status": gr.status.value if gr.status else "-",
                }
            )

        # Get attachments
        attachments = attachment_service.list_for_entity(
            db,
            organization_id=org_id,
            entity_type="SUPPLIER",
            entity_id=supplier.supplier_id,
        )
        attachments_view = [
            {
                "attachment_id": str(att.attachment_id),
                "file_name": att.file_name,
                "file_size_display": format_file_size(att.file_size),
                "content_type": att.content_type,
                "uploaded_at": att.uploaded_at.strftime("%Y-%m-%d %H:%M"),
                "description": att.description or "",
            }
            for att in attachments
        ]

        logger.debug(
            "supplier_detail_context: found %d invoices, %d payments, "
            "%d purchase orders, %d goods receipts",
            len(invoices_view),
            len(payments_view),
            len(purchase_orders_view),
            len(goods_receipts_view),
        )

        return {
            "supplier": supplier_detail_view(supplier, balance),
            "invoices": invoices_view,
            "payments": payments_view,
            "purchase_orders": purchase_orders_view,
            "goods_receipts": goods_receipts_view,
            "attachments": attachments_view,
            "recent_activity": recent_activity_view(
                db,
                org_id,
                table_schema="ap",
                table_name="supplier",
                record_id=str(supplier.supplier_id),
                limit=10,
            ),
        }

    @staticmethod
    def delete_supplier(
        db: Session,
        organization_id: str,
        supplier_id: str,
    ) -> str | None:
        """Delete a supplier. Returns error message or None on success."""
        logger.debug(
            "delete_supplier: org=%s supplier_id=%s", organization_id, supplier_id
        )
        org_id = coerce_uuid(organization_id)
        sup_id = coerce_uuid(supplier_id)

        try:
            supplier_service.delete_supplier(db, org_id, sup_id)
            logger.info(
                "delete_supplier: deleted supplier %s for org %s", sup_id, org_id
            )
            return None
        except HTTPException as exc:
            return exc.detail
        except Exception as e:
            logger.exception("delete_supplier: failed for org %s", org_id)
            return f"Failed to delete supplier: {str(e)}"

    # =====================================================================
    # HTTP Response Methods
    # =====================================================================

    def list_suppliers_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None,
        status: str | None,
        page: int,
        limit: int = 50,
        sort: str | None = None,
        sort_dir: str | None = None,
    ) -> HTMLResponse:
        """Render supplier list page."""
        context = base_context(request, auth, "Suppliers", "ap")
        context.update(
            self.list_suppliers_context(
                db,
                str(auth.organization_id),
                search=search,
                status=status,
                page=page,
                limit=limit,
                sort=sort,
                sort_dir=sort_dir,
            )
        )
        return templates.TemplateResponse(request, "finance/ap/suppliers.html", context)

    def supplier_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render new supplier form."""
        context = base_context(request, auth, "New Supplier", "ap")
        context.update(self.supplier_form_context(db, str(auth.organization_id)))
        return templates.TemplateResponse(
            request, "finance/ap/supplier_form.html", context
        )

    def supplier_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        supplier_id: str,
    ) -> HTMLResponse:
        """Render supplier detail page."""
        context = base_context(request, auth, "Supplier Details", "ap")
        context.update(
            self.supplier_detail_context(
                db,
                str(auth.organization_id),
                supplier_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/ap/supplier_detail.html", context
        )

    def supplier_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        supplier_id: str,
    ) -> HTMLResponse:
        """Render supplier edit form."""
        context = base_context(request, auth, "Edit Supplier", "ap")
        context.update(
            self.supplier_form_context(db, str(auth.organization_id), supplier_id)
        )
        return templates.TemplateResponse(
            request, "finance/ap/supplier_form.html", context
        )

    async def create_supplier_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle supplier creation form submission."""
        form_data = await request.form()

        try:
            org_id = auth.organization_id
            if org_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            input_data = self.build_supplier_input(db, dict(form_data), org_id)

            supplier_service.create_supplier(
                db=db,
                organization_id=org_id,
                input=input_data,
            )

            return RedirectResponse(
                url="/finance/ap/suppliers?success=Supplier+created+successfully",
                status_code=303,
            )

        except Exception as e:
            logger.exception("create_supplier_response: failed")
            context = base_context(request, auth, "New Supplier", "ap")
            context.update(self.supplier_form_context(db, str(auth.organization_id)))
            context["error"] = str(e)
            context["form_data"] = dict(form_data)
            return templates.TemplateResponse(
                request, "finance/ap/supplier_form.html", context
            )

    async def update_supplier_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        supplier_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle supplier update form submission."""
        form_data = await request.form()

        try:
            org_id = auth.organization_id
            if org_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            input_data = self.build_supplier_input(db, dict(form_data), org_id)

            supplier_service.update_supplier(
                db=db,
                organization_id=org_id,
                supplier_id=UUID(supplier_id),
                input=input_data,
            )

            return RedirectResponse(
                url="/finance/ap/suppliers?success=Supplier+updated+successfully",
                status_code=303,
            )

        except Exception as e:
            logger.exception("update_supplier_response: failed")
            context = base_context(request, auth, "Edit Supplier", "ap")
            context.update(
                self.supplier_form_context(db, str(auth.organization_id), supplier_id)
            )
            context["error"] = str(e)
            context["form_data"] = dict(form_data)
            return templates.TemplateResponse(
                request, "finance/ap/supplier_form.html", context
            )

    def delete_supplier_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        supplier_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle supplier deletion."""
        error = self.delete_supplier(db, str(auth.organization_id), supplier_id)

        if error:
            context = base_context(request, auth, "Supplier Details", "ap")
            context.update(
                self.supplier_detail_context(
                    db,
                    str(auth.organization_id),
                    supplier_id,
                )
            )
            context["error"] = error
            return templates.TemplateResponse(
                request, "finance/ap/supplier_detail.html", context
            )

        return RedirectResponse(
            url="/finance/ap/suppliers?success=Record+deleted+successfully",
            status_code=303,
        )

    async def upload_supplier_attachment_response(
        self,
        supplier_id: str,
        file: UploadFile,
        description: str | None,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Handle supplier attachment upload."""
        try:
            org_id = auth.organization_id
            user_id = auth.person_id
            if org_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            if user_id is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            supplier = supplier_service.get(db, org_id, supplier_id)
            if not supplier or supplier.organization_id != auth.organization_id:
                return RedirectResponse(
                    url=f"/finance/ap/suppliers/{supplier_id}?error=Supplier+not+found",
                    status_code=303,
                )

            input_data = AttachmentInput(
                entity_type="SUPPLIER",
                entity_id=supplier_id,
                file_name=file.filename or "unnamed",
                content_type=file.content_type or "application/octet-stream",
                category=AttachmentCategory.SUPPLIER,
                description=description,
            )

            attachment_service.save_file(
                db=db,
                organization_id=org_id,
                input=input_data,
                file_content=file.file,
                uploaded_by=user_id,
            )

            return RedirectResponse(
                url=f"/finance/ap/suppliers/{supplier_id}?success=Attachment+uploaded",
                status_code=303,
            )

        except ValueError as e:
            return RedirectResponse(
                url=f"/finance/ap/suppliers/{supplier_id}?error={str(e)}",
                status_code=303,
            )
        except Exception:
            logger.exception("upload_supplier_attachment_response: failed")
            return RedirectResponse(
                url=f"/finance/ap/suppliers/{supplier_id}?error=Upload+failed",
                status_code=303,
            )


# Module-level instance for convenience
supplier_web_service = SupplierWebService()
