# Dotmac ERP

Unified ERP for finance, HR, and operations. Multi-tenant business software built with FastAPI, featuring comprehensive financial modules, human resources, authentication, RBAC, audit logging, background jobs, and full observability.

## Features

### Financial Modules

- **General Ledger (GL)**
  - Chart of Accounts management
  - Journal entries with double-entry validation
  - Fiscal periods and year-end closing
  - Trial balance and financial reporting

- **Accounts Payable (AP)**
  - Supplier management
  - Purchase orders and goods receipts
  - Supplier invoices and payment processing
  - AP aging reports

- **Accounts Receivable (AR)**
  - Customer management
  - Quotes and sales orders
  - Customer invoices and receipts
  - Credit notes and AR aging

- **Inventory (INV)**
  - Item management with FIFO valuation
  - Lot and serial tracking
  - Stock movements and adjustments
  - Bill of Materials (BOM)

- **Banking**
  - Bank account management
  - Statement import and reconciliation
  - Transaction categorization rules

- **Fixed Assets (FA)**
  - Asset registration and categorization
  - Depreciation schedules (straight-line, declining balance)
  - Revaluation and disposal

- **Tax**
  - Tax configuration and rates
  - Tax transaction tracking
  - Tax return preparation

- **Expenses**
  - Expense tracking and categorization
  - Cost allocation

- **Leases**
  - Lease contract management
  - Right-of-use asset and liability calculation

### People & HR Modules

- **Employee Management**
  - Employee records and onboarding
  - Organization structure and departments

- **Attendance & Leave**
  - Attendance tracking and timesheets
  - Leave requests and approvals

- **Payroll**
  - Salary processing and payslips
  - Tax deductions and benefits

- **Recruitment**
  - Job postings and applicant tracking
  - Interview scheduling

- **Performance**
  - Performance reviews and goals
  - 360-degree feedback

### Platform Features

- **Authentication & Security**
  - JWT-based authentication with refresh token rotation
  - Multi-factor authentication (TOTP, SMS, Email)
  - API key management with rate limiting
  - Session management with token hashing
  - Password policies and account lockout

- **Authorization**
  - Role-based access control (RBAC)
  - Fine-grained permissions system
  - Multi-tenant organization support

- **Audit & Compliance**
  - Comprehensive audit logging
  - Request/response tracking
  - Actor and IP address logging

- **Background Jobs**
  - Celery workers with Redis broker
  - Database-backed Beat scheduler
  - Recurring transaction automation

- **Observability**
  - Prometheus metrics
  - OpenTelemetry distributed tracing
  - Structured JSON logging

## Tech Stack

| Component | Technology |
|-----------|------------|
| Framework | FastAPI 0.111.0 |
| Database | PostgreSQL 16 |
| ORM | SQLAlchemy 2.0 |
| Migrations | Alembic |
| Cache/Broker | Redis 7 |
| Task Queue | Celery 5.4 |
| Auth | python-jose, passlib, pyotp |
| Tracing | OpenTelemetry |
| Metrics | Prometheus |
| Frontend | Jinja2, TailwindCSS |

## Project Structure

```
├── app/
│   ├── api/              # API route handlers
│   │   ├── finance/      # Finance module APIs (GL, AP, AR, etc.)
│   │   └── people/       # HR module APIs
│   ├── models/           # SQLAlchemy ORM models
│   │   ├── finance/      # Finance module models
│   │   └── people/       # HR module models
│   ├── schemas/          # Pydantic validation schemas
│   ├── services/         # Business logic layer
│   │   ├── finance/      # Finance module services
│   │   └── people/       # HR module services
│   ├── web/              # Web UI route handlers
│   │   ├── finance/      # Finance module web handlers
│   │   └── people/       # HR module web handlers
│   ├── main.py           # FastAPI app initialization
│   ├── config.py         # Application settings
│   ├── db.py             # Database configuration
│   ├── celery_app.py     # Celery configuration
│   └── telemetry.py      # OpenTelemetry setup
├── templates/            # Jinja2 HTML templates
│   ├── finance/          # Finance module templates
│   └── people/           # HR module templates
├── static/               # Static assets
├── alembic/              # Database migrations
├── scripts/              # Utility scripts
├── tests/                # Test suite
│   ├── e2e/              # End-to-end Playwright tests
│   └── ifrs/             # Finance module unit tests
├── docker-compose.yml    # Container orchestration
└── Dockerfile            # Container image
```

## Getting Started

### Documentation

- `docs/getting_started.md`
- `docs/development.md`
- `docs/architecture.md`
- `docs/deployment.md`
- `docs/troubleshooting.md`

### Prerequisites

- Python 3.11 or 3.12
- PostgreSQL 16
- Redis 7
- [Poetry](https://python-poetry.org/) (recommended) or pip

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/dotmac/dotmac-erp.git
   cd dotmac-erp
   ```

2. **Set up environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

3. **Install dependencies**
   ```bash
   # Using Poetry (recommended)
   poetry install

   # Or using pip
   pip install -r requirements.txt
   ```

### Running with Docker (Recommended)

The easiest way to run the application is with Docker Compose:

```bash
# Start all services
docker compose up -d

# View logs
docker compose logs -f app

# Stop all services
docker compose down
```

Services:
- **App**: http://localhost:8001
- **PostgreSQL**: localhost:5434
- **Redis**: localhost:6379

### Running Locally

1. **Start PostgreSQL and Redis** (or use Docker for just the databases)
   ```bash
   docker compose up -d db redis
   ```

2. **Run database migrations**
   ```bash
   alembic upgrade head
   ```

3. **Seed initial data**
   ```bash
   # Create E2E test user with admin role
   python scripts/setup_e2e_user.py

   # Or create custom admin user
   python scripts/seed_admin.py --email admin@example.com \
     --first-name Admin --last-name User \
     --username admin --password YourPassword123
   ```

4. **Start the application**
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
   ```

5. **Start Celery worker** (in a separate terminal)
   ```bash
   celery -A app.celery_app worker -l info
   ```

6. **Start Celery Beat scheduler** (in a separate terminal)
   ```bash
   celery -A app.celery_app beat -l info
   ```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql+psycopg://postgres:postgres@localhost:5434/dotmac_erp` |
| `REDIS_URL` | Redis connection string | `redis://:redis@localhost:6379/0` |
| `CELERY_BROKER_URL` | Celery broker URL | `redis://:redis@localhost:6379/0` |
| `CELERY_RESULT_BACKEND` | Celery result backend | `redis://:redis@localhost:6379/1` |
| `JWT_SECRET` | JWT signing secret | Required |
| `JWT_ALGORITHM` | JWT algorithm | `HS256` |
| `JWT_ACCESS_TTL_MINUTES` | Access token TTL | `15` |
| `JWT_REFRESH_TTL_DAYS` | Refresh token TTL | `30` |
| `TOTP_ISSUER` | TOTP issuer name | `dotmac_erp` |
| `TOTP_ENCRYPTION_KEY` | TOTP secret encryption key | Required |
| `BRAND_NAME` | Application brand name | `Dotmac ERP` |
| `BRAND_TAGLINE` | Application tagline | `Unified ERP for finance, HR, and operations` |
| `BRAND_MARK` | Two-letter brand mark override | - |
| `LANDING_HERO_BADGE` | Landing page hero badge text | `Dotmac ERP` |
| `LANDING_HERO_TITLE` | Landing page hero title | `Run your entire business on one ERP` |
| `LANDING_HERO_SUBTITLE` | Landing page hero subtitle | `Finance, HR, and operations with real-time reporting.` |
| `LANDING_CTA_PRIMARY` | Landing page primary CTA label | `Get started` |
| `LANDING_CTA_SECONDARY` | Landing page secondary CTA label | `Explore modules` |
| `LANDING_CONTENT_JSON` | Optional JSON to override landing page content | - |
| `OTEL_ENABLED` | Enable OpenTelemetry | `false` |
| `OTEL_SERVICE_NAME` | Service name for tracing | `dotmac_erp` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP collector endpoint | - |

### OpenBao Integration

Secrets can be resolved from OpenBao by using the `openbao://` prefix:

```bash
JWT_SECRET=openbao://secret/data/dotmac_erp#jwt_secret
```

## Default Credentials

For development and testing:

| Username | Password | Role |
|----------|----------|------|
| `admin` | `admin123` | admin |
| `e2e_testuser` | `e2e_testpassword123` | admin |

## Database Schemas

The application uses PostgreSQL schemas for domain separation:

| Schema | Description |
|--------|-------------|
| `public` | Core auth, RBAC, and platform tables |
| `gl` | General Ledger |
| `ap` | Accounts Payable |
| `ar` | Accounts Receivable |
| `inv` | Inventory |
| `banking` | Banking and reconciliation |
| `fa` | Fixed Assets |
| `tax` | Tax management |
| `exp` | Expenses |
| `lease` | Lease accounting |
| `fin_inst` | Financial instruments |
| `cons` | Consolidation |
| `core_org` | Organization management |
| `core_config` | Configuration |
| `automation` | Recurring templates and workflows |
| `rpt` | Reporting |
| `audit` | Audit logging |
| `platform` | Event sourcing |

## Testing

```bash
# Run all unit tests
pytest tests/ --ignore=tests/e2e/

# Run with coverage
pytest --cov=app --cov-report=html

# Run E2E tests (requires running server)
python scripts/setup_e2e_user.py
pytest tests/e2e/ -v

# Run integration tests (PostgreSQL required)
pytest -m integration tests/integration/ -p no:conftest
```

## Scripts

| Script | Description |
|--------|-------------|
| `scripts/seed_admin.py` | Create admin user |
| `scripts/setup_e2e_user.py` | Create E2E test user with admin role |
| `scripts/seed_rbac.py` | Initialize roles and permissions |
| `scripts/seed_nigeria.py` | Seed Nigerian tax configuration |

## License

Proprietary - All rights reserved.
