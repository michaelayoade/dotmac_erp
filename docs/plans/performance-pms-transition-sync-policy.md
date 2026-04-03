# Performance/PMS Transition Sync Policy (Phase 2)

Date: 2026-04-02

## Source of truth during transition
- `core_org.organization.performance_mode` is the primary setting.
- `core_org.organization.pms_ohcsf_enabled` remains as a legacy compatibility flag.

## One-way sync rules
- If `performance_mode` is explicitly changed:
  - `PRIVATE` => `pms_ohcsf_enabled = false`
  - `GOVERNMENT_PMS` => `pms_ohcsf_enabled = true`
  - `HYBRID` => `pms_ohcsf_enabled = true`
- If legacy PMS toggle is used:
  - `true` and current mode is `PRIVATE` => mode set to `GOVERNMENT_PMS`
  - `false` and current mode is `GOVERNMENT_PMS` => mode set to `PRIVATE`
  - `HYBRID` is preserved unless explicitly changed in mode selector.

## Runtime fallback
- If `performance_mode` is missing/invalid in runtime objects, infer from legacy flag:
  - `pms_ohcsf_enabled=true` => `GOVERNMENT_PMS`
  - otherwise => `PRIVATE`

## Migration/backfill behavior
- Existing rows with `pms_ohcsf_enabled=true` and mode `PRIVATE` are backfilled to `GOVERNMENT_PMS`.
