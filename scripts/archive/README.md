# Retired one-off scripts

Everything here **has already run**. It is kept for provenance — so a future
question about how a balance, a VAT return or a set of journals came to look
the way it does has an answer — and is not intended to be run again.

Nothing in this directory is executed by the application, a task, a cron, CI
or a deploy. `scripts/check_session_context.py` skips it for exactly that
reason: an archived script is a record, not an entry point, so the
tenant-context priming rules that govern live entry points do not apply.

## Why these were moved

`scripts/` had been excluded from semgrep, pre-commit and ruff on the premise
that it holds "one-off maintenance scripts". The premise was true of files
like these and false of the tree as a whole, and 100 scripts opening unscoped
database sessions accumulated behind the exemption. Separating the genuine
one-offs from the operational scripts is what makes the exemption honest
again — and it removed the larger part of the unscoped surface without
changing a line of logic.

A script was moved here only if all four held:

1. its name describes a past event (a date, `fix_`, `backfill_`, `dedup_`,
   `cleanup_`, a numbered `phase`, a named period like `jan_2026`);
2. it was committed once and never revisited;
3. nothing outside this set refers to it — no Makefile target, runbook, CI
   job, doc or other script; and
4. it depends on nothing that stayed behind.

A second wave then retired the **repair verbs** (`rebuild_`, `repair_`,
`resync_`, `reopen_`, `remediate_`, `reclassify_`, `void_`, `review_`,
`match_`). Those deliberately failed criterion 1 the first time round:
whether such a script is finished or still part of the operational routine is
operational knowledge, not something a naming convention can settle. Michael
ruled on 2026-08-10 that all fifteen were spent, which also unblocked
`finish_interbank_matching` and `fix_splynx_credit_note_signs` — each had
been held back only because it depended on, or was referenced by, one of
them.

`migration/2026-04-30_backfill_deferred_vat.py` is still live: it is cited by
`docs/deferred_vat_rollout_runbook_2026-04-29.md` and its release note, so
criterion 3 genuinely fails.

A third wave retired `sync_salary_assignments.py` while converting the
recurring scripts. Its NAME says `sync_`, which is why it was first
classified as a recurring operation — but its docstring says "January 2026",
it reads `EXCEL_PATH = Path("/root/.dotmac/jan paye (2) (1).xlsx")`, and it
takes no path argument. An absolute path into one person's home directory,
pointing at a file whose name records that it was downloaded twice, is not a
recurring operation however it is named. Building a scheduled task around it
would have been the wrong answer to the right question.

That the repair verbs needed a human ruling is the point, not a wrinkle. A
naming convention can recognise a spent one-off; only the person running the
month-end close knows whether a `rebuild_` script is still part of it.

## Import paths here are stale, deliberately

Archived code was moved, not rewritten. `clean_sweep/` still does
`from scripts.clean_sweep.config import ...`, which no longer resolves now
that the package lives under `scripts/archive/`. That is left alone on
purpose: these files are evidence of what ran, and editing them to keep them
runnable would both falsify that record and undercut the point of retiring
them. If one ever genuinely needs to run again, that is a decision to make
explicitly — move it back out, fix its imports, and give it a scoped session.

## Adding to this directory

`git mv` the script here once it has served its purpose, and delete its line
from `scripts/session_context_legacy.txt`. The guard requires both: an entry
that is no longer scanned fails the build until it is removed, so retirement
is recorded rather than assumed.

## A note on what this directory reveals

Several files here are repeat visits to one problem — `fix_paystack_consolidation_v2`,
`match_jan_2026_phase2` beside `match_jan_2026_remaining`, `match_2025_remaining_ngn`
beside `match_2025_unmatched_ngn`, four separate `fix_source_*` passes. A `v2`
means the first repair did not hold; a `_remaining` means a pass was partial
and someone finished it by hand.

That pattern is not an argument for better scripts or for running them on a
schedule. It says the matching and reconciliation logic is not idempotent and
does not repair its own drift, which is what a reconciler is for. Treat this
directory as a defect list for that work, not as a library to copy from.

## Splynx and ERPNext (2026-08-10)

Michael ruled that both integrations are retired. Six scripts moved here in
one wave: `sync_splynx`, `allocate_splynx_fifo`, `match_zenith_splynx`,
`reconcile_jan_2026_splynx`, `match_erpnext_banking_links` and
`relink_ticket_projects`.

Two scripts that MENTION them stayed live, because a mention is not a
dependency: `allocate_exact_match_payments` only cross-referenced the FIFO
script in its docstring, and `post_and_match_paystack_opex_expense_reimbursements`
is a Paystack operation that happens to describe historical ERPNext-synced
claims.

`app/services/finance/ar/fifo_allocation_service.py` is now unreferenced by
any script, task or service. It is deliberately NOT deleted here — removing
application code is a separate decision from retiring the scripts that drove
it — but note that retirement does more than orphan it. Its docstring records
the design premise: it creates allocation records *without* modifying
`invoice.amount_paid` or `invoice.status`, "because Splynx sync owns those
fields". Once Splynx is not the owner, that premise inverts and the service
is wrong rather than merely unused.
