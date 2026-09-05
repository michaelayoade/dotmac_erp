"""Pure contract for CUSTODY and RECONCILIATION of the ``app_admin`` migration DSN.

This module is a contract, not a reconciler. It performs no I/O, opens no
connection, reads no store, and — by construction — never holds, receives,
derives, prints or returns credential material. Everything here is a
*pointer*, an *authorization*, an *observation* or a *receipt*.

Read ``docs/adr/0011-the-app-admin-migration-credential-has-one-custody-pointer.md``
for the decision and ``docs/inventories/2026-09-04-erp-migration-credential-custody.md``
for the declared pointer and the forbidden-credential list.

WHY THIS EXISTS
---------------
``deploy/product.toml``'s ``[migration].owner_material`` names
``MIGRATION_DATABASE_URL``, and ``scripts/deploy.sh`` refuses to run without it
and never falls back to ``DATABASE_URL``. That makes ``app_admin`` the sole
migration executor for EVERY deployment candidate, not merely for privilege
work. What the repository never said is *where that credential lives* and *who
may change it*. "The approved secret source" is prose; it does not resolve.

A pointer that does not resolve cannot be reconciled, and a credential that
cannot be reconciled is one failed authentication away from blocking every
deployment. That is the state ERP is in.

WHAT A POINTER IS
-----------------
``mount`` + ``path`` + ``field``. Nothing else. A pointer identifies where
material is kept; it is not, and may never become, a container for it.
``CustodyPointer`` refuses at construction anything that looks like material,
and ``ReconciliationReceipt`` re-applies the same refusal across every one of
its own string fields — a receipt is the artifact most likely to be pasted into
a ticket, so it is the one that must be structurally unable to carry a secret.

ENFORCEMENT STATUS (starter ADR-0018)
-------------------------------------
Nothing in this module is enforced by a test today. There is no architecture
gate binding ``CUSTODY_*`` to the deployment inventory, no gate proving the
rendered Compose artifact keeps the owner material to the ``migrate`` service,
and no recorded inspection of the running host. Those regions are
**unmonitored, not exempt** — an exemption states an enforceable premise, and
none of these can state one yet. The inventory names each gap and what would
close it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields
from datetime import datetime
from enum import Enum
from typing import Final

from app.migration_database_roles import MIGRATION_EXECUTOR, ROLE_CONTRACT

# ---------------------------------------------------------------------------
# The declared pointer. Pointer only — path and field, never a value.
#
# The shape follows the one custody pointer ERP had already written down:
# `secret/dotmac/postgres/erp-shared-primary/postgres`, named in
# `docs/runbooks/database-restore.md` as the approved source for the cluster
# superuser after a restore. This is the same cluster, keyed by the same
# `<mount>/dotmac/postgres/<cluster>/<role>` grammar, so the two are siblings
# rather than two inventions.
#
# The field is named for the environment variable the material is installed as,
# so a reader of the store can tell what the value is FOR without dereferencing
# it. `deploy/product.toml`'s `owner_material` and this field name are the same
# string on purpose.
# ---------------------------------------------------------------------------

CUSTODY_MOUNT: Final[str] = "secret"
CUSTODY_PATH: Final[str] = "dotmac/postgres/erp-shared-primary/app_admin"
CUSTODY_FIELD: Final[str] = "MIGRATION_DATABASE_URL"

#: Two-phase rotation stages the candidate at a SEPARATE path so the canonical
#: path keeps resolving to working material for the whole rotation. See the
#: ADR's "the window between install and promote".
CANDIDATE_PATH: Final[str] = "dotmac/postgres/erp-shared-primary/app_admin_candidate"


class CustodyError(ValueError):
    """A custody contract was violated. Raised at construction, never later."""


def _looks_like_material(value: str) -> bool:
    """True when a string is shaped like a credential rather than a pointer.

    Deliberately narrow and decidable, in the shape
    ``tests/architecture/test_no_committed_credentials.py`` argues for: a DSN
    carrying userinfo (``scheme://user:pw@host``) and a ``password=``/``pwd=``
    keyword are the two forms this actually shows up in. An entropy heuristic
    would fire on digests and identifiers, get muted, and then catch nothing.

    A digest of the material is treated as material. A hash of a credential is
    an offline guessing oracle, and a receipt that carries one has not avoided
    disclosing the secret — it has disclosed a slower version of it.
    """
    lowered = value.lower()
    if "password=" in lowered or "pwd=" in lowered:
        return True
    if "://" not in lowered:
        return False
    authority = lowered.split("://", 1)[1].split("/", 1)[0]
    return "@" in authority


@dataclass(frozen=True, slots=True)
class CustodyPointer:
    """Where material is kept. Never what it is.

    ``mount``/``path``/``field`` are the three coordinates OpenBao needs and the
    three a reviewer needs. A pointer is safe to commit, safe to log, safe to
    paste into a ticket — which is the entire reason the contract is expressed
    as one.
    """

    mount: str
    path: str
    field: str

    def __post_init__(self) -> None:
        for name in ("mount", "path", "field"):
            raw = getattr(self, name)
            if not isinstance(raw, str) or not raw.strip():
                raise CustodyError(f"custody pointer {name} must be a non-empty string")
            if raw != raw.strip():
                raise CustodyError(f"custody pointer {name} carries surrounding space")
            if _looks_like_material(raw):
                raise CustodyError(
                    f"custody pointer {name} is shaped like credential material; a "
                    "pointer is a path and a field, and holds no value"
                )
        if "://" in self.path or "://" in self.mount:
            raise CustodyError(
                "custody pointer path/mount is a store location, not a URL; the "
                "openbao:// form is produced by reference(), not stored here"
            )

    def reference(self) -> str:
        """The repository's existing ``openbao://`` grammar for this pointer.

        ``app/services/secrets.py`` already resolves ``openbao://<mount>/data/
        <path>#<field>`` (KV v2 inserts ``data``). Producing the reference here
        keeps one spelling of it; nothing in this module dereferences it.
        """
        return f"openbao://{self.mount}/data/{self.path}#{self.field}"


CANONICAL_POINTER: Final[CustodyPointer] = CustodyPointer(
    mount=CUSTODY_MOUNT, path=CUSTODY_PATH, field=CUSTODY_FIELD
)
CANDIDATE_POINTER: Final[CustodyPointer] = CustodyPointer(
    mount=CUSTODY_MOUNT, path=CANDIDATE_PATH, field=CUSTODY_FIELD
)


class Operation(str, Enum):
    """What a single authorization permits. One authorization, one operation."""

    #: New material generated, installed, verified, promoted; predecessor retired.
    ROTATE = "rotate"
    #: Existing custody material re-installed on the role because the database
    #: and the store disagree. No new version is created.
    REINSTALL = "reinstall"
    #: Prove the canonical pointer authenticates. Changes nothing, anywhere.
    VERIFY = "verify"


class ReconciliationOutcome(str, Enum):
    """How a reconciliation ended. Four outcomes, and no fifth."""

    #: A precondition did not match. Nothing was staged, installed or promoted.
    REFUSED_BEFORE_ANY_CHANGE = "refused_before_any_change"
    #: Installed, verified from the real migration container, then promoted.
    VERIFIED_AND_PROMOTED = "verified_and_promoted"
    #: Verification failed and the predecessor was positively re-installed and
    #: re-verified. The deployment is exactly where it started.
    VERIFICATION_FAILED_ROLLED_BACK = "verification_failed_rolled_back"
    #: Verification failed and rollback could NOT be positively proven. Both
    #: versions remain readable, neither is retired, and the next deployment is
    #: expected to refuse until an operator resolves it. See the ADR's failure
    #: window: an ambiguous outcome is not a rollback.
    VERIFICATION_FAILED_DIVERGED = "verification_failed_diverged"


@dataclass(frozen=True, slots=True)
class DatabaseIdentity:
    """Which database. Host, port and name — never a user, never a password.

    ``bootstrap_database_roles.py --verify-only`` asserts ``current_user`` and
    role posture but does NOT assert WHICH database it reached. A reconciliation
    authorized for one database and executed against another would pass every
    check that script makes. This type is what closes that.
    """

    host: str
    port: int
    dbname: str

    def __post_init__(self) -> None:
        if not self.host.strip() or not self.dbname.strip():
            raise CustodyError("database identity requires a host and a dbname")
        if not 1 <= self.port <= 65535:
            raise CustodyError(f"database port {self.port} is out of range")

    def describe(self) -> str:
        return f"{self.host}:{self.port}/{self.dbname}"


@dataclass(frozen=True, slots=True)
class ReconciliationAuthorization:
    """Michael's approval of ONE reconciliation, bound to its exact target.

    Every field is a binding. An authorization that named fewer of them would
    be reusable against a target it was never granted for — which is the whole
    failure this type exists to make impossible. ``expected_current_version`` is
    the one people leave out and the one that matters most: it makes the
    approval refer to the state of custody at the moment of approval, so a
    concurrent rotation invalidates it rather than being silently overwritten.
    """

    environment: str
    database: DatabaseIdentity
    role: str
    pointer: CustodyPointer
    expected_current_version: int
    operation: Operation
    authorization_reference: str
    expires_at: datetime

    def __post_init__(self) -> None:
        if not self.environment.strip():
            raise CustodyError("authorization requires an environment")
        if self.role != MIGRATION_EXECUTOR:
            raise CustodyError(
                f"authorization names role {self.role!r}; this contract covers "
                f"only {MIGRATION_EXECUTOR!r}, the sole migration executor"
            )
        if self.expected_current_version < 1:
            raise CustodyError("expected_current_version must be a real KV version")
        if not self.authorization_reference.strip():
            raise CustodyError(
                "authorization_reference must name where the approval is "
                "recorded; an unreferenced approval is not one"
            )
        if self.expires_at.tzinfo is None:
            raise CustodyError("expires_at must be timezone-aware UTC")
        for value in (self.environment, self.authorization_reference):
            if _looks_like_material(value):
                raise CustodyError("authorization field carries credential material")


@dataclass(frozen=True, slots=True)
class ObservedTarget:
    """What the reconciler actually found, before it is allowed to change it.

    Populated from live reads, and compared against the authorization by
    :func:`authorization_refusals`. Nothing here is assumed from configuration.
    """

    environment: str
    database: DatabaseIdentity
    current_user: str
    is_superuser: bool
    bypasses_rls: bool
    pointer: CustodyPointer
    current_version: int


def authorization_refusals(
    authorization: ReconciliationAuthorization,
    observed: ObservedTarget,
    now: datetime,
) -> tuple[str, ...]:
    """Name EVERY way this authorization does not match this target.

    Every mismatch, not the first: an operator who fixes one refusal and re-runs
    into the next has been told the truth twice instead of once, and the
    round-trip is against production.

    An empty tuple is the only thing that permits a change to be made.
    """
    bypassrls_expected, superuser_expected = ROLE_CONTRACT[MIGRATION_EXECUTOR]
    refusals: list[str] = []

    if now.tzinfo is None:
        refusals.append("refusal clock is naive; expiry cannot be evaluated")
    elif now >= authorization.expires_at:
        refusals.append(
            f"authorization expired at {authorization.expires_at.isoformat()}"
        )

    if observed.environment != authorization.environment:
        refusals.append(
            f"target environment is {observed.environment!r}, authorized "
            f"{authorization.environment!r}"
        )
    if observed.database != authorization.database:
        refusals.append(
            f"target database is {observed.database.describe()}, authorized "
            f"{authorization.database.describe()}"
        )
    if observed.current_user != authorization.role:
        refusals.append(
            f"connected as {observed.current_user!r}, authorized to reconcile "
            f"{authorization.role!r}"
        )
    if observed.is_superuser != superuser_expected:
        refusals.append(
            f"{observed.current_user!r} is "
            f"{'SUPERUSER' if observed.is_superuser else 'NOSUPERUSER'}, contract "
            f"requires {'SUPERUSER' if superuser_expected else 'NOSUPERUSER'}"
        )
    if observed.bypasses_rls != bypassrls_expected:
        refusals.append(
            f"{observed.current_user!r} is "
            f"{'BYPASSRLS' if observed.bypasses_rls else 'NOBYPASSRLS'}, contract "
            f"requires {'BYPASSRLS' if bypassrls_expected else 'NOBYPASSRLS'}"
        )
    if observed.pointer != authorization.pointer:
        refusals.append(
            f"custody pointer in use is {observed.pointer.reference()}, authorized "
            f"{authorization.pointer.reference()}"
        )
    if observed.current_version != authorization.expected_current_version:
        refusals.append(
            f"custody holds version {observed.current_version}, authorization "
            f"expects {authorization.expected_current_version}; custody moved "
            "since this was approved and the approval no longer describes it"
        )
    return tuple(refusals)


@dataclass(frozen=True, slots=True)
class VerificationOutcome:
    """What the ACTUAL one-shot migration container observed.

    Not the reconciler's own connection, and not a psql on the host. The claim
    being made is "the migration container can authenticate and execute", and
    only that container can make it. Steps 5 and 6 of the slice are exactly the
    fields below: the two commands' exit codes and the four identity assertions.
    """

    image_digest: str
    role_contract_exit_code: int
    alembic_current_exit_code: int
    alembic_heads_observed: tuple[str, ...]
    connected_as: str
    connected_database: DatabaseIdentity
    is_superuser: bool
    bypasses_rls: bool

    def refusals(self, expected_database: DatabaseIdentity) -> tuple[str, ...]:
        bypassrls_expected, superuser_expected = ROLE_CONTRACT[MIGRATION_EXECUTOR]
        found: list[str] = []
        if not self.image_digest.startswith("sha256:"):
            found.append(
                "verification did not record an immutable image digest; a tag "
                "does not identify what actually ran"
            )
        if self.role_contract_exit_code != 0:
            found.append(
                "bootstrap_database_roles.py --verify-only exited "
                f"{self.role_contract_exit_code}"
            )
        if self.alembic_current_exit_code != 0:
            found.append(f"alembic current exited {self.alembic_current_exit_code}")
        if not self.alembic_heads_observed:
            found.append(
                "alembic current reported no heads; an exit code alone does not "
                "prove the migration state was readable"
            )
        if self.connected_as != MIGRATION_EXECUTOR:
            found.append(
                f"migration container connected as {self.connected_as!r}, "
                f"required {MIGRATION_EXECUTOR!r}"
            )
        if self.connected_database != expected_database:
            found.append(
                f"migration container reached {self.connected_database.describe()}, "
                f"expected {expected_database.describe()}"
            )
        if self.is_superuser != superuser_expected:
            found.append("migration executor superuser posture is wrong")
        if self.bypasses_rls != bypassrls_expected:
            found.append("migration executor BYPASSRLS posture is wrong")
        return tuple(found)

    def verified(self, expected_database: DatabaseIdentity) -> bool:
        return not self.refusals(expected_database)


@dataclass(frozen=True, slots=True)
class ReconciliationReceipt:
    """The durable record. Versions, identities and a result — never material.

    The receipt is the artifact that gets pasted into a ticket, attached to a
    change record and read a year later. It is therefore the one place where
    "we would never put a secret in there" has to be a property of the type
    rather than a habit, which is what ``__post_init__`` enforces.

    It also encodes the ordering: ``predecessor_retired`` cannot be true unless
    the run actually verified and promoted. Retiring the predecessor before
    verification is the single move that strands the credential, and the type
    refuses to describe a run that did it.
    """

    receipt_id: str
    recorded_at: datetime
    executed_by: str
    authorization_reference: str
    environment: str
    database: DatabaseIdentity
    role: str
    pointer: CustodyPointer
    candidate_pointer: CustodyPointer
    predecessor_version: int
    installed_version: int | None
    promoted_version: int | None
    predecessor_retired: bool
    operation: Operation
    outcome: ReconciliationOutcome
    verification: VerificationOutcome | None

    def __post_init__(self) -> None:
        if self.recorded_at.tzinfo is None:
            raise CustodyError("recorded_at must be timezone-aware UTC")
        if self.role != MIGRATION_EXECUTOR:
            raise CustodyError(f"receipt names role {self.role!r}")
        for spec in fields(self):
            value = getattr(self, spec.name)
            if isinstance(value, str) and _looks_like_material(value):
                raise CustodyError(
                    f"receipt field {spec.name!r} is shaped like credential "
                    "material; a receipt records versions, identities and a "
                    "result, and a digest of the material is material"
                )
        if self.predecessor_retired and (
            self.outcome is not ReconciliationOutcome.VERIFIED_AND_PROMOTED
            or self.promoted_version is None
        ):
            raise CustodyError(
                "predecessor_retired is only describable after a verified "
                "promotion; retiring it earlier is what strands the credential"
            )
        if (
            self.outcome is ReconciliationOutcome.REFUSED_BEFORE_ANY_CHANGE
            and self.installed_version is not None
        ):
            raise CustodyError(
                "a refusal before any change cannot report an installed version"
            )
        if (
            self.outcome is ReconciliationOutcome.VERIFIED_AND_PROMOTED
            and self.verification is None
        ):
            raise CustodyError("a promotion must carry the verification that earned it")


def receipt_disclosure_refusals(payload: Mapping[str, object]) -> tuple[str, ...]:
    """Name every entry of a serialized receipt that carries material.

    The counterpart to :class:`ReconciliationReceipt`'s own guard, for the
    moment a receipt is rendered to JSON, a log line or a ticket comment and
    has stopped being a typed object.
    """
    return tuple(
        f"receipt entry {key!r} carries credential material"
        for key, value in sorted(payload.items())
        if isinstance(value, str) and _looks_like_material(value)
    )


def environment_leak_refusals(
    service_environments: Mapping[str, Iterable[str]],
    *,
    permitted_services: frozenset[str] = frozenset({"migrate"}),
) -> tuple[str, ...]:
    """Name every service that can see ``MIGRATION_DATABASE_URL`` and may not.

    Step 8's decision function. It takes VARIABLE NAMES only — never values —
    because reading the value to check for it would be the disclosure the check
    exists to prevent.

    The caller supplies the observation, and which caller supplies it is what
    determines what has actually been established:

    * the rendered Compose artifact (``deploy/rendered/docker-compose.yml``)
      proves what the declarative artifact says;
    * ``docker inspect`` over the running containers proves what production
      actually holds, and only an operator on the host can produce it.

    The second is the one that matters and the one nothing produces today. See
    the inventory: ERP's own checked-in evidence says app, worker and beat
    currently DO see this variable.
    """
    return tuple(
        f"service {service!r} can see {CUSTODY_FIELD}; only "
        f"{sorted(permitted_services)} may"
        for service, names in sorted(service_environments.items())
        if service not in permitted_services and CUSTODY_FIELD in set(names)
    )


__all__ = [
    "CANDIDATE_POINTER",
    "CANDIDATE_PATH",
    "CANONICAL_POINTER",
    "CUSTODY_FIELD",
    "CUSTODY_MOUNT",
    "CUSTODY_PATH",
    "CustodyError",
    "CustodyPointer",
    "DatabaseIdentity",
    "ObservedTarget",
    "Operation",
    "ReconciliationAuthorization",
    "ReconciliationOutcome",
    "ReconciliationReceipt",
    "VerificationOutcome",
    "authorization_refusals",
    "environment_leak_refusals",
    "receipt_disclosure_refusals",
]
