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
python scripts/deploy_production.py \
  --host erp.dotmac.io \
  --forward-fix-after-migration \
  --expected-checkout-sha=<full-40-hex-release-commit>
```

Use the last form only for a reviewed release whose migration cannot safely
be followed by the previous image. The full expected SHA must name the
approved checkout after `git pull`; a mismatch refuses before runtime drain
or migration. The mode stops app, worker, and beat and verifies no old Compose
runtime remains before invoking Alembic. From the migration attempt onward,
including an ambiguous failure, it leaves old code and image stopped for a
reviewed forward fix. It does not automatically restore the previous release.
Employment Type activation retains its separate authority probe and cannot be
combined with this mode.

Before the first use, the remote checkout must already contain this option:
the controller invokes the checkout's `deploy.sh` before that script performs
its internal pull. Updating that checkout is a separate reviewed step; the
full-SHA check still runs after the deploy script's pull and before the drain.

`scripts/deploy.sh` continues to accept an operator-supplied
`MIGRATION_DATABASE_URL` as a break-glass path. The normal production entrypoint
is the controller wrapper above.

## Production runtime binding

The deployment controller's migration AppRole remains required for the canonical
`app_admin` DSN. Runtime OpenBao aliases are separate: the production descriptor
now omits those unused materials after the bounded 2026-10-14 known-consumer
census completed all catalog organizations, including inactive ones, with zero
reference prefixes. This source change does not record a live configuration
change or claim global completeness across unknown stores.

Activate `deploy/profiles/production-runtime-no-openbao.yml` as the ordinary
`docker-compose.override.yml` in the production checkout. If an override already
exists, stop and review a merge; preserve its current image, commands and other
bindings. The profile empties all four OpenBao/Vault address/token aliases only
for app, worker and Beat. Remove all assignments of `OPENBAO_ADDR`,
`OPENBAO_TOKEN`, `VAULT_ADDR` and `VAULT_TOKEN` from protected `.env`, including
duplicates, without displaying their values. Never place the controller's AppRole
coordinates there. See `deploy/README.md` for the evidence and coverage boundary.

Configured staging OpenBao remains authenticated and required. Before a future
production runtime reference is activated, provision its declared credential
access, remove this production binding, and pass authenticated readiness plus
the candidate boot-material/reference checks. No material gate is disabled.

## Candidate boot-material gate

The existing deploy script runs `python -m scripts.verify_deployment_secrets`
through each mandatory production Compose service (`app`, `worker`, `beat`),
after candidate image selection/pull and before DDL, then again after successful
migration and runtime admission, immediately before service replacement.
Compose supplies each role's own environment, mounts and the already selected
`APP_IMAGE`; the check bypasses the service startup command and starts no
Compose dependencies. Raw probe output and stderr are suppressed. A missing
CLI, unavailable probe or any nonzero status refuses with a fixed role diagnostic.

This is a bounded legacy security fix protecting DDL and service replacement.
It does **not** protect the earlier backup: the existing migration credential
preflight remains there, and installed images predating the CLI are not probed.
`--quick` also requires the retained image to provide the CLI, otherwise it
refuses before migration. No candidate image pin or promotion authority changes.

A postmigration refusal leaves migrations applied and does not recreate services
or invoke ordinary rollback. Ordinary deployments retain the serving old
containers; activation and forward-fix modes retain their existing forward-only
standing with old runtimes stopped. Repair material and rerun the reviewed
release. Full Foundation v4 adoption, including its paired role/image/mount
contract, remains pending; this gate does not claim that guarantee.

## Verification

Before relying on the principal, prove all of the following without printing
material:

1. AppRole login succeeds over loopback and returns a short-lived token.
2. That token can read the canonical field.
3. Reads of a sibling path and writes to the canonical path are denied.
4. Run the standalone read-only proof below against the explicitly approved
   running immutable image and database. `--quick` is a deployment mode and
   must never be used as a credential probe.

```bash
python scripts/verify_production_deployment_credentials.py \
  --host erp.dotmac.io \
  --ssh-user erp-proof \
  --identity-file /approved/protected/controller-key \
  --expected-image ghcr.io/michaelayoade/dotmac_erp@sha256:<64-hex-digest> \
  --expected-database dotmac_erp \
  --expected-authentication scram-sha-256:app_admin
```

Run this on the deployment controller with its approved private AppRole files
and a separately enrolled restricted proof identity. No Observe-to-ERP proof
SSH identity has been approved or enrolled. The command
requires a known SSH host key; it never accepts a new host key automatically.
The command validates nonsecret selectors and inspects the running app before
fetching the DSN. It binds the proof to that immutable container ID, rechecks
its running state, configured image, image ID and RepoDigest, and refuses
application/interpreter mounts, Python or dynamic loader overrides, or protected
file drift.
It streams the DSN through SSH and `docker exec` stdin into an isolated process
inside that exact container. It creates no container or persistent secret.

The installed image's `verify_migration_connection` remains the authority for
roles, ownership, database identity, direct authentication and reachable
privileges. The proof connects directly as `app_admin`, binds both explicit
expectations, sets transaction read-only before verifier queries, bounds
connection/statement/lock and server idle-transaction time, and rolls back even
on refusal. Kernel-enforced probe deadlines remain armed through rollback,
close and receipt: a blocked probe is terminated without a success receipt,
and its parent reports a closed refusal. It reports only `stage` and `status` names in JSON; exit 0 means
`database` / `verified`, and exit 2 is a refusal. Raw SSH, Docker, database and
verifier diagnostics are discarded, so investigate refusals through a
separately authorized diagnostic procedure.

This proves only the canonical credential against the selected **installed
runtime at the time of the probe**. It does not prove candidate acceptance,
backup, migration execution, all service boot materials, or AppRole sibling
path/write denials (items 1–3 require their own authorized custody evidence).
Missing verifier code or an uninspectable/overlaid runtime refuses without a
deployment fallback. The command does not pull images, change services, run
migrations, repair roles, write secrets, notify, or enable Talk. It is source
for a separately authorized invocation; adding it does not record a live
production verification.

Recreate the AppRole SecretID and replace the local `0600` file if controller
access is suspected to be compromised. Rotating the SecretID does not rotate
the database password or modify the canonical secret.

## Restricted proof enrollment kit — source only, NOT ENROLLED

`deploy/credential-proof/` contains review templates, not live configuration.
The proof uses only the literal SSH command `dotmac-erp-credential-proof-v1`.
The controller accepts only `erp-proof`, requires an explicit protected identity
path and uses only that key with agent access and alternate authentication
methods disabled.
The forced launcher compares that exact string and invokes only the no-argument
root helper. It never evaluates `SSH_ORIGINAL_COMMAND`. There is no uploaded-code
fallback, caller-selected Python, shell, file path, or Docker command.

Install the reviewed standalone `scripts/erp_credential_proof_server.py` as
`/usr/local/libexec/dotmac-erp-credential-proof.py` (root:root, 0755) and the
launcher as `/usr/local/libexec/dotmac-erp-proof-launcher` (root:root, 0755).
The helper uses the checked-in `#!/usr/bin/python3 -I` isolated interpreter
shebang, `/usr/bin/docker` and the fixed local Docker socket.
All helper/policy parents must be root-owned, without group/world writes or
symlinks. Install the approved policy at `/etc/dotmac-erp/credential-proof.json`
(root:root, 0600); its endpoint must be the independently verified application
network database endpoint, not an inferred management IP. The placeholder
policy deliberately refuses until that endpoint is supplied. The allowed
container, image repository, database and SCRAM app_admin authentication are
fixed in the helper as well as the policy. The DSN must contain an explicit
approved host and port and no query parameters; hostaddr, options, service,
passfile, multihost and alternate destinations refuse before Docker execution.

Create a dedicated locked-password `erp-proof` account with no Docker group
membership, no direct Docker socket access and no other sudo grant. Its home,
`.ssh` directory and authorized_keys must be root-owned and non-writable by
that account; disable user RC/environment and forwarding through the supplied
sshd Match template. Apply the authorized_keys template with the exact verified
controller source IP and approved public key. `restrict` denies PTY, agent,
port and X11 forwarding; the sshd rule also forces the launcher. The sudoers
template permits exactly one helper with an empty argument list, forbids
SETENV and explicitly disables sudo input/output logging for this command with
`NOLOG_INPUT: NOLOG_OUTPUT:`. Before enrollment, verify the effective matching
sudo policy has both I/O logging directions disabled even when global defaults
enable them; refuse enrollment if another matching rule or external I/O recorder
can capture the credential-bearing stdin. Validate the concrete sudoers and sshd changes before reload, verify
account group/socket permissions and review effective matching configuration.
No wildcard shell, interpreter or Docker sudo grant is permitted.

Each SSH invocation reads one version-1 bounded JSON object. Inspection contains
only selectors; probe additionally contains the attested container ID and DSN.
Duplicate/unknown keys, unsupported versions, trailing JSON and oversized input
refuse. The helper checks its independent policy and repeats full source/image/
mount/diff inspection before the material-bearing DB exec. Its fixed installed
image driver reuses `bootstrap_database_roles.verify_migration_connection`,
with read-only transactions, rollback and hard deadlines through cleanup.
Only closed runtime/database receipts leave the helper. The controller obtains
material only after successful inspection. This kit does not replace the
credential resolver, Deployment Foundation, signing, Rust or Gate 0/D7 owners.

A separate live approval must name: the verified controller source IP; controller
key protected path and public-key fingerprint; `erp-proof` account; pinned ERP
host-key fingerprint; exact helper, launcher and policy SHA-256 hashes; verified
DB endpoint/port; and the exact SSH authorized_keys/sshd and no-argument sudoers
changes. Review the complete concrete enrollment diff before that approval.
Source review or passing CI does not authorize account/key creation, host
installation, enrollment, SSH execution or a live credential proof. No such
changes are recorded by this source kit.
