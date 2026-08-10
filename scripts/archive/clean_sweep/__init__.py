"""
2025 Clean Sweep — Delete & Re-Import from ERPNext.

Phases:
    1. phase1_delete      — Delete all 2025 financial data from DotMac
    2. phase2_accounts    — Map ERPNext accounts → DotMac numbered chart
    3. phase3_import_gl   — Import 167K GL entries → ~68K journals
    4. phase4_import_source_docs — Import AR/AP/Expense source documents
    5. phase5_import_banking     — Import 38K bank transactions + reconciliation
    6. phase6_rebuild_verify     — Rebuild balances + verification queries

Each phase is idempotent (safe to re-run) and runs independently:
    docker exec dotmac_erp_app python -m scripts.clean_sweep.phase1_delete
"""
