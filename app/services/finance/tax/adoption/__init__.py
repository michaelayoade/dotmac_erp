"""ERP's typed seams for `dotmac-tax` — adoption ledger item C1.

Three modules, one direction each:

- `contracts` — the two ERP-owned value objects
  (`ERPSourceTaxFactV1`, `ApplyTaxDeterminationSetV1`) named by
  `docs/architecture/dotmac-tax-adoption-boundary.md` § "Typed ERP seams".
- `inbound` — one mapper per named source-fact family, ERP row → source fact
  → public `dotmac_tax.TaxFact`.
- `outbound` — an approved determination set → ERP's accounting-consequence
  path, without the module learning that a general ledger exists.

Nothing here composes the module.  `composition` states, and tests assert,
that `dotmac-tax` is not pinned, its lineage is not in `alembic.ini`, and no
ERP writer has been repointed.  C2/C3/C4 are separate gated steps.
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
    ApplyTaxDeterminationSetV1,
    ERPSourceTaxFactV1,
    SourceFactFamily,
    TaxAdapterRefusal,
    TaxDeterminationComponentV1,
    TransactionSide,
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
    "ApplyTaxDeterminationSetV1",
    "ConsequencePosting",
    "ConsequencePostingLine",
    "ERPSourceTaxFactV1",
    "SourceFactFamily",
    "TaxAccountMap",
    "TaxAdapterRefusal",
    "TaxCodeAccounts",
    "TaxCompositionNotReady",
    "TaxDeterminationComponentV1",
    "TransactionSide",
    "ap_supplier_invoice_line_fact",
    "ar_invoice_line_fact",
    "composition_state",
    "payroll_taxable_pay_fact",
    "project_determination_set",
    "require_composition_ready",
    "to_tax_fact",
    "to_tax_fact_kwargs",
]
