"""ERP's typed seams for ``dotmac-tax`` adoption.

The module's public ``TaxDeterminationSetV1`` is the read result; ERP owns the
source fact, application context, FX evidence and accounting consequence.

- `contracts` — ERP-owned source, application and FX values.
- `inbound` — one mapper per named source-fact family, ERP row → source fact
  → public `dotmac_tax.TaxFact`.
- `outbound` — the public module result → ERP's accounting consequence.
- `shadow` — typed C3 comparison or retained adjudication evidence.

Nothing here changes authority. The a3 dependency/lineage composition remains
isolated until the release has an authoritative publication oracle.
"""

from app.services.finance.tax.adoption.composition import (
    COMPOSITION_ENABLED,
    CONTRACT_VERSION,
    DISTRIBUTION,
    IMPORT_PACKAGE,
    MIGRATION_VERSION_LOCATION,
    MODULE_CODE,
    TaxCompositionNotReady,
    composition_state,
    require_composition_ready,
)
from app.services.finance.tax.adoption.contracts import (
    RECOGNITION_BASIS_ACCRUAL,
    RECOGNITION_BASIS_CASH,
    RECOGNITION_BASIS_PAYROLL_PERIOD,
    TREATMENTS_WITHOUT_A_TAX_CONSEQUENCE,
    AccountingConsequence,
    ERPSourceTaxFactV1,
    SourceFactFamily,
    TaxApplicationContextV1,
    TaxAdapterRefusal,
    TaxPostingFXEvidenceV1,
    TransactionSide,
)
from app.services.finance.tax.adoption.fx import (
    FunctionalPostingAmounts,
    allocate_functional_line_amounts,
)
from app.services.finance.tax.adoption.inbound import (
    MAPPED_FAMILIES,
    TAX_FACT_FIELDS,
    UNMAPPED_FAMILIES,
    ap_supplier_invoice_line_fact,
    ar_invoice_line_fact,
    payroll_taxable_pay_fact,
    to_tax_fact,
    to_tax_fact_kwargs,
)
from app.services.finance.tax.adoption.outbound import (
    SOURCE_MODULE,
    ConsequencePosting,
    ConsequencePostingLine,
    TaxAccountMap,
    TaxCodeAccounts,
    project_determination_set,
)
from app.services.finance.tax.adoption.shadow import (
    LegacyTaxComponentProjectionV1,
    LegacyTaxProjectionV1,
    ShadowComparedField,
    ShadowComparisonOutcomeV1,
    ShadowComparisonStatus,
    ShadowMismatchV1,
    ShadowNonComparableReason,
    compare_shadow_determination,
)

__all__ = [
    "COMPOSITION_ENABLED",
    "CONTRACT_VERSION",
    "DISTRIBUTION",
    "IMPORT_PACKAGE",
    "MAPPED_FAMILIES",
    "MIGRATION_VERSION_LOCATION",
    "MODULE_CODE",
    "RECOGNITION_BASIS_ACCRUAL",
    "RECOGNITION_BASIS_CASH",
    "RECOGNITION_BASIS_PAYROLL_PERIOD",
    "SOURCE_MODULE",
    "TAX_FACT_FIELDS",
    "TREATMENTS_WITHOUT_A_TAX_CONSEQUENCE",
    "UNMAPPED_FAMILIES",
    "AccountingConsequence",
    "ConsequencePosting",
    "ConsequencePostingLine",
    "ERPSourceTaxFactV1",
    "FunctionalPostingAmounts",
    "LegacyTaxComponentProjectionV1",
    "LegacyTaxProjectionV1",
    "ShadowComparedField",
    "ShadowComparisonOutcomeV1",
    "ShadowComparisonStatus",
    "ShadowMismatchV1",
    "ShadowNonComparableReason",
    "SourceFactFamily",
    "TaxApplicationContextV1",
    "TaxAccountMap",
    "TaxAdapterRefusal",
    "TaxCodeAccounts",
    "TaxCompositionNotReady",
    "TaxPostingFXEvidenceV1",
    "TransactionSide",
    "allocate_functional_line_amounts",
    "ap_supplier_invoice_line_fact",
    "ar_invoice_line_fact",
    "composition_state",
    "compare_shadow_determination",
    "payroll_taxable_pay_fact",
    "project_determination_set",
    "require_composition_ready",
    "to_tax_fact",
    "to_tax_fact_kwargs",
]
