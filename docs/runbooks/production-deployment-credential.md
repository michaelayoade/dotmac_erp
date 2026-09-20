# Production deployment credential

The production deployment controller reads the `app_admin` migration DSN from
the canonical OpenBao pointer and streams it over SSH to the one-shot migration
commands on the explicitly named production host. The DSN is never placed in
Git, `.env`, process arguments, or a long-running application container.

## Ownership and boundaries

- Custody remains OpenBao at
  `secret/dotmac/postgres/erp-shared-primary/app_admin`, field
  `MIGRATION_DATABASE_URL`.
- `erp-production-deployer` is an AppRole used only by the deployment
  controller. Its policy is checked in at
  `deploy/openbao/erp-production-deployer.hcl` and grants one `read` capability
  on the canonical KV v2 data path.
- The AppRole may not rotate or write the credential. Rotation remains a
  separately authorized one-shot custody operation.
- The controller exchanges its AppRole material for a short-lived token. The
  token and DSN stay in process memory and the DSN crosses hosts only through
  SSH.
- Plain HTTP is accepted only for a loopback OpenBao address. A remote OpenBao
  endpoint must use HTTPS.

The controller stores AppRole coordinates in local files owned by the
deployment user and mode `0600`:

```text
~/.config/dotmac-erp/production-openbao-role-id
~/.config/dotmac-erp/production-openbao-secret-id
```

These are approved local pointers, not repository or synchronized files. Never
copy either value into `.env`, a report, a pull request, or runtime Compose
configuration.

## Deploy

Name the production target explicitly on every invocation:

```bash
python scripts/deploy_production.py --host erp.dotmac.io
```

The wrapper supports the same guarded selectors used by `deploy.sh`:

```bash
python scripts/deploy_production.py --host erp.dotmac.io --quick
python scripts/deploy_production.py --host erp.dotmac.io sha256:<64-hex-digest>
python scripts/deploy_production.py \
  --host erp.dotmac.io \
  --people-employment-type-activation
```

`scripts/deploy.sh` continues to accept an operator-supplied
`MIGRATION_DATABASE_URL` as a break-glass path. The normal production entrypoint
is the controller wrapper above.

## Verification

Before relying on the principal, prove all of the following without printing
material:

1. AppRole login succeeds over loopback and returns a short-lived token.
2. That token can read the canonical field.
3. Reads of a sibling path and writes to the canonical path are denied.
4. `scripts/deploy_production.py --host erp.dotmac.io --quick` reaches the
   normal deployment preflight without placing the DSN in argv or `.env`.

Recreate the AppRole SecretID and replace the local `0600` file if controller
access is suspected to be compromised. Rotating the SecretID does not rotate
the database password or modify the canonical secret.
