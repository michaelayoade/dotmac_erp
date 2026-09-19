"""Actual report SQL over a projection of the real ORM column types."""

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine, insert, update
from sqlalchemy.orm import Session

from app.models.finance.ap.goods_receipt import GoodsReceipt
from app.models.finance.ap.goods_receipt_line import GoodsReceiptLine
from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import (
    PostingStatus,
    SupplierInvoice,
    SupplierInvoiceStatus,
    SupplierInvoiceType,
)
from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine
from app.models.inventory.item import Item, ItemType
from app.models.inventory.item_category import ItemCategory
from app.models.inventory.warehouse import Warehouse
from app.services.inventory import purchase_report as module
from app.services.inventory.purchase_report import (
    PurchaseReportFilters,
    PurchaseReportService,
    resolve_purchase_period,
)

TODAY = date(2026, 9, 18)
ORG, OTHER_ORG = uuid4(), uuid4()
ITEM, SUPPLIER, CATEGORY, WAREHOUSE = (uuid4() for _ in range(4))


@pytest.mark.parametrize(
    "kind,anchor,start,end,through",
    [
        ("weekly", None, "2026-09-14", "2026-09-20", "2026-09-18"),
        ("weekly", "2026-01-01", "2025-12-29", "2026-01-04", "2026-01-04"),
        ("monthly", "2024-02-29", "2024-02-01", "2024-02-29", "2024-02-29"),
        ("monthly", "2026-09-01", "2026-09-01", "2026-09-30", "2026-09-18"),
        ("monthly", "2025-12-31", "2025-12-01", "2025-12-31", "2025-12-31"),
        ("quarterly", "2026-01-31", "2026-01-01", "2026-03-31", "2026-03-31"),
        ("quarterly", "2026-06-30", "2026-04-01", "2026-06-30", "2026-06-30"),
        ("quarterly", "2026-09-18", "2026-07-01", "2026-09-30", "2026-09-18"),
        ("quarterly", "2025-12-31", "2025-10-01", "2025-12-31", "2025-12-31"),
        ("yearly", "2026-09-18", "2026-01-01", "2026-12-31", "2026-09-18"),
        ("yearly", "2024-02-29", "2024-01-01", "2024-12-31", "2024-12-31"),
    ],
)
def test_period_boundaries(kind, anchor, start, end, through):
    p = resolve_purchase_period(kind, anchor, today=TODAY)
    assert (str(p.start), str(p.end), str(p.through)) == (start, end, through)
    assert p.label and p.unit


@pytest.mark.parametrize(
    "arguments",
    [
        {"period": "daily"},
        {"period_start": "20260230"},
        {"period_start": "2026-02-30"},
        {"period_start": "1899-12-31"},
        {"period_start": "9999-12-31"},
        {"period_start": "2026-09-21"},
        {"period": "monthly", "month": "2026-13"},
        {"period": "monthly", "month": "2026-9"},
        {"period": "quarterly", "quarter": "5"},
        {"period": "yearly", "year": "9999"},
        {"period": "yearly", "year": "2027"},
        {"period": "monthly", "week_start": "2026-09-14"},
        {"supplier": "invalid"},
        {"warehouse": "invalid"},
        {"category": "invalid"},
        {"search": "x" * 101},
    ],
)
def test_invalid_input(arguments):
    with pytest.raises(ValueError):
        PurchaseReportFilters.parse(today=TODAY, **arguments)


def test_form_selectors_legacy_alias_and_lagos_rollover():
    assert selected(week_start="2026-09-15").query_params() == {
        "period": "weekly",
        "period_start": "2026-09-14",
    }
    assert selected(period="monthly", month="2026-08").period.start == date(2026, 8, 1)
    assert (
        selected(period="quarterly", quarter="2", year="2026").period.label == "Q2 2026"
    )
    assert selected(period="yearly", year="2025").period.start == date(2025, 1, 1)
    utc = datetime(2026, 9, 13, 23, 30, tzinfo=timezone.utc)
    with patch.object(module, "datetime") as clock:
        clock.now.return_value = utc.astimezone(module.REPORT_TIMEZONE)
        p = resolve_purchase_period()
    assert p.start == date(2026, 9, 14)
    assert p.through == p.start


def selected(**kwargs):
    return PurchaseReportFilters.parse(today=TODAY, **kwargs)


@pytest.fixture
def purchase_projection():
    names = {
        SupplierInvoice: "invoice_id organization_id supplier_id invoice_number invoice_date invoice_type status posting_status currency_code is_prepayment total_amount",
        SupplierInvoiceLine: "line_id line_number invoice_id item_id description goods_receipt_line_id receipt_warehouse_id quantity unit_price line_amount tax_amount",
        Supplier: "supplier_id organization_id legal_name is_active",
        Item: "item_id organization_id item_code item_name item_type category_id track_inventory is_active",
        ItemCategory: "category_id organization_id category_name is_active",
        Warehouse: "warehouse_id organization_id warehouse_name is_active",
        GoodsReceipt: "receipt_id organization_id warehouse_id",
        GoodsReceiptLine: "line_id receipt_id",
    }
    metadata, tables = MetaData(), {}
    for model, columns in names.items():
        table = model.__table__
        tables[model] = Table(
            table.name,
            metadata,
            *(
                Column(n, table.c[n].type, primary_key=table.c[n].primary_key)
                for n in columns.split()
            ),
            schema=table.schema,
        )
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"ap": None, "inv": None}},
    )
    metadata.create_all(engine)
    with engine.connect() as connection:

        def put(model, **values):
            connection.execute(insert(tables[model]).values(**values))

        put(
            Supplier,
            supplier_id=SUPPLIER,
            organization_id=ORG,
            legal_name="Supplier",
            is_active=False,
        )
        put(
            ItemCategory,
            category_id=CATEGORY,
            organization_id=ORG,
            category_name="Fibre",
            is_active=False,
        )
        put(
            Warehouse,
            warehouse_id=WAREHOUSE,
            organization_id=ORG,
            warehouse_name="Store",
            is_active=False,
        )
        put(
            Item,
            item_id=ITEM,
            organization_id=ORG,
            item_code="CODE",
            item_name="Current item name",
            category_id=CATEGORY,
            item_type=ItemType.INVENTORY,
            track_inventory=True,
            is_active=False,
        )
        counters, header_totals, kinds = {}, {}, {}

        def purchase(
            *,
            invoice_id=None,
            amount="100",
            tax="7.5",
            item=ITEM,
            description="Invoice description",
            warehouse=WAREHOUSE,
            currency="NGN",
            kind=SupplierInvoiceType.STANDARD,
            status=SupplierInvoiceStatus.POSTED,
            posting=PostingStatus.POSTED,
            when=TODAY,
            org=ORG,
            supplier=SUPPLIER,
            prepayment=False,
            receipt_line=None,
        ):
            if invoice_id is None:
                invoice_id = uuid4()
                counters[invoice_id], header_totals[invoice_id] = 0, Decimal("0")
                kinds[invoice_id] = kind
                put(
                    SupplierInvoice,
                    invoice_id=invoice_id,
                    organization_id=org,
                    supplier_id=supplier,
                    invoice_number=str(invoice_id),
                    invoice_date=when,
                    invoice_type=kind,
                    status=status,
                    posting_status=posting,
                    currency_code=currency,
                    is_prepayment=prepayment,
                    total_amount=Decimal("0"),
                )
            counters[invoice_id] += 1
            line_id = uuid4()
            put(
                SupplierInvoiceLine,
                line_id=line_id,
                line_number=counters[invoice_id],
                invoice_id=invoice_id,
                item_id=item,
                description=description,
                receipt_warehouse_id=warehouse,
                goods_receipt_line_id=receipt_line,
                quantity=Decimal("2"),
                unit_price=Decimal("60"),
                line_amount=Decimal(amount),
                tax_amount=Decimal(tax),
            )
            value = Decimal(amount) + Decimal(tax)
            if kinds[invoice_id] == SupplierInvoiceType.CREDIT_NOTE:
                value = -abs(Decimal(amount)) - abs(Decimal(tax))
            header_totals[invoice_id] += value
            set_total(invoice_id, header_totals[invoice_id])
            return invoice_id, line_id

        def set_total(invoice_id, amount):
            connection.execute(
                update(tables[SupplierInvoice])
                .where(tables[SupplierInvoice].c.invoice_id == invoice_id)
                .values(total_amount=Decimal(amount))
            )
            connection.commit()

        with Session(bind=connection) as db:
            yield PurchaseReportService(db), purchase, put, set_total
    engine.dispose()


def test_all_saved_lines_and_invoices_without_catalogue_items(purchase_projection):
    service, purchase, put, _ = purchase_projection
    invoice, _ = purchase(description="Historical description")
    purchase(
        invoice_id=invoice,
        item=None,
        amount="20",
        tax="1.5",
        description="Cable locally purchased",
    )
    purchase(invoice_id=invoice, item=None, amount="5", tax="0", description="Delivery")
    purchase(item=None, warehouse=None, amount="10", tax="0")
    for kind in (ItemType.SERVICE, ItemType.NON_INVENTORY):
        item = uuid4()
        put(
            Item,
            item_id=item,
            organization_id=ORG,
            item_code="SERVICE",
            item_name="Service",
            category_id=CATEGORY,
            item_type=kind,
            track_inventory=False,
        )
        purchase(item=item)
    purchase(item=uuid4(), description="Deleted item still billed")
    report = service.report(ORG, selected())
    assert report["total_count"] == 7
    assert report["summary"]["unlinked_count"] == 4
    assert report["summary"]["item_count"] == 3
    assert report["currency_totals"][0]["difference"] == 0
    assert report["currency_totals"][0]["matching_total"] == Decimal("466.5")
    assert {r["description"] for r in report["purchase_rows"]} >= {
        "Delivery",
        "Historical description",
    }


def test_filtered_totals_and_invoice_counting_do_not_duplicate_headers(
    purchase_projection,
):
    service, purchase, _, set_total = purchase_projection
    first, _ = purchase(amount="100", tax="0", description="Router")
    purchase(
        invoice_id=first,
        item=None,
        warehouse=None,
        amount="25",
        tax="0",
        description="Delivery",
    )
    second, _ = purchase(amount="125", tax="0", description="Router")
    report = service.report(ORG, selected(search="Router"))
    total = report["currency_totals"][0]
    assert total["matching_total"] == Decimal("225")
    assert total["invoice_total"] == Decimal("250")
    assert total["full_line_total"] == Decimal("250")
    assert total["difference"] == 0
    assert total["partial_count"] == 1
    set_total(first, "126")
    set_total(second, "124")
    total = service.report(ORG, selected())["currency_totals"][0]
    assert total["difference"] == 0  # Opposite discrepancies MUST NOT hide each other.
    assert total["mismatch_count"] == 2


def test_credit_signs_currency_and_saved_tax_values(purchase_projection):
    service, purchase, _, _ = purchase_projection
    purchase(amount="100", tax="20")  # Recorded price is 60 x 2, tax inclusive.
    purchase(kind=SupplierInvoiceType.CREDIT_NOTE, amount="10", tax="2")
    purchase(kind=SupplierInvoiceType.CREDIT_NOTE, amount="-5", tax="-1")
    purchase(kind=SupplierInvoiceType.DEBIT_NOTE, amount="3", tax="0")
    purchase(currency="USD", amount="9", tax="0", item=None)
    totals = {
        r["currency_code"]: r
        for r in service.report(ORG, selected())["currency_totals"]
    }
    assert totals["NGN"]["purchases"] == Decimal("103")
    assert totals["NGN"]["credits"] == Decimal("15")
    assert totals["NGN"]["matching_total"] == Decimal("105")
    assert totals["USD"]["matching_total"] == Decimal("9")
    assert all(r["difference"] == 0 for r in totals.values())


def test_dates_eligibility_and_all_four_periods(purchase_projection):
    service, purchase, _, _ = purchase_projection
    for state in SupplierInvoiceStatus:
        purchase(status=state, posting=PostingStatus.NOT_POSTED)
    purchase(status=SupplierInvoiceStatus.ON_HOLD)
    purchase(status=SupplierInvoiceStatus.DISPUTED)
    purchase(prepayment=True)
    purchase(when=date(2026, 9, 19))
    purchase(when=date(2026, 9, 1))
    purchase(when=date(2026, 7, 1))
    purchase(when=date(2026, 1, 1))
    purchase(when=date(2025, 12, 31))
    for kind, count in [("weekly", 5), ("monthly", 6), ("quarterly", 7), ("yearly", 8)]:
        assert service.report(ORG, selected(period=kind))["total_count"] == count


def test_tenant_boundaries_preserve_owned_lines_but_hide_foreign_metadata(
    purchase_projection,
):
    service, purchase, put, _ = purchase_projection
    foreign_item, foreign_supplier, foreign_warehouse = uuid4(), uuid4(), uuid4()
    put(
        Item,
        item_id=foreign_item,
        organization_id=OTHER_ORG,
        item_code="PRIVATE",
        item_name="PRIVATE",
    )
    put(
        Supplier,
        supplier_id=foreign_supplier,
        organization_id=OTHER_ORG,
        legal_name="PRIVATE",
    )
    put(
        Warehouse,
        warehouse_id=foreign_warehouse,
        organization_id=OTHER_ORG,
        warehouse_name="PRIVATE",
    )
    purchase(org=OTHER_ORG, item=foreign_item, supplier=foreign_supplier)
    purchase(item=foreign_item, supplier=foreign_supplier, warehouse=foreign_warehouse)
    rows = service.export_rows(ORG, selected())
    assert len(rows) == 1
    assert rows[0]["item_id"] is None and rows[0]["supplier_name"] is None
    assert "PRIVATE" not in str(rows)
    assert "PRIVATE" not in str(service.options(ORG, selected()))
    assert not service.export_rows(ORG, selected(supplier=str(foreign_supplier)))
    assert not service.export_rows(ORG, selected(warehouse=str(foreign_warehouse)))
    with pytest.raises(ValueError, match="organization"):
        service.statement(None, selected())


def test_receipt_enrichment_unspecified_filters_and_literal_description_search(
    purchase_projection,
):
    service, purchase, put, _ = purchase_projection
    receipt, line = uuid4(), uuid4()
    put(GoodsReceipt, receipt_id=receipt, organization_id=ORG, warehouse_id=WAREHOUSE)
    put(GoodsReceiptLine, line_id=line, receipt_id=receipt)
    purchase(warehouse=None, receipt_line=line)
    purchase(item=None, warehouse=None, description="CABLE_10%")
    purchase(item=None, warehouse=None, description="CABLEA10OTHER")
    assert len(service.export_rows(ORG, selected(warehouse=str(WAREHOUSE)))) == 1
    assert len(service.export_rows(ORG, selected(warehouse="unspecified"))) == 2
    assert len(service.export_rows(ORG, selected(category="unspecified"))) == 2
    assert len(service.export_rows(ORG, selected(search="CABLE_10%"))) == 1
    receipt2, line2 = uuid4(), uuid4()
    put(
        GoodsReceipt,
        receipt_id=receipt2,
        organization_id=OTHER_ORG,
        warehouse_id=WAREHOUSE,
    )
    put(GoodsReceiptLine, line_id=line2, receipt_id=receipt2)
    purchase(warehouse=None, receipt_line=line2)
    assert len(service.export_rows(ORG, selected(warehouse=str(WAREHOUSE)))) == 1


def test_pagination_export_limits_and_full_invoice_reconciliation(purchase_projection):
    service, purchase, _, _ = purchase_projection
    invoice, _ = purchase(amount="1", tax="0")
    for _ in range(54):
        purchase(invoice_id=invoice, item=None, amount="1", tax="0")
    report = service.report(ORG, selected(), page=1)
    assert len(report["purchase_rows"]) == 50
    assert report["total_count"] == 55
    assert report["invoice_rows"][0]["full_line_total"] == Decimal("55")
    assert report["invoice_rows"][0]["full_line_count"] == 55
    assert len(service.export_rows(ORG, selected())) == 55
    assert len(service.export_rows(ORG, selected(), view="invoices")) == 1
    assert len(service.report(ORG, selected(), page=99)["purchase_rows"]) == 5
    with patch.object(module, "MAX_EXPORT_ROWS", 54):
        with pytest.raises(ValueError, match="exceeds"):
            service.export_rows(ORG, selected())
    with pytest.raises(ValueError):
        service.export_rows(ORG, selected(), view="unknown")
    assert service.report(ORG, selected(period_start="2025-01-01"))["total_count"] == 0
