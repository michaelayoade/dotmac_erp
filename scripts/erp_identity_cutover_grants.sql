-- GENERATED FILE -- do not edit. Regenerate with:
--     python scripts/generate_privilege_manifest.py
-- Source of truth: docs/inventories/erp-identity-cutover-manifest-2026-09-04.json
-- Census:          docs/inventories/erp-privilege-census-2026-09-04.json
--
-- Change 1, routine half: mirror the legacy estate onto app_user. Mechanical rows only -- nothing here needs a judgement call.
--
-- Apply with:  psql -v ON_ERROR_STOP=1 -f <this file>
--
-- IDEMPOTENT ON RE-RUN. `GRANT` in PostgreSQL is an assertion about an ACL,
-- not an append: granting a privilege the role already holds leaves the ACL
-- byte-identical and returns success. There is nothing to make conditional,
-- no `IF NOT EXISTS` to add, and no ordering that changes the outcome. Every
-- statement below names exactly ONE privilege on exactly ONE object, so a
-- partial re-run converges to the same state as a full one.
--
-- This file contains GRANT statements only. No REVOKE, no ALTER, no CREATE,
-- no DROP, no ownership change, no role membership, no `GRANT ALL`.
--
-- Rows: 1736 to grant. NO denied row appears in this file at all --
-- the denials live in scripts/erp_identity_cutover_denied.sql, which
-- contains no executable statement, so there is nothing here to uncomment.


BEGIN;

-- ===== section: schema_usage =====
-- schema:ap -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "ap" TO "app_user";
-- schema:ar -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "ar" TO "app_user";
-- schema:attendance -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "attendance" TO "app_user";
-- schema:audit -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "audit" TO "app_user";
-- schema:automation -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "automation" TO "app_user";
-- schema:banking -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "banking" TO "app_user";
-- schema:common -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "common" TO "app_user";
-- schema:cons -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "cons" TO "app_user";
-- schema:core_config -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "core_config" TO "app_user";
-- schema:core_fx -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "core_fx" TO "app_user";
-- schema:core_org -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "core_org" TO "app_user";
-- schema:erpnext_staging -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "erpnext_staging" TO "app_user";
-- schema:exp -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "exp" TO "app_user";
-- schema:expense -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "expense" TO "app_user";
-- schema:fa -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "fa" TO "app_user";
-- schema:fin_inst -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "fin_inst" TO "app_user";
-- schema:fleet -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "fleet" TO "app_user";
-- schema:forms -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "forms" TO "app_user";
-- schema:gl -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "gl" TO "app_user";
-- schema:inv -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "inv" TO "app_user";
-- schema:ipsas -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "ipsas" TO "app_user";
-- schema:lease -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "lease" TO "app_user";
-- schema:leave -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "leave" TO "app_user";
-- schema:migration -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "migration" TO "app_user";
-- schema:payments -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "payments" TO "app_user";
-- schema:payroll -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "payroll" TO "app_user";
-- schema:people -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "people" TO "app_user";
-- schema:perf -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "perf" TO "app_user";
-- schema:platform -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "platform" TO "app_user";
-- schema:pm -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "pm" TO "app_user";
-- schema:proc -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "proc" TO "app_user";
-- schema:recruit -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "recruit" TO "app_user";
-- schema:scheduling -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "scheduling" TO "app_user";
-- schema:settings -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "settings" TO "app_user";
-- schema:support -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "support" TO "app_user";
-- schema:tax -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "tax" TO "app_user";
-- schema:training -- mirrored-schema-usage
GRANT USAGE ON SCHEMA "training" TO "app_user";
-- ===== section: relations =====
-- relation:ap.ap_aging_snapshot [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ap"."ap_aging_snapshot" TO "app_user";
GRANT INSERT ON TABLE "ap"."ap_aging_snapshot" TO "app_user";
GRANT SELECT ON TABLE "ap"."ap_aging_snapshot" TO "app_user";
GRANT UPDATE ON TABLE "ap"."ap_aging_snapshot" TO "app_user";
-- relation:ap.goods_receipt [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ap"."goods_receipt" TO "app_user";
GRANT INSERT ON TABLE "ap"."goods_receipt" TO "app_user";
GRANT SELECT ON TABLE "ap"."goods_receipt" TO "app_user";
GRANT UPDATE ON TABLE "ap"."goods_receipt" TO "app_user";
-- relation:ap.goods_receipt_line [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ap"."goods_receipt_line" TO "app_user";
GRANT INSERT ON TABLE "ap"."goods_receipt_line" TO "app_user";
GRANT SELECT ON TABLE "ap"."goods_receipt_line" TO "app_user";
GRANT UPDATE ON TABLE "ap"."goods_receipt_line" TO "app_user";
-- relation:ap.invoice_inventory_receipt_approval [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ap"."invoice_inventory_receipt_approval" TO "app_user";
GRANT INSERT ON TABLE "ap"."invoice_inventory_receipt_approval" TO "app_user";
GRANT SELECT ON TABLE "ap"."invoice_inventory_receipt_approval" TO "app_user";
GRANT UPDATE ON TABLE "ap"."invoice_inventory_receipt_approval" TO "app_user";
-- relation:ap.payment_allocation [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ap"."payment_allocation" TO "app_user";
GRANT INSERT ON TABLE "ap"."payment_allocation" TO "app_user";
GRANT SELECT ON TABLE "ap"."payment_allocation" TO "app_user";
GRANT UPDATE ON TABLE "ap"."payment_allocation" TO "app_user";
-- relation:ap.payment_batch [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ap"."payment_batch" TO "app_user";
GRANT INSERT ON TABLE "ap"."payment_batch" TO "app_user";
GRANT SELECT ON TABLE "ap"."payment_batch" TO "app_user";
GRANT UPDATE ON TABLE "ap"."payment_batch" TO "app_user";
-- relation:ap.purchase_order [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ap"."purchase_order" TO "app_user";
GRANT INSERT ON TABLE "ap"."purchase_order" TO "app_user";
GRANT SELECT ON TABLE "ap"."purchase_order" TO "app_user";
GRANT UPDATE ON TABLE "ap"."purchase_order" TO "app_user";
-- relation:ap.purchase_order_line [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ap"."purchase_order_line" TO "app_user";
GRANT INSERT ON TABLE "ap"."purchase_order_line" TO "app_user";
GRANT SELECT ON TABLE "ap"."purchase_order_line" TO "app_user";
GRANT UPDATE ON TABLE "ap"."purchase_order_line" TO "app_user";
-- relation:ap.supplier [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ap"."supplier" TO "app_user";
GRANT INSERT ON TABLE "ap"."supplier" TO "app_user";
GRANT SELECT ON TABLE "ap"."supplier" TO "app_user";
GRANT UPDATE ON TABLE "ap"."supplier" TO "app_user";
-- relation:ap.supplier_invoice [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ap"."supplier_invoice" TO "app_user";
GRANT INSERT ON TABLE "ap"."supplier_invoice" TO "app_user";
GRANT SELECT ON TABLE "ap"."supplier_invoice" TO "app_user";
GRANT UPDATE ON TABLE "ap"."supplier_invoice" TO "app_user";
-- relation:ap.supplier_invoice_line [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ap"."supplier_invoice_line" TO "app_user";
GRANT INSERT ON TABLE "ap"."supplier_invoice_line" TO "app_user";
GRANT SELECT ON TABLE "ap"."supplier_invoice_line" TO "app_user";
GRANT UPDATE ON TABLE "ap"."supplier_invoice_line" TO "app_user";
-- relation:ap.supplier_invoice_line_tax [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ap"."supplier_invoice_line_tax" TO "app_user";
GRANT INSERT ON TABLE "ap"."supplier_invoice_line_tax" TO "app_user";
GRANT SELECT ON TABLE "ap"."supplier_invoice_line_tax" TO "app_user";
GRANT UPDATE ON TABLE "ap"."supplier_invoice_line_tax" TO "app_user";
-- relation:ap.supplier_payment [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ap"."supplier_payment" TO "app_user";
GRANT INSERT ON TABLE "ap"."supplier_payment" TO "app_user";
GRANT SELECT ON TABLE "ap"."supplier_payment" TO "app_user";
GRANT UPDATE ON TABLE "ap"."supplier_payment" TO "app_user";
-- relation:ar.ar_aging_snapshot [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ar"."ar_aging_snapshot" TO "app_user";
GRANT INSERT ON TABLE "ar"."ar_aging_snapshot" TO "app_user";
GRANT SELECT ON TABLE "ar"."ar_aging_snapshot" TO "app_user";
GRANT UPDATE ON TABLE "ar"."ar_aging_snapshot" TO "app_user";
-- relation:ar.contract [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ar"."contract" TO "app_user";
GRANT INSERT ON TABLE "ar"."contract" TO "app_user";
GRANT SELECT ON TABLE "ar"."contract" TO "app_user";
GRANT UPDATE ON TABLE "ar"."contract" TO "app_user";
-- relation:ar.customer [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ar"."customer" TO "app_user";
GRANT INSERT ON TABLE "ar"."customer" TO "app_user";
GRANT SELECT ON TABLE "ar"."customer" TO "app_user";
GRANT UPDATE ON TABLE "ar"."customer" TO "app_user";
-- relation:ar.customer_payment [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ar"."customer_payment" TO "app_user";
GRANT INSERT ON TABLE "ar"."customer_payment" TO "app_user";
GRANT SELECT ON TABLE "ar"."customer_payment" TO "app_user";
GRANT UPDATE ON TABLE "ar"."customer_payment" TO "app_user";
-- relation:ar.dotmac_sub_sync_watermark [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ar"."dotmac_sub_sync_watermark" TO "app_user";
GRANT INSERT ON TABLE "ar"."dotmac_sub_sync_watermark" TO "app_user";
GRANT SELECT ON TABLE "ar"."dotmac_sub_sync_watermark" TO "app_user";
GRANT UPDATE ON TABLE "ar"."dotmac_sub_sync_watermark" TO "app_user";
-- relation:ar.expected_credit_loss [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ar"."expected_credit_loss" TO "app_user";
GRANT INSERT ON TABLE "ar"."expected_credit_loss" TO "app_user";
GRANT SELECT ON TABLE "ar"."expected_credit_loss" TO "app_user";
GRANT UPDATE ON TABLE "ar"."expected_credit_loss" TO "app_user";
-- relation:ar.external_sync [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ar"."external_sync" TO "app_user";
GRANT INSERT ON TABLE "ar"."external_sync" TO "app_user";
GRANT SELECT ON TABLE "ar"."external_sync" TO "app_user";
GRANT UPDATE ON TABLE "ar"."external_sync" TO "app_user";
-- relation:ar.invoice [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ar"."invoice" TO "app_user";
GRANT INSERT ON TABLE "ar"."invoice" TO "app_user";
GRANT SELECT ON TABLE "ar"."invoice" TO "app_user";
GRANT UPDATE ON TABLE "ar"."invoice" TO "app_user";
-- relation:ar.invoice_line [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ar"."invoice_line" TO "app_user";
GRANT INSERT ON TABLE "ar"."invoice_line" TO "app_user";
GRANT SELECT ON TABLE "ar"."invoice_line" TO "app_user";
GRANT UPDATE ON TABLE "ar"."invoice_line" TO "app_user";
-- relation:ar.invoice_line_tax [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ar"."invoice_line_tax" TO "app_user";
GRANT INSERT ON TABLE "ar"."invoice_line_tax" TO "app_user";
GRANT SELECT ON TABLE "ar"."invoice_line_tax" TO "app_user";
GRANT UPDATE ON TABLE "ar"."invoice_line_tax" TO "app_user";
-- relation:ar.payment_allocation [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ar"."payment_allocation" TO "app_user";
GRANT INSERT ON TABLE "ar"."payment_allocation" TO "app_user";
GRANT SELECT ON TABLE "ar"."payment_allocation" TO "app_user";
GRANT UPDATE ON TABLE "ar"."payment_allocation" TO "app_user";
-- relation:ar.payment_terms [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ar"."payment_terms" TO "app_user";
GRANT INSERT ON TABLE "ar"."payment_terms" TO "app_user";
GRANT SELECT ON TABLE "ar"."payment_terms" TO "app_user";
GRANT UPDATE ON TABLE "ar"."payment_terms" TO "app_user";
-- relation:ar.performance_obligation [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ar"."performance_obligation" TO "app_user";
GRANT INSERT ON TABLE "ar"."performance_obligation" TO "app_user";
GRANT SELECT ON TABLE "ar"."performance_obligation" TO "app_user";
GRANT UPDATE ON TABLE "ar"."performance_obligation" TO "app_user";
-- relation:ar.quote [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ar"."quote" TO "app_user";
GRANT INSERT ON TABLE "ar"."quote" TO "app_user";
GRANT SELECT ON TABLE "ar"."quote" TO "app_user";
GRANT UPDATE ON TABLE "ar"."quote" TO "app_user";
-- relation:ar.quote_line [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ar"."quote_line" TO "app_user";
GRANT INSERT ON TABLE "ar"."quote_line" TO "app_user";
GRANT SELECT ON TABLE "ar"."quote_line" TO "app_user";
GRANT UPDATE ON TABLE "ar"."quote_line" TO "app_user";
-- relation:ar.revenue_recognition_event [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ar"."revenue_recognition_event" TO "app_user";
GRANT INSERT ON TABLE "ar"."revenue_recognition_event" TO "app_user";
GRANT SELECT ON TABLE "ar"."revenue_recognition_event" TO "app_user";
GRANT UPDATE ON TABLE "ar"."revenue_recognition_event" TO "app_user";
-- relation:ar.sales_order [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ar"."sales_order" TO "app_user";
GRANT INSERT ON TABLE "ar"."sales_order" TO "app_user";
GRANT SELECT ON TABLE "ar"."sales_order" TO "app_user";
GRANT UPDATE ON TABLE "ar"."sales_order" TO "app_user";
-- relation:ar.sales_order_line [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ar"."sales_order_line" TO "app_user";
GRANT INSERT ON TABLE "ar"."sales_order_line" TO "app_user";
GRANT SELECT ON TABLE "ar"."sales_order_line" TO "app_user";
GRANT UPDATE ON TABLE "ar"."sales_order_line" TO "app_user";
-- relation:ar.shipment [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ar"."shipment" TO "app_user";
GRANT INSERT ON TABLE "ar"."shipment" TO "app_user";
GRANT SELECT ON TABLE "ar"."shipment" TO "app_user";
GRANT UPDATE ON TABLE "ar"."shipment" TO "app_user";
-- relation:ar.shipment_line [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ar"."shipment_line" TO "app_user";
GRANT INSERT ON TABLE "ar"."shipment_line" TO "app_user";
GRANT SELECT ON TABLE "ar"."shipment_line" TO "app_user";
GRANT UPDATE ON TABLE "ar"."shipment_line" TO "app_user";
-- relation:attendance.attendance [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "attendance"."attendance" TO "app_user";
GRANT INSERT ON TABLE "attendance"."attendance" TO "app_user";
GRANT SELECT ON TABLE "attendance"."attendance" TO "app_user";
GRANT UPDATE ON TABLE "attendance"."attendance" TO "app_user";
-- relation:attendance.attendance_request [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "attendance"."attendance_request" TO "app_user";
GRANT INSERT ON TABLE "attendance"."attendance_request" TO "app_user";
GRANT SELECT ON TABLE "attendance"."attendance_request" TO "app_user";
GRANT UPDATE ON TABLE "attendance"."attendance_request" TO "app_user";
-- relation:attendance.shift_assignment [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "attendance"."shift_assignment" TO "app_user";
GRANT INSERT ON TABLE "attendance"."shift_assignment" TO "app_user";
GRANT SELECT ON TABLE "attendance"."shift_assignment" TO "app_user";
GRANT UPDATE ON TABLE "attendance"."shift_assignment" TO "app_user";
-- relation:attendance.shift_type [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "attendance"."shift_type" TO "app_user";
GRANT INSERT ON TABLE "attendance"."shift_type" TO "app_user";
GRANT SELECT ON TABLE "attendance"."shift_type" TO "app_user";
GRANT UPDATE ON TABLE "attendance"."shift_type" TO "app_user";
-- relation:audit.approval_decision [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "audit"."approval_decision" TO "app_user";
GRANT INSERT ON TABLE "audit"."approval_decision" TO "app_user";
GRANT SELECT ON TABLE "audit"."approval_decision" TO "app_user";
GRANT UPDATE ON TABLE "audit"."approval_decision" TO "app_user";
-- relation:audit.approval_request [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "audit"."approval_request" TO "app_user";
GRANT INSERT ON TABLE "audit"."approval_request" TO "app_user";
GRANT SELECT ON TABLE "audit"."approval_request" TO "app_user";
GRANT UPDATE ON TABLE "audit"."approval_request" TO "app_user";
-- relation:audit.approval_workflow [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "audit"."approval_workflow" TO "app_user";
GRANT INSERT ON TABLE "audit"."approval_workflow" TO "app_user";
GRANT SELECT ON TABLE "audit"."approval_workflow" TO "app_user";
GRANT UPDATE ON TABLE "audit"."approval_workflow" TO "app_user";
-- relation:audit.audit_log [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "audit"."audit_log" TO "app_user";
GRANT INSERT ON TABLE "audit"."audit_log" TO "app_user";
GRANT SELECT ON TABLE "audit"."audit_log" TO "app_user";
GRANT UPDATE ON TABLE "audit"."audit_log" TO "app_user";
-- relation:audit.field_change_log [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "audit"."field_change_log" TO "app_user";
GRANT INSERT ON TABLE "audit"."field_change_log" TO "app_user";
GRANT SELECT ON TABLE "audit"."field_change_log" TO "app_user";
GRANT UPDATE ON TABLE "audit"."field_change_log" TO "app_user";
-- relation:automation.custom_field_definition [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "automation"."custom_field_definition" TO "app_user";
GRANT INSERT ON TABLE "automation"."custom_field_definition" TO "app_user";
GRANT SELECT ON TABLE "automation"."custom_field_definition" TO "app_user";
GRANT UPDATE ON TABLE "automation"."custom_field_definition" TO "app_user";
-- relation:automation.document_template [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "automation"."document_template" TO "app_user";
GRANT INSERT ON TABLE "automation"."document_template" TO "app_user";
GRANT SELECT ON TABLE "automation"."document_template" TO "app_user";
GRANT UPDATE ON TABLE "automation"."document_template" TO "app_user";
-- relation:automation.generated_document [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "automation"."generated_document" TO "app_user";
GRANT INSERT ON TABLE "automation"."generated_document" TO "app_user";
GRANT SELECT ON TABLE "automation"."generated_document" TO "app_user";
GRANT UPDATE ON TABLE "automation"."generated_document" TO "app_user";
-- relation:automation.recurring_log [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "automation"."recurring_log" TO "app_user";
GRANT INSERT ON TABLE "automation"."recurring_log" TO "app_user";
GRANT SELECT ON TABLE "automation"."recurring_log" TO "app_user";
GRANT UPDATE ON TABLE "automation"."recurring_log" TO "app_user";
-- relation:automation.recurring_template [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "automation"."recurring_template" TO "app_user";
GRANT INSERT ON TABLE "automation"."recurring_template" TO "app_user";
GRANT SELECT ON TABLE "automation"."recurring_template" TO "app_user";
GRANT UPDATE ON TABLE "automation"."recurring_template" TO "app_user";
-- relation:automation.workflow_execution [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "automation"."workflow_execution" TO "app_user";
GRANT INSERT ON TABLE "automation"."workflow_execution" TO "app_user";
GRANT SELECT ON TABLE "automation"."workflow_execution" TO "app_user";
GRANT UPDATE ON TABLE "automation"."workflow_execution" TO "app_user";
-- relation:automation.workflow_rule [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "automation"."workflow_rule" TO "app_user";
GRANT INSERT ON TABLE "automation"."workflow_rule" TO "app_user";
GRANT SELECT ON TABLE "automation"."workflow_rule" TO "app_user";
GRANT UPDATE ON TABLE "automation"."workflow_rule" TO "app_user";
-- relation:automation.workflow_rule_version [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "automation"."workflow_rule_version" TO "app_user";
GRANT INSERT ON TABLE "automation"."workflow_rule_version" TO "app_user";
GRANT SELECT ON TABLE "automation"."workflow_rule_version" TO "app_user";
GRANT UPDATE ON TABLE "automation"."workflow_rule_version" TO "app_user";
-- relation:banking.bank_accounts [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "banking"."bank_accounts" TO "app_user";
GRANT INSERT ON TABLE "banking"."bank_accounts" TO "app_user";
GRANT SELECT ON TABLE "banking"."bank_accounts" TO "app_user";
GRANT UPDATE ON TABLE "banking"."bank_accounts" TO "app_user";
-- relation:banking.bank_reconciliation_lines [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "banking"."bank_reconciliation_lines" TO "app_user";
GRANT INSERT ON TABLE "banking"."bank_reconciliation_lines" TO "app_user";
GRANT SELECT ON TABLE "banking"."bank_reconciliation_lines" TO "app_user";
GRANT UPDATE ON TABLE "banking"."bank_reconciliation_lines" TO "app_user";
-- relation:banking.bank_reconciliations [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "banking"."bank_reconciliations" TO "app_user";
GRANT INSERT ON TABLE "banking"."bank_reconciliations" TO "app_user";
GRANT SELECT ON TABLE "banking"."bank_reconciliations" TO "app_user";
GRANT UPDATE ON TABLE "banking"."bank_reconciliations" TO "app_user";
-- relation:banking.bank_statement_line_matches [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "banking"."bank_statement_line_matches" TO "app_user";
GRANT INSERT ON TABLE "banking"."bank_statement_line_matches" TO "app_user";
GRANT SELECT ON TABLE "banking"."bank_statement_line_matches" TO "app_user";
GRANT UPDATE ON TABLE "banking"."bank_statement_line_matches" TO "app_user";
-- relation:banking.bank_statement_lines [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "banking"."bank_statement_lines" TO "app_user";
GRANT INSERT ON TABLE "banking"."bank_statement_lines" TO "app_user";
GRANT SELECT ON TABLE "banking"."bank_statement_lines" TO "app_user";
GRANT UPDATE ON TABLE "banking"."bank_statement_lines" TO "app_user";
-- relation:banking.bank_statements [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "banking"."bank_statements" TO "app_user";
GRANT INSERT ON TABLE "banking"."bank_statements" TO "app_user";
GRANT SELECT ON TABLE "banking"."bank_statements" TO "app_user";
GRANT UPDATE ON TABLE "banking"."bank_statements" TO "app_user";
-- relation:banking.payee [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "banking"."payee" TO "app_user";
GRANT INSERT ON TABLE "banking"."payee" TO "app_user";
GRANT SELECT ON TABLE "banking"."payee" TO "app_user";
GRANT UPDATE ON TABLE "banking"."payee" TO "app_user";
-- relation:banking.reconciliation_match_log [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "banking"."reconciliation_match_log" TO "app_user";
GRANT INSERT ON TABLE "banking"."reconciliation_match_log" TO "app_user";
GRANT SELECT ON TABLE "banking"."reconciliation_match_log" TO "app_user";
GRANT UPDATE ON TABLE "banking"."reconciliation_match_log" TO "app_user";
-- relation:banking.reconciliation_match_rule [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "banking"."reconciliation_match_rule" TO "app_user";
GRANT INSERT ON TABLE "banking"."reconciliation_match_rule" TO "app_user";
GRANT SELECT ON TABLE "banking"."reconciliation_match_rule" TO "app_user";
GRANT UPDATE ON TABLE "banking"."reconciliation_match_rule" TO "app_user";
-- relation:banking.reconciliation_policy_profile [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "banking"."reconciliation_policy_profile" TO "app_user";
GRANT INSERT ON TABLE "banking"."reconciliation_policy_profile" TO "app_user";
GRANT SELECT ON TABLE "banking"."reconciliation_policy_profile" TO "app_user";
GRANT UPDATE ON TABLE "banking"."reconciliation_policy_profile" TO "app_user";
-- relation:banking.transaction_rule [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "banking"."transaction_rule" TO "app_user";
GRANT INSERT ON TABLE "banking"."transaction_rule" TO "app_user";
GRANT SELECT ON TABLE "banking"."transaction_rule" TO "app_user";
GRANT UPDATE ON TABLE "banking"."transaction_rule" TO "app_user";
-- relation:common.attachment [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "common"."attachment" TO "app_user";
GRANT INSERT ON TABLE "common"."attachment" TO "app_user";
GRANT SELECT ON TABLE "common"."attachment" TO "app_user";
GRANT UPDATE ON TABLE "common"."attachment" TO "app_user";
-- relation:cons.consolidated_balance [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "cons"."consolidated_balance" TO "app_user";
GRANT INSERT ON TABLE "cons"."consolidated_balance" TO "app_user";
GRANT SELECT ON TABLE "cons"."consolidated_balance" TO "app_user";
GRANT UPDATE ON TABLE "cons"."consolidated_balance" TO "app_user";
-- relation:cons.consolidation_run [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "cons"."consolidation_run" TO "app_user";
GRANT INSERT ON TABLE "cons"."consolidation_run" TO "app_user";
GRANT SELECT ON TABLE "cons"."consolidation_run" TO "app_user";
GRANT UPDATE ON TABLE "cons"."consolidation_run" TO "app_user";
-- relation:cons.elimination_entry [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "cons"."elimination_entry" TO "app_user";
GRANT INSERT ON TABLE "cons"."elimination_entry" TO "app_user";
GRANT SELECT ON TABLE "cons"."elimination_entry" TO "app_user";
GRANT UPDATE ON TABLE "cons"."elimination_entry" TO "app_user";
-- relation:cons.intercompany_balance [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "cons"."intercompany_balance" TO "app_user";
GRANT INSERT ON TABLE "cons"."intercompany_balance" TO "app_user";
GRANT SELECT ON TABLE "cons"."intercompany_balance" TO "app_user";
GRANT UPDATE ON TABLE "cons"."intercompany_balance" TO "app_user";
-- relation:cons.legal_entity [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "cons"."legal_entity" TO "app_user";
GRANT INSERT ON TABLE "cons"."legal_entity" TO "app_user";
GRANT SELECT ON TABLE "cons"."legal_entity" TO "app_user";
GRANT UPDATE ON TABLE "cons"."legal_entity" TO "app_user";
-- relation:cons.ownership_interest [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "cons"."ownership_interest" TO "app_user";
GRANT INSERT ON TABLE "cons"."ownership_interest" TO "app_user";
GRANT SELECT ON TABLE "cons"."ownership_interest" TO "app_user";
GRANT UPDATE ON TABLE "cons"."ownership_interest" TO "app_user";
-- relation:core_config.numbering_sequence [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "core_config"."numbering_sequence" TO "app_user";
GRANT INSERT ON TABLE "core_config"."numbering_sequence" TO "app_user";
GRANT SELECT ON TABLE "core_config"."numbering_sequence" TO "app_user";
GRANT UPDATE ON TABLE "core_config"."numbering_sequence" TO "app_user";
-- relation:core_config.system_configuration [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "core_config"."system_configuration" TO "app_user";
GRANT INSERT ON TABLE "core_config"."system_configuration" TO "app_user";
GRANT SELECT ON TABLE "core_config"."system_configuration" TO "app_user";
GRANT UPDATE ON TABLE "core_config"."system_configuration" TO "app_user";
-- relation:core_fx.currency [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "core_fx"."currency" TO "app_user";
GRANT INSERT ON TABLE "core_fx"."currency" TO "app_user";
GRANT SELECT ON TABLE "core_fx"."currency" TO "app_user";
GRANT UPDATE ON TABLE "core_fx"."currency" TO "app_user";
-- relation:core_fx.currency_translation_adjustment [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "core_fx"."currency_translation_adjustment" TO "app_user";
GRANT INSERT ON TABLE "core_fx"."currency_translation_adjustment" TO "app_user";
GRANT SELECT ON TABLE "core_fx"."currency_translation_adjustment" TO "app_user";
GRANT UPDATE ON TABLE "core_fx"."currency_translation_adjustment" TO "app_user";
-- relation:core_fx.exchange_rate [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "core_fx"."exchange_rate" TO "app_user";
GRANT INSERT ON TABLE "core_fx"."exchange_rate" TO "app_user";
GRANT SELECT ON TABLE "core_fx"."exchange_rate" TO "app_user";
GRANT UPDATE ON TABLE "core_fx"."exchange_rate" TO "app_user";
-- relation:core_fx.exchange_rate_type [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "core_fx"."exchange_rate_type" TO "app_user";
GRANT INSERT ON TABLE "core_fx"."exchange_rate_type" TO "app_user";
GRANT SELECT ON TABLE "core_fx"."exchange_rate_type" TO "app_user";
GRANT UPDATE ON TABLE "core_fx"."exchange_rate_type" TO "app_user";
-- relation:core_org.bank_directory [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "core_org"."bank_directory" TO "app_user";
GRANT INSERT ON TABLE "core_org"."bank_directory" TO "app_user";
GRANT SELECT ON TABLE "core_org"."bank_directory" TO "app_user";
GRANT UPDATE ON TABLE "core_org"."bank_directory" TO "app_user";
-- relation:core_org.business_unit [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "core_org"."business_unit" TO "app_user";
GRANT INSERT ON TABLE "core_org"."business_unit" TO "app_user";
GRANT SELECT ON TABLE "core_org"."business_unit" TO "app_user";
GRANT UPDATE ON TABLE "core_org"."business_unit" TO "app_user";
-- relation:core_org.cost_center [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "core_org"."cost_center" TO "app_user";
GRANT INSERT ON TABLE "core_org"."cost_center" TO "app_user";
GRANT SELECT ON TABLE "core_org"."cost_center" TO "app_user";
GRANT UPDATE ON TABLE "core_org"."cost_center" TO "app_user";
-- relation:core_org.location [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "core_org"."location" TO "app_user";
GRANT INSERT ON TABLE "core_org"."location" TO "app_user";
GRANT SELECT ON TABLE "core_org"."location" TO "app_user";
GRANT UPDATE ON TABLE "core_org"."location" TO "app_user";
-- relation:core_org.organization [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "core_org"."organization" TO "app_user";
GRANT INSERT ON TABLE "core_org"."organization" TO "app_user";
GRANT SELECT ON TABLE "core_org"."organization" TO "app_user";
GRANT UPDATE ON TABLE "core_org"."organization" TO "app_user";
-- relation:core_org.organization_branding [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "core_org"."organization_branding" TO "app_user";
GRANT INSERT ON TABLE "core_org"."organization_branding" TO "app_user";
GRANT SELECT ON TABLE "core_org"."organization_branding" TO "app_user";
GRANT UPDATE ON TABLE "core_org"."organization_branding" TO "app_user";
-- relation:core_org.pfa_directory [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "core_org"."pfa_directory" TO "app_user";
GRANT INSERT ON TABLE "core_org"."pfa_directory" TO "app_user";
GRANT SELECT ON TABLE "core_org"."pfa_directory" TO "app_user";
GRANT UPDATE ON TABLE "core_org"."pfa_directory" TO "app_user";
-- relation:core_org.project [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "core_org"."project" TO "app_user";
GRANT INSERT ON TABLE "core_org"."project" TO "app_user";
GRANT SELECT ON TABLE "core_org"."project" TO "app_user";
GRANT UPDATE ON TABLE "core_org"."project" TO "app_user";
-- relation:core_org.reporting_segment [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "core_org"."reporting_segment" TO "app_user";
GRANT INSERT ON TABLE "core_org"."reporting_segment" TO "app_user";
GRANT SELECT ON TABLE "core_org"."reporting_segment" TO "app_user";
GRANT UPDATE ON TABLE "core_org"."reporting_segment" TO "app_user";
-- relation:erpnext_staging.gl_entry [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "erpnext_staging"."gl_entry" TO "app_user";
GRANT INSERT ON TABLE "erpnext_staging"."gl_entry" TO "app_user";
GRANT SELECT ON TABLE "erpnext_staging"."gl_entry" TO "app_user";
GRANT UPDATE ON TABLE "erpnext_staging"."gl_entry" TO "app_user";
-- relation:erpnext_staging.purchase_invoice [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "erpnext_staging"."purchase_invoice" TO "app_user";
GRANT INSERT ON TABLE "erpnext_staging"."purchase_invoice" TO "app_user";
GRANT SELECT ON TABLE "erpnext_staging"."purchase_invoice" TO "app_user";
GRANT UPDATE ON TABLE "erpnext_staging"."purchase_invoice" TO "app_user";
-- relation:erpnext_staging.sales_invoice [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "erpnext_staging"."sales_invoice" TO "app_user";
GRANT INSERT ON TABLE "erpnext_staging"."sales_invoice" TO "app_user";
GRANT SELECT ON TABLE "erpnext_staging"."sales_invoice" TO "app_user";
GRANT UPDATE ON TABLE "erpnext_staging"."sales_invoice" TO "app_user";
-- relation:exp.expense_entry [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "exp"."expense_entry" TO "app_user";
GRANT INSERT ON TABLE "exp"."expense_entry" TO "app_user";
GRANT SELECT ON TABLE "exp"."expense_entry" TO "app_user";
GRANT UPDATE ON TABLE "exp"."expense_entry" TO "app_user";
-- relation:expense.card_transaction [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "expense"."card_transaction" TO "app_user";
GRANT INSERT ON TABLE "expense"."card_transaction" TO "app_user";
GRANT SELECT ON TABLE "expense"."card_transaction" TO "app_user";
GRANT UPDATE ON TABLE "expense"."card_transaction" TO "app_user";
-- relation:expense.cash_advance [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "expense"."cash_advance" TO "app_user";
GRANT INSERT ON TABLE "expense"."cash_advance" TO "app_user";
GRANT SELECT ON TABLE "expense"."cash_advance" TO "app_user";
GRANT UPDATE ON TABLE "expense"."cash_advance" TO "app_user";
-- relation:expense.corporate_card [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "expense"."corporate_card" TO "app_user";
GRANT INSERT ON TABLE "expense"."corporate_card" TO "app_user";
GRANT SELECT ON TABLE "expense"."corporate_card" TO "app_user";
GRANT UPDATE ON TABLE "expense"."corporate_card" TO "app_user";
-- relation:expense.expense_approver_budget_adjustment [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "expense"."expense_approver_budget_adjustment" TO "app_user";
GRANT INSERT ON TABLE "expense"."expense_approver_budget_adjustment" TO "app_user";
GRANT SELECT ON TABLE "expense"."expense_approver_budget_adjustment" TO "app_user";
GRANT UPDATE ON TABLE "expense"."expense_approver_budget_adjustment" TO "app_user";
-- relation:expense.expense_approver_limit [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "expense"."expense_approver_limit" TO "app_user";
GRANT INSERT ON TABLE "expense"."expense_approver_limit" TO "app_user";
GRANT SELECT ON TABLE "expense"."expense_approver_limit" TO "app_user";
GRANT UPDATE ON TABLE "expense"."expense_approver_limit" TO "app_user";
-- relation:expense.expense_approver_limit_reset [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "expense"."expense_approver_limit_reset" TO "app_user";
GRANT INSERT ON TABLE "expense"."expense_approver_limit_reset" TO "app_user";
GRANT SELECT ON TABLE "expense"."expense_approver_limit_reset" TO "app_user";
GRANT UPDATE ON TABLE "expense"."expense_approver_limit_reset" TO "app_user";
-- relation:expense.expense_category [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "expense"."expense_category" TO "app_user";
GRANT INSERT ON TABLE "expense"."expense_category" TO "app_user";
GRANT SELECT ON TABLE "expense"."expense_category" TO "app_user";
GRANT UPDATE ON TABLE "expense"."expense_category" TO "app_user";
-- relation:expense.expense_claim [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "expense"."expense_claim" TO "app_user";
GRANT INSERT ON TABLE "expense"."expense_claim" TO "app_user";
GRANT SELECT ON TABLE "expense"."expense_claim" TO "app_user";
GRANT UPDATE ON TABLE "expense"."expense_claim" TO "app_user";
-- relation:expense.expense_claim_action [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "expense"."expense_claim_action" TO "app_user";
GRANT INSERT ON TABLE "expense"."expense_claim_action" TO "app_user";
GRANT SELECT ON TABLE "expense"."expense_claim_action" TO "app_user";
GRANT UPDATE ON TABLE "expense"."expense_claim_action" TO "app_user";
-- relation:expense.expense_claim_approval_step [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "expense"."expense_claim_approval_step" TO "app_user";
GRANT INSERT ON TABLE "expense"."expense_claim_approval_step" TO "app_user";
GRANT SELECT ON TABLE "expense"."expense_claim_approval_step" TO "app_user";
GRANT UPDATE ON TABLE "expense"."expense_claim_approval_step" TO "app_user";
-- relation:expense.expense_claim_item [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "expense"."expense_claim_item" TO "app_user";
GRANT INSERT ON TABLE "expense"."expense_claim_item" TO "app_user";
GRANT SELECT ON TABLE "expense"."expense_claim_item" TO "app_user";
GRANT UPDATE ON TABLE "expense"."expense_claim_item" TO "app_user";
-- relation:expense.expense_limit_evaluation [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "expense"."expense_limit_evaluation" TO "app_user";
GRANT INSERT ON TABLE "expense"."expense_limit_evaluation" TO "app_user";
GRANT SELECT ON TABLE "expense"."expense_limit_evaluation" TO "app_user";
GRANT UPDATE ON TABLE "expense"."expense_limit_evaluation" TO "app_user";
-- relation:expense.expense_limit_rule [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "expense"."expense_limit_rule" TO "app_user";
GRANT INSERT ON TABLE "expense"."expense_limit_rule" TO "app_user";
GRANT SELECT ON TABLE "expense"."expense_limit_rule" TO "app_user";
GRANT UPDATE ON TABLE "expense"."expense_limit_rule" TO "app_user";
-- relation:expense.expense_period_usage [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "expense"."expense_period_usage" TO "app_user";
GRANT INSERT ON TABLE "expense"."expense_period_usage" TO "app_user";
GRANT SELECT ON TABLE "expense"."expense_period_usage" TO "app_user";
GRANT UPDATE ON TABLE "expense"."expense_period_usage" TO "app_user";
-- relation:fa.asset [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "fa"."asset" TO "app_user";
GRANT INSERT ON TABLE "fa"."asset" TO "app_user";
GRANT SELECT ON TABLE "fa"."asset" TO "app_user";
GRANT UPDATE ON TABLE "fa"."asset" TO "app_user";
-- relation:fa.asset_category [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "fa"."asset_category" TO "app_user";
GRANT INSERT ON TABLE "fa"."asset_category" TO "app_user";
GRANT SELECT ON TABLE "fa"."asset_category" TO "app_user";
GRANT UPDATE ON TABLE "fa"."asset_category" TO "app_user";
-- relation:fa.asset_component [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "fa"."asset_component" TO "app_user";
GRANT INSERT ON TABLE "fa"."asset_component" TO "app_user";
GRANT SELECT ON TABLE "fa"."asset_component" TO "app_user";
GRANT UPDATE ON TABLE "fa"."asset_component" TO "app_user";
-- relation:fa.asset_disposal [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "fa"."asset_disposal" TO "app_user";
GRANT INSERT ON TABLE "fa"."asset_disposal" TO "app_user";
GRANT SELECT ON TABLE "fa"."asset_disposal" TO "app_user";
GRANT UPDATE ON TABLE "fa"."asset_disposal" TO "app_user";
-- relation:fa.asset_impairment [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "fa"."asset_impairment" TO "app_user";
GRANT INSERT ON TABLE "fa"."asset_impairment" TO "app_user";
GRANT SELECT ON TABLE "fa"."asset_impairment" TO "app_user";
GRANT UPDATE ON TABLE "fa"."asset_impairment" TO "app_user";
-- relation:fa.asset_revaluation [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "fa"."asset_revaluation" TO "app_user";
GRANT INSERT ON TABLE "fa"."asset_revaluation" TO "app_user";
GRANT SELECT ON TABLE "fa"."asset_revaluation" TO "app_user";
GRANT UPDATE ON TABLE "fa"."asset_revaluation" TO "app_user";
-- relation:fa.cash_generating_unit [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "fa"."cash_generating_unit" TO "app_user";
GRANT INSERT ON TABLE "fa"."cash_generating_unit" TO "app_user";
GRANT SELECT ON TABLE "fa"."cash_generating_unit" TO "app_user";
GRANT UPDATE ON TABLE "fa"."cash_generating_unit" TO "app_user";
-- relation:fa.depreciation_run [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "fa"."depreciation_run" TO "app_user";
GRANT INSERT ON TABLE "fa"."depreciation_run" TO "app_user";
GRANT SELECT ON TABLE "fa"."depreciation_run" TO "app_user";
GRANT UPDATE ON TABLE "fa"."depreciation_run" TO "app_user";
-- relation:fa.depreciation_schedule [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "fa"."depreciation_schedule" TO "app_user";
GRANT INSERT ON TABLE "fa"."depreciation_schedule" TO "app_user";
GRANT SELECT ON TABLE "fa"."depreciation_schedule" TO "app_user";
GRANT UPDATE ON TABLE "fa"."depreciation_schedule" TO "app_user";
-- relation:fa.gl_reconciliation_exception [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "fa"."gl_reconciliation_exception" TO "app_user";
GRANT INSERT ON TABLE "fa"."gl_reconciliation_exception" TO "app_user";
GRANT SELECT ON TABLE "fa"."gl_reconciliation_exception" TO "app_user";
GRANT UPDATE ON TABLE "fa"."gl_reconciliation_exception" TO "app_user";
-- relation:fa.gl_reconciliation_run [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "fa"."gl_reconciliation_run" TO "app_user";
GRANT INSERT ON TABLE "fa"."gl_reconciliation_run" TO "app_user";
GRANT SELECT ON TABLE "fa"."gl_reconciliation_run" TO "app_user";
GRANT UPDATE ON TABLE "fa"."gl_reconciliation_run" TO "app_user";
-- relation:fa.maintenance_request [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "fa"."maintenance_request" TO "app_user";
GRANT INSERT ON TABLE "fa"."maintenance_request" TO "app_user";
GRANT SELECT ON TABLE "fa"."maintenance_request" TO "app_user";
GRANT UPDATE ON TABLE "fa"."maintenance_request" TO "app_user";
-- relation:fa.maintenance_status_log [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "fa"."maintenance_status_log" TO "app_user";
GRANT INSERT ON TABLE "fa"."maintenance_status_log" TO "app_user";
GRANT SELECT ON TABLE "fa"."maintenance_status_log" TO "app_user";
GRANT UPDATE ON TABLE "fa"."maintenance_status_log" TO "app_user";
-- relation:fa.maintenance_work_order [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "fa"."maintenance_work_order" TO "app_user";
GRANT INSERT ON TABLE "fa"."maintenance_work_order" TO "app_user";
GRANT SELECT ON TABLE "fa"."maintenance_work_order" TO "app_user";
GRANT UPDATE ON TABLE "fa"."maintenance_work_order" TO "app_user";
-- relation:fa.maintenance_work_order_part [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "fa"."maintenance_work_order_part" TO "app_user";
GRANT INSERT ON TABLE "fa"."maintenance_work_order_part" TO "app_user";
GRANT SELECT ON TABLE "fa"."maintenance_work_order_part" TO "app_user";
GRANT UPDATE ON TABLE "fa"."maintenance_work_order_part" TO "app_user";
-- relation:fin_inst.financial_instrument [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "fin_inst"."financial_instrument" TO "app_user";
GRANT INSERT ON TABLE "fin_inst"."financial_instrument" TO "app_user";
GRANT SELECT ON TABLE "fin_inst"."financial_instrument" TO "app_user";
GRANT UPDATE ON TABLE "fin_inst"."financial_instrument" TO "app_user";
-- relation:fin_inst.hedge_effectiveness [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "fin_inst"."hedge_effectiveness" TO "app_user";
GRANT INSERT ON TABLE "fin_inst"."hedge_effectiveness" TO "app_user";
GRANT SELECT ON TABLE "fin_inst"."hedge_effectiveness" TO "app_user";
GRANT UPDATE ON TABLE "fin_inst"."hedge_effectiveness" TO "app_user";
-- relation:fin_inst.hedge_relationship [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "fin_inst"."hedge_relationship" TO "app_user";
GRANT INSERT ON TABLE "fin_inst"."hedge_relationship" TO "app_user";
GRANT SELECT ON TABLE "fin_inst"."hedge_relationship" TO "app_user";
GRANT UPDATE ON TABLE "fin_inst"."hedge_relationship" TO "app_user";
-- relation:fin_inst.instrument_valuation [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "fin_inst"."instrument_valuation" TO "app_user";
GRANT INSERT ON TABLE "fin_inst"."instrument_valuation" TO "app_user";
GRANT SELECT ON TABLE "fin_inst"."instrument_valuation" TO "app_user";
GRANT UPDATE ON TABLE "fin_inst"."instrument_valuation" TO "app_user";
-- relation:fin_inst.interest_accrual [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "fin_inst"."interest_accrual" TO "app_user";
GRANT INSERT ON TABLE "fin_inst"."interest_accrual" TO "app_user";
GRANT SELECT ON TABLE "fin_inst"."interest_accrual" TO "app_user";
GRANT UPDATE ON TABLE "fin_inst"."interest_accrual" TO "app_user";
-- relation:fleet.fuel_log_entry [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "fleet"."fuel_log_entry" TO "app_user";
GRANT INSERT ON TABLE "fleet"."fuel_log_entry" TO "app_user";
GRANT SELECT ON TABLE "fleet"."fuel_log_entry" TO "app_user";
GRANT UPDATE ON TABLE "fleet"."fuel_log_entry" TO "app_user";
-- relation:fleet.maintenance_record [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "fleet"."maintenance_record" TO "app_user";
GRANT INSERT ON TABLE "fleet"."maintenance_record" TO "app_user";
GRANT SELECT ON TABLE "fleet"."maintenance_record" TO "app_user";
GRANT UPDATE ON TABLE "fleet"."maintenance_record" TO "app_user";
-- relation:fleet.vehicle [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "fleet"."vehicle" TO "app_user";
GRANT INSERT ON TABLE "fleet"."vehicle" TO "app_user";
GRANT SELECT ON TABLE "fleet"."vehicle" TO "app_user";
GRANT UPDATE ON TABLE "fleet"."vehicle" TO "app_user";
-- relation:fleet.vehicle_assignment [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "fleet"."vehicle_assignment" TO "app_user";
GRANT INSERT ON TABLE "fleet"."vehicle_assignment" TO "app_user";
GRANT SELECT ON TABLE "fleet"."vehicle_assignment" TO "app_user";
GRANT UPDATE ON TABLE "fleet"."vehicle_assignment" TO "app_user";
-- relation:fleet.vehicle_document [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "fleet"."vehicle_document" TO "app_user";
GRANT INSERT ON TABLE "fleet"."vehicle_document" TO "app_user";
GRANT SELECT ON TABLE "fleet"."vehicle_document" TO "app_user";
GRANT UPDATE ON TABLE "fleet"."vehicle_document" TO "app_user";
-- relation:fleet.vehicle_incident [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "fleet"."vehicle_incident" TO "app_user";
GRANT INSERT ON TABLE "fleet"."vehicle_incident" TO "app_user";
GRANT SELECT ON TABLE "fleet"."vehicle_incident" TO "app_user";
GRANT UPDATE ON TABLE "fleet"."vehicle_incident" TO "app_user";
-- relation:fleet.vehicle_reservation [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "fleet"."vehicle_reservation" TO "app_user";
GRANT INSERT ON TABLE "fleet"."vehicle_reservation" TO "app_user";
GRANT SELECT ON TABLE "fleet"."vehicle_reservation" TO "app_user";
GRANT UPDATE ON TABLE "fleet"."vehicle_reservation" TO "app_user";
-- relation:forms.form [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "forms"."form" TO "app_user";
GRANT INSERT ON TABLE "forms"."form" TO "app_user";
GRANT SELECT ON TABLE "forms"."form" TO "app_user";
GRANT UPDATE ON TABLE "forms"."form" TO "app_user";
-- relation:forms.form_answer [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "forms"."form_answer" TO "app_user";
GRANT INSERT ON TABLE "forms"."form_answer" TO "app_user";
GRANT SELECT ON TABLE "forms"."form_answer" TO "app_user";
GRANT UPDATE ON TABLE "forms"."form_answer" TO "app_user";
-- relation:forms.form_field [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "forms"."form_field" TO "app_user";
GRANT INSERT ON TABLE "forms"."form_field" TO "app_user";
GRANT SELECT ON TABLE "forms"."form_field" TO "app_user";
GRANT UPDATE ON TABLE "forms"."form_field" TO "app_user";
-- relation:forms.form_field_option [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "forms"."form_field_option" TO "app_user";
GRANT INSERT ON TABLE "forms"."form_field_option" TO "app_user";
GRANT SELECT ON TABLE "forms"."form_field_option" TO "app_user";
GRANT UPDATE ON TABLE "forms"."form_field_option" TO "app_user";
-- relation:forms.form_section [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "forms"."form_section" TO "app_user";
GRANT INSERT ON TABLE "forms"."form_section" TO "app_user";
GRANT SELECT ON TABLE "forms"."form_section" TO "app_user";
GRANT UPDATE ON TABLE "forms"."form_section" TO "app_user";
-- relation:forms.form_submission [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "forms"."form_submission" TO "app_user";
GRANT INSERT ON TABLE "forms"."form_submission" TO "app_user";
GRANT SELECT ON TABLE "forms"."form_submission" TO "app_user";
GRANT UPDATE ON TABLE "forms"."form_submission" TO "app_user";
-- relation:forms.form_version [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "forms"."form_version" TO "app_user";
GRANT INSERT ON TABLE "forms"."form_version" TO "app_user";
GRANT SELECT ON TABLE "forms"."form_version" TO "app_user";
GRANT UPDATE ON TABLE "forms"."form_version" TO "app_user";
-- relation:gl.account [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "gl"."account" TO "app_user";
GRANT INSERT ON TABLE "gl"."account" TO "app_user";
GRANT SELECT ON TABLE "gl"."account" TO "app_user";
GRANT UPDATE ON TABLE "gl"."account" TO "app_user";
-- relation:gl.account_balance [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "gl"."account_balance" TO "app_user";
GRANT INSERT ON TABLE "gl"."account_balance" TO "app_user";
GRANT SELECT ON TABLE "gl"."account_balance" TO "app_user";
GRANT UPDATE ON TABLE "gl"."account_balance" TO "app_user";
-- relation:gl.account_category [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "gl"."account_category" TO "app_user";
GRANT INSERT ON TABLE "gl"."account_category" TO "app_user";
GRANT SELECT ON TABLE "gl"."account_category" TO "app_user";
GRANT UPDATE ON TABLE "gl"."account_category" TO "app_user";
-- relation:gl.balance_refresh_queue [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "gl"."balance_refresh_queue" TO "app_user";
GRANT INSERT ON TABLE "gl"."balance_refresh_queue" TO "app_user";
GRANT SELECT ON TABLE "gl"."balance_refresh_queue" TO "app_user";
GRANT UPDATE ON TABLE "gl"."balance_refresh_queue" TO "app_user";
-- relation:gl.budget [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "gl"."budget" TO "app_user";
GRANT INSERT ON TABLE "gl"."budget" TO "app_user";
GRANT SELECT ON TABLE "gl"."budget" TO "app_user";
GRANT UPDATE ON TABLE "gl"."budget" TO "app_user";
-- relation:gl.budget_line [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "gl"."budget_line" TO "app_user";
GRANT INSERT ON TABLE "gl"."budget_line" TO "app_user";
GRANT SELECT ON TABLE "gl"."budget_line" TO "app_user";
GRANT UPDATE ON TABLE "gl"."budget_line" TO "app_user";
-- relation:gl.fiscal_period [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "gl"."fiscal_period" TO "app_user";
GRANT INSERT ON TABLE "gl"."fiscal_period" TO "app_user";
GRANT SELECT ON TABLE "gl"."fiscal_period" TO "app_user";
GRANT UPDATE ON TABLE "gl"."fiscal_period" TO "app_user";
-- relation:gl.fiscal_year [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "gl"."fiscal_year" TO "app_user";
GRANT INSERT ON TABLE "gl"."fiscal_year" TO "app_user";
GRANT SELECT ON TABLE "gl"."fiscal_year" TO "app_user";
GRANT UPDATE ON TABLE "gl"."fiscal_year" TO "app_user";
-- relation:gl.journal_entry [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "gl"."journal_entry" TO "app_user";
GRANT INSERT ON TABLE "gl"."journal_entry" TO "app_user";
GRANT SELECT ON TABLE "gl"."journal_entry" TO "app_user";
GRANT UPDATE ON TABLE "gl"."journal_entry" TO "app_user";
-- relation:gl.journal_entry_line [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "gl"."journal_entry_line" TO "app_user";
GRANT INSERT ON TABLE "gl"."journal_entry_line" TO "app_user";
GRANT SELECT ON TABLE "gl"."journal_entry_line" TO "app_user";
GRANT UPDATE ON TABLE "gl"."journal_entry_line" TO "app_user";
-- relation:gl.posted_ledger_line [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "gl"."posted_ledger_line" TO "app_user";
GRANT INSERT ON TABLE "gl"."posted_ledger_line" TO "app_user";
GRANT SELECT ON TABLE "gl"."posted_ledger_line" TO "app_user";
GRANT UPDATE ON TABLE "gl"."posted_ledger_line" TO "app_user";
-- relation:gl.posting_batch [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "gl"."posting_batch" TO "app_user";
GRANT INSERT ON TABLE "gl"."posting_batch" TO "app_user";
GRANT SELECT ON TABLE "gl"."posting_batch" TO "app_user";
GRANT UPDATE ON TABLE "gl"."posting_batch" TO "app_user";
-- relation:hr.asset_assignment [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."asset_assignment" TO "app_user";
GRANT INSERT ON TABLE "hr"."asset_assignment" TO "app_user";
GRANT SELECT ON TABLE "hr"."asset_assignment" TO "app_user";
GRANT UPDATE ON TABLE "hr"."asset_assignment" TO "app_user";
-- relation:hr.asset_assignment_movement [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."asset_assignment_movement" TO "app_user";
GRANT INSERT ON TABLE "hr"."asset_assignment_movement" TO "app_user";
GRANT SELECT ON TABLE "hr"."asset_assignment_movement" TO "app_user";
GRANT UPDATE ON TABLE "hr"."asset_assignment_movement" TO "app_user";
-- relation:hr.asset_audit_adjustment [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."asset_audit_adjustment" TO "app_user";
GRANT INSERT ON TABLE "hr"."asset_audit_adjustment" TO "app_user";
GRANT SELECT ON TABLE "hr"."asset_audit_adjustment" TO "app_user";
GRANT UPDATE ON TABLE "hr"."asset_audit_adjustment" TO "app_user";
-- relation:hr.asset_audit_discrepancy [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."asset_audit_discrepancy" TO "app_user";
GRANT INSERT ON TABLE "hr"."asset_audit_discrepancy" TO "app_user";
GRANT SELECT ON TABLE "hr"."asset_audit_discrepancy" TO "app_user";
GRANT UPDATE ON TABLE "hr"."asset_audit_discrepancy" TO "app_user";
-- relation:hr.asset_audit_line [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."asset_audit_line" TO "app_user";
GRANT INSERT ON TABLE "hr"."asset_audit_line" TO "app_user";
GRANT SELECT ON TABLE "hr"."asset_audit_line" TO "app_user";
GRANT UPDATE ON TABLE "hr"."asset_audit_line" TO "app_user";
-- relation:hr.asset_audit_plan [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."asset_audit_plan" TO "app_user";
GRANT INSERT ON TABLE "hr"."asset_audit_plan" TO "app_user";
GRANT SELECT ON TABLE "hr"."asset_audit_plan" TO "app_user";
GRANT UPDATE ON TABLE "hr"."asset_audit_plan" TO "app_user";
-- relation:hr.asset_lifecycle_event [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."asset_lifecycle_event" TO "app_user";
GRANT INSERT ON TABLE "hr"."asset_lifecycle_event" TO "app_user";
GRANT SELECT ON TABLE "hr"."asset_lifecycle_event" TO "app_user";
GRANT UPDATE ON TABLE "hr"."asset_lifecycle_event" TO "app_user";
-- relation:hr.asset_tracking_event [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."asset_tracking_event" TO "app_user";
GRANT INSERT ON TABLE "hr"."asset_tracking_event" TO "app_user";
GRANT SELECT ON TABLE "hr"."asset_tracking_event" TO "app_user";
GRANT UPDATE ON TABLE "hr"."asset_tracking_event" TO "app_user";
-- relation:hr.case_action [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."case_action" TO "app_user";
GRANT INSERT ON TABLE "hr"."case_action" TO "app_user";
GRANT SELECT ON TABLE "hr"."case_action" TO "app_user";
GRANT UPDATE ON TABLE "hr"."case_action" TO "app_user";
-- relation:hr.case_document [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."case_document" TO "app_user";
GRANT INSERT ON TABLE "hr"."case_document" TO "app_user";
GRANT SELECT ON TABLE "hr"."case_document" TO "app_user";
GRANT UPDATE ON TABLE "hr"."case_document" TO "app_user";
-- relation:hr.case_response [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."case_response" TO "app_user";
GRANT INSERT ON TABLE "hr"."case_response" TO "app_user";
GRANT SELECT ON TABLE "hr"."case_response" TO "app_user";
GRANT UPDATE ON TABLE "hr"."case_response" TO "app_user";
-- relation:hr.case_witness [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."case_witness" TO "app_user";
GRANT INSERT ON TABLE "hr"."case_witness" TO "app_user";
GRANT SELECT ON TABLE "hr"."case_witness" TO "app_user";
GRANT UPDATE ON TABLE "hr"."case_witness" TO "app_user";
-- relation:hr.checklist_template [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."checklist_template" TO "app_user";
GRANT INSERT ON TABLE "hr"."checklist_template" TO "app_user";
GRANT SELECT ON TABLE "hr"."checklist_template" TO "app_user";
GRANT UPDATE ON TABLE "hr"."checklist_template" TO "app_user";
-- relation:hr.checklist_template_item [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."checklist_template_item" TO "app_user";
GRANT INSERT ON TABLE "hr"."checklist_template_item" TO "app_user";
GRANT SELECT ON TABLE "hr"."checklist_template_item" TO "app_user";
GRANT UPDATE ON TABLE "hr"."checklist_template_item" TO "app_user";
-- relation:hr.clearance_item [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."clearance_item" TO "app_user";
GRANT INSERT ON TABLE "hr"."clearance_item" TO "app_user";
GRANT SELECT ON TABLE "hr"."clearance_item" TO "app_user";
GRANT UPDATE ON TABLE "hr"."clearance_item" TO "app_user";
-- relation:hr.competency [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."competency" TO "app_user";
GRANT INSERT ON TABLE "hr"."competency" TO "app_user";
GRANT SELECT ON TABLE "hr"."competency" TO "app_user";
GRANT UPDATE ON TABLE "hr"."competency" TO "app_user";
-- relation:hr.department [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."department" TO "app_user";
GRANT INSERT ON TABLE "hr"."department" TO "app_user";
GRANT SELECT ON TABLE "hr"."department" TO "app_user";
GRANT UPDATE ON TABLE "hr"."department" TO "app_user";
-- relation:hr.designation [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."designation" TO "app_user";
GRANT INSERT ON TABLE "hr"."designation" TO "app_user";
GRANT SELECT ON TABLE "hr"."designation" TO "app_user";
GRANT UPDATE ON TABLE "hr"."designation" TO "app_user";
-- relation:hr.disciplinary_case [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."disciplinary_case" TO "app_user";
GRANT INSERT ON TABLE "hr"."disciplinary_case" TO "app_user";
GRANT SELECT ON TABLE "hr"."disciplinary_case" TO "app_user";
GRANT UPDATE ON TABLE "hr"."disciplinary_case" TO "app_user";
-- relation:hr.employee [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."employee" TO "app_user";
GRANT INSERT ON TABLE "hr"."employee" TO "app_user";
GRANT SELECT ON TABLE "hr"."employee" TO "app_user";
GRANT UPDATE ON TABLE "hr"."employee" TO "app_user";
-- relation:hr.employee_certification [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."employee_certification" TO "app_user";
GRANT INSERT ON TABLE "hr"."employee_certification" TO "app_user";
GRANT SELECT ON TABLE "hr"."employee_certification" TO "app_user";
GRANT UPDATE ON TABLE "hr"."employee_certification" TO "app_user";
-- relation:hr.employee_demotion [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."employee_demotion" TO "app_user";
GRANT INSERT ON TABLE "hr"."employee_demotion" TO "app_user";
GRANT SELECT ON TABLE "hr"."employee_demotion" TO "app_user";
GRANT UPDATE ON TABLE "hr"."employee_demotion" TO "app_user";
-- relation:hr.employee_demotion_detail [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."employee_demotion_detail" TO "app_user";
GRANT INSERT ON TABLE "hr"."employee_demotion_detail" TO "app_user";
GRANT SELECT ON TABLE "hr"."employee_demotion_detail" TO "app_user";
GRANT UPDATE ON TABLE "hr"."employee_demotion_detail" TO "app_user";
-- relation:hr.employee_dependent [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."employee_dependent" TO "app_user";
GRANT INSERT ON TABLE "hr"."employee_dependent" TO "app_user";
GRANT SELECT ON TABLE "hr"."employee_dependent" TO "app_user";
GRANT UPDATE ON TABLE "hr"."employee_dependent" TO "app_user";
-- relation:hr.employee_document [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."employee_document" TO "app_user";
GRANT INSERT ON TABLE "hr"."employee_document" TO "app_user";
GRANT SELECT ON TABLE "hr"."employee_document" TO "app_user";
GRANT UPDATE ON TABLE "hr"."employee_document" TO "app_user";
-- relation:hr.employee_grade [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."employee_grade" TO "app_user";
GRANT INSERT ON TABLE "hr"."employee_grade" TO "app_user";
GRANT SELECT ON TABLE "hr"."employee_grade" TO "app_user";
GRANT UPDATE ON TABLE "hr"."employee_grade" TO "app_user";
-- relation:hr.employee_info_change_batch [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."employee_info_change_batch" TO "app_user";
GRANT INSERT ON TABLE "hr"."employee_info_change_batch" TO "app_user";
GRANT SELECT ON TABLE "hr"."employee_info_change_batch" TO "app_user";
GRANT UPDATE ON TABLE "hr"."employee_info_change_batch" TO "app_user";
-- relation:hr.employee_info_change_request [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."employee_info_change_request" TO "app_user";
GRANT INSERT ON TABLE "hr"."employee_info_change_request" TO "app_user";
GRANT SELECT ON TABLE "hr"."employee_info_change_request" TO "app_user";
GRANT UPDATE ON TABLE "hr"."employee_info_change_request" TO "app_user";
-- relation:hr.employee_onboarding [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."employee_onboarding" TO "app_user";
GRANT INSERT ON TABLE "hr"."employee_onboarding" TO "app_user";
GRANT SELECT ON TABLE "hr"."employee_onboarding" TO "app_user";
GRANT UPDATE ON TABLE "hr"."employee_onboarding" TO "app_user";
-- relation:hr.employee_onboarding_activity [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."employee_onboarding_activity" TO "app_user";
GRANT INSERT ON TABLE "hr"."employee_onboarding_activity" TO "app_user";
GRANT SELECT ON TABLE "hr"."employee_onboarding_activity" TO "app_user";
GRANT UPDATE ON TABLE "hr"."employee_onboarding_activity" TO "app_user";
-- relation:hr.employee_promotion [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."employee_promotion" TO "app_user";
GRANT INSERT ON TABLE "hr"."employee_promotion" TO "app_user";
GRANT SELECT ON TABLE "hr"."employee_promotion" TO "app_user";
GRANT UPDATE ON TABLE "hr"."employee_promotion" TO "app_user";
-- relation:hr.employee_promotion_detail [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."employee_promotion_detail" TO "app_user";
GRANT INSERT ON TABLE "hr"."employee_promotion_detail" TO "app_user";
GRANT SELECT ON TABLE "hr"."employee_promotion_detail" TO "app_user";
GRANT UPDATE ON TABLE "hr"."employee_promotion_detail" TO "app_user";
-- relation:hr.employee_qualification [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."employee_qualification" TO "app_user";
GRANT INSERT ON TABLE "hr"."employee_qualification" TO "app_user";
GRANT SELECT ON TABLE "hr"."employee_qualification" TO "app_user";
GRANT UPDATE ON TABLE "hr"."employee_qualification" TO "app_user";
-- relation:hr.employee_separation [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."employee_separation" TO "app_user";
GRANT INSERT ON TABLE "hr"."employee_separation" TO "app_user";
GRANT SELECT ON TABLE "hr"."employee_separation" TO "app_user";
GRANT UPDATE ON TABLE "hr"."employee_separation" TO "app_user";
-- relation:hr.employee_separation_activity [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."employee_separation_activity" TO "app_user";
GRANT INSERT ON TABLE "hr"."employee_separation_activity" TO "app_user";
GRANT SELECT ON TABLE "hr"."employee_separation_activity" TO "app_user";
GRANT UPDATE ON TABLE "hr"."employee_separation_activity" TO "app_user";
-- relation:hr.employee_skill [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."employee_skill" TO "app_user";
GRANT INSERT ON TABLE "hr"."employee_skill" TO "app_user";
GRANT SELECT ON TABLE "hr"."employee_skill" TO "app_user";
GRANT UPDATE ON TABLE "hr"."employee_skill" TO "app_user";
-- relation:hr.employee_transfer [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."employee_transfer" TO "app_user";
GRANT INSERT ON TABLE "hr"."employee_transfer" TO "app_user";
GRANT SELECT ON TABLE "hr"."employee_transfer" TO "app_user";
GRANT UPDATE ON TABLE "hr"."employee_transfer" TO "app_user";
-- relation:hr.employee_transfer_detail [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."employee_transfer_detail" TO "app_user";
GRANT INSERT ON TABLE "hr"."employee_transfer_detail" TO "app_user";
GRANT SELECT ON TABLE "hr"."employee_transfer_detail" TO "app_user";
GRANT UPDATE ON TABLE "hr"."employee_transfer_detail" TO "app_user";
-- relation:hr.employment_contract [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."employment_contract" TO "app_user";
GRANT INSERT ON TABLE "hr"."employment_contract" TO "app_user";
GRANT SELECT ON TABLE "hr"."employment_contract" TO "app_user";
GRANT UPDATE ON TABLE "hr"."employment_contract" TO "app_user";
-- relation:hr.exit_interview [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."exit_interview" TO "app_user";
GRANT INSERT ON TABLE "hr"."exit_interview" TO "app_user";
GRANT SELECT ON TABLE "hr"."exit_interview" TO "app_user";
GRANT UPDATE ON TABLE "hr"."exit_interview" TO "app_user";
-- relation:hr.grievance [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."grievance" TO "app_user";
GRANT INSERT ON TABLE "hr"."grievance" TO "app_user";
GRANT SELECT ON TABLE "hr"."grievance" TO "app_user";
GRANT UPDATE ON TABLE "hr"."grievance" TO "app_user";
-- relation:hr.hr_document [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."hr_document" TO "app_user";
GRANT INSERT ON TABLE "hr"."hr_document" TO "app_user";
GRANT SELECT ON TABLE "hr"."hr_document" TO "app_user";
GRANT UPDATE ON TABLE "hr"."hr_document" TO "app_user";
-- relation:hr.hr_document_acknowledgment [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."hr_document_acknowledgment" TO "app_user";
GRANT INSERT ON TABLE "hr"."hr_document_acknowledgment" TO "app_user";
GRANT SELECT ON TABLE "hr"."hr_document_acknowledgment" TO "app_user";
GRANT UPDATE ON TABLE "hr"."hr_document_acknowledgment" TO "app_user";
-- relation:hr.job_description [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."job_description" TO "app_user";
GRANT INSERT ON TABLE "hr"."job_description" TO "app_user";
GRANT SELECT ON TABLE "hr"."job_description" TO "app_user";
GRANT UPDATE ON TABLE "hr"."job_description" TO "app_user";
-- relation:hr.job_description_competency [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."job_description_competency" TO "app_user";
GRANT INSERT ON TABLE "hr"."job_description_competency" TO "app_user";
GRANT SELECT ON TABLE "hr"."job_description_competency" TO "app_user";
GRANT UPDATE ON TABLE "hr"."job_description_competency" TO "app_user";
-- relation:hr.position [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."position" TO "app_user";
GRANT INSERT ON TABLE "hr"."position" TO "app_user";
GRANT SELECT ON TABLE "hr"."position" TO "app_user";
GRANT UPDATE ON TABLE "hr"."position" TO "app_user";
-- relation:hr.position_assignment [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."position_assignment" TO "app_user";
GRANT INSERT ON TABLE "hr"."position_assignment" TO "app_user";
GRANT SELECT ON TABLE "hr"."position_assignment" TO "app_user";
GRANT UPDATE ON TABLE "hr"."position_assignment" TO "app_user";
-- relation:hr.salary_review [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."salary_review" TO "app_user";
GRANT INSERT ON TABLE "hr"."salary_review" TO "app_user";
GRANT SELECT ON TABLE "hr"."salary_review" TO "app_user";
GRANT UPDATE ON TABLE "hr"."salary_review" TO "app_user";
-- relation:hr.skill [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."skill" TO "app_user";
GRANT INSERT ON TABLE "hr"."skill" TO "app_user";
GRANT SELECT ON TABLE "hr"."skill" TO "app_user";
GRANT UPDATE ON TABLE "hr"."skill" TO "app_user";
-- relation:hr.succession_candidate [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."succession_candidate" TO "app_user";
GRANT INSERT ON TABLE "hr"."succession_candidate" TO "app_user";
GRANT SELECT ON TABLE "hr"."succession_candidate" TO "app_user";
GRANT UPDATE ON TABLE "hr"."succession_candidate" TO "app_user";
-- relation:hr.succession_plan [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."succession_plan" TO "app_user";
GRANT INSERT ON TABLE "hr"."succession_plan" TO "app_user";
GRANT SELECT ON TABLE "hr"."succession_plan" TO "app_user";
GRANT UPDATE ON TABLE "hr"."succession_plan" TO "app_user";
-- relation:hr.survey [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."survey" TO "app_user";
GRANT INSERT ON TABLE "hr"."survey" TO "app_user";
GRANT SELECT ON TABLE "hr"."survey" TO "app_user";
GRANT UPDATE ON TABLE "hr"."survey" TO "app_user";
-- relation:hr.survey_answer [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."survey_answer" TO "app_user";
GRANT INSERT ON TABLE "hr"."survey_answer" TO "app_user";
GRANT SELECT ON TABLE "hr"."survey_answer" TO "app_user";
GRANT UPDATE ON TABLE "hr"."survey_answer" TO "app_user";
-- relation:hr.survey_question [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."survey_question" TO "app_user";
GRANT INSERT ON TABLE "hr"."survey_question" TO "app_user";
GRANT SELECT ON TABLE "hr"."survey_question" TO "app_user";
GRANT UPDATE ON TABLE "hr"."survey_question" TO "app_user";
-- relation:hr.survey_response [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "hr"."survey_response" TO "app_user";
GRANT INSERT ON TABLE "hr"."survey_response" TO "app_user";
GRANT SELECT ON TABLE "hr"."survey_response" TO "app_user";
GRANT UPDATE ON TABLE "hr"."survey_response" TO "app_user";
-- relation:inv.bill_of_materials [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "inv"."bill_of_materials" TO "app_user";
GRANT INSERT ON TABLE "inv"."bill_of_materials" TO "app_user";
GRANT SELECT ON TABLE "inv"."bill_of_materials" TO "app_user";
GRANT UPDATE ON TABLE "inv"."bill_of_materials" TO "app_user";
-- relation:inv.bom_component [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "inv"."bom_component" TO "app_user";
GRANT INSERT ON TABLE "inv"."bom_component" TO "app_user";
GRANT SELECT ON TABLE "inv"."bom_component" TO "app_user";
GRANT UPDATE ON TABLE "inv"."bom_component" TO "app_user";
-- relation:inv.inventory_count [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "inv"."inventory_count" TO "app_user";
GRANT INSERT ON TABLE "inv"."inventory_count" TO "app_user";
GRANT SELECT ON TABLE "inv"."inventory_count" TO "app_user";
GRANT UPDATE ON TABLE "inv"."inventory_count" TO "app_user";
-- relation:inv.inventory_count_line [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "inv"."inventory_count_line" TO "app_user";
GRANT INSERT ON TABLE "inv"."inventory_count_line" TO "app_user";
GRANT SELECT ON TABLE "inv"."inventory_count_line" TO "app_user";
GRANT UPDATE ON TABLE "inv"."inventory_count_line" TO "app_user";
-- relation:inv.inventory_lot [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "inv"."inventory_lot" TO "app_user";
GRANT INSERT ON TABLE "inv"."inventory_lot" TO "app_user";
GRANT SELECT ON TABLE "inv"."inventory_lot" TO "app_user";
GRANT UPDATE ON TABLE "inv"."inventory_lot" TO "app_user";
-- relation:inv.inventory_lot_balance [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "inv"."inventory_lot_balance" TO "app_user";
GRANT INSERT ON TABLE "inv"."inventory_lot_balance" TO "app_user";
GRANT SELECT ON TABLE "inv"."inventory_lot_balance" TO "app_user";
GRANT UPDATE ON TABLE "inv"."inventory_lot_balance" TO "app_user";
-- relation:inv.inventory_return [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "inv"."inventory_return" TO "app_user";
GRANT INSERT ON TABLE "inv"."inventory_return" TO "app_user";
GRANT SELECT ON TABLE "inv"."inventory_return" TO "app_user";
GRANT UPDATE ON TABLE "inv"."inventory_return" TO "app_user";
-- relation:inv.inventory_serial [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "inv"."inventory_serial" TO "app_user";
GRANT INSERT ON TABLE "inv"."inventory_serial" TO "app_user";
GRANT SELECT ON TABLE "inv"."inventory_serial" TO "app_user";
GRANT UPDATE ON TABLE "inv"."inventory_serial" TO "app_user";
-- relation:inv.inventory_serial_movement [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "inv"."inventory_serial_movement" TO "app_user";
GRANT INSERT ON TABLE "inv"."inventory_serial_movement" TO "app_user";
GRANT SELECT ON TABLE "inv"."inventory_serial_movement" TO "app_user";
GRANT UPDATE ON TABLE "inv"."inventory_serial_movement" TO "app_user";
-- relation:inv.inventory_transaction [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "inv"."inventory_transaction" TO "app_user";
GRANT INSERT ON TABLE "inv"."inventory_transaction" TO "app_user";
GRANT SELECT ON TABLE "inv"."inventory_transaction" TO "app_user";
GRANT UPDATE ON TABLE "inv"."inventory_transaction" TO "app_user";
-- relation:inv.inventory_valuation [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "inv"."inventory_valuation" TO "app_user";
GRANT INSERT ON TABLE "inv"."inventory_valuation" TO "app_user";
GRANT SELECT ON TABLE "inv"."inventory_valuation" TO "app_user";
GRANT UPDATE ON TABLE "inv"."inventory_valuation" TO "app_user";
-- relation:inv.item [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "inv"."item" TO "app_user";
GRANT INSERT ON TABLE "inv"."item" TO "app_user";
GRANT SELECT ON TABLE "inv"."item" TO "app_user";
GRANT UPDATE ON TABLE "inv"."item" TO "app_user";
-- relation:inv.item_category [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "inv"."item_category" TO "app_user";
GRANT INSERT ON TABLE "inv"."item_category" TO "app_user";
GRANT SELECT ON TABLE "inv"."item_category" TO "app_user";
GRANT UPDATE ON TABLE "inv"."item_category" TO "app_user";
-- relation:inv.item_wac_ledger [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "inv"."item_wac_ledger" TO "app_user";
GRANT INSERT ON TABLE "inv"."item_wac_ledger" TO "app_user";
GRANT SELECT ON TABLE "inv"."item_wac_ledger" TO "app_user";
GRANT UPDATE ON TABLE "inv"."item_wac_ledger" TO "app_user";
-- relation:inv.material_request [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "inv"."material_request" TO "app_user";
GRANT INSERT ON TABLE "inv"."material_request" TO "app_user";
GRANT SELECT ON TABLE "inv"."material_request" TO "app_user";
GRANT UPDATE ON TABLE "inv"."material_request" TO "app_user";
-- relation:inv.material_request_item [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "inv"."material_request_item" TO "app_user";
GRANT INSERT ON TABLE "inv"."material_request_item" TO "app_user";
GRANT SELECT ON TABLE "inv"."material_request_item" TO "app_user";
GRANT UPDATE ON TABLE "inv"."material_request_item" TO "app_user";
-- relation:inv.price_list [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "inv"."price_list" TO "app_user";
GRANT INSERT ON TABLE "inv"."price_list" TO "app_user";
GRANT SELECT ON TABLE "inv"."price_list" TO "app_user";
GRANT UPDATE ON TABLE "inv"."price_list" TO "app_user";
-- relation:inv.price_list_item [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "inv"."price_list_item" TO "app_user";
GRANT INSERT ON TABLE "inv"."price_list_item" TO "app_user";
GRANT SELECT ON TABLE "inv"."price_list_item" TO "app_user";
GRANT UPDATE ON TABLE "inv"."price_list_item" TO "app_user";
-- relation:inv.stock_reservation [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "inv"."stock_reservation" TO "app_user";
GRANT INSERT ON TABLE "inv"."stock_reservation" TO "app_user";
GRANT SELECT ON TABLE "inv"."stock_reservation" TO "app_user";
GRANT UPDATE ON TABLE "inv"."stock_reservation" TO "app_user";
-- relation:inv.warehouse [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "inv"."warehouse" TO "app_user";
GRANT INSERT ON TABLE "inv"."warehouse" TO "app_user";
GRANT SELECT ON TABLE "inv"."warehouse" TO "app_user";
GRANT UPDATE ON TABLE "inv"."warehouse" TO "app_user";
-- relation:inv.warehouse_location [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "inv"."warehouse_location" TO "app_user";
GRANT INSERT ON TABLE "inv"."warehouse_location" TO "app_user";
GRANT SELECT ON TABLE "inv"."warehouse_location" TO "app_user";
GRANT UPDATE ON TABLE "inv"."warehouse_location" TO "app_user";
-- relation:ipsas.allotment [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ipsas"."allotment" TO "app_user";
GRANT INSERT ON TABLE "ipsas"."allotment" TO "app_user";
GRANT SELECT ON TABLE "ipsas"."allotment" TO "app_user";
GRANT UPDATE ON TABLE "ipsas"."allotment" TO "app_user";
-- relation:ipsas.appropriation [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ipsas"."appropriation" TO "app_user";
GRANT INSERT ON TABLE "ipsas"."appropriation" TO "app_user";
GRANT SELECT ON TABLE "ipsas"."appropriation" TO "app_user";
GRANT UPDATE ON TABLE "ipsas"."appropriation" TO "app_user";
-- relation:ipsas.coa_segment_definition [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ipsas"."coa_segment_definition" TO "app_user";
GRANT INSERT ON TABLE "ipsas"."coa_segment_definition" TO "app_user";
GRANT SELECT ON TABLE "ipsas"."coa_segment_definition" TO "app_user";
GRANT UPDATE ON TABLE "ipsas"."coa_segment_definition" TO "app_user";
-- relation:ipsas.coa_segment_value [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ipsas"."coa_segment_value" TO "app_user";
GRANT INSERT ON TABLE "ipsas"."coa_segment_value" TO "app_user";
GRANT SELECT ON TABLE "ipsas"."coa_segment_value" TO "app_user";
GRANT UPDATE ON TABLE "ipsas"."coa_segment_value" TO "app_user";
-- relation:ipsas.commitment [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ipsas"."commitment" TO "app_user";
GRANT INSERT ON TABLE "ipsas"."commitment" TO "app_user";
GRANT SELECT ON TABLE "ipsas"."commitment" TO "app_user";
GRANT UPDATE ON TABLE "ipsas"."commitment" TO "app_user";
-- relation:ipsas.commitment_line [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ipsas"."commitment_line" TO "app_user";
GRANT INSERT ON TABLE "ipsas"."commitment_line" TO "app_user";
GRANT SELECT ON TABLE "ipsas"."commitment_line" TO "app_user";
GRANT UPDATE ON TABLE "ipsas"."commitment_line" TO "app_user";
-- relation:ipsas.fund [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ipsas"."fund" TO "app_user";
GRANT INSERT ON TABLE "ipsas"."fund" TO "app_user";
GRANT SELECT ON TABLE "ipsas"."fund" TO "app_user";
GRANT UPDATE ON TABLE "ipsas"."fund" TO "app_user";
-- relation:ipsas.virement [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "ipsas"."virement" TO "app_user";
GRANT INSERT ON TABLE "ipsas"."virement" TO "app_user";
GRANT SELECT ON TABLE "ipsas"."virement" TO "app_user";
GRANT UPDATE ON TABLE "ipsas"."virement" TO "app_user";
-- relation:lease.lease_asset [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "lease"."lease_asset" TO "app_user";
GRANT INSERT ON TABLE "lease"."lease_asset" TO "app_user";
GRANT SELECT ON TABLE "lease"."lease_asset" TO "app_user";
GRANT UPDATE ON TABLE "lease"."lease_asset" TO "app_user";
-- relation:lease.lease_contract [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "lease"."lease_contract" TO "app_user";
GRANT INSERT ON TABLE "lease"."lease_contract" TO "app_user";
GRANT SELECT ON TABLE "lease"."lease_contract" TO "app_user";
GRANT UPDATE ON TABLE "lease"."lease_contract" TO "app_user";
-- relation:lease.lease_liability [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "lease"."lease_liability" TO "app_user";
GRANT INSERT ON TABLE "lease"."lease_liability" TO "app_user";
GRANT SELECT ON TABLE "lease"."lease_liability" TO "app_user";
GRANT UPDATE ON TABLE "lease"."lease_liability" TO "app_user";
-- relation:lease.lease_modification [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "lease"."lease_modification" TO "app_user";
GRANT INSERT ON TABLE "lease"."lease_modification" TO "app_user";
GRANT SELECT ON TABLE "lease"."lease_modification" TO "app_user";
GRANT UPDATE ON TABLE "lease"."lease_modification" TO "app_user";
-- relation:lease.lease_payment_schedule [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "lease"."lease_payment_schedule" TO "app_user";
GRANT INSERT ON TABLE "lease"."lease_payment_schedule" TO "app_user";
GRANT SELECT ON TABLE "lease"."lease_payment_schedule" TO "app_user";
GRANT UPDATE ON TABLE "lease"."lease_payment_schedule" TO "app_user";
-- relation:leave.holiday [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "leave"."holiday" TO "app_user";
GRANT INSERT ON TABLE "leave"."holiday" TO "app_user";
GRANT SELECT ON TABLE "leave"."holiday" TO "app_user";
GRANT UPDATE ON TABLE "leave"."holiday" TO "app_user";
-- relation:leave.holiday_list [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "leave"."holiday_list" TO "app_user";
GRANT INSERT ON TABLE "leave"."holiday_list" TO "app_user";
GRANT SELECT ON TABLE "leave"."holiday_list" TO "app_user";
GRANT UPDATE ON TABLE "leave"."holiday_list" TO "app_user";
-- relation:leave.leave_allocation [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "leave"."leave_allocation" TO "app_user";
GRANT INSERT ON TABLE "leave"."leave_allocation" TO "app_user";
GRANT SELECT ON TABLE "leave"."leave_allocation" TO "app_user";
GRANT UPDATE ON TABLE "leave"."leave_allocation" TO "app_user";
-- relation:leave.leave_application [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "leave"."leave_application" TO "app_user";
GRANT INSERT ON TABLE "leave"."leave_application" TO "app_user";
GRANT SELECT ON TABLE "leave"."leave_application" TO "app_user";
GRANT UPDATE ON TABLE "leave"."leave_application" TO "app_user";
-- relation:leave.leave_type [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "leave"."leave_type" TO "app_user";
GRANT INSERT ON TABLE "leave"."leave_type" TO "app_user";
GRANT SELECT ON TABLE "leave"."leave_type" TO "app_user";
GRANT UPDATE ON TABLE "leave"."leave_type" TO "app_user";
-- relation:migration.company_org_map [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "migration"."company_org_map" TO "app_user";
GRANT INSERT ON TABLE "migration"."company_org_map" TO "app_user";
GRANT SELECT ON TABLE "migration"."company_org_map" TO "app_user";
GRANT UPDATE ON TABLE "migration"."company_org_map" TO "app_user";
-- relation:migration.id_mapping [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "migration"."id_mapping" TO "app_user";
GRANT INSERT ON TABLE "migration"."id_mapping" TO "app_user";
GRANT SELECT ON TABLE "migration"."id_mapping" TO "app_user";
GRANT UPDATE ON TABLE "migration"."id_mapping" TO "app_user";
-- relation:payments.payment_intent [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "payments"."payment_intent" TO "app_user";
GRANT INSERT ON TABLE "payments"."payment_intent" TO "app_user";
GRANT SELECT ON TABLE "payments"."payment_intent" TO "app_user";
GRANT UPDATE ON TABLE "payments"."payment_intent" TO "app_user";
-- relation:payments.payment_webhook [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "payments"."payment_webhook" TO "app_user";
GRANT INSERT ON TABLE "payments"."payment_webhook" TO "app_user";
GRANT SELECT ON TABLE "payments"."payment_webhook" TO "app_user";
GRANT UPDATE ON TABLE "payments"."payment_webhook" TO "app_user";
-- relation:payments.remita_rrr [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "payments"."remita_rrr" TO "app_user";
GRANT INSERT ON TABLE "payments"."remita_rrr" TO "app_user";
GRANT SELECT ON TABLE "payments"."remita_rrr" TO "app_user";
GRANT UPDATE ON TABLE "payments"."remita_rrr" TO "app_user";
-- relation:payments.transfer_batch [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "payments"."transfer_batch" TO "app_user";
GRANT INSERT ON TABLE "payments"."transfer_batch" TO "app_user";
GRANT SELECT ON TABLE "payments"."transfer_batch" TO "app_user";
GRANT UPDATE ON TABLE "payments"."transfer_batch" TO "app_user";
-- relation:payments.transfer_batch_item [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "payments"."transfer_batch_item" TO "app_user";
GRANT INSERT ON TABLE "payments"."transfer_batch_item" TO "app_user";
GRANT SELECT ON TABLE "payments"."transfer_batch_item" TO "app_user";
GRANT UPDATE ON TABLE "payments"."transfer_batch_item" TO "app_user";
-- relation:payroll.employee_loan [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "payroll"."employee_loan" TO "app_user";
GRANT INSERT ON TABLE "payroll"."employee_loan" TO "app_user";
GRANT SELECT ON TABLE "payroll"."employee_loan" TO "app_user";
GRANT UPDATE ON TABLE "payroll"."employee_loan" TO "app_user";
-- relation:payroll.employee_tax_profile [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "payroll"."employee_tax_profile" TO "app_user";
GRANT INSERT ON TABLE "payroll"."employee_tax_profile" TO "app_user";
GRANT SELECT ON TABLE "payroll"."employee_tax_profile" TO "app_user";
GRANT UPDATE ON TABLE "payroll"."employee_tax_profile" TO "app_user";
-- relation:payroll.loan_repayment [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "payroll"."loan_repayment" TO "app_user";
GRANT INSERT ON TABLE "payroll"."loan_repayment" TO "app_user";
GRANT SELECT ON TABLE "payroll"."loan_repayment" TO "app_user";
GRANT UPDATE ON TABLE "payroll"."loan_repayment" TO "app_user";
-- relation:payroll.loan_type [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "payroll"."loan_type" TO "app_user";
GRANT INSERT ON TABLE "payroll"."loan_type" TO "app_user";
GRANT SELECT ON TABLE "payroll"."loan_type" TO "app_user";
GRANT UPDATE ON TABLE "payroll"."loan_type" TO "app_user";
-- relation:payroll.payroll_entry [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "payroll"."payroll_entry" TO "app_user";
GRANT INSERT ON TABLE "payroll"."payroll_entry" TO "app_user";
GRANT SELECT ON TABLE "payroll"."payroll_entry" TO "app_user";
GRANT UPDATE ON TABLE "payroll"."payroll_entry" TO "app_user";
-- relation:payroll.salary_component [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "payroll"."salary_component" TO "app_user";
GRANT INSERT ON TABLE "payroll"."salary_component" TO "app_user";
GRANT SELECT ON TABLE "payroll"."salary_component" TO "app_user";
GRANT UPDATE ON TABLE "payroll"."salary_component" TO "app_user";
-- relation:payroll.salary_slip [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "payroll"."salary_slip" TO "app_user";
GRANT INSERT ON TABLE "payroll"."salary_slip" TO "app_user";
GRANT SELECT ON TABLE "payroll"."salary_slip" TO "app_user";
GRANT UPDATE ON TABLE "payroll"."salary_slip" TO "app_user";
-- relation:payroll.salary_slip_deduction [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "payroll"."salary_slip_deduction" TO "app_user";
GRANT INSERT ON TABLE "payroll"."salary_slip_deduction" TO "app_user";
GRANT SELECT ON TABLE "payroll"."salary_slip_deduction" TO "app_user";
GRANT UPDATE ON TABLE "payroll"."salary_slip_deduction" TO "app_user";
-- relation:payroll.salary_slip_earning [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "payroll"."salary_slip_earning" TO "app_user";
GRANT INSERT ON TABLE "payroll"."salary_slip_earning" TO "app_user";
GRANT SELECT ON TABLE "payroll"."salary_slip_earning" TO "app_user";
GRANT UPDATE ON TABLE "payroll"."salary_slip_earning" TO "app_user";
-- relation:payroll.salary_slip_loan_deduction [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "payroll"."salary_slip_loan_deduction" TO "app_user";
GRANT INSERT ON TABLE "payroll"."salary_slip_loan_deduction" TO "app_user";
GRANT SELECT ON TABLE "payroll"."salary_slip_loan_deduction" TO "app_user";
GRANT UPDATE ON TABLE "payroll"."salary_slip_loan_deduction" TO "app_user";
-- relation:payroll.salary_structure [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "payroll"."salary_structure" TO "app_user";
GRANT INSERT ON TABLE "payroll"."salary_structure" TO "app_user";
GRANT SELECT ON TABLE "payroll"."salary_structure" TO "app_user";
GRANT UPDATE ON TABLE "payroll"."salary_structure" TO "app_user";
-- relation:payroll.salary_structure_assignment [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "payroll"."salary_structure_assignment" TO "app_user";
GRANT INSERT ON TABLE "payroll"."salary_structure_assignment" TO "app_user";
GRANT SELECT ON TABLE "payroll"."salary_structure_assignment" TO "app_user";
GRANT UPDATE ON TABLE "payroll"."salary_structure_assignment" TO "app_user";
-- relation:payroll.salary_structure_deduction [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "payroll"."salary_structure_deduction" TO "app_user";
GRANT INSERT ON TABLE "payroll"."salary_structure_deduction" TO "app_user";
GRANT SELECT ON TABLE "payroll"."salary_structure_deduction" TO "app_user";
GRANT UPDATE ON TABLE "payroll"."salary_structure_deduction" TO "app_user";
-- relation:payroll.salary_structure_earning [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "payroll"."salary_structure_earning" TO "app_user";
GRANT INSERT ON TABLE "payroll"."salary_structure_earning" TO "app_user";
GRANT SELECT ON TABLE "payroll"."salary_structure_earning" TO "app_user";
GRANT UPDATE ON TABLE "payroll"."salary_structure_earning" TO "app_user";
-- relation:payroll.tax_band [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "payroll"."tax_band" TO "app_user";
GRANT INSERT ON TABLE "payroll"."tax_band" TO "app_user";
GRANT SELECT ON TABLE "payroll"."tax_band" TO "app_user";
GRANT UPDATE ON TABLE "payroll"."tax_band" TO "app_user";
-- relation:people.payroll_number_sequence [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "people"."payroll_number_sequence" TO "app_user";
GRANT INSERT ON TABLE "people"."payroll_number_sequence" TO "app_user";
GRANT SELECT ON TABLE "people"."payroll_number_sequence" TO "app_user";
GRANT UPDATE ON TABLE "people"."payroll_number_sequence" TO "app_user";
-- relation:perf.appraisal [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "perf"."appraisal" TO "app_user";
GRANT INSERT ON TABLE "perf"."appraisal" TO "app_user";
GRANT SELECT ON TABLE "perf"."appraisal" TO "app_user";
GRANT UPDATE ON TABLE "perf"."appraisal" TO "app_user";
-- relation:perf.appraisal_appeal [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "perf"."appraisal_appeal" TO "app_user";
GRANT INSERT ON TABLE "perf"."appraisal_appeal" TO "app_user";
GRANT SELECT ON TABLE "perf"."appraisal_appeal" TO "app_user";
GRANT UPDATE ON TABLE "perf"."appraisal_appeal" TO "app_user";
-- relation:perf.appraisal_cycle [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "perf"."appraisal_cycle" TO "app_user";
GRANT INSERT ON TABLE "perf"."appraisal_cycle" TO "app_user";
GRANT SELECT ON TABLE "perf"."appraisal_cycle" TO "app_user";
GRANT UPDATE ON TABLE "perf"."appraisal_cycle" TO "app_user";
-- relation:perf.appraisal_feedback [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "perf"."appraisal_feedback" TO "app_user";
GRANT INSERT ON TABLE "perf"."appraisal_feedback" TO "app_user";
GRANT SELECT ON TABLE "perf"."appraisal_feedback" TO "app_user";
GRANT UPDATE ON TABLE "perf"."appraisal_feedback" TO "app_user";
-- relation:perf.appraisal_kra_score [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "perf"."appraisal_kra_score" TO "app_user";
GRANT INSERT ON TABLE "perf"."appraisal_kra_score" TO "app_user";
GRANT SELECT ON TABLE "perf"."appraisal_kra_score" TO "app_user";
GRANT UPDATE ON TABLE "perf"."appraisal_kra_score" TO "app_user";
-- relation:perf.appraisal_outcome_action [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "perf"."appraisal_outcome_action" TO "app_user";
GRANT INSERT ON TABLE "perf"."appraisal_outcome_action" TO "app_user";
GRANT SELECT ON TABLE "perf"."appraisal_outcome_action" TO "app_user";
GRANT UPDATE ON TABLE "perf"."appraisal_outcome_action" TO "app_user";
-- relation:perf.appraisal_template [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "perf"."appraisal_template" TO "app_user";
GRANT INSERT ON TABLE "perf"."appraisal_template" TO "app_user";
GRANT SELECT ON TABLE "perf"."appraisal_template" TO "app_user";
GRANT UPDATE ON TABLE "perf"."appraisal_template" TO "app_user";
-- relation:perf.appraisal_template_kra [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "perf"."appraisal_template_kra" TO "app_user";
GRANT INSERT ON TABLE "perf"."appraisal_template_kra" TO "app_user";
GRANT SELECT ON TABLE "perf"."appraisal_template_kra" TO "app_user";
GRANT UPDATE ON TABLE "perf"."appraisal_template_kra" TO "app_user";
-- relation:perf.competency_assessment [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "perf"."competency_assessment" TO "app_user";
GRANT INSERT ON TABLE "perf"."competency_assessment" TO "app_user";
GRANT SELECT ON TABLE "perf"."competency_assessment" TO "app_user";
GRANT UPDATE ON TABLE "perf"."competency_assessment" TO "app_user";
-- relation:perf.contract_amendment_workflow [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "perf"."contract_amendment_workflow" TO "app_user";
GRANT INSERT ON TABLE "perf"."contract_amendment_workflow" TO "app_user";
GRANT SELECT ON TABLE "perf"."contract_amendment_workflow" TO "app_user";
GRANT UPDATE ON TABLE "perf"."contract_amendment_workflow" TO "app_user";
-- relation:perf.department_performance_template [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "perf"."department_performance_template" TO "app_user";
GRANT INSERT ON TABLE "perf"."department_performance_template" TO "app_user";
GRANT SELECT ON TABLE "perf"."department_performance_template" TO "app_user";
GRANT UPDATE ON TABLE "perf"."department_performance_template" TO "app_user";
-- relation:perf.institutional_criteria_template [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "perf"."institutional_criteria_template" TO "app_user";
GRANT INSERT ON TABLE "perf"."institutional_criteria_template" TO "app_user";
GRANT SELECT ON TABLE "perf"."institutional_criteria_template" TO "app_user";
GRANT UPDATE ON TABLE "perf"."institutional_criteria_template" TO "app_user";
-- relation:perf.institutional_governance_action [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "perf"."institutional_governance_action" TO "app_user";
GRANT INSERT ON TABLE "perf"."institutional_governance_action" TO "app_user";
GRANT SELECT ON TABLE "perf"."institutional_governance_action" TO "app_user";
GRANT UPDATE ON TABLE "perf"."institutional_governance_action" TO "app_user";
-- relation:perf.institutional_performance [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "perf"."institutional_performance" TO "app_user";
GRANT INSERT ON TABLE "perf"."institutional_performance" TO "app_user";
GRANT SELECT ON TABLE "perf"."institutional_performance" TO "app_user";
GRANT UPDATE ON TABLE "perf"."institutional_performance" TO "app_user";
-- relation:perf.kpi [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "perf"."kpi" TO "app_user";
GRANT INSERT ON TABLE "perf"."kpi" TO "app_user";
GRANT SELECT ON TABLE "perf"."kpi" TO "app_user";
GRANT UPDATE ON TABLE "perf"."kpi" TO "app_user";
-- relation:perf.kra [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "perf"."kra" TO "app_user";
GRANT INSERT ON TABLE "perf"."kra" TO "app_user";
GRANT SELECT ON TABLE "perf"."kra" TO "app_user";
GRANT UPDATE ON TABLE "perf"."kra" TO "app_user";
-- relation:perf.monthly_review [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "perf"."monthly_review" TO "app_user";
GRANT INSERT ON TABLE "perf"."monthly_review" TO "app_user";
GRANT SELECT ON TABLE "perf"."monthly_review" TO "app_user";
GRANT UPDATE ON TABLE "perf"."monthly_review" TO "app_user";
-- relation:perf.performance_contract [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "perf"."performance_contract" TO "app_user";
GRANT INSERT ON TABLE "perf"."performance_contract" TO "app_user";
GRANT SELECT ON TABLE "perf"."performance_contract" TO "app_user";
GRANT UPDATE ON TABLE "perf"."performance_contract" TO "app_user";
-- relation:perf.performance_improvement_plan [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "perf"."performance_improvement_plan" TO "app_user";
GRANT INSERT ON TABLE "perf"."performance_improvement_plan" TO "app_user";
GRANT SELECT ON TABLE "perf"."performance_improvement_plan" TO "app_user";
GRANT UPDATE ON TABLE "perf"."performance_improvement_plan" TO "app_user";
-- relation:perf.pms_governance_grievance [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "perf"."pms_governance_grievance" TO "app_user";
GRANT INSERT ON TABLE "perf"."pms_governance_grievance" TO "app_user";
GRANT SELECT ON TABLE "perf"."pms_governance_grievance" TO "app_user";
GRANT UPDATE ON TABLE "perf"."pms_governance_grievance" TO "app_user";
-- relation:perf.pms_stakeholder_feedback [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "perf"."pms_stakeholder_feedback" TO "app_user";
GRANT INSERT ON TABLE "perf"."pms_stakeholder_feedback" TO "app_user";
GRANT SELECT ON TABLE "perf"."pms_stakeholder_feedback" TO "app_user";
GRANT UPDATE ON TABLE "perf"."pms_stakeholder_feedback" TO "app_user";
-- relation:perf.scorecard [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "perf"."scorecard" TO "app_user";
GRANT INSERT ON TABLE "perf"."scorecard" TO "app_user";
GRANT SELECT ON TABLE "perf"."scorecard" TO "app_user";
GRANT UPDATE ON TABLE "perf"."scorecard" TO "app_user";
-- relation:perf.scorecard_item [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "perf"."scorecard_item" TO "app_user";
GRANT INSERT ON TABLE "perf"."scorecard_item" TO "app_user";
GRANT SELECT ON TABLE "perf"."scorecard_item" TO "app_user";
GRANT UPDATE ON TABLE "perf"."scorecard_item" TO "app_user";
-- relation:perf.strategic_objective [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "perf"."strategic_objective" TO "app_user";
GRANT INSERT ON TABLE "perf"."strategic_objective" TO "app_user";
GRANT SELECT ON TABLE "perf"."strategic_objective" TO "app_user";
GRANT UPDATE ON TABLE "perf"."strategic_objective" TO "app_user";
-- relation:perf.weekly_meeting_action_item [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "perf"."weekly_meeting_action_item" TO "app_user";
GRANT INSERT ON TABLE "perf"."weekly_meeting_action_item" TO "app_user";
GRANT SELECT ON TABLE "perf"."weekly_meeting_action_item" TO "app_user";
GRANT UPDATE ON TABLE "perf"."weekly_meeting_action_item" TO "app_user";
-- relation:perf.weekly_meeting_participant [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "perf"."weekly_meeting_participant" TO "app_user";
GRANT INSERT ON TABLE "perf"."weekly_meeting_participant" TO "app_user";
GRANT SELECT ON TABLE "perf"."weekly_meeting_participant" TO "app_user";
GRANT UPDATE ON TABLE "perf"."weekly_meeting_participant" TO "app_user";
-- relation:perf.weekly_meeting_report [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "perf"."weekly_meeting_report" TO "app_user";
GRANT INSERT ON TABLE "perf"."weekly_meeting_report" TO "app_user";
GRANT SELECT ON TABLE "perf"."weekly_meeting_report" TO "app_user";
GRANT UPDATE ON TABLE "perf"."weekly_meeting_report" TO "app_user";
-- relation:platform.event_handler_checkpoint [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "platform"."event_handler_checkpoint" TO "app_user";
GRANT INSERT ON TABLE "platform"."event_handler_checkpoint" TO "app_user";
GRANT SELECT ON TABLE "platform"."event_handler_checkpoint" TO "app_user";
GRANT UPDATE ON TABLE "platform"."event_handler_checkpoint" TO "app_user";
-- relation:platform.event_outbox [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "platform"."event_outbox" TO "app_user";
GRANT INSERT ON TABLE "platform"."event_outbox" TO "app_user";
GRANT SELECT ON TABLE "platform"."event_outbox" TO "app_user";
GRANT UPDATE ON TABLE "platform"."event_outbox" TO "app_user";
-- relation:platform.idempotency_record [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "platform"."idempotency_record" TO "app_user";
GRANT INSERT ON TABLE "platform"."idempotency_record" TO "app_user";
GRANT SELECT ON TABLE "platform"."idempotency_record" TO "app_user";
GRANT UPDATE ON TABLE "platform"."idempotency_record" TO "app_user";
-- relation:platform.saga_execution [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "platform"."saga_execution" TO "app_user";
GRANT INSERT ON TABLE "platform"."saga_execution" TO "app_user";
GRANT SELECT ON TABLE "platform"."saga_execution" TO "app_user";
GRANT UPDATE ON TABLE "platform"."saga_execution" TO "app_user";
-- relation:platform.saga_step [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "platform"."saga_step" TO "app_user";
GRANT INSERT ON TABLE "platform"."saga_step" TO "app_user";
GRANT SELECT ON TABLE "platform"."saga_step" TO "app_user";
GRANT UPDATE ON TABLE "platform"."saga_step" TO "app_user";
-- relation:platform.service_hook [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "platform"."service_hook" TO "app_user";
GRANT INSERT ON TABLE "platform"."service_hook" TO "app_user";
GRANT SELECT ON TABLE "platform"."service_hook" TO "app_user";
GRANT UPDATE ON TABLE "platform"."service_hook" TO "app_user";
-- relation:platform.service_hook_execution [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "platform"."service_hook_execution" TO "app_user";
GRANT INSERT ON TABLE "platform"."service_hook_execution" TO "app_user";
GRANT SELECT ON TABLE "platform"."service_hook_execution" TO "app_user";
GRANT UPDATE ON TABLE "platform"."service_hook_execution" TO "app_user";
-- relation:pm.milestone [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "pm"."milestone" TO "app_user";
GRANT INSERT ON TABLE "pm"."milestone" TO "app_user";
GRANT SELECT ON TABLE "pm"."milestone" TO "app_user";
GRANT UPDATE ON TABLE "pm"."milestone" TO "app_user";
-- relation:pm.pm_comment [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "pm"."pm_comment" TO "app_user";
GRANT INSERT ON TABLE "pm"."pm_comment" TO "app_user";
GRANT SELECT ON TABLE "pm"."pm_comment" TO "app_user";
GRANT UPDATE ON TABLE "pm"."pm_comment" TO "app_user";
-- relation:pm.pm_comment_attachment [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "pm"."pm_comment_attachment" TO "app_user";
GRANT INSERT ON TABLE "pm"."pm_comment_attachment" TO "app_user";
GRANT SELECT ON TABLE "pm"."pm_comment_attachment" TO "app_user";
GRANT UPDATE ON TABLE "pm"."pm_comment_attachment" TO "app_user";
-- relation:pm.project_template [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "pm"."project_template" TO "app_user";
GRANT INSERT ON TABLE "pm"."project_template" TO "app_user";
GRANT SELECT ON TABLE "pm"."project_template" TO "app_user";
GRANT UPDATE ON TABLE "pm"."project_template" TO "app_user";
-- relation:pm.project_template_task [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "pm"."project_template_task" TO "app_user";
GRANT INSERT ON TABLE "pm"."project_template_task" TO "app_user";
GRANT SELECT ON TABLE "pm"."project_template_task" TO "app_user";
GRANT UPDATE ON TABLE "pm"."project_template_task" TO "app_user";
-- relation:pm.project_template_task_dependency [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "pm"."project_template_task_dependency" TO "app_user";
GRANT INSERT ON TABLE "pm"."project_template_task_dependency" TO "app_user";
GRANT SELECT ON TABLE "pm"."project_template_task_dependency" TO "app_user";
GRANT UPDATE ON TABLE "pm"."project_template_task_dependency" TO "app_user";
-- relation:pm.resource_allocation [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "pm"."resource_allocation" TO "app_user";
GRANT INSERT ON TABLE "pm"."resource_allocation" TO "app_user";
GRANT SELECT ON TABLE "pm"."resource_allocation" TO "app_user";
GRANT UPDATE ON TABLE "pm"."resource_allocation" TO "app_user";
-- relation:pm.task [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "pm"."task" TO "app_user";
GRANT INSERT ON TABLE "pm"."task" TO "app_user";
GRANT SELECT ON TABLE "pm"."task" TO "app_user";
GRANT UPDATE ON TABLE "pm"."task" TO "app_user";
-- relation:pm.task_dependency [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "pm"."task_dependency" TO "app_user";
GRANT INSERT ON TABLE "pm"."task_dependency" TO "app_user";
GRANT SELECT ON TABLE "pm"."task_dependency" TO "app_user";
GRANT UPDATE ON TABLE "pm"."task_dependency" TO "app_user";
-- relation:pm.time_entry [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "pm"."time_entry" TO "app_user";
GRANT INSERT ON TABLE "pm"."time_entry" TO "app_user";
GRANT SELECT ON TABLE "pm"."time_entry" TO "app_user";
GRANT UPDATE ON TABLE "pm"."time_entry" TO "app_user";
-- relation:proc.bid_evaluation [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "proc"."bid_evaluation" TO "app_user";
GRANT INSERT ON TABLE "proc"."bid_evaluation" TO "app_user";
GRANT SELECT ON TABLE "proc"."bid_evaluation" TO "app_user";
GRANT UPDATE ON TABLE "proc"."bid_evaluation" TO "app_user";
-- relation:proc.bid_evaluation_score [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "proc"."bid_evaluation_score" TO "app_user";
GRANT INSERT ON TABLE "proc"."bid_evaluation_score" TO "app_user";
GRANT SELECT ON TABLE "proc"."bid_evaluation_score" TO "app_user";
GRANT UPDATE ON TABLE "proc"."bid_evaluation_score" TO "app_user";
-- relation:proc.procurement_contract [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "proc"."procurement_contract" TO "app_user";
GRANT INSERT ON TABLE "proc"."procurement_contract" TO "app_user";
GRANT SELECT ON TABLE "proc"."procurement_contract" TO "app_user";
GRANT UPDATE ON TABLE "proc"."procurement_contract" TO "app_user";
-- relation:proc.procurement_plan [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "proc"."procurement_plan" TO "app_user";
GRANT INSERT ON TABLE "proc"."procurement_plan" TO "app_user";
GRANT SELECT ON TABLE "proc"."procurement_plan" TO "app_user";
GRANT UPDATE ON TABLE "proc"."procurement_plan" TO "app_user";
-- relation:proc.procurement_plan_item [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "proc"."procurement_plan_item" TO "app_user";
GRANT INSERT ON TABLE "proc"."procurement_plan_item" TO "app_user";
GRANT SELECT ON TABLE "proc"."procurement_plan_item" TO "app_user";
GRANT UPDATE ON TABLE "proc"."procurement_plan_item" TO "app_user";
-- relation:proc.purchase_requisition [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "proc"."purchase_requisition" TO "app_user";
GRANT INSERT ON TABLE "proc"."purchase_requisition" TO "app_user";
GRANT SELECT ON TABLE "proc"."purchase_requisition" TO "app_user";
GRANT UPDATE ON TABLE "proc"."purchase_requisition" TO "app_user";
-- relation:proc.purchase_requisition_line [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "proc"."purchase_requisition_line" TO "app_user";
GRANT INSERT ON TABLE "proc"."purchase_requisition_line" TO "app_user";
GRANT SELECT ON TABLE "proc"."purchase_requisition_line" TO "app_user";
GRANT UPDATE ON TABLE "proc"."purchase_requisition_line" TO "app_user";
-- relation:proc.quotation_response [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "proc"."quotation_response" TO "app_user";
GRANT INSERT ON TABLE "proc"."quotation_response" TO "app_user";
GRANT SELECT ON TABLE "proc"."quotation_response" TO "app_user";
GRANT UPDATE ON TABLE "proc"."quotation_response" TO "app_user";
-- relation:proc.quotation_response_line [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "proc"."quotation_response_line" TO "app_user";
GRANT INSERT ON TABLE "proc"."quotation_response_line" TO "app_user";
GRANT SELECT ON TABLE "proc"."quotation_response_line" TO "app_user";
GRANT UPDATE ON TABLE "proc"."quotation_response_line" TO "app_user";
-- relation:proc.request_for_quotation [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "proc"."request_for_quotation" TO "app_user";
GRANT INSERT ON TABLE "proc"."request_for_quotation" TO "app_user";
GRANT SELECT ON TABLE "proc"."request_for_quotation" TO "app_user";
GRANT UPDATE ON TABLE "proc"."request_for_quotation" TO "app_user";
-- relation:proc.rfq_invitation [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "proc"."rfq_invitation" TO "app_user";
GRANT INSERT ON TABLE "proc"."rfq_invitation" TO "app_user";
GRANT SELECT ON TABLE "proc"."rfq_invitation" TO "app_user";
GRANT UPDATE ON TABLE "proc"."rfq_invitation" TO "app_user";
-- relation:proc.vendor_prequalification [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "proc"."vendor_prequalification" TO "app_user";
GRANT INSERT ON TABLE "proc"."vendor_prequalification" TO "app_user";
GRANT SELECT ON TABLE "proc"."vendor_prequalification" TO "app_user";
GRANT UPDATE ON TABLE "proc"."vendor_prequalification" TO "app_user";
-- relation:public._clean_sweep_2025_audit [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."_clean_sweep_2025_audit" TO "app_user";
GRANT INSERT ON TABLE "public"."_clean_sweep_2025_audit" TO "app_user";
GRANT SELECT ON TABLE "public"."_clean_sweep_2025_audit" TO "app_user";
GRANT UPDATE ON TABLE "public"."_clean_sweep_2025_audit" TO "app_user";
-- relation:public._migration_account_remap [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."_migration_account_remap" TO "app_user";
GRANT INSERT ON TABLE "public"."_migration_account_remap" TO "app_user";
GRANT SELECT ON TABLE "public"."_migration_account_remap" TO "app_user";
GRANT UPDATE ON TABLE "public"."_migration_account_remap" TO "app_user";
-- relation:public._migration_void_erpnext_invoices [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."_migration_void_erpnext_invoices" TO "app_user";
GRANT INSERT ON TABLE "public"."_migration_void_erpnext_invoices" TO "app_user";
GRANT SELECT ON TABLE "public"."_migration_void_erpnext_invoices" TO "app_user";
GRANT UPDATE ON TABLE "public"."_migration_void_erpnext_invoices" TO "app_user";
-- relation:public._migration_void_orphan_journals [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."_migration_void_orphan_journals" TO "app_user";
GRANT INSERT ON TABLE "public"."_migration_void_orphan_journals" TO "app_user";
GRANT SELECT ON TABLE "public"."_migration_void_orphan_journals" TO "app_user";
GRANT UPDATE ON TABLE "public"."_migration_void_orphan_journals" TO "app_user";
-- relation:public.alembic_version [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."alembic_version" TO "app_user";
GRANT INSERT ON TABLE "public"."alembic_version" TO "app_user";
GRANT SELECT ON TABLE "public"."alembic_version" TO "app_user";
GRANT UPDATE ON TABLE "public"."alembic_version" TO "app_user";
-- relation:public.api_keys [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."api_keys" TO "app_user";
GRANT INSERT ON TABLE "public"."api_keys" TO "app_user";
GRANT SELECT ON TABLE "public"."api_keys" TO "app_user";
GRANT UPDATE ON TABLE "public"."api_keys" TO "app_user";
-- relation:public.audit_events [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."audit_events" TO "app_user";
GRANT INSERT ON TABLE "public"."audit_events" TO "app_user";
GRANT SELECT ON TABLE "public"."audit_events" TO "app_user";
GRANT UPDATE ON TABLE "public"."audit_events" TO "app_user";
-- relation:public.batch_operations [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."batch_operations" TO "app_user";
GRANT INSERT ON TABLE "public"."batch_operations" TO "app_user";
GRANT SELECT ON TABLE "public"."batch_operations" TO "app_user";
GRANT UPDATE ON TABLE "public"."batch_operations" TO "app_user";
-- relation:public.coach_insight [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."coach_insight" TO "app_user";
GRANT INSERT ON TABLE "public"."coach_insight" TO "app_user";
GRANT SELECT ON TABLE "public"."coach_insight" TO "app_user";
GRANT UPDATE ON TABLE "public"."coach_insight" TO "app_user";
-- relation:public.coach_report [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."coach_report" TO "app_user";
GRANT INSERT ON TABLE "public"."coach_report" TO "app_user";
GRANT SELECT ON TABLE "public"."coach_report" TO "app_user";
GRANT UPDATE ON TABLE "public"."coach_report" TO "app_user";
-- relation:public.device_token [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."device_token" TO "app_user";
GRANT INSERT ON TABLE "public"."device_token" TO "app_user";
GRANT SELECT ON TABLE "public"."device_token" TO "app_user";
GRANT UPDATE ON TABLE "public"."device_token" TO "app_user";
-- relation:public.domain_setting_history [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."domain_setting_history" TO "app_user";
GRANT INSERT ON TABLE "public"."domain_setting_history" TO "app_user";
GRANT SELECT ON TABLE "public"."domain_setting_history" TO "app_user";
GRANT UPDATE ON TABLE "public"."domain_setting_history" TO "app_user";
-- relation:public.domain_settings [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."domain_settings" TO "app_user";
GRANT INSERT ON TABLE "public"."domain_settings" TO "app_user";
GRANT SELECT ON TABLE "public"."domain_settings" TO "app_user";
GRANT UPDATE ON TABLE "public"."domain_settings" TO "app_user";
-- relation:public.email_profile [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."email_profile" TO "app_user";
GRANT INSERT ON TABLE "public"."email_profile" TO "app_user";
GRANT SELECT ON TABLE "public"."email_profile" TO "app_user";
GRANT UPDATE ON TABLE "public"."email_profile" TO "app_user";
-- relation:public.feature_flag_registry [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."feature_flag_registry" TO "app_user";
GRANT INSERT ON TABLE "public"."feature_flag_registry" TO "app_user";
GRANT SELECT ON TABLE "public"."feature_flag_registry" TO "app_user";
GRANT UPDATE ON TABLE "public"."feature_flag_registry" TO "app_user";
-- relation:public.federated_identities [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."federated_identities" TO "app_user";
GRANT INSERT ON TABLE "public"."federated_identities" TO "app_user";
GRANT SELECT ON TABLE "public"."federated_identities" TO "app_user";
GRANT UPDATE ON TABLE "public"."federated_identities" TO "app_user";
-- relation:public.help_article_feedback [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."help_article_feedback" TO "app_user";
GRANT INSERT ON TABLE "public"."help_article_feedback" TO "app_user";
GRANT SELECT ON TABLE "public"."help_article_feedback" TO "app_user";
GRANT UPDATE ON TABLE "public"."help_article_feedback" TO "app_user";
-- relation:public.help_article_override [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."help_article_override" TO "app_user";
GRANT INSERT ON TABLE "public"."help_article_override" TO "app_user";
GRANT SELECT ON TABLE "public"."help_article_override" TO "app_user";
GRANT UPDATE ON TABLE "public"."help_article_override" TO "app_user";
-- relation:public.help_search_event [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."help_search_event" TO "app_user";
GRANT INSERT ON TABLE "public"."help_search_event" TO "app_user";
GRANT SELECT ON TABLE "public"."help_search_event" TO "app_user";
GRANT UPDATE ON TABLE "public"."help_search_event" TO "app_user";
-- relation:public.help_user_progress [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."help_user_progress" TO "app_user";
GRANT INSERT ON TABLE "public"."help_user_progress" TO "app_user";
GRANT SELECT ON TABLE "public"."help_user_progress" TO "app_user";
GRANT UPDATE ON TABLE "public"."help_user_progress" TO "app_user";
-- relation:public.infrastructure_alert [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."infrastructure_alert" TO "app_user";
GRANT INSERT ON TABLE "public"."infrastructure_alert" TO "app_user";
GRANT SELECT ON TABLE "public"."infrastructure_alert" TO "app_user";
GRANT UPDATE ON TABLE "public"."infrastructure_alert" TO "app_user";
-- relation:public.infrastructure_health_status [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."infrastructure_health_status" TO "app_user";
GRANT INSERT ON TABLE "public"."infrastructure_health_status" TO "app_user";
GRANT SELECT ON TABLE "public"."infrastructure_health_status" TO "app_user";
GRANT UPDATE ON TABLE "public"."infrastructure_health_status" TO "app_user";
-- relation:public.mfa_methods [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."mfa_methods" TO "app_user";
GRANT INSERT ON TABLE "public"."mfa_methods" TO "app_user";
GRANT SELECT ON TABLE "public"."mfa_methods" TO "app_user";
GRANT UPDATE ON TABLE "public"."mfa_methods" TO "app_user";
-- relation:public.module_email_routing [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."module_email_routing" TO "app_user";
GRANT INSERT ON TABLE "public"."module_email_routing" TO "app_user";
GRANT SELECT ON TABLE "public"."module_email_routing" TO "app_user";
GRANT UPDATE ON TABLE "public"."module_email_routing" TO "app_user";
-- relation:public.notification [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."notification" TO "app_user";
GRANT INSERT ON TABLE "public"."notification" TO "app_user";
GRANT SELECT ON TABLE "public"."notification" TO "app_user";
GRANT UPDATE ON TABLE "public"."notification" TO "app_user";
-- relation:public.org_metric_snapshot [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."org_metric_snapshot" TO "app_user";
GRANT INSERT ON TABLE "public"."org_metric_snapshot" TO "app_user";
GRANT SELECT ON TABLE "public"."org_metric_snapshot" TO "app_user";
GRANT UPDATE ON TABLE "public"."org_metric_snapshot" TO "app_user";
-- relation:public.people [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."people" TO "app_user";
GRANT INSERT ON TABLE "public"."people" TO "app_user";
GRANT SELECT ON TABLE "public"."people" TO "app_user";
GRANT UPDATE ON TABLE "public"."people" TO "app_user";
-- relation:public.permissions [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."permissions" TO "app_user";
GRANT INSERT ON TABLE "public"."permissions" TO "app_user";
GRANT SELECT ON TABLE "public"."permissions" TO "app_user";
GRANT UPDATE ON TABLE "public"."permissions" TO "app_user";
-- relation:public.person_roles [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."person_roles" TO "app_user";
GRANT INSERT ON TABLE "public"."person_roles" TO "app_user";
GRANT SELECT ON TABLE "public"."person_roles" TO "app_user";
GRANT UPDATE ON TABLE "public"."person_roles" TO "app_user";
-- relation:public.role_permissions [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."role_permissions" TO "app_user";
GRANT INSERT ON TABLE "public"."role_permissions" TO "app_user";
GRANT SELECT ON TABLE "public"."role_permissions" TO "app_user";
GRANT UPDATE ON TABLE "public"."role_permissions" TO "app_user";
-- relation:public.roles [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."roles" TO "app_user";
GRANT INSERT ON TABLE "public"."roles" TO "app_user";
GRANT SELECT ON TABLE "public"."roles" TO "app_user";
GRANT UPDATE ON TABLE "public"."roles" TO "app_user";
-- relation:public.scheduled_tasks [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."scheduled_tasks" TO "app_user";
GRANT INSERT ON TABLE "public"."scheduled_tasks" TO "app_user";
GRANT SELECT ON TABLE "public"."scheduled_tasks" TO "app_user";
GRANT UPDATE ON TABLE "public"."scheduled_tasks" TO "app_user";
-- relation:public.sessions [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."sessions" TO "app_user";
GRANT INSERT ON TABLE "public"."sessions" TO "app_user";
GRANT SELECT ON TABLE "public"."sessions" TO "app_user";
GRANT UPDATE ON TABLE "public"."sessions" TO "app_user";
-- relation:public.user_credentials [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."user_credentials" TO "app_user";
GRANT INSERT ON TABLE "public"."user_credentials" TO "app_user";
GRANT SELECT ON TABLE "public"."user_credentials" TO "app_user";
GRANT UPDATE ON TABLE "public"."user_credentials" TO "app_user";
-- relation:public.workflow_task [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "public"."workflow_task" TO "app_user";
GRANT INSERT ON TABLE "public"."workflow_task" TO "app_user";
GRANT SELECT ON TABLE "public"."workflow_task" TO "app_user";
GRANT UPDATE ON TABLE "public"."workflow_task" TO "app_user";
-- relation:recruit.interview [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "recruit"."interview" TO "app_user";
GRANT INSERT ON TABLE "recruit"."interview" TO "app_user";
GRANT SELECT ON TABLE "recruit"."interview" TO "app_user";
GRANT UPDATE ON TABLE "recruit"."interview" TO "app_user";
-- relation:recruit.job_applicant [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "recruit"."job_applicant" TO "app_user";
GRANT INSERT ON TABLE "recruit"."job_applicant" TO "app_user";
GRANT SELECT ON TABLE "recruit"."job_applicant" TO "app_user";
GRANT UPDATE ON TABLE "recruit"."job_applicant" TO "app_user";
-- relation:recruit.job_offer [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "recruit"."job_offer" TO "app_user";
GRANT INSERT ON TABLE "recruit"."job_offer" TO "app_user";
GRANT SELECT ON TABLE "recruit"."job_offer" TO "app_user";
GRANT UPDATE ON TABLE "recruit"."job_offer" TO "app_user";
-- relation:recruit.job_opening [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "recruit"."job_opening" TO "app_user";
GRANT INSERT ON TABLE "recruit"."job_opening" TO "app_user";
GRANT SELECT ON TABLE "recruit"."job_opening" TO "app_user";
GRANT UPDATE ON TABLE "recruit"."job_opening" TO "app_user";
-- relation:rpt.analysis_cube [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "rpt"."analysis_cube" TO "app_user";
GRANT INSERT ON TABLE "rpt"."analysis_cube" TO "app_user";
GRANT SELECT ON TABLE "rpt"."analysis_cube" TO "app_user";
GRANT UPDATE ON TABLE "rpt"."analysis_cube" TO "app_user";
-- relation:rpt.disclosure_checklist [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "rpt"."disclosure_checklist" TO "app_user";
GRANT INSERT ON TABLE "rpt"."disclosure_checklist" TO "app_user";
GRANT SELECT ON TABLE "rpt"."disclosure_checklist" TO "app_user";
GRANT UPDATE ON TABLE "rpt"."disclosure_checklist" TO "app_user";
-- relation:rpt.financial_statement_line [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "rpt"."financial_statement_line" TO "app_user";
GRANT INSERT ON TABLE "rpt"."financial_statement_line" TO "app_user";
GRANT SELECT ON TABLE "rpt"."financial_statement_line" TO "app_user";
GRANT UPDATE ON TABLE "rpt"."financial_statement_line" TO "app_user";
-- relation:rpt.report_definition [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "rpt"."report_definition" TO "app_user";
GRANT INSERT ON TABLE "rpt"."report_definition" TO "app_user";
GRANT SELECT ON TABLE "rpt"."report_definition" TO "app_user";
GRANT UPDATE ON TABLE "rpt"."report_definition" TO "app_user";
-- relation:rpt.report_instance [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "rpt"."report_instance" TO "app_user";
GRANT INSERT ON TABLE "rpt"."report_instance" TO "app_user";
GRANT SELECT ON TABLE "rpt"."report_instance" TO "app_user";
GRANT UPDATE ON TABLE "rpt"."report_instance" TO "app_user";
-- relation:rpt.report_schedule [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "rpt"."report_schedule" TO "app_user";
GRANT INSERT ON TABLE "rpt"."report_schedule" TO "app_user";
GRANT SELECT ON TABLE "rpt"."report_schedule" TO "app_user";
GRANT UPDATE ON TABLE "rpt"."report_schedule" TO "app_user";
-- relation:rpt.sales_analysis_mv [relkind m] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "rpt"."sales_analysis_mv" TO "app_user";
GRANT INSERT ON TABLE "rpt"."sales_analysis_mv" TO "app_user";
GRANT SELECT ON TABLE "rpt"."sales_analysis_mv" TO "app_user";
GRANT UPDATE ON TABLE "rpt"."sales_analysis_mv" TO "app_user";
-- relation:rpt.saved_analysis [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "rpt"."saved_analysis" TO "app_user";
GRANT INSERT ON TABLE "rpt"."saved_analysis" TO "app_user";
GRANT SELECT ON TABLE "rpt"."saved_analysis" TO "app_user";
GRANT UPDATE ON TABLE "rpt"."saved_analysis" TO "app_user";
-- relation:scheduling.schedule_audit_event [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "scheduling"."schedule_audit_event" TO "app_user";
GRANT INSERT ON TABLE "scheduling"."schedule_audit_event" TO "app_user";
GRANT SELECT ON TABLE "scheduling"."schedule_audit_event" TO "app_user";
GRANT UPDATE ON TABLE "scheduling"."schedule_audit_event" TO "app_user";
-- relation:scheduling.schedule_notification_log [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "scheduling"."schedule_notification_log" TO "app_user";
GRANT INSERT ON TABLE "scheduling"."schedule_notification_log" TO "app_user";
GRANT SELECT ON TABLE "scheduling"."schedule_notification_log" TO "app_user";
GRANT UPDATE ON TABLE "scheduling"."schedule_notification_log" TO "app_user";
-- relation:scheduling.scheduling_policy [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "scheduling"."scheduling_policy" TO "app_user";
GRANT INSERT ON TABLE "scheduling"."scheduling_policy" TO "app_user";
GRANT SELECT ON TABLE "scheduling"."scheduling_policy" TO "app_user";
GRANT UPDATE ON TABLE "scheduling"."scheduling_policy" TO "app_user";
-- relation:scheduling.shift_pattern [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "scheduling"."shift_pattern" TO "app_user";
GRANT INSERT ON TABLE "scheduling"."shift_pattern" TO "app_user";
GRANT SELECT ON TABLE "scheduling"."shift_pattern" TO "app_user";
GRANT UPDATE ON TABLE "scheduling"."shift_pattern" TO "app_user";
-- relation:scheduling.shift_pattern_assignment [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "scheduling"."shift_pattern_assignment" TO "app_user";
GRANT INSERT ON TABLE "scheduling"."shift_pattern_assignment" TO "app_user";
GRANT SELECT ON TABLE "scheduling"."shift_pattern_assignment" TO "app_user";
GRANT UPDATE ON TABLE "scheduling"."shift_pattern_assignment" TO "app_user";
-- relation:scheduling.shift_schedule [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "scheduling"."shift_schedule" TO "app_user";
GRANT INSERT ON TABLE "scheduling"."shift_schedule" TO "app_user";
GRANT SELECT ON TABLE "scheduling"."shift_schedule" TO "app_user";
GRANT UPDATE ON TABLE "scheduling"."shift_schedule" TO "app_user";
-- relation:scheduling.shift_swap_request [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "scheduling"."shift_swap_request" TO "app_user";
GRANT INSERT ON TABLE "scheduling"."shift_swap_request" TO "app_user";
GRANT SELECT ON TABLE "scheduling"."shift_swap_request" TO "app_user";
GRANT UPDATE ON TABLE "scheduling"."shift_swap_request" TO "app_user";
-- relation:scheduling.work_schedule [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "scheduling"."work_schedule" TO "app_user";
GRANT INSERT ON TABLE "scheduling"."work_schedule" TO "app_user";
GRANT SELECT ON TABLE "scheduling"."work_schedule" TO "app_user";
GRANT UPDATE ON TABLE "scheduling"."work_schedule" TO "app_user";
-- relation:settings.org_bank_directory [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "settings"."org_bank_directory" TO "app_user";
GRANT INSERT ON TABLE "settings"."org_bank_directory" TO "app_user";
GRANT SELECT ON TABLE "settings"."org_bank_directory" TO "app_user";
GRANT UPDATE ON TABLE "settings"."org_bank_directory" TO "app_user";
-- relation:support.support_team [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "support"."support_team" TO "app_user";
GRANT INSERT ON TABLE "support"."support_team" TO "app_user";
GRANT SELECT ON TABLE "support"."support_team" TO "app_user";
GRANT UPDATE ON TABLE "support"."support_team" TO "app_user";
-- relation:support.support_team_member [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "support"."support_team_member" TO "app_user";
GRANT INSERT ON TABLE "support"."support_team_member" TO "app_user";
GRANT SELECT ON TABLE "support"."support_team_member" TO "app_user";
GRANT UPDATE ON TABLE "support"."support_team_member" TO "app_user";
-- relation:support.ticket [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "support"."ticket" TO "app_user";
GRANT INSERT ON TABLE "support"."ticket" TO "app_user";
GRANT SELECT ON TABLE "support"."ticket" TO "app_user";
GRANT UPDATE ON TABLE "support"."ticket" TO "app_user";
-- relation:support.ticket_attachment [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "support"."ticket_attachment" TO "app_user";
GRANT INSERT ON TABLE "support"."ticket_attachment" TO "app_user";
GRANT SELECT ON TABLE "support"."ticket_attachment" TO "app_user";
GRANT UPDATE ON TABLE "support"."ticket_attachment" TO "app_user";
-- relation:support.ticket_category [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "support"."ticket_category" TO "app_user";
GRANT INSERT ON TABLE "support"."ticket_category" TO "app_user";
GRANT SELECT ON TABLE "support"."ticket_category" TO "app_user";
GRANT UPDATE ON TABLE "support"."ticket_category" TO "app_user";
-- relation:support.ticket_comment [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "support"."ticket_comment" TO "app_user";
GRANT INSERT ON TABLE "support"."ticket_comment" TO "app_user";
GRANT SELECT ON TABLE "support"."ticket_comment" TO "app_user";
GRANT UPDATE ON TABLE "support"."ticket_comment" TO "app_user";
-- relation:sync.integration_config [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "sync"."integration_config" TO "app_user";
GRANT INSERT ON TABLE "sync"."integration_config" TO "app_user";
GRANT SELECT ON TABLE "sync"."integration_config" TO "app_user";
GRANT UPDATE ON TABLE "sync"."integration_config" TO "app_user";
-- relation:sync.staging_department [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "sync"."staging_department" TO "app_user";
GRANT INSERT ON TABLE "sync"."staging_department" TO "app_user";
GRANT SELECT ON TABLE "sync"."staging_department" TO "app_user";
GRANT UPDATE ON TABLE "sync"."staging_department" TO "app_user";
-- relation:sync.staging_designation [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "sync"."staging_designation" TO "app_user";
GRANT INSERT ON TABLE "sync"."staging_designation" TO "app_user";
GRANT SELECT ON TABLE "sync"."staging_designation" TO "app_user";
GRANT UPDATE ON TABLE "sync"."staging_designation" TO "app_user";
-- relation:sync.staging_employee [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "sync"."staging_employee" TO "app_user";
GRANT INSERT ON TABLE "sync"."staging_employee" TO "app_user";
GRANT SELECT ON TABLE "sync"."staging_employee" TO "app_user";
GRANT UPDATE ON TABLE "sync"."staging_employee" TO "app_user";
-- relation:sync.staging_employee_grade [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "sync"."staging_employee_grade" TO "app_user";
GRANT INSERT ON TABLE "sync"."staging_employee_grade" TO "app_user";
GRANT SELECT ON TABLE "sync"."staging_employee_grade" TO "app_user";
GRANT UPDATE ON TABLE "sync"."staging_employee_grade" TO "app_user";
-- relation:sync.staging_employment_type [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "sync"."staging_employment_type" TO "app_user";
GRANT INSERT ON TABLE "sync"."staging_employment_type" TO "app_user";
GRANT SELECT ON TABLE "sync"."staging_employment_type" TO "app_user";
GRANT UPDATE ON TABLE "sync"."staging_employment_type" TO "app_user";
-- relation:sync.staging_sync_batch [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "sync"."staging_sync_batch" TO "app_user";
GRANT INSERT ON TABLE "sync"."staging_sync_batch" TO "app_user";
GRANT SELECT ON TABLE "sync"."staging_sync_batch" TO "app_user";
GRANT UPDATE ON TABLE "sync"."staging_sync_batch" TO "app_user";
-- relation:sync.sync_entity [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "sync"."sync_entity" TO "app_user";
GRANT INSERT ON TABLE "sync"."sync_entity" TO "app_user";
GRANT SELECT ON TABLE "sync"."sync_entity" TO "app_user";
GRANT UPDATE ON TABLE "sync"."sync_entity" TO "app_user";
-- relation:sync.sync_history [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "sync"."sync_history" TO "app_user";
GRANT INSERT ON TABLE "sync"."sync_history" TO "app_user";
GRANT SELECT ON TABLE "sync"."sync_history" TO "app_user";
GRANT UPDATE ON TABLE "sync"."sync_history" TO "app_user";
-- relation:tax.control_evidence [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "tax"."control_evidence" TO "app_user";
GRANT INSERT ON TABLE "tax"."control_evidence" TO "app_user";
GRANT SELECT ON TABLE "tax"."control_evidence" TO "app_user";
GRANT UPDATE ON TABLE "tax"."control_evidence" TO "app_user";
-- relation:tax.deferred_tax_basis [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "tax"."deferred_tax_basis" TO "app_user";
GRANT INSERT ON TABLE "tax"."deferred_tax_basis" TO "app_user";
GRANT SELECT ON TABLE "tax"."deferred_tax_basis" TO "app_user";
GRANT UPDATE ON TABLE "tax"."deferred_tax_basis" TO "app_user";
-- relation:tax.deferred_tax_movement [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "tax"."deferred_tax_movement" TO "app_user";
GRANT INSERT ON TABLE "tax"."deferred_tax_movement" TO "app_user";
GRANT SELECT ON TABLE "tax"."deferred_tax_movement" TO "app_user";
GRANT UPDATE ON TABLE "tax"."deferred_tax_movement" TO "app_user";
-- relation:tax.fiscal_position [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "tax"."fiscal_position" TO "app_user";
GRANT INSERT ON TABLE "tax"."fiscal_position" TO "app_user";
GRANT SELECT ON TABLE "tax"."fiscal_position" TO "app_user";
GRANT UPDATE ON TABLE "tax"."fiscal_position" TO "app_user";
-- relation:tax.fiscal_position_account_map [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "tax"."fiscal_position_account_map" TO "app_user";
GRANT INSERT ON TABLE "tax"."fiscal_position_account_map" TO "app_user";
GRANT SELECT ON TABLE "tax"."fiscal_position_account_map" TO "app_user";
GRANT UPDATE ON TABLE "tax"."fiscal_position_account_map" TO "app_user";
-- relation:tax.fiscal_position_tax_map [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "tax"."fiscal_position_tax_map" TO "app_user";
GRANT INSERT ON TABLE "tax"."fiscal_position_tax_map" TO "app_user";
GRANT SELECT ON TABLE "tax"."fiscal_position_tax_map" TO "app_user";
GRANT UPDATE ON TABLE "tax"."fiscal_position_tax_map" TO "app_user";
-- relation:tax.tax_code [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "tax"."tax_code" TO "app_user";
GRANT INSERT ON TABLE "tax"."tax_code" TO "app_user";
GRANT SELECT ON TABLE "tax"."tax_code" TO "app_user";
GRANT UPDATE ON TABLE "tax"."tax_code" TO "app_user";
-- relation:tax.tax_jurisdiction [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "tax"."tax_jurisdiction" TO "app_user";
GRANT INSERT ON TABLE "tax"."tax_jurisdiction" TO "app_user";
GRANT SELECT ON TABLE "tax"."tax_jurisdiction" TO "app_user";
GRANT UPDATE ON TABLE "tax"."tax_jurisdiction" TO "app_user";
-- relation:tax.tax_period [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "tax"."tax_period" TO "app_user";
GRANT INSERT ON TABLE "tax"."tax_period" TO "app_user";
GRANT SELECT ON TABLE "tax"."tax_period" TO "app_user";
GRANT UPDATE ON TABLE "tax"."tax_period" TO "app_user";
-- relation:tax.tax_reconciliation [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "tax"."tax_reconciliation" TO "app_user";
GRANT INSERT ON TABLE "tax"."tax_reconciliation" TO "app_user";
GRANT SELECT ON TABLE "tax"."tax_reconciliation" TO "app_user";
GRANT UPDATE ON TABLE "tax"."tax_reconciliation" TO "app_user";
-- relation:tax.tax_return [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "tax"."tax_return" TO "app_user";
GRANT INSERT ON TABLE "tax"."tax_return" TO "app_user";
GRANT SELECT ON TABLE "tax"."tax_return" TO "app_user";
GRANT UPDATE ON TABLE "tax"."tax_return" TO "app_user";
-- relation:tax.tax_transaction [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "tax"."tax_transaction" TO "app_user";
GRANT INSERT ON TABLE "tax"."tax_transaction" TO "app_user";
GRANT SELECT ON TABLE "tax"."tax_transaction" TO "app_user";
GRANT UPDATE ON TABLE "tax"."tax_transaction" TO "app_user";
-- relation:training.academy_learning_progress [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "training"."academy_learning_progress" TO "app_user";
GRANT INSERT ON TABLE "training"."academy_learning_progress" TO "app_user";
GRANT SELECT ON TABLE "training"."academy_learning_progress" TO "app_user";
GRANT UPDATE ON TABLE "training"."academy_learning_progress" TO "app_user";
-- relation:training.academy_learning_requirement [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "training"."academy_learning_requirement" TO "app_user";
GRANT INSERT ON TABLE "training"."academy_learning_requirement" TO "app_user";
GRANT SELECT ON TABLE "training"."academy_learning_requirement" TO "app_user";
GRANT UPDATE ON TABLE "training"."academy_learning_requirement" TO "app_user";
-- relation:training.training_assessment [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "training"."training_assessment" TO "app_user";
GRANT INSERT ON TABLE "training"."training_assessment" TO "app_user";
GRANT SELECT ON TABLE "training"."training_assessment" TO "app_user";
GRANT UPDATE ON TABLE "training"."training_assessment" TO "app_user";
-- relation:training.training_assessment_question [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "training"."training_assessment_question" TO "app_user";
GRANT INSERT ON TABLE "training"."training_assessment_question" TO "app_user";
GRANT SELECT ON TABLE "training"."training_assessment_question" TO "app_user";
GRANT UPDATE ON TABLE "training"."training_assessment_question" TO "app_user";
-- relation:training.training_attendee [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "training"."training_attendee" TO "app_user";
GRANT INSERT ON TABLE "training"."training_attendee" TO "app_user";
GRANT SELECT ON TABLE "training"."training_attendee" TO "app_user";
GRANT UPDATE ON TABLE "training"."training_attendee" TO "app_user";
-- relation:training.training_course [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "training"."training_course" TO "app_user";
GRANT INSERT ON TABLE "training"."training_course" TO "app_user";
GRANT SELECT ON TABLE "training"."training_course" TO "app_user";
GRANT UPDATE ON TABLE "training"."training_course" TO "app_user";
-- relation:training.training_course_assignment [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "training"."training_course_assignment" TO "app_user";
GRANT INSERT ON TABLE "training"."training_course_assignment" TO "app_user";
GRANT SELECT ON TABLE "training"."training_course_assignment" TO "app_user";
GRANT UPDATE ON TABLE "training"."training_course_assignment" TO "app_user";
-- relation:training.training_course_module [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "training"."training_course_module" TO "app_user";
GRANT INSERT ON TABLE "training"."training_course_module" TO "app_user";
GRANT SELECT ON TABLE "training"."training_course_module" TO "app_user";
GRANT UPDATE ON TABLE "training"."training_course_module" TO "app_user";
-- relation:training.training_course_prerequisite [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "training"."training_course_prerequisite" TO "app_user";
GRANT INSERT ON TABLE "training"."training_course_prerequisite" TO "app_user";
GRANT SELECT ON TABLE "training"."training_course_prerequisite" TO "app_user";
GRANT UPDATE ON TABLE "training"."training_course_prerequisite" TO "app_user";
-- relation:training.training_course_progress [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "training"."training_course_progress" TO "app_user";
GRANT INSERT ON TABLE "training"."training_course_progress" TO "app_user";
GRANT SELECT ON TABLE "training"."training_course_progress" TO "app_user";
GRANT UPDATE ON TABLE "training"."training_course_progress" TO "app_user";
-- relation:training.training_event [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "training"."training_event" TO "app_user";
GRANT INSERT ON TABLE "training"."training_event" TO "app_user";
GRANT SELECT ON TABLE "training"."training_event" TO "app_user";
GRANT UPDATE ON TABLE "training"."training_event" TO "app_user";
-- relation:training.training_exam_answer [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "training"."training_exam_answer" TO "app_user";
GRANT INSERT ON TABLE "training"."training_exam_answer" TO "app_user";
GRANT SELECT ON TABLE "training"."training_exam_answer" TO "app_user";
GRANT UPDATE ON TABLE "training"."training_exam_answer" TO "app_user";
-- relation:training.training_exam_attempt [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "training"."training_exam_attempt" TO "app_user";
GRANT INSERT ON TABLE "training"."training_exam_attempt" TO "app_user";
GRANT SELECT ON TABLE "training"."training_exam_attempt" TO "app_user";
GRANT UPDATE ON TABLE "training"."training_exam_attempt" TO "app_user";
-- relation:training.training_lesson [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "training"."training_lesson" TO "app_user";
GRANT INSERT ON TABLE "training"."training_lesson" TO "app_user";
GRANT SELECT ON TABLE "training"."training_lesson" TO "app_user";
GRANT UPDATE ON TABLE "training"."training_lesson" TO "app_user";
-- relation:training.training_lesson_progress [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "training"."training_lesson_progress" TO "app_user";
GRANT INSERT ON TABLE "training"."training_lesson_progress" TO "app_user";
GRANT SELECT ON TABLE "training"."training_lesson_progress" TO "app_user";
GRANT UPDATE ON TABLE "training"."training_lesson_progress" TO "app_user";
-- relation:training.training_program [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "training"."training_program" TO "app_user";
GRANT INSERT ON TABLE "training"."training_program" TO "app_user";
GRANT SELECT ON TABLE "training"."training_program" TO "app_user";
GRANT UPDATE ON TABLE "training"."training_program" TO "app_user";
-- relation:training.training_question [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "training"."training_question" TO "app_user";
GRANT INSERT ON TABLE "training"."training_question" TO "app_user";
GRANT SELECT ON TABLE "training"."training_question" TO "app_user";
GRANT UPDATE ON TABLE "training"."training_question" TO "app_user";
-- relation:training.training_question_bank [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "training"."training_question_bank" TO "app_user";
GRANT INSERT ON TABLE "training"."training_question_bank" TO "app_user";
GRANT SELECT ON TABLE "training"."training_question_bank" TO "app_user";
GRANT UPDATE ON TABLE "training"."training_question_bank" TO "app_user";
-- relation:training.training_question_option [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "training"."training_question_option" TO "app_user";
GRANT INSERT ON TABLE "training"."training_question_option" TO "app_user";
GRANT SELECT ON TABLE "training"."training_question_option" TO "app_user";
GRANT UPDATE ON TABLE "training"."training_question_option" TO "app_user";
-- relation:training.training_question_tag [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "training"."training_question_tag" TO "app_user";
GRANT INSERT ON TABLE "training"."training_question_tag" TO "app_user";
GRANT SELECT ON TABLE "training"."training_question_tag" TO "app_user";
GRANT UPDATE ON TABLE "training"."training_question_tag" TO "app_user";
-- relation:training.training_question_tag_map [relkind r] -- legacy-estate-compatibility-baseline
GRANT DELETE ON TABLE "training"."training_question_tag_map" TO "app_user";
GRANT INSERT ON TABLE "training"."training_question_tag_map" TO "app_user";
GRANT SELECT ON TABLE "training"."training_question_tag_map" TO "app_user";
GRANT UPDATE ON TABLE "training"."training_question_tag_map" TO "app_user";
-- ===== section: sequences =====
-- sequence:people.payroll_number_sequence_id_seq -- legacy-estate-compatibility-baseline
GRANT SELECT ON SEQUENCE "people"."payroll_number_sequence_id_seq" TO "app_user";
GRANT UPDATE ON SEQUENCE "people"."payroll_number_sequence_id_seq" TO "app_user";
GRANT USAGE ON SEQUENCE "people"."payroll_number_sequence_id_seq" TO "app_user";

COMMIT;
