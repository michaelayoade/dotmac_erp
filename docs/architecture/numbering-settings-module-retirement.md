# Retiring the NumberingSequence row — design

**Status:** Design, 2026-10-27. Follows the 2026-10-19 numbering cutover
(all 27 series issued by `dotmac-numbering`) and the 2026-10-27 dead-code
retirement (async legacy allocator deleted; sync issuance fails closed
for non-cut-over series).

## Why the row still exists

`NumberingSequence` (the legacy row) is load-bearing in four roles today,
all inside `allocate_from_module` (`numbering_module.py`) and the
settings surface (`NumberingService`, `finance/settings_web.py`):

1. **Settings/config record** — prefix, separator, digits, year/month
   inclusion, reset frequency. Settings CRUD reads/writes the row.
2. **Series definition source** — every allocation (re)configures the
   module series from the row via `series_configuration_for(...)`.
3. **Seed + lock** — the caller holds `SELECT … FOR UPDATE` on the row;
   `next_value` is derived from `row.current_number + 1` (or 1 on reset)
   and passed as the series `start_value` when (re)configuring.
4. **Mirror** — after allocation, `row.current_number` is written back
   from `allocation.value`; settings list views display it.

Deleting the row without replacing these four roles breaks issuance or
settings. That is why the 2026-10-27 retirement stopped at dead code.

## Target

The module owns series state outright; the row goes away.

- **Configuration:** settings CRUD reads/writes module series
  configuration directly (`configure_series` + module reads), scoped by
  organization through the existing tenant scope. ERP keeps only the
  series-code mapping (`SequenceType` → scoped identifier), which is a
  code-level table, not data.
- **Identity edits:** today, identity-shaping edits to a configured
  series are refused (module freezes identity). The settings
  re-identification path (rename scoped identifier + counter continuity)
  was noted as not built during the cutover; the target needs it or an
  explicit "identity is immutable, create a new series" product rule.
  This is the main open decision.
- **Issuance:** allocation no longer takes the row lock or seeds from
  `current_number`; it relies on the module's own per-period counter
  atomicity. One-time continuity proof per org/series (module counter ≥
  row counter, as the October continuity report established) gates the
  switch.
- **Display:** "last number used" reads come from module period state,
  not a mirrored column.
- **Reset:** counters never move backward in the module; the settings
  reset affordance is already refused for cut-over series and is deleted
  with the row.

## Sequence

1. Settings read path dual-reads module configuration (shadow compare
   against row-derived configuration; report divergences).
2. Settings writes go to the module; row becomes write-through cache,
   then read-only, then unreferenced.
3. Issuance stops locking/seeding from the row (continuity proof per
   series first, continuity-report pattern).
4. Drop the mirror write, then the model/table in a normal ERP migration
   once no code references it (`SyncNumberingService` shrinks to the
   module facade; `NumberingService` settings CRUD re-points).

## Non-goals

No change to issued number formats, series codes, or the module's
period/reset semantics. `DocumentSequence` (bank-statement identity) is
a separate legacy surface and is not covered here.
