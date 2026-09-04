-- GENERATED FILE -- do not edit. Regenerate with:
--     python scripts/generate_privilege_manifest.py
-- Source of truth: docs/inventories/erp-identity-cutover-manifest-2026-09-04.json
-- Census:          docs/inventories/erp-privilege-census-2026-09-04.json
--
-- Change 1, EXCEPTIONAL half: SECURITY DEFINER function EXECUTE (review required, one body at a time) and the control-plane module-era relation (DENIED -- rendered as comments, never executed). DO NOT APPLY until each remaining row has been individually reviewed and signed off. These are deliberately NOT folded into the sweep, permanently: exceptional authorization does not belong inside mechanical compatibility. The five derived schema-USAGE rows that used to live here were SETTLED on 2026-09-04 as no-ops and removed.
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
-- Rows: 5 to grant, 4 DENIED.
-- A DENIED row is rendered as a comment and is NEVER executed. It is kept
-- here so the refusal is visible: a denial that is merely absent cannot be
-- told apart from a denial nobody thought of.


BEGIN;

-- ===== section: functions =====
-- function:hr.enforce_employment_type_projection() -- security-definer-execute-individual-review
GRANT EXECUTE ON FUNCTION "hr"."enforce_employment_type_projection"() TO "app_user";
-- function:public.claim_outbox_batch(text, integer, integer) -- security-definer-execute-individual-review
GRANT EXECUTE ON FUNCTION "public"."claim_outbox_batch"(text, integer, integer) TO "app_user";
-- function:public.claim_platform_outbox_batch(text, integer, integer) -- security-definer-execute-individual-review
GRANT EXECUTE ON FUNCTION "public"."claim_platform_outbox_batch"(text, integer, integer) TO "app_user";
-- function:public.settle_outbox_event(uuid, text, text, timestamp with time zone, integer, text) -- security-definer-execute-individual-review
GRANT EXECUTE ON FUNCTION "public"."settle_outbox_event"(uuid, text, text, timestamp with time zone, integer, text) TO "app_user";
-- function:public.settle_platform_outbox_event(uuid, text, text, timestamp with time zone, integer, text) -- security-definer-execute-individual-review
GRANT EXECUTE ON FUNCTION "public"."settle_platform_outbox_event"(uuid, text, text, timestamp with time zone, integer, text) TO "app_user";
-- ===== section: module_era =====
-- relation:mod_files.platform_stored_files [relkind r] -- denied-by-architecture-control-plane-relation
--   DENIED (denied_by_architecture): ADR-0023 control-plane relation.
--   The statements below are NOT executed and must not be uncommented.
--   NOT GRANTED: GRANT DELETE ON TABLE "mod_files"."platform_stored_files" TO "app_user";
--   NOT GRANTED: GRANT INSERT ON TABLE "mod_files"."platform_stored_files" TO "app_user";
--   NOT GRANTED: GRANT SELECT ON TABLE "mod_files"."platform_stored_files" TO "app_user";
--   NOT GRANTED: GRANT UPDATE ON TABLE "mod_files"."platform_stored_files" TO "app_user";

COMMIT;
