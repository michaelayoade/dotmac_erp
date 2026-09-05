# Architecture decisions

ERP had no decision record directory until 2026-08-15. Program decisions lived
in plan documents (`docs/plans/`, `docs/superpowers/`) and in the adoption
ledger (`docs/PLATFORM_ADOPTION_LEDGER.md`), which is a *state* table: it says
what class a module is in and what gates it, not why the boundary is where it
is. A gate that names an ADR which does not exist cannot be cleared, and that
is exactly what blocked E8 for two weeks.

## The convention

- One decision per file, `NNNN-short-kebab-title.md`, never renumbered and
  never renamed.
- **The number comes from `reservations.toml`, not from this directory.** Take
  `next_free`, add your `[[reservation]]` row with `status = "reserved"`, and
  land that change **on its own, on `main`,** before you write the ADR. Reading
  the directory and taking the next gap is a read-then-write with no lock
  performed on branches that cannot see each other — 0003, 0004, 0006 and 0008
  each name more than one decision because that is what everyone did. A number
  is never reused, including after withdrawal.
- Every ADR states **Status**, **Date**, **Context**, **Decision**,
  **Consequences**, and **Alternatives rejected**. A decision with no rejected
  alternative was not a decision.
- Status is one of `Proposed`, `Accepted`, `Superseded by NNNN`. An accepted
  ADR is amended in place with a dated amendment block rather than quietly
  edited — the record of what changed is the point.
- An ADR records a decision. It does not implement one: the code, migration,
  test and ledger update land in their own change, and the ADR is what that
  change cites.

## Relationship to the other documents

- `docs/PLATFORM_ADOPTION_LEDGER.md` — as-built classification and gates. It
  cites ADRs; it does not replace them.
- `docs/SOT_RELATIONSHIP_MAP.md` — who owns which fact. An ADR that moves an
  ownership boundary updates the map in the change that implements it.
- `docs/plans/`, `docs/superpowers/` — intent, not authority. Where a plan and
  an ADR disagree, the ADR wins.

## Numbers

`reservations.toml` is the register, and
`tests/architecture/test_adr_number_allocation.py` is its checker. The index
below lists the `authored` rows and nothing else; the register additionally
carries numbers claimed on branches that have not merged, which is what stops
a second decision taking one of them.

Four numbers are contested today, from before the register existed. A branch
carrying a contested number **cannot merge until the claim is reconciled** —
not by policy but mechanically: the moment its file lands, the register says
that number has no document and CI fails. The reconciliation is Michael's
sequencing call and is not recorded here as though it were settled.

## Index

| ADR | Title | Status |
|---|---|---|
| [0001](0001-kernel-idempotency-is-erps-only-at-most-once-owner.md) | The kernel is ERP's only at-most-once owner | Accepted |
| [0002](0002-bank-statement-numbers-take-the-module-grammar.md) | Document numbers take the module grammar; `QUOTE` is the first family | Accepted (amended 2026-09-04) |
| [0003](0003-clean-install-starts-from-governed-opening-state.md) | The composable ERP starts from governed opening state | Accepted |
| [0004](0004-the-erp-bill-of-materials-is-frozen.md) | The ERP bill of materials is frozen before composition starts | Accepted |
| [0005](0005-payment-intent-status-has-one-writer.md) | `PaymentIntent.status` has one writer | Accepted |
| [0007](0007-unobserved-is-not-failed.md) | An unobserved transfer is not a failed one | Accepted |
