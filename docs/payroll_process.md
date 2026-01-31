Payroll Process Documentation
=============================

Overview
--------
This document describes the end-to-end payroll process in this system: how payroll runs are created, how slips are generated, how taxes and deductions are calculated, how approvals and posting work, and how monthly operations are simplified via clone/variance/adjustments.

Primary User Journeys
---------------------
1) Run-Based Payroll (recommended for monthly cycles)
   - Create payroll run
   - Generate slips
   - Review + apply adjustments
   - Submit → Approve → Post to GL
   - Payout (optional)

2) Individual Slip Workflow (ad-hoc)
   - Create slip
   - Submit → Approve → Post to GL
   - Payout (optional)

Roles & Permissions (typical)
-----------------------------
- Payroll Admin: creates runs, generates slips, applies adjustments, submits runs.
- Approver: approves runs/slips (segregation of duties enforced).
- Finance/Accounting: posts to GL.

Status Lifecycle
----------------
Payroll Runs (PayrollEntryStatus)
  DRAFT → SLIPS_CREATED → SUBMITTED → APPROVED → POSTED → (CANCELLED)

Salary Slips (SalarySlipStatus)
  DRAFT → SUBMITTED → APPROVED → POSTED → PAID → (CANCELLED)

Segregation of Duties (SoD)
- A user who creates a slip/run cannot approve that same slip/run.

Payroll Run Process (Detailed)
------------------------------
1) Create Run
   UI: Payroll Runs → New Payroll Run
   - Sets pay period, frequency, currency, and filters.
   - Creates a PayrollEntry in DRAFT.

2) Generate Slips
   UI: Run detail → Generate Salary Slips
   - Uses active Salary Structure Assignments during the period.
   - Generates SalarySlip records with earnings and deductions.
   - Updates run totals (gross, deductions, net, headcount).

3) Apply Bulk Adjustments (optional)
   UI: Run detail → Bulk Adjustments
   - Applies a one-time earning or deduction across all DRAFT slips in the run.
   - Updates slip totals and run totals.

4) Submit Run
   - Transitions run to SUBMITTED and all slips to SUBMITTED.
   - Emits RunSubmitted event.

5) Approve Run
   - Transitions run to APPROVED and all slips to APPROVED.
   - SoD enforced.
   - Emits RunApproved event.

6) Post to GL
   - Posts consolidated journal for the run.
   - Updates all slips to POSTED, run to POSTED.
   - Emits RunPosted event (no per-slip posted events).

7) Payout (optional)
   - Marks slips PAID and emits SlipPaid events.

Individual Slip Process (Detailed)
----------------------------------
1) Create Slip
   - Uses salary structure to compute earnings and deductions.
   - Uses tax calculator (PAYE) for statutory deductions.

2) Submit → Approve → Post
   - Each state transition is enforced by the lifecycle state machine.
   - Events emitted on transitions.

Tax Calculation (PAYE)
----------------------
Tax calculations follow NTA 2025 bands and use:
- Monthly Gross
- Monthly Basic
- Optional rent relief and statutory rates

Rounding Policy
---------------
All currency values are rounded using ROUND_HALF_UP to 2 decimal places:
- Tax calculator outputs monthly statutory deductions rounded to 2dp.
- Salary slip totals are rounded after summing line items (gross, deductions, net).
- Run totals are sums of rounded slip totals and rounded to 2dp.

UI: Payroll → PAYE Calculator
API: POST /tax/calculate

Sandbox Mode (pre-posting)
--------------------------
Admins can test monthly tax payable and net liability before posting:
UI: Payroll → PAYE Calculator
- Enter gross, basic, statutory rates, and "other deductions".
- Outputs net pay, total deductions, PAYE payable, employer cost, and total liability.

Monthly Task Simplification
---------------------------
1) Clone Previous Run
   UI: Run detail or Runs list → Clone Run
   - Prefills new run with previous period filters, currency, and frequency.
   - Automatically advances pay period.

2) Variance Report
   UI: Run detail or Runs list → Variance
   - Compares current run vs previous run for the same frequency/filters.
   - Flags new/removed employees and changes in gross/deductions/net.

3) Bulk Adjustments
   UI: Run detail or Runs list → Adjust
   - Apply a one-time component to all draft slips.
   - Use for bonuses, penalties, one-off deductions.

Events and Notifications
------------------------
Payroll events are emitted after transitions:
- SlipSubmitted, SlipApproved, SlipPosted, SlipPaid, SlipCancelled, SlipRejected
- RunSubmitted, RunApproved, RunPosted, RunCancelled

Event handlers are used for side effects (e.g., notifications).

GL Posting
----------
GL posting is unified via PayrollLifecycle:
- post_slip_to_gl (single slip)
- post_run_to_gl (consolidated run)

Posting validates:
- Approved status
- For run: all slips approved
- Single currency/exchange rate per run

Data Sources & Inputs
---------------------
- Salary Structures and Assignments
- Employee tax profiles
- Statutory components (PAYE, pension, NHF, NHIS)
- Employee status (ACTIVE/ON_LEAVE)

Operational Checklist (Monthly)
-------------------------------
1) Clone previous run
2) Review assignments and employee changes
3) Generate slips
4) Review variance report
5) Apply bulk adjustments (if needed)
6) Submit run
7) Approve run
8) Post to GL
9) Send payslips (email) and confirm notifications
10) Payout (optional)

YTD Reporting
-------------
UI: Payroll → Reports → YTD Report
- Uses posted/paid slips only
- Breaks down totals per employee, including PAYE, pension, NHF, NHIS
- Supports custom date range filters (optional)

Payslip Email Dispatch
----------------------
- Runs can queue payslip emails after posting.
- If needed, emails can be re-queued (Resend Payslips) with dedupe safeguards.
- Run detail shows email progress and polls for updates while running.

Common Error Messages
---------------------
- "Salary slips already created for this entry"
- "All slips must be DRAFT to submit/approve"
- "Segregation of duties: creator cannot approve"
- "Mixed currency or exchange rate in payroll run"

Support / Escalation
--------------------
If payroll totals or deductions look incorrect:
1) Verify salary structure assignments
2) Verify tax profile overrides
3) Use PAYE calculator sandbox to validate expected values
4) Review slip details for deduction components and rates
