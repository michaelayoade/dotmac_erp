-- GENERATED FILE -- do not edit. Regenerate with:
--     python scripts/generate_privilege_manifest.py
-- Source of truth: docs/inventories/erp-identity-cutover-manifest-2026-09-04.json
-- Census:          docs/inventories/erp-privilege-census-2026-09-04.json
--
-- Change 1, REVIEW-REQUIRED half: SECURITY DEFINER function EXECUTE, the control-plane module-era relation, and schema USAGE the census did not observe. DO NOT APPLY until each row below has been individually reviewed and signed off. These are deliberately NOT folded into the sweep.
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
-- Rows: 14


BEGIN;

-- ===== section: schema_usage =====
-- schema:hr -- derived-usage-unrecorded-in-census
GRANT USAGE ON SCHEMA "hr" TO "app_user";
-- schema:mod_files -- derived-usage-unrecorded-in-census
GRANT USAGE ON SCHEMA "mod_files" TO "app_user";
-- schema:public -- derived-usage-unrecorded-in-census
GRANT USAGE ON SCHEMA "public" TO "app_user";
-- schema:rpt -- derived-usage-unrecorded-in-census
GRANT USAGE ON SCHEMA "rpt" TO "app_user";
-- schema:sync -- derived-usage-unrecorded-in-census
GRANT USAGE ON SCHEMA "sync" TO "app_user";
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
-- relation:mod_files.platform_stored_files [relkind r] -- module-era-grant-already-held-by-legacy-role
GRANT DELETE ON TABLE "mod_files"."platform_stored_files" TO "app_user";
GRANT INSERT ON TABLE "mod_files"."platform_stored_files" TO "app_user";
GRANT SELECT ON TABLE "mod_files"."platform_stored_files" TO "app_user";
GRANT UPDATE ON TABLE "mod_files"."platform_stored_files" TO "app_user";

COMMIT;
