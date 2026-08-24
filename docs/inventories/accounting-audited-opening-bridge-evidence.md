# Audited-opening bridge evidence

Date: 2026-08-24

Scope: Gate D memorandum §1a; Dotmac production organization
`00000000-0000-0000-0000-000000000001`.

## Decision

**The §1a gate is not complete. Do not post the composite-reversal correction.**

The production host contains physically signed 2024 audited financial statements
and the workbook used by the earlier opening-balance analysis. Those sources
establish that `OB-000001` originally carried the audited AR and AP control
balances. A later raw-SQL cleanup deleted those lines and rewrote retained
earnings. The surviving customer/supplier opening journals are GL-only records,
not linked AR/AP subledger documents, and their totals do not tie to the audited
controls.

The evidence narrows the problem, but it does not supply the signed trial balance,
customer and supplier opening ageings, WHT schedules or an approved opening
workpaper needed to choose the replacement representation safely.

No accounting data was changed while producing this schedule.

## Controlled source inventory

Source files remain on `erp.dotmac.io`; they are not copied into Git.

| source | integrity / form | conclusion |
| --- | --- | --- |
| `/root/DOTMAC-AUDITED ACCOUNTS-2024.pdf` | SHA-256 `b62c30d147b617875eea41f16c59d650cb0a38ed5b491edefeacfb587c464676`; 21-page scan; auditor stamp/signature and two director signatures are visible; no PDF cryptographic signature | signed 2024 financial statements, not a signed trial balance |
| `/root/2024 TB.xlsx` | SHA-256 `751547f16118dc29b9c64bb4f7b19ac5e037023c6001d31d2e62a283885e70bf`; one sheet, `TB`, 185 rows × 17 columns; no Office digital-signature package | unsigned working TB; formulas reproduce the detailed amounts below |
| Git commit `b6488e2c18ec0950edf526fc83d909898be16aaf` | deleted historical workpaper `docs/2024_tb_to_2025_opening_bridge.md` | contemporaneous, unsigned reconstruction of the workbook-to-OB load |
| Git commit `922bc5d560330cc910aaee62591990a06a8701c1` | cleanup workpapers and `scripts/migration/2026-04-28_opening_balance_cleanup_{dry_run,apply}.sql` | exact specification of the later destructive cleanup |
| production GL, queried read-only on 2026-08-24 | host-local primary; `pg_is_in_recovery() = false` | proves the cleanup effects persist |

The signed PDF has one source-quality discrepancy that Finance/auditor must
clarify: its heading, statements and notes are for 2024, but the independent
auditor's opinion paragraph says “as at December 31, 2021”. The file is therefore
useful signed evidence, but not an unambiguous substitute for the missing signed
2024 trial balance.

The application attachment catalog contains no filename or description matching
`trial`, `TB`, `opening`, `audit`, `WHT`, `ageing`/`aging`, or `retained`. A bounded
production filesystem search found no customer/supplier opening ageing, WHT
opening schedule or retained-earnings workpaper. The 2026-03-14
`reports/{ar,ap}_subledger_reconciliation.csv` files are system-generated
reconciliations, not signed opening schedules.

## Signed close to unsigned TB

| item | signed statements (rounded naira) | workbook formula value | result |
| --- | ---: | ---: | --- |
| Trade Receivables | 20,591,053 | 20,591,053.354 | ties on rounding |
| WHT receivable | 68,308,470 | 68,308,470.023 | ties on rounding |
| Trade Payables | 40,310,714 | 40,310,713.500 | ties on rounding |
| WHT payable | 3,136,500 | workbook adjustment note states 3,136,500 | ties on rounding |
| Tax Audit Liability | 7,587,459 | 7,587,458.53 | ties on rounding |
| Accrued Expenses | 600,000 | 599,999.52 | ties on rounding |

The retained-earnings bridge is not the ₦1.42 rounding conclusion in the deleted
2026-04-28 workpaper. That workpaper stopped at profit/loss **before tax**:

| retained-earnings component | amount |
| --- | ---: |
| opening retained earnings | 6,734,260.89 credit |
| workbook / signed profit-or-loss before tax | 53,234,029.06 loss / 53,234,030 rounded |
| signed income-tax expense | 508,996 |
| signed loss for the year | 53,743,025 |
| signed closing retained-earnings deficit | 47,008,765 |

The original `OB-000001` net retained-earnings debit of ₦46,499,766.75 was the
pre-tax result. It omitted the signed ₦508,996 income-tax expense and corresponding
current tax payable. The old ₦1.42 conclusion therefore proved only that the
opening journal followed the pre-tax workbook; it did not prove that the journal
carried the signed audited close.

## Original OB-000001 to current OB-000001

The current journal contains neither 1400 nor 2000, but that was not its original
state.

| account | original OB evidence | 2026-04-29 cleanup | current OB |
| --- | ---: | --- | ---: |
| 1200 Zenith Bank | Dr 21,442,780.30 | line deleted | no line |
| 1400 Trade Receivables | Dr 20,591,053.35 | line deleted | no line |
| 2000 Trade Payables | Cr 40,310,713.50 | line deleted | no line |
| 3100 Retained Earnings | net Dr 46,499,766.75 | credit line changed from 6,734,260.89 to 5,011,140.74 | net Dr 48,222,886.90 |

The three deleted line IDs are absent from both the current journal-line set and
the posted ledger. `OB-000001` now totals ₦325,261,764.85 per side, exactly the
post-cleanup total encoded in the raw SQL; its pre-cleanup total was
₦367,295,598.50.

This operation deleted immutable posted-ledger history and rewrote a posted
journal. It must be treated as a historical defect, not as an accounting model to
repeat. Any repair must add linked correction records and preserve the surviving
history.

## AR/AP representation after the cleanup

The 17 `CUS OP BAL` journals and 26 selected `PUR OP BAL`/supplier-opening
journals all report `source_module = gl`, `source_document_type = JOURNAL`, and
`source_document_id IS NULL`. They preserve descriptive detail, but they are not
AR invoices, AP invoices or another linked subledger opening object.

| opening population | audited control | GL-only detail | difference before composite/later reversals |
| --- | ---: | ---: | ---: |
| AR / 1400 | Dr 20,591,053.35 | Dr 20,917,253.35 | Dr 326,200.00 excess |
| AP / 2000 | Cr 40,310,713.50 | Cr 40,186,751.04 | Cr 123,962.46 short |

The current relevant opening stack then includes these additional entries:

| account | GL-only detail | `REV-SYNC-OB-001` | exact six later reversals | net relevant opening stack | variance from audited control |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1400 | Dr 20,917,253.35 | Cr 2,841,816.25 | Cr 1,619,218.75 | Dr 16,456,218.35 | Dr 4,134,835.00 short |
| 2000 | Cr 40,186,751.04 | Dr 3,040,000.00 | — | Cr 37,146,751.04 | Cr 3,163,962.46 short |

These are bounded opening-population comparisons, not present-day account
balances. They prove the April cleanup and composite cannot both be treated as a
coherent “keep the detailed opening” policy.

The six later reversals were enumerated by exact journal number. A lexical SQL
range from `JE202604-43275` to `JE202604-43280` also includes unrelated journal
`JE202604-4328`; it must not be used as evidence for this population.

## End-to-end bridge disposition

| bridge edge | status | evidence / blocker |
| --- | --- | --- |
| signed audited close → unsigned TB | **partial** | rounded balance-sheet lines tie; tax closes the retained-earnings roll-forward; signed TB itself is missing; auditor opinion has a 2021 date error |
| unsigned TB → original `OB-000001` | **partial** | major controls tie exactly; current-tax payable and tax effect on retained earnings were omitted |
| original OB → ERPNext detail | **not reconciled** | AR detail is ₦326,200 high; AP detail is ₦123,962.46 low; no signed customer/supplier schedules |
| original OB → 2026-04-29 cleanup | **proven** | three line deletes and retained-earnings rewrite persist exactly |
| detail → `REV-SYNC-OB-001` | **not approved** | composite predates the deletion of the OB controls; later cleanup switched the claimed survivor to unlinked detail without unwinding the composite |
| composite → exact six later reversals | **proven duplicate effect** | ₦1,619,218.75 on 1400/3100 |
| opening layer → current AR/AP subledgers | **not reconciled** | opening journals have no subledger document IDs; required signed ageings are absent |

## Required Finance/auditor package

Before an operator builds or executes the composite correction, obtain and
approve:

1. the signed 2024 audited trial balance or an auditor-confirmed final ledger
   schedule that agrees to the signed statements;
2. a corrected/clarified auditor report for the opinion paragraph's 2021 date;
3. the customer-level 2024-12-31 AR ageing totaling ₦20,591,053.35;
4. the supplier-level 2024-12-31 AP ageing totaling ₦40,310,713.50;
5. WHT receivable/payable opening schedules;
6. an opening workpaper that identifies the approved subledger representation,
   resolves the ₦326,200.00 AR and ₦123,962.46 AP detail differences, and includes
   the ₦508,996 current-tax close;
7. Finance sign-off on whether to reconstruct subledger openings or retain a
   GL-only control representation.

Once those are signed, generate one idempotent, dry-run-first correction plan
from current production state. It must use linked correction journals, never
delete or update posted history, and must prove both GL totals and customer/
supplier subledger totals before commit.
