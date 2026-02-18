# Approval Pack: Soft-Closed Period Blocking AR Posting

Generated: 2026-02-18 (UTC)
Organization: `00000000-0000-0000-0000-000000000001`

## Current Blocker Snapshot
- Blocked APPROVED invoices: **845**
- Blocked amount: **27,804,708.10**
- Month split:
  - `2026-01`: 345 invoices, 10,587,664.54
  - `2026-02`: 500 invoices, 17,217,043.56
- Representative error: `Ledger posting failed: 400: Period 'test' is soft-closed; requires approval to post`

## Evidence of Root Cause
There is an overlapping fiscal period configuration for 2026:
- `January 2026` (`OPEN`, 2026-01-01..2026-01-31)
- `February 2026` (`OPEN`, 2026-02-01..2026-02-28)
- `test` (`SOFT_CLOSED`, 2026-01-01..2026-12-31)

Because `test` overlaps monthly open periods and is soft-closed, many invoice postings resolve to this blocked period.

## Finance Approvals Requested
1. **Temporary posting window approval**
   - Approve temporary reopening of period `test` only for backlog posting run.
2. **Period governance approval**
   - Approve removal or date narrowing of period `test` after backlog is posted, so monthly periods remain the only active posting periods for 2026.

## Attachment
- Full invoice list for sign-off:
  - `/root/dotmac/docs/ops/approved_invoices_blocked_2026-02-18.csv`
  - Rows: 845 invoices + header (846 lines)

## Controlled Execution Plan (post-approval)
1. Reopen `test` period temporarily.
2. Run `auto_post_approved_invoices` in batches (250) until no progress.
3. Re-close or remove/narrow `test` period per approved governance action.
4. Verify end state:
   - `APPROVED` backlog reduced to expected residual exceptions (if any)
   - no new unbalanced journals
   - no false-paid invoices

## Rollback
If posting run shows unexpected behavior:
- stop batch execution;
- restore period status to `SOFT_CLOSED`;
- investigate failed invoices from latest batch only.
