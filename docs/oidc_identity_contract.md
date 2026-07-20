# OIDC Identity Contract

## Decision

Dotmac ERP consumes external authentication through OpenID Connect
Authorization Code flow with PKCE. The identity provider is replaceable and is
not an ERP database, session store, authorization service, or shared cookie
authority.

| Concern | Authority |
|---|---|
| Authentication ceremony, issuer and subject | Configured OIDC provider |
| Issuer/subject to ERP person binding | Dotmac ERP `federated_identities` |
| ERP user active/inactive decision | Dotmac ERP `people` |
| ERP roles and permissions | Dotmac ERP RBAC |
| ERP access/refresh tokens and sessions | Dotmac ERP |
| ERP cookies and logout | Dotmac ERP |

Provider role, group, scope, organization, and employee claims are not accepted
as ERP authorization. Email is display evidence only and is never used for
automatic account linking.

## Protocol

1. `/login` creates signed, short-lived state containing a nonce, PKCE verifier,
   and local return path.
2. ERP redirects to the provider authorization endpoint discovered from the
   configured issuer.
3. `/auth/oidc/callback` verifies state, exchanges the code using the client
   credential, and validates the ID token using provider JWKS, issuer, audience,
   expiration, issued-at time, and nonce.
4. ERP resolves the exact `(issuer, subject)` binding and verifies that the
   local person is active.
5. ERP creates its own local session and cookies, loading roles and permissions
   only from ERP.

All redirect targets are restricted to relative ERP paths or the current ERP
host. OIDC state uses an HttpOnly, Secure, SameSite=Lax callback cookie; ERP
session cookies retain ERP-local domain settings.

## Configuration

- `OIDC_ENABLED`
- `OIDC_ISSUER`
- `OIDC_CLIENT_ID`
- `OIDC_CLIENT_SECRET` (an OpenBao reference is supported)
- `OIDC_DISCOVERY_URL` (optional; defaults from issuer)
- `OIDC_REDIRECT_URI` (optional; defaults to `/auth/oidc/callback` on ERP)
- `OIDC_SCOPES` (defaults to `openid profile email`)
- `OIDC_REQUEST_TIMEOUT`

The removed `AUTH_DATABASE_URL`, `SSO_PROVIDER_MODE`, `SSO_JWT_SECRET`,
`SSO_COOKIE_DOMAIN`, and `SSO_PROVIDER_URL` settings must not be restored.

## Provisioning and cutover

An ERP administrator provisions bindings through
`POST /api/v1/auth/oidc/identities` with an existing ERP `person_id` and the
provider's opaque subject. Bindings are unique per issuer and person. Disabling
a binding retains the record for auditability.

Cutover gate:

1. Register ERP as an OIDC confidential client with the exact callback URI.
2. Store the client secret in OpenBao and configure only its pointer.
3. Provision and verify bindings for pilot users.
4. Test login, local role changes, user deactivation, logout, and provider
   outage behavior.
5. Enable `OIDC_ENABLED`. Keep local administrator access available during the
   pilot and remove obsolete deployment variables after verification.

Replacing the provider changes OIDC configuration and requires explicit new
issuer/subject bindings. It does not change ERP people, RBAC, sessions, or any
Dotmac Sub schema or service.
