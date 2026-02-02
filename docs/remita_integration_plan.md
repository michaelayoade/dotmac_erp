# Remita Integration Plan

## Overview

Generic, reusable Remita service for generating RRR (Remita Retrieval Reference) and tracking payments for any government-related transactions:

- Payroll: PAYE, NHF, Pension, NSITF
- Taxes: Company Income Tax, Stamp Duty, WHT, VAT
- Procurement: Bid documents, licenses, permits
- Fees: Regulatory fees, registrations, renewals

---

## API Details

### Endpoints

| Environment | Base URL |
|-------------|----------|
| Demo | `https://demo.remita.net` |
| Live | `https://login.remita.net` |

**Generate RRR:**
```
POST /remita/exapp/api/v1/send/api/echannelsvc/merchant/api/paymentinit
```

**Check Status:**
```
GET /remita/exapp/api/v1/send/api/echannelsvc/{merchantId}/{rrr}/{apiHash}/status.reg
```

### Authentication

**Hash for RRR Generation:**
```python
api_hash = sha512(merchantId + serviceTypeId + orderId + amount + apiKey)
```

**Hash for Status Check:**
```python
api_hash = sha512(rrr + apiKey + merchantId)
```

**Authorization Header:**
```
Authorization: remitaConsumerKey={merchantId},remitaConsumerToken={apiHash}
```

### Request Payload (Generate RRR)

```json
{
  "serviceTypeId": "4430731",
  "amount": "20000",
  "orderId": "unique_order_id",
  "payerName": "Company Name",
  "payerEmail": "email@company.com",
  "payerPhone": "08012345678",
  "description": "Payment description"
}
```

### Demo Credentials

```python
merchantId = "2547916"
apiKey = "1946"
serviceTypeId = "4430731"
```

### Response Format

Response is JSONP-wrapped:
```
jsonp({"statuscode":"025","RRR":"310007769676","status":"Payment Reference generated"})
```

Extract JSON by stripping `jsonp(` prefix and `)` suffix.

---

## Architecture

### Directory Structure

```
app/
├── models/
│   └── remita/
│       ├── __init__.py
│       └── rrr.py              # RemitaRRR model
│
├── services/
│   └── remita/
│       ├── __init__.py
│       ├── client.py           # API client (auth, HTTP)
│       ├── rrr_service.py      # Generate, verify, list RRRs
│       └── web/
│           └── remita_web.py   # Web UI routes
│
└── templates/
    └── remita/
        ├── index.html          # RRR list/dashboard
        ├── generate.html       # Generate RRR form
        └── detail.html         # RRR detail & payment status
```

### Data Model

```python
class RemitaRRR(Base):
    __tablename__ = "remita_rrr"
    __table_args__ = {"schema": "public"}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("core_org.organization.organization_id"))

    # The RRR itself
    rrr: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))

    # Payer info
    payer_name: Mapped[str] = mapped_column(String(200))
    payer_email: Mapped[str] = mapped_column(String(255))
    payer_phone: Mapped[Optional[str]] = mapped_column(String(20))

    # Remita biller/service
    biller_id: Mapped[str] = mapped_column(String(50))       # "FIRS", "FMBN", "BPP"
    biller_name: Mapped[str] = mapped_column(String(200))    # "Federal Inland Revenue Service"
    service_type_id: Mapped[str] = mapped_column(String(50)) # Remita service code
    service_name: Mapped[str] = mapped_column(String(200))   # "PAYE Tax"

    description: Mapped[str] = mapped_column(Text)

    # Generic source linking (caller decides what to put)
    source_type: Mapped[Optional[str]] = mapped_column(String(50))  # "payroll_paye", "stamp_duty", etc.
    source_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))

    # Status
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, paid, expired, failed

    # Timestamps
    generated_at: Mapped[datetime] = mapped_column(default=func.now())
    expires_at: Mapped[Optional[datetime]] = mapped_column()
    paid_at: Mapped[Optional[datetime]] = mapped_column()

    # Payment info
    payment_reference: Mapped[Optional[str]] = mapped_column(String(100))
    payment_channel: Mapped[Optional[str]] = mapped_column(String(50))

    # API response (for debugging)
    api_response: Mapped[Optional[dict]] = mapped_column(JSONB)

    # Audit
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("people.id"))
    created_at: Mapped[datetime] = mapped_column(default=func.now())
```

### API Client

```python
class RemitaClient:
    DEMO_URL = "https://demo.remita.net"
    LIVE_URL = "https://login.remita.net"

    RRR_ENDPOINT = "/remita/exapp/api/v1/send/api/echannelsvc/merchant/api/paymentinit"
    STATUS_ENDPOINT = "/remita/exapp/api/v1/send/api/echannelsvc/{merchant_id}/{rrr}/{api_hash}/status.reg"

    def __init__(self, merchant_id: str, api_key: str, is_live: bool = False):
        self.merchant_id = merchant_id
        self.api_key = api_key
        self.base_url = self.LIVE_URL if is_live else self.DEMO_URL

    def _generate_hash(self, *args) -> str:
        """SHA512 hash of concatenated args."""
        return hashlib.sha512("".join(str(a) for a in args).encode()).hexdigest()

    def _auth_header(self, api_hash: str) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"remitaConsumerKey={self.merchant_id},remitaConsumerToken={api_hash}"
        }

    def _parse_response(self, response_text: str) -> dict:
        """Parse JSONP response: jsonp({...}) -> {...}"""
        if response_text.startswith("jsonp("):
            json_str = response_text[6:-1]  # Strip jsonp( and )
            return json.loads(json_str)
        return json.loads(response_text)

    def generate_rrr(
        self,
        service_type_id: str,
        amount: Decimal,
        order_id: str,
        payer_name: str,
        payer_email: str,
        payer_phone: Optional[str] = None,
        description: str = "",
    ) -> dict:
        """Generate RRR via Remita API."""
        api_hash = self._generate_hash(
            self.merchant_id,
            service_type_id,
            order_id,
            str(amount),
            self.api_key
        )

        payload = {
            "serviceTypeId": service_type_id,
            "amount": str(amount),
            "orderId": order_id,
            "payerName": payer_name,
            "payerEmail": payer_email,
            "payerPhone": payer_phone or "",
            "description": description,
        }

        response = requests.post(
            f"{self.base_url}{self.RRR_ENDPOINT}",
            headers=self._auth_header(api_hash),
            json=payload,
        )

        return self._parse_response(response.text)

    def check_status(self, rrr: str) -> dict:
        """Check RRR payment status."""
        api_hash = self._generate_hash(rrr, self.api_key, self.merchant_id)

        url = f"{self.base_url}{self.STATUS_ENDPOINT}".format(
            merchant_id=self.merchant_id,
            rrr=rrr,
            api_hash=api_hash,
        )

        response = requests.get(url, headers=self._auth_header(api_hash))
        return self._parse_response(response.text)
```

### Service Layer

```python
class RemitaRRRService:
    """Generic RRR service - used by all modules."""

    def __init__(self, db: Session):
        self.db = db
        self.client = RemitaClient(
            merchant_id=settings.REMITA_MERCHANT_ID,
            api_key=settings.REMITA_API_KEY,
            is_live=settings.REMITA_IS_LIVE,
        )

    def generate_rrr(
        self,
        organization_id: UUID,
        biller_id: str,
        biller_name: str,
        service_type_id: str,
        service_name: str,
        amount: Decimal,
        payer_name: str,
        payer_email: str,
        payer_phone: Optional[str] = None,
        description: str = "",
        source_type: Optional[str] = None,
        source_id: Optional[UUID] = None,
        created_by_id: UUID = None,
    ) -> RemitaRRR:
        """Generate RRR via Remita API and save to DB."""

        # Validate
        if amount <= 0:
            raise ValueError("Amount must be positive")

        # Generate unique order ID
        order_id = f"{organization_id}-{uuid.uuid4()}"

        # Call Remita API
        response = self.client.generate_rrr(
            service_type_id=service_type_id,
            amount=amount,
            order_id=order_id,
            payer_name=payer_name,
            payer_email=payer_email,
            payer_phone=payer_phone,
            description=description,
        )

        if response.get("statuscode") != "025":
            raise RemitaAPIError(response.get("status", "RRR generation failed"))

        # Save to DB
        rrr_record = RemitaRRR(
            organization_id=organization_id,
            rrr=response["RRR"],
            amount=amount,
            payer_name=payer_name,
            payer_email=payer_email,
            payer_phone=payer_phone,
            biller_id=biller_id,
            biller_name=biller_name,
            service_type_id=service_type_id,
            service_name=service_name,
            description=description,
            source_type=source_type,
            source_id=source_id,
            status="pending",
            api_response=response,
            created_by_id=created_by_id,
        )

        self.db.add(rrr_record)
        self.db.flush()

        return rrr_record

    def check_status(self, rrr_id: UUID) -> RemitaRRR:
        """Check and update RRR payment status."""
        rrr_record = self.db.get(RemitaRRR, rrr_id)
        if not rrr_record:
            raise NotFoundError(f"RRR {rrr_id} not found")

        response = self.client.check_status(rrr_record.rrr)

        # Update status based on response
        # (status codes TBD from Remita docs)

        return rrr_record

    def mark_paid(
        self,
        rrr_id: UUID,
        payment_reference: str,
        payment_channel: str = "Bank",
    ) -> RemitaRRR:
        """Manually mark RRR as paid."""
        rrr_record = self.db.get(RemitaRRR, rrr_id)
        rrr_record.status = "paid"
        rrr_record.paid_at = datetime.now(timezone.utc)
        rrr_record.payment_reference = payment_reference
        rrr_record.payment_channel = payment_channel
        return rrr_record

    def list_rrrs(
        self,
        organization_id: UUID,
        status: Optional[str] = None,
        source_type: Optional[str] = None,
        biller_id: Optional[str] = None,
    ) -> List[RemitaRRR]:
        """List RRRs with filters."""
        stmt = select(RemitaRRR).where(
            RemitaRRR.organization_id == organization_id
        )

        if status:
            stmt = stmt.where(RemitaRRR.status == status)
        if source_type:
            stmt = stmt.where(RemitaRRR.source_type == source_type)
        if biller_id:
            stmt = stmt.where(RemitaRRR.biller_id == biller_id)

        stmt = stmt.order_by(RemitaRRR.created_at.desc())

        return list(self.db.scalars(stmt).all())
```

---

## Usage Examples

### From Payroll Module

```python
rrr = remita_service.generate_rrr(
    organization_id=org.id,
    biller_id="FIRS",
    biller_name="Federal Inland Revenue Service",
    service_type_id="4012",
    service_name="PAYE Tax",
    amount=payroll_run.total_paye,
    payer_name=org.legal_name,
    payer_email=org.finance_email,
    description=f"PAYE Tax - {payroll_run.period_name}",
    source_type="payroll_paye",
    source_id=payroll_run.id,
    created_by_id=current_user.id,
)
```

### From Finance (Stamp Duty)

```python
rrr = remita_service.generate_rrr(
    organization_id=org.id,
    biller_id="FIRS",
    biller_name="Federal Inland Revenue Service",
    service_type_id="STAMP_DUTY",
    service_name="Stamp Duty",
    amount=Decimal("15000"),
    payer_name=org.legal_name,
    payer_email=org.finance_email,
    description="Stamp Duty - Share Transfer Agreement",
    source_type="stamp_duty",
    source_id=document_id,
    created_by_id=current_user.id,
)
```

### Generic (Bid Document)

```python
rrr = remita_service.generate_rrr(
    organization_id=org.id,
    biller_id="BPP",
    biller_name="Bureau of Public Procurement",
    service_type_id="BID_FEE",
    service_name="Bid Document Fee",
    amount=Decimal("50000"),
    payer_name=org.legal_name,
    payer_email=org.procurement_email,
    description="Bid Document - Ministry of Works Contract",
    created_by_id=current_user.id,
    # No source linking - standalone
)
```

---

## Configuration

Add to `app/config.py`:

```python
# Remita Integration
REMITA_MERCHANT_ID: str = ""
REMITA_API_KEY: str = ""
REMITA_IS_LIVE: bool = False  # True for production
```

Add to `.env`:

```
REMITA_MERCHANT_ID=2547916
REMITA_API_KEY=1946
REMITA_IS_LIVE=false
```

---

## UI Location

Finance → Remita (or Finance → Payments → Remita)

---

## Sources

- [Remita Python SDK](https://github.com/RemitaNet/remita-rrr-generator-status-python)
- [Remita Demo Credentials](https://github.com/RemitaNet/remita-demo-credentials)
- [Remita Billing Gateway SDK](https://github.com/RemitaNet/billing-gateway-sdk-php)
- [CodeFlare Integration Guide](https://codeflarelimited.com/blog/remita-payment-integration-how-to-generate-rrr-and-check-transaction-status-in-react-js/)
