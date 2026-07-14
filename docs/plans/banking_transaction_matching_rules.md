# Banking Transaction Matching Rules

This document defines the proposed matching rules for Banking -> Transactions.
It is a review artifact only; implementation should follow in a separate change.

## Global Rules

- Auto-match only when confidence is 95% or higher.
- Match only on exact amount.
- Match only within +/- 3 days of the expected transaction date.
- Use +/- 1 day only for internal transfers.
- Do not auto-match if more than one possible match exists.
- Do not auto-create journal entries during matching.
- Do not match to suspense, opening balance, discrepancy, or uncategorized accounts automatically.
- Low-confidence, incomplete, or ambiguous matches must remain unmatched for manual review.

## Customer Payments

Auto-match customer receipts only when all apply:

- Exact amount matches.
- Date is within +/- 3 days.
- Bank narration or reference matches a customer, invoice number, sales order, receipt reference, or approved deposit reference.
- The target account is one of:
  - Customer Deposits
  - Trade Receivables / Customer Ledger
  - Sales / Internet Revenue
  - Customer Advance Account
  - Unearned Revenue

Do not match customer payments using narration alone if the customer or reference is unclear.

## Expense Payments

Auto-match expense payments only when all apply:

- Exact amount matches.
- Vendor or payee matches an approved supplier, expense claim, bill, or payment reference.
- Date is within +/- 3 days.
- Expense category or account is already known from an approved document or vendor rule.

Do not match expenses based only on narration text.

## Employee Reimbursements

Auto-match only when all apply:

- Exact amount matches.
- Employee name, employee ID, or reimbursement reference matches.
- Related expense claim or reimbursement record exists.
- Date is within +/- 3 days.

Do not match employee reimbursements to salaries, payroll deductions, vendor bills, or generic expense accounts.

## Tax Payments

Auto-match only when all apply:

- Exact amount matches.
- Tax type is clearly identified, for example:
  - VAT
  - PAYE
  - WHT
  - NHF
  - Pension
  - NSITF
  - ITF
- Period or payment reference matches where available.
- Date is within +/- 3 days.

If the tax type or period is unclear, leave the transaction for manual review.

## Internal Transfers

Auto-match only when all apply:

- Both sides are company-owned bank, wallet, or cash accounts.
- Exact amount matches.
- Dates are within +/- 1 day.
- Direction is opposite: debit in one account and credit in another.
- Currency matches, unless an approved FX transfer record exists.

Do not match transfers to customer, vendor, payroll, tax, or suspense accounts.

## Manual Review Required

Always require manual review for:

- Reconciliation Discrepancies
- Temporary Opening Account
- Accrued Expenses
- Opening Balance Adjustments
- Suspense or clearing accounts without approved source documents
- Uncategorized Accounts
- Multiple possible matches
- Partial payments
- Split payments
- Fees deducted from receipts
- FX differences
- Chargebacks or reversals
- Transactions outside the allowed date window

## Confidence Scoring

Suggested scoring:

- Exact amount match: 40%
- Date within allowed window: 20%
- Strong reference match: 25%
- Counterparty, customer, vendor, or employee match: 10%
- Account/type compatibility: 5%

Auto-match only when the total score is 95% or higher and there is exactly one candidate.

## Implementation Notes

- Matching should be deterministic and auditable.
- Store the score components and reason codes used to produce each candidate match.
- Keep rejected candidates available for diagnostics, but do not expose noisy candidates in the primary reconciliation UI.
- Future implementation should preserve tenant scoping and existing Banking permissions.
