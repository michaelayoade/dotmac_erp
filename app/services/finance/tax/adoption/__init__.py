"""ERP's typed seams for the composed, disabled `dotmac-tax` adoption.

Three modules, one direction each:

- `contracts` — ERP's source fact and local posting context. The determination
  result comes directly from the released public module contract.
- `inbound` — one mapper per named source-fact family, ERP row → source fact
  → public `dotmac_tax.TaxFact`.
- `outbound` — an approved determination set → ERP's accounting-consequence
  path, without the module learning that a general ledger exists.

C2 composes storage and the public contract while keeping the authority flag
off. No ERP writer has been repointed; C3 shadow and C4 cutover remain separate.
"""

from app.services.finance.tax.adoption.composition import (
    COMPOSITION_ENABLED,
    CONTRACT_VERSION,
    DISTRIBUTION,
    IMPORT_PACKAGE,
    LINEAGE_HEAD,
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
    AccountingConsequence,
    ERPSourceTaxFactV1,
    SourceFactFamily,
    TaxApplicationContextV1,
    TaxAdapterRefusal,
    TransactionSide,
)
from app.services.finance.tax.adoption.inbound import (
    MAPPED_FAMILIES,
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
    "LINEAGE_HEAD",
    "MAPPED_FAMILIES",
    "MIGRATION_VERSION_LOCATION",
    "MODULE_CODE",
    "RECOGNITION_BASIS_ACCRUAL",
    "RECOGNITION_BASIS_CASH",
    "RECOGNITION_BASIS_PAYROLL_PERIOD",
    "SOURCE_MODULE",
    "UNMAPPED_FAMILIES",
    "AccountingConsequence",
    "ConsequencePosting",
    "ConsequencePostingLine",
    "ERPSourceTaxFactV1",
    "SourceFactFamily",
    "TaxApplicationContextV1",
    "TaxAccountMap",
    "TaxAdapterRefusal",
    "TaxCodeAccounts",
    "TaxCompositionNotReady",
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
