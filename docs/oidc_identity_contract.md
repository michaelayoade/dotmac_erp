# OIDC Identity Contract

> **Status: SHARED ADAPTER ADOPTED (amended 2026-08-17).** ERP has no local
> protocol implementation. It deliberately composes published
> `dotmac-auth-oidc` and keeps Person, binding, session and cookie authority
> local as recorded by ADR-0004.

## Amendment — 2026-08-17 ERP-local finalization is implemented

This amendment supersedes the earlier “login is still blocked” state below,
which remains as decision history. `ERPExternalIdentityAuthority` is the sole
writer for an organization-scoped exact `(provider_binding, issuer, subject)`
binding. Login only finalizes an active pre-provisioned binding and active
Person; it never creates or links by email. `AuthFlow` issues a provenance-
bearing ERP session under the same database transaction. Disable locks the same
binding row and selectively revokes only its sessions in that transaction.

The published adapter owns discovery, HTTPS endpoints, asymmetric signature
verification, required issuer/subject/audience, multi-audience `azp`, S256 and
nonce. ERP consumes only verified issuer and subject. Its server-side ceremony
store is FORCE-RLS protected and claims with atomic `DELETE ... RETURNING`.
Client secret material is installed once at startup; an explicit failed refresh
retains the working held registration, and request paths do not read the
environment or secret store. Kernel Party/AuthSession remain prohibited.

Managed provisioning does not trust caller-copied subject values. The
product-owned capability composition maps the public `identity.user.lifecycle`
APPLY receipt's exact `issuer_url` and immutable `subject` into ERP's desired
external-subject tuple. The upstream `identity_ref` remains the IdP account's
stable lookup/correlation key; ERP still resolves only its own exact tuple to
Person. ERP's local `provider_binding` is never sourced from provider evidence.

## Superseded decision history — managed lifecycle was present while login was blocked

ERP now owns the provider-neutral `erp.application.lifecycle.v1` contract and a
durable local PLAN/APPLY/OBSERVE/CANCEL receipt. This does **not** reintroduce
OIDC. PLAN records the exact target and current-state digests; APPLY verifies
those pins and then returns the stable failure
`external_identity_not_adopted` before changing Person, credentials or sessions.

The reason is an authoritative conflict, not missing adapter code:
`docs/PLATFORM_ADOPTION_LEDGER.md` prohibits kernel `Party`, `UserCredential`
and `AuthSession`, while the released `dotmac-auth-oidc` adoption path resolves
kernel external-identity bindings and stamps kernel session provenance. Using
the retained, globally unique ERP `federated_identities` table would restore the
retired parallel identity owner; writing provenance into ERP `sessions` would
create a second session-revocation contract. An explicit ERP identity/session
authority migration ADR must supersede that prohibition before APPLY or login
can be enabled. Until then, the capability's activation check remains false and
the API fails closed.

## Amendment — the implementation was deleted

`erp.dotmac.io` was inspected read-only on 2026-08-15. In the running
`dotmac_erp_app` container `OIDC_ENABLED` was unset (so the feature was off by
its own default), `OIDC_ISSUER` / `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` were
all unset, and `federated_identities` held **0 rows, 0 active**. The
implementation had never been enabled, had never authenticated anyone, and held
no data. It was therefore deleted rather than cut over or migrated.

Deleted:

- `app/services/sso/` — the entire protocol adapter (discovery, PKCE, code
  exchange, JWKS, ID-token validation, binding resolution).
- The `/auth/oidc/callback` web route and the login-page OIDC branch.
- `GET`/`POST`/`DELETE /api/v1/auth/oidc/identities` and their
  `FederatedIdentityCreate` / `FederatedIdentityRead` schemas.
- Every `OIDC_*` setting in `app/config.py` and `.env.example`.
- `tests/test_oidc_boundary.py`.

Two live defects were removed with it, neither of which needs a fix now because
the code that carried them is gone:

1. **Shared signing secret.** `oidc.py` imported `_jwt_secret` from
   `app.services.auth_flow` and signed OIDC state with the *same* key ERP signs
   its session JWTs with, making each protocol a forgery oracle for the other.
   Any reintroduction MUST derive or provision a distinct key for protocol
   state; reusing the session-JWT secret is prohibited.
2. **Audience was not actually verified.** `jwt.decode(..., audience=client_id)`
   does not reject a token with a *missing* `aud` claim under `python-jose`
   3.3.0 — `_validate_aud` returns early when the claim is absent (the `raise`
   is commented out in the library) — and ERP never listed `aud` in its
   `require` options. A token with no audience passed. Any reintroduction MUST
   require `aud` explicitly, not merely pass an expected value.

The removed code was also untested where it mattered: `_validate_id_token` and
`_exchange_code` were monkeypatched out of every test, so signature
verification, the algorithm allowlist, `kid` handling, nonce mismatch, and every
claim-validation failure path had zero real coverage. That is the direct reason
defect 2 survived review.

Retained, deliberately:

- The `federated_identities` **table** and migration
  `20260720_federated_identity`, and the `FederatedIdentity` ORM class that
  describes it. The table is empty in production and harmless; dropping it is a
  destructive schema change that belongs in its own reviewed commit (see
  "Retiring the table" below). The ORM class stays only so the live schema is
  not left undescribed in Python — nothing under `app/` reads or writes it, and
  `tests/architecture/test_identity_protocol_boundary.py` fails if anything
  starts.
- `AuthProvider.sso` as an enum value, because it is a persisted
  `user_credentials.provider` value. `AuthFlow.login` still refuses any
  non-`local` provider; the rejection message no longer promises an OIDC flow.
- `python-jose`, which ERP still needs for its own HS256 session tokens
  (`app/services/auth_flow.py`, `app/observability.py`) and for the
  `cryptography` extra that `app/licensing` depends on. Only the
  *asymmetric third-party token verification* use disappeared.

## What is true today

| Concern | Authority |
|---|---|
| Authentication ceremony | Dotmac ERP — local username and password only |
| ERP user active/inactive decision | Dotmac ERP `people` |
| ERP roles and permissions | Dotmac ERP RBAC |
| ERP access/refresh tokens and sessions | Dotmac ERP |
| ERP cookies and logout | Dotmac ERP |

There is no external identity provider, no federated login, and no route that
accepts a token minted anywhere but ERP.

`OIDC_ENABLED`, `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`,
`OIDC_DISCOVERY_URL`, `OIDC_REDIRECT_URI`, `OIDC_SCOPES`, and
`OIDC_REQUEST_TIMEOUT` are read by nothing. Setting them has no effect and must
not be reintroduced ad hoc — a knob that configures nothing is worse than no
knob, because an operator who sets it believes federated login is on. They join
the already-retired `AUTH_DATABASE_URL`, `SSO_PROVIDER_MODE`, `SSO_JWT_SECRET`,
`SSO_COOKIE_DOMAIN`, and `SSO_PROVIDER_URL` on the must-not-restore list.

## Reintroducing external identity

ERP remains the intended **second** consumer of the `dotmac-auth-oidc` package
(the Workspace pilot is first). Deleting the local implementation does not
preclude that — it means reintroduction is a clean adoption of a released,
independently tested adapter rather than a port of untested code, which is the
cheaper and safer of the two paths.

Reintroduction requires all of the following, and none of it is a
"re-add the file" change:

1. Adopt the released `dotmac-auth-oidc` package. Do **not** re-implement the
   protocol in this repository — `tests/architecture/test_identity_protocol_boundary.py`
   fails the build if in-process protocol machinery reappears under `app/`.
2. Prove signature verification, the algorithm allowlist, `kid` selection,
   `iss`/`aud`/`exp`/`iat` requirement (including a **missing** `aud`), and
   nonce mismatch against real tokens. Monkeypatching the validator out is what
   hid defect 2; a test suite that does it again is not acceptance evidence.
3. Hold the ceremony **server-side**, addressed by an opaque, single-use
   state id, in a **shared atomic `StateStore`**.

   Not "state signing material distinct from the session-JWT secret" — that was
   this document's earlier requirement and it is superseded. It described a
   better version of the wrong design.

   Signing the ceremony into the `state` parameter makes it tamper-evident but
   still **readable** by anything that sees the URL: a referrer header, a proxy
   log, browser history. The PKCE verifier is in there, and possession of it
   plus an intercepted code completes the exchange — which is the interception
   PKCE exists to prevent. Its confidentiality would rest on the consumer
   setting an `HttpOnly` cookie, which is a property of the integration rather
   than of the design.

   An opaque id has nothing to leak, so the question disappears instead of being
   managed. It also removes the moving part: with nothing serialized there is no
   signing key, no domain separator, and no key separation to get wrong — which
   is what defect 1 above was. **The safest version of that defect is not a
   better key; it is no key.**

   The store must be SHARED across every process serving the callback and its
   claim must be ATOMIC (`DELETE ... RETURNING`, or Redis `GETDEL`). ERP runs
   `cpu_count() * 2 + 1` gunicorn workers, so a per-process store fails a share
   of logins at random. Claiming the ceremony is how the verifier is recovered,
   so single use is structural rather than an added check: a replayed callback
   finds nothing to exchange with.

   `dotmac-auth-oidc` implements exactly this and requires a store — there is no
   null-store mode to fall into.
4. Re-register an `auth.oidc` owner in `app/services/sot_relationships.py`.
5. Re-add the admin binding routes and regenerate
   `tests/architecture/openapi_contract_surface.json` with
   `python scripts/update_openapi_contract.py`, so the contract change is a
   reviewed diff.
6. Recreate `federated_identities` if it has been retired by then.

The target boundary, unchanged: the identity provider proves identity and
nothing else. Provider role, group, scope, organization, and employee claims are
never accepted as ERP authorization. Email is display evidence only and is never
used for automatic account linking. ERP resolves the exact `(issuer, subject)`
binding to a local person, verifies that person is active, and issues its own
session and cookies. Replacing the provider changes provider configuration and
bindings only; it does not change ERP people, RBAC, sessions, or any Dotmac Sub
schema or service.

## Retiring the table (recommended, separate change)

`federated_identities` should be dropped, but not in the same commit as this
deletion. The recommendation and its implications:

- The table is empty in production (verified 0 rows), so the drop loses no data
  and needs no backfill, export, or archive policy.
- Migration `20260720_federated_identity` must **not** be edited or deleted:
  `20260721_add_extended_info_change_request_fields` revises it, so removing it
  breaks the lineage for every deployment that has already applied it. The drop
  is a NEW migration at the current head whose `downgrade()` recreates the table
  exactly as `20260720` defined it.
- The same commit removes the `FederatedIdentity` class, its export from
  `app/models/__init__.py`, and its entry in `tests/conftest.py`'s
  `SQLITE_COMPATIBLE_TABLES`.
- **Replace `test_federated_identity_has_no_reader_or_writer`; do not simply
  delete it.** That guard protects the interim state — a table with no
  consumer — and once the table is gone its premise has evaporated. But
  deleting it leaves the region UNMONITORED rather than resolved, which is the
  distinction ADR-0018 draws: an exemption (or a retirement) has to state an
  enforceable premise, and "nothing checks this any more" is not one.

  The successor asserts the stronger post-drop invariant, in three parts:
  **no table** (absent from the models metadata and from the migration head's
  schema), **no model** (no `FederatedIdentity` class anywhere), and **no
  consumer** (no reference to the class and no raw `federated_identities` in
  SQL — the raw-name half is what a model-only scan misses). Carry the AST
  scanners from the current guard rather than rewriting them as text matches,
  and keep their sensitivity proofs.
- If external identity is reintroduced before the drop lands, drop the drop
  instead: the table is already the right shape.
