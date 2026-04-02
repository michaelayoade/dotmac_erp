# PMS 2022 Summary-to-Feature Traceability Matrix

Source baseline: OHCSF PMS Guidelines (2022) summary provided by business stakeholders.
Matrix version: 2026-04-02.

## Clause Coverage

| Clause | Summary Topic | Primary Feature Areas | Primary Test Evidence |
|---|---|---|---|
| C01 | Introduction and objectives | `pms_config_service`, dashboard/reporting | `test_pms_config_service`, `test_ohcsf_reporting_service` |
| C02 | Institutional roles/responsibilities | governance service + institutional workflow UI | `test_governance_service` |
| C03 | National instruments + target setting | strategic objectives + contracts | `test_strategic_objective_service`, `test_contract_service` |
| C04 | MDA performance system | institutional scoring/review flows | `test_institutional_service` |
| C05 | KRA weightings | OHCSF weight seed templates | `test_pms_config_service` |
| C06 | EPMS subsystem | appraisal + monthly review lifecycle | `test_monthly_review_service`, `test_ohcsf_appraisal_workflow` |
| C07 | EPMS role responsibilities | role-gated governance transitions/logging | `test_governance_service` |
| C08 | EPMS cycle cadence | monthly/quarterly/annual transitions | `test_ohcsf_appraisal_workflow`, `test_monthly_review_service` |
| C09 | Performance planning | contract objective count and quality gates | `test_contract_service` |
| C10 | KRAs/KPIs/targets/competencies | competency selection + evidence enforcement | `test_contract_service`, `test_ohcsf_appraisal_workflow` |
| C11 | Review, appraisal, scoring, appeals | deadline gates + scoring + appeal windows | `test_perf_service_deadlines`, `test_appeal_service`, `test_ohcsf_composite_scoring` |
| C12 | Underperformance management (PIP) | proactive PIP trigger + completion gate | `test_ohcsf_appraisal_workflow`, `test_pip_service`, `test_perf_service_pip_gate` |
| C13 | Approved absence handling | carryover + structured documentary evidence | `test_perf_service_absence` |
| C14 | Post-appraisal and reconciliation | committee reconciliation + SLA automation/reporting | `test_perf_service_reconciliation`, `test_dispute_sla_service`, `test_ohcsf_reporting_service` |
| C15 | Rewards and recognition | eligibility filtering + transparent nomination flow | `test_reward_service` |
| C16 | Capacity building interventions | competency seed + development reporting | `test_pms_config_service`, `test_ohcsf_reporting_service` |

## Key Insights Coverage

- KI01: Institutional and individual alignment
- KI02: Accountability through role gates and audit logs
- KI03: SMART/OKR-style measurable objective structure
- KI04: Competency and behavioral evidence integrated into appraisal
- KI05: Continuous feedback and improvement loop
- KI06: Formal underperformance and appeal pathways
- KI07: Data-driven decisions through PMS reporting
- KI08: Transparency and motivation via rewards/governance trail

## Verification Contract

Machine-verifiable source of truth for this matrix lives in:
- `docs/compliance/pms_2022_traceability_matrix.json`

Automated guard tests:
- `tests/people/perf/test_pms_summary_traceability.py`

These tests enforce that every clause has:
- feature references that exist in repository
- test references that exist in repository
- referenced test case names present in referenced test files
