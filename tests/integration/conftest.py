"""
Integration Test Fixtures - Real PostgreSQL Database.

These tests use the actual PostgreSQL database with transaction rollback
for isolation. This ensures tests work with real SQLAlchemy models and
PostgreSQL-specific features (JSONB, ARRAY, UUID, etc.).

Environment Setup:
- Requires TEST_DATABASE_URL or uses main DATABASE_URL
- Tests run in transactions that are rolled back after each test
- No data persists between tests

IMPORTANT: This file must be loaded BEFORE the main tests/conftest.py
to avoid SQLite mock issues. Use: pytest tests/integration/ -p no:conftest
Or run directly: pytest tests/integration/ifrs/test_*.py
"""

import os
import sys
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Generator

# Load environment variables FIRST
from dotenv import load_dotenv
load_dotenv()

# Set required environment variables before any app imports
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-integration-tests")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("TOTP_ENCRYPTION_KEY", "QLUJktsTSfZEbST4R-37XmQ0tCkiVCBXZN2Zt053w8g=")
os.environ.setdefault("TOTP_ISSUER", "TestApp")

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker


def get_test_database_url() -> str:
    """Get database URL for tests."""
    # Use TEST_DATABASE_URL if set, otherwise use main DATABASE_URL
    test_url = os.getenv("TEST_DATABASE_URL")
    if test_url:
        return test_url

    # Use the main database URL (be careful in production!)
    main_url = os.getenv("DATABASE_URL")
    if main_url:
        return main_url

    # Default to localhost
    return "postgresql+psycopg://postgres:postgres@localhost:5434/dotmac_erp"


# Create engine for tests
_test_engine = create_engine(
    get_test_database_url(),
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)


@pytest.fixture(scope="session")
def engine():
    """Provide the test database engine."""
    return _test_engine


@pytest.fixture(scope="function")
def db(engine) -> Generator[Session, None, None]:
    """
    Provide a database session with transaction rollback.

    Each test runs in a transaction that is rolled back after the test,
    ensuring complete isolation between tests.
    """
    connection = engine.connect()
    transaction = connection.begin()

    # Create session bound to this connection
    TestSession = sessionmaker(bind=connection)
    session = TestSession()

    # Begin a nested transaction (savepoint)
    nested = connection.begin_nested()

    # If the session is committed, start a new nested transaction
    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(session, transaction):
        nonlocal nested
        if transaction.nested and not transaction._parent.nested:
            nested = connection.begin_nested()

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture(scope="function")
def organization(db: Session) -> "Organization":
    """Create an organization for testing."""
    from app.models.finance.core_org.organization import Organization

    org = Organization(
        organization_code=f"TEST-{uuid.uuid4().hex[:8].upper()}",
        legal_name="Test Organization",
        functional_currency_code="USD",
        presentation_currency_code="USD",
        fiscal_year_end_month=12,
        fiscal_year_end_day=31,
        is_active=True,
    )
    db.add(org)
    db.flush()
    return org


@pytest.fixture(scope="function")
def org_id(organization) -> uuid.UUID:
    """Get the organization ID from the organization fixture."""
    return organization.organization_id


@pytest.fixture(scope="function")
def user_id() -> uuid.UUID:
    """Generate a unique user ID for each test."""
    return uuid.uuid4()


# =============================================================================
# IFRS Model Fixtures
# =============================================================================

@pytest.fixture
def fiscal_year(db: Session, org_id: uuid.UUID):
    """Create a fiscal year for testing."""
    from app.models.finance.gl.fiscal_year import FiscalYear

    fy = FiscalYear(
        organization_id=org_id,
        year_code="FY2024",
        year_name="FY 2024",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
        is_closed=False,
    )
    db.add(fy)
    db.flush()
    return fy


@pytest.fixture
def fiscal_period(db: Session, org_id: uuid.UUID, fiscal_year):
    """Create an open fiscal period for testing."""
    from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus

    period = FiscalPeriod(
        organization_id=org_id,
        fiscal_year_id=fiscal_year.fiscal_year_id,
        period_number=1,
        period_name="January 2024",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        status=PeriodStatus.OPEN,
    )
    db.add(period)
    db.flush()
    return period


@pytest.fixture
def account_category(db: Session, org_id: uuid.UUID):
    """Create an account category for testing."""
    from app.models.finance.gl.account_category import AccountCategory, IFRSCategory

    category = AccountCategory(
        organization_id=org_id,
        category_code="ASSETS",
        category_name="Assets",
        ifrs_category=IFRSCategory.ASSETS,
        hierarchy_level=1,
        display_order=1,
    )
    db.add(category)
    db.flush()
    return category


@pytest.fixture
def gl_account(db: Session, org_id: uuid.UUID, account_category):
    """Create a GL account for testing."""
    from app.models.finance.gl.account import Account, AccountType, NormalBalance

    account = Account(
        organization_id=org_id,
        account_code="1000",
        account_name="Cash",
        account_type=AccountType.POSTING,
        normal_balance=NormalBalance.DEBIT,
        category_id=account_category.category_id,
        is_active=True,
    )
    db.add(account)
    db.flush()
    return account


@pytest.fixture
def expense_account(db: Session, org_id: uuid.UUID, account_category):
    """Create an expense account for testing."""
    from app.models.finance.gl.account import Account, AccountType, NormalBalance

    account = Account(
        organization_id=org_id,
        account_code="5000",
        account_name="General Expenses",
        account_type=AccountType.POSTING,
        normal_balance=NormalBalance.DEBIT,
        category_id=account_category.category_id,
        is_active=True,
    )
    db.add(account)
    db.flush()
    return account


@pytest.fixture
def inventory_account(db: Session, org_id: uuid.UUID, account_category):
    """Create an inventory account for testing."""
    from app.models.finance.gl.account import Account, AccountType, NormalBalance

    account = Account(
        organization_id=org_id,
        account_code="1300",
        account_name="Inventory",
        account_type=AccountType.POSTING,
        normal_balance=NormalBalance.DEBIT,
        category_id=account_category.category_id,
        is_active=True,
    )
    db.add(account)
    db.flush()
    return account


@pytest.fixture
def cogs_account(db: Session, org_id: uuid.UUID, account_category):
    """Create a COGS account for testing."""
    from app.models.finance.gl.account import Account, AccountType, NormalBalance

    account = Account(
        organization_id=org_id,
        account_code="4000",
        account_name="Cost of Goods Sold",
        account_type=AccountType.POSTING,
        normal_balance=NormalBalance.DEBIT,
        category_id=account_category.category_id,
        is_active=True,
    )
    db.add(account)
    db.flush()
    return account


@pytest.fixture
def ap_control_account(db: Session, org_id: uuid.UUID, account_category):
    """Create an AP control account for testing."""
    from app.models.finance.gl.account import Account, AccountType, NormalBalance

    account = Account(
        organization_id=org_id,
        account_code="2000",
        account_name="Accounts Payable",
        account_type=AccountType.CONTROL,
        normal_balance=NormalBalance.CREDIT,
        category_id=account_category.category_id,
        subledger_type="AP",
        is_active=True,
    )
    db.add(account)
    db.flush()
    return account


@pytest.fixture
def ar_control_account(db: Session, org_id: uuid.UUID, account_category):
    """Create an AR control account for testing."""
    from app.models.finance.gl.account import Account, AccountType, NormalBalance

    account = Account(
        organization_id=org_id,
        account_code="1100",
        account_name="Accounts Receivable",
        account_type=AccountType.CONTROL,
        normal_balance=NormalBalance.DEBIT,
        category_id=account_category.category_id,
        subledger_type="AR",
        is_active=True,
    )
    db.add(account)
    db.flush()
    return account


@pytest.fixture
def revenue_account(db: Session, org_id: uuid.UUID, account_category):
    """Create a revenue account for testing."""
    from app.models.finance.gl.account import Account, AccountType, NormalBalance

    account = Account(
        organization_id=org_id,
        account_code="3000",
        account_name="Sales Revenue",
        account_type=AccountType.POSTING,
        normal_balance=NormalBalance.CREDIT,
        category_id=account_category.category_id,
        is_active=True,
    )
    db.add(account)
    db.flush()
    return account


# =============================================================================
# Fixed Assets Fixtures
# =============================================================================

@pytest.fixture
def fa_asset_account(db: Session, org_id: uuid.UUID, account_category):
    """Create a fixed asset account for testing."""
    from app.models.finance.gl.account import Account, AccountType, NormalBalance

    account = Account(
        organization_id=org_id,
        account_code="1500",
        account_name="Fixed Assets",
        account_type=AccountType.POSTING,
        normal_balance=NormalBalance.DEBIT,
        category_id=account_category.category_id,
        is_active=True,
    )
    db.add(account)
    db.flush()
    return account


@pytest.fixture
def fa_accum_depr_account(db: Session, org_id: uuid.UUID, account_category):
    """Create accumulated depreciation account for testing."""
    from app.models.finance.gl.account import Account, AccountType, NormalBalance

    account = Account(
        organization_id=org_id,
        account_code="1510",
        account_name="Accumulated Depreciation",
        account_type=AccountType.POSTING,
        normal_balance=NormalBalance.CREDIT,
        category_id=account_category.category_id,
        is_active=True,
    )
    db.add(account)
    db.flush()
    return account


@pytest.fixture
def fa_depr_expense_account(db: Session, org_id: uuid.UUID, account_category):
    """Create depreciation expense account for testing."""
    from app.models.finance.gl.account import Account, AccountType, NormalBalance

    account = Account(
        organization_id=org_id,
        account_code="5100",
        account_name="Depreciation Expense",
        account_type=AccountType.POSTING,
        normal_balance=NormalBalance.DEBIT,
        category_id=account_category.category_id,
        is_active=True,
    )
    db.add(account)
    db.flush()
    return account


@pytest.fixture
def fa_gain_loss_account(db: Session, org_id: uuid.UUID, account_category):
    """Create gain/loss on disposal account for testing."""
    from app.models.finance.gl.account import Account, AccountType, NormalBalance

    account = Account(
        organization_id=org_id,
        account_code="6000",
        account_name="Gain/Loss on Disposal",
        account_type=AccountType.POSTING,
        normal_balance=NormalBalance.DEBIT,
        category_id=account_category.category_id,
        is_active=True,
    )
    db.add(account)
    db.flush()
    return account


@pytest.fixture
def asset_category(
    db: Session,
    org_id: uuid.UUID,
    fa_asset_account,
    fa_accum_depr_account,
    fa_depr_expense_account,
    fa_gain_loss_account,
):
    """Create an asset category for testing."""
    from app.models.finance.fa.asset_category import AssetCategory, DepreciationMethod

    category = AssetCategory(
        organization_id=org_id,
        category_code="EQUIPMENT",
        category_name="Office Equipment",
        depreciation_method=DepreciationMethod.STRAIGHT_LINE,
        useful_life_months=60,
        residual_value_percent=Decimal("10"),
        asset_account_id=fa_asset_account.account_id,
        accumulated_depreciation_account_id=fa_accum_depr_account.account_id,
        depreciation_expense_account_id=fa_depr_expense_account.account_id,
        gain_loss_disposal_account_id=fa_gain_loss_account.account_id,
        capitalization_threshold=Decimal("1000"),
        is_active=True,
    )
    db.add(category)
    db.flush()
    return category


# =============================================================================
# AP Fixtures
# =============================================================================

@pytest.fixture
def supplier(db: Session, org_id: uuid.UUID, ap_control_account):
    """Create a supplier for testing."""
    from app.models.finance.ap.supplier import Supplier, SupplierType

    supplier = Supplier(
        organization_id=org_id,
        supplier_code="SUP001",
        supplier_type=SupplierType.VENDOR,
        legal_name="Test Supplier Inc.",
        trading_name="Test Supplier",
        payment_terms_days=30,
        currency_code="USD",
        ap_control_account_id=ap_control_account.account_id,
        is_active=True,
    )
    db.add(supplier)
    db.flush()
    return supplier


@pytest.fixture
def supplier_invoice(db: Session, org_id: uuid.UUID, supplier, user_id: uuid.UUID):
    """Create a supplier invoice for testing."""
    from app.models.finance.ap.supplier_invoice import (
        SupplierInvoice,
        SupplierInvoiceStatus,
        SupplierInvoiceType,
    )

    invoice = SupplierInvoice(
        organization_id=org_id,
        supplier_id=supplier.supplier_id,
        invoice_number="APINV-001",
        supplier_invoice_number="VENDOR-001",
        invoice_type=SupplierInvoiceType.STANDARD,
        invoice_date=date(2024, 1, 15),
        received_date=date(2024, 1, 15),
        due_date=date(2024, 2, 14),
        currency_code="USD",
        exchange_rate=Decimal("1.0"),
        subtotal=Decimal("5000.00"),
        tax_amount=Decimal("0"),
        total_amount=Decimal("5000.00"),
        functional_currency_amount=Decimal("5000.00"),
        status=SupplierInvoiceStatus.DRAFT,
        ap_control_account_id=supplier.ap_control_account_id,
        created_by_user_id=user_id,
    )
    db.add(invoice)
    db.flush()
    return invoice


# =============================================================================
# AR Fixtures
# =============================================================================

@pytest.fixture
def customer(db: Session, org_id: uuid.UUID, ar_control_account):
    """Create a customer for testing."""
    from app.models.finance.ar.customer import Customer, CustomerType

    customer = Customer(
        organization_id=org_id,
        customer_code="CUS001",
        customer_type=CustomerType.COMPANY,
        legal_name="Test Customer Ltd.",
        trading_name="Test Customer",
        credit_terms_days=30,
        currency_code="USD",
        ar_control_account_id=ar_control_account.account_id,
        is_active=True,
    )
    db.add(customer)
    db.flush()
    return customer


# =============================================================================
# Inventory Fixtures
# =============================================================================

@pytest.fixture
def warehouse(db: Session, org_id: uuid.UUID):
    """Create a warehouse for testing."""
    from app.models.finance.inv.warehouse import Warehouse

    warehouse = Warehouse(
        organization_id=org_id,
        warehouse_code="WH001",
        warehouse_name="Main Warehouse",
        is_active=True,
    )
    db.add(warehouse)
    db.flush()
    return warehouse


@pytest.fixture
def inventory_adjustment_account(db: Session, org_id: uuid.UUID, account_category):
    """Create an inventory adjustment account for testing."""
    from app.models.finance.gl.account import Account, AccountType, NormalBalance

    account = Account(
        organization_id=org_id,
        account_code="5200",
        account_name="Inventory Adjustment",
        account_type=AccountType.POSTING,
        normal_balance=NormalBalance.DEBIT,
        category_id=account_category.category_id,
        is_active=True,
    )
    db.add(account)
    db.flush()
    return account


@pytest.fixture
def item_category(db: Session, org_id: uuid.UUID, inventory_account, cogs_account, revenue_account, inventory_adjustment_account):
    """Create an item category for testing."""
    from app.models.finance.inv.item_category import ItemCategory

    category = ItemCategory(
        organization_id=org_id,
        category_code="GOODS",
        category_name="General Goods",
        inventory_account_id=inventory_account.account_id,
        cogs_account_id=cogs_account.account_id,
        revenue_account_id=revenue_account.account_id,
        inventory_adjustment_account_id=inventory_adjustment_account.account_id,
        is_active=True,
    )
    db.add(category)
    db.flush()
    return category


@pytest.fixture
def inventory_item(db: Session, org_id: uuid.UUID, item_category, inventory_account, cogs_account, revenue_account):
    """Create an inventory item for testing."""
    from app.models.finance.inv.item import Item, CostingMethod

    item = Item(
        organization_id=org_id,
        item_code="ITEM001",
        item_name="Test Item",
        base_uom="EACH",
        category_id=item_category.category_id,
        costing_method=CostingMethod.WEIGHTED_AVERAGE,
        standard_cost=Decimal("10.00"),
        average_cost=Decimal("10.00"),
        currency_code="USD",
        inventory_account_id=inventory_account.account_id,
        cogs_account_id=cogs_account.account_id,
        revenue_account_id=revenue_account.account_id,
        track_inventory=True,
        is_active=True,
        is_purchaseable=True,
        is_saleable=True,
    )
    db.add(item)
    db.flush()
    return item


@pytest.fixture
def initial_inventory_transaction(
    db: Session,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    inventory_item,
    warehouse,
    fiscal_period,
):
    """Create initial inventory through a receipt transaction."""
    from app.models.finance.inv.inventory_transaction import InventoryTransaction, TransactionType

    txn = InventoryTransaction(
        organization_id=org_id,
        transaction_type=TransactionType.RECEIPT,
        transaction_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        fiscal_period_id=fiscal_period.fiscal_period_id,
        item_id=inventory_item.item_id,
        warehouse_id=warehouse.warehouse_id,
        quantity=Decimal("100"),
        unit_cost=Decimal("10.00"),
        total_cost=Decimal("1000.00"),
        uom=inventory_item.base_uom,
        currency_code="USD",
        quantity_before=Decimal("0"),
        quantity_after=Decimal("100"),
        source_document_type="INITIAL_BALANCE",
        reference="Initial stock",
        created_by_user_id=user_id,
    )
    db.add(txn)
    db.flush()
    return txn


@pytest.fixture
def inventory_with_balance(
    db: Session,
    org_id: uuid.UUID,
    inventory_item,
    warehouse,
    fiscal_period,
    initial_inventory_transaction,
):
    """Fixture that ensures inventory has balance (creates transaction)."""
    # The initial_inventory_transaction creates the balance
    # Return the item with balance info
    return {
        "item": inventory_item,
        "warehouse": warehouse,
        "quantity": Decimal("100"),
        "unit_cost": Decimal("10.00"),
    }


# =============================================================================
# AR Invoice Fixtures
# =============================================================================

@pytest.fixture
def ar_invoice(db: Session, org_id: uuid.UUID, customer, user_id: uuid.UUID):
    """Create an AR invoice for testing."""
    from app.models.finance.ar.invoice import Invoice, InvoiceStatus, InvoiceType

    invoice = Invoice(
        organization_id=org_id,
        customer_id=customer.customer_id,
        invoice_number="INV-001",
        invoice_type=InvoiceType.STANDARD,
        invoice_date=date(2024, 1, 15),
        due_date=date(2024, 2, 14),
        currency_code="USD",
        exchange_rate=Decimal("1.0"),
        subtotal=Decimal("1000.00"),
        tax_amount=Decimal("0"),
        total_amount=Decimal("1000.00"),
        functional_currency_amount=Decimal("1000.00"),
        status=InvoiceStatus.DRAFT,
        ar_control_account_id=customer.ar_control_account_id,
        created_by_user_id=user_id,
    )
    db.add(invoice)
    db.flush()
    return invoice


# =============================================================================
# Inventory Lot Fixtures
# =============================================================================

@pytest.fixture
def inventory_lot(db: Session, org_id: uuid.UUID, inventory_item, warehouse):
    """Create an inventory lot for testing."""
    from app.models.finance.inv.inventory_lot import InventoryLot

    lot = InventoryLot(
        organization_id=org_id,
        item_id=inventory_item.item_id,
        warehouse_id=warehouse.warehouse_id,
        lot_number="LOT-001",
        received_date=date(2024, 1, 1),
        initial_quantity=Decimal("100"),
        quantity_on_hand=Decimal("100"),
        quantity_available=Decimal("100"),
        unit_cost=Decimal("10.00"),
        is_active=True,
        is_quarantined=False,
    )
    db.add(lot)
    db.flush()
    return lot


# =============================================================================
# Numbering Sequence Fixtures (needed for inventory transactions)
# =============================================================================

@pytest.fixture
def inv_transaction_sequence(db: Session, org_id: uuid.UUID):
    """Create numbering sequence for inventory transactions."""
    from app.models.finance.core_config.numbering_sequence import (
        NumberingSequence,
        SequenceType,
    )

    sequence = NumberingSequence(
        organization_id=org_id,
        sequence_type=SequenceType.ITEM,  # Using ITEM for inventory transactions
        prefix="TXN",
        current_number=0,
        min_digits=4,
    )
    db.add(sequence)
    db.flush()
    return sequence


@pytest.fixture
def fa_asset_sequence(db: Session, org_id: uuid.UUID):
    """Create numbering sequence for fixed assets."""
    from app.models.finance.core_config.numbering_sequence import (
        NumberingSequence,
        SequenceType,
    )

    sequence = NumberingSequence(
        organization_id=org_id,
        sequence_type=SequenceType.ASSET,
        prefix="FA",
        current_number=0,
        min_digits=4,
    )
    db.add(sequence)
    db.flush()
    return sequence
