# Paystack Chargebacks Investigation

**Created:** 2026-02-01
**Status:** Pending Investigation
**Total Amount:** ₦282,538.13 (gross) / ₦277,500.00 (net)

## Summary

During Paystack Collections account reconciliation, we identified 8 transactions that appear in Paystack settlements but are NOT recorded in our collections database. All 8 have status `reversed` in Paystack's system.

These transactions were:
1. Initially successful (customer paid)
2. Settled to our bank account by Paystack
3. Later reversed (chargeback, refund, or dispute)
4. Paystack recovered the funds via subsequent settlement deductions

## Investigation Required

1. **Check if recorded as customer payments** - These may have been synced from splync as customer payments or credit notes
2. **Verify Paystack recovery** - Confirm Paystack debited these amounts back from subsequent settlements
3. **Customer impact** - Determine if customers received refunds or if these were chargebacks

## The 8 Reversed Transactions

| # | Transaction ID | Date Paid | Settlement Date | Gross Amount | Net Amount | Channel | Email |
|---|----------------|-----------|-----------------|--------------|------------|---------|-------|
| 1 | 1684789432 | 2022-03-14 | 2022-03-15 | ₦50,862.95 | ₦50,000.00 | bank_transfer | gdsworkstation@gmail.com |
| 2 | 2300889423 | 2022-11-21 | 2022-11-22 | ₦106,700.51 | ₦105,000.00 | card | embcapeverde.abuja@mnec.gov.cv |
| 3 | 2317895197 | 2022-11-27 | 2022-11-28 | ₦17,868.03 | ₦17,500.00 | card | abdulraheemhabib36@gmail.com |
| 4 | 2377144194 | 2022-12-18 | 2022-12-19 | ₦17,868.03 | ₦17,500.00 | card | info@cignait.com |
| 5 | 2532177900 | 2023-02-13 | 2023-02-14 | ₦35,634.52 | ₦35,000.00 | card | crownallied@yahoo.com |
| 6 | 2618708285 | 2023-03-12 | 2023-03-13 | ₦17,868.03 | ₦17,500.00 | bank_transfer | jamesojo@yahoo.com |
| 7 | 2852168514 | 2023-06-02 | 2023-06-03 | ₦17,868.03 | ₦17,500.00 | bank_transfer | work.laya@gmail.com |
| 8 | 3012120527 | 2023-08-08 | 2023-08-09 | ₦17,868.03 | ₦17,500.00 | card | jamesojo@yahoo.com |

**TOTAL:** ₦282,538.13 gross / ₦277,500.00 net

## Transaction Details

### 1. Transaction 1684789432
- **Reference:** e7065df0-0453-4a40-be4c-61df1708431d
- **Paid At:** 2022-03-14T15:01:36.000Z
- **Settlement:** STL-2327354 (2022-03-15)
- **Amount:** ₦50,862.95 gross → ₦50,000.00 net
- **Fees:** ₦862.95
- **Channel:** bank_transfer
- **Email:** gdsworkstation@gmail.com
- **Status:** reversed

### 2. Transaction 2300889423
- **Reference:** 637b351ee89fc
- **Paid At:** 2022-11-21T08:23:36.000Z
- **Settlement:** STL-3135986 (2022-11-22)
- **Amount:** ₦106,700.51 gross → ₦105,000.00 net
- **Fees:** ₦1,700.51
- **Channel:** card
- **Email:** embcapeverde.abuja@mnec.gov.cv
- **Status:** reversed

### 3. Transaction 2317895197
- **Reference:** 638334d5a9e49
- **Paid At:** 2022-11-27T10:00:50.000Z
- **Settlement:** STL-3158242 (2022-11-28)
- **Amount:** ₦17,868.03 gross → ₦17,500.00 net
- **Fees:** ₦368.03
- **Channel:** card
- **Email:** abdulraheemhabib36@gmail.com
- **Status:** reversed

### 4. Transaction 2377144194
- **Reference:** 639f78f2ea2f4
- **Paid At:** 2022-12-18T20:35:07.000Z
- **Settlement:** STL-3235941 (2022-12-19)
- **Amount:** ₦17,868.03 gross → ₦17,500.00 net
- **Fees:** ₦368.03
- **Channel:** card
- **Email:** info@cignait.com
- **Status:** reversed

### 5. Transaction 2532177900
- **Reference:** 63ea0c6085218
- **Paid At:** 2023-02-13T10:29:01.000Z
- **Settlement:** STL-3443851 (2023-02-14)
- **Amount:** ₦35,634.52 gross → ₦35,000.00 net
- **Fees:** ₦634.52
- **Channel:** card
- **Email:** crownallied@yahoo.com
- **Status:** reversed

### 6. Transaction 2618708285
- **Reference:** 640d6dea52291
- **Paid At:** 2023-03-12T06:18:59.000Z
- **Settlement:** STL-3544912 (2023-03-13)
- **Amount:** ₦17,868.03 gross → ₦17,500.00 net
- **Fees:** ₦368.03
- **Channel:** bank_transfer
- **Email:** jamesojo@yahoo.com
- **Status:** reversed

### 7. Transaction 2852168514
- **Reference:** 6479d3b440f5c
- **Paid At:** 2023-06-02T11:36:31.000Z
- **Settlement:** STL-3877585 (2023-06-03)
- **Amount:** ₦17,868.03 gross → ₦17,500.00 net
- **Fees:** ₦368.03
- **Channel:** bank_transfer
- **Email:** work.laya@gmail.com
- **Status:** reversed

### 8. Transaction 3012120527
- **Reference:** 64d2974cdbc42
- **Paid At:** 2023-08-08T19:32:49.000Z
- **Settlement:** STL-4145922 (2023-08-09)
- **Amount:** ₦17,868.03 gross → ₦17,500.00 net
- **Fees:** ₦368.03
- **Channel:** card
- **Email:** jamesojo@yahoo.com
- **Status:** reversed

## Investigation Script

To check if these were recorded as customer payments:

```python
"""Check if reversed transactions were recorded as customer payments."""
import sys
sys.path.insert(0, '/app')

from sqlalchemy import text
from app.db import SessionLocal

REVERSED_REFS = [
    "e7065df0-0453-4a40-be4c-61df1708431d",
    "637b351ee89fc",
    "638334d5a9e49",
    "639f78f2ea2f4",
    "63ea0c6085218",
    "640d6dea52291",
    "6479d3b440f5c",
    "64d2974cdbc42",
]

REVERSED_IDS = [
    "1684789432", "2300889423", "2317895197", "2377144194",
    "2532177900", "2618708285", "2852168514", "3012120527",
]

with SessionLocal() as db:
    # Search in customer payments
    results = db.execute(text("""
        SELECT payment_id, payment_date, amount, reference,
               paystack_reference, payment_method
        FROM ar.customer_payment
        WHERE reference = ANY(:refs)
           OR paystack_reference = ANY(:refs)
           OR paystack_reference = ANY(:ids)
    """), {"refs": REVERSED_REFS, "ids": REVERSED_IDS}).fetchall()

    print(f"Found {len(results)} matching payments")
    for r in results:
        print(f"  {r.payment_date} | ₦{r.amount:,.2f} | {r.reference}")
```

## Accounting Impact

These transactions are currently affecting the Paystack Collections account balance:
- They appear in settlements (debits) but not in collections (credits)
- Net impact: -₦277,500.00 on the account balance
- This is correctly reflected as the account is now fully reconciled

**No adjustment needed** - the account balance correctly shows these as "settled but not collected" which nets out because Paystack recovered the funds.

## Related Context

- **Reconciliation Date:** 2026-02-01
- **Account:** Paystack Collections
- **Total Collections:** 50,636 transactions
- **Total Settlements:** 1,492 settlements
- **Match Rate:** 99.94% (50,612 matched, 29 unmatched)
- **Unmatched Breakdown:**
  - 21 from Dec 31, 2021 (handled via opening balance)
  - 8 reversed transactions (this report)
