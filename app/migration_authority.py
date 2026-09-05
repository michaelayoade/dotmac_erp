"""Two versioned authority policies over ONE shared catalogue observation.

`app.migration_database_roles` answers "is each role's own
`(rolbypassrls, rolsuper)` pair the one the contract froze?". That question is
frozen on purpose: `ROLE_CONTRACT` is what the applied `20260814` revision
asserted, and rewriting it would change the meaning of a revision already in
every database. This module asks the two questions that contract does NOT ask,
without touching it.

## Why two policies and not one list of exemptions

The shortcut is to add `app_admin` to the runtime subject set. It was rejected
(2026-09-04): the two authorities do not want the same answer.

===================  ==========================================  =====================================================
policy               direct posture                              forbidden membership closure
===================  ==========================================  =====================================================
runtime roles        NOSUPERUSER NOCREATEROLE NOBYPASSRLS        SUPERUSER, CREATEROLE, BYPASSRLS, server file/program
`app_admin`          BYPASSRLS NOSUPERUSER NOCREATEROLE          SUPERUSER, CREATEROLE, server file/program
                     NOCREATEDB NOREPLICATION
===================  ==========================================  =====================================================

A membership leading only to another BYPASSRLS role is not an escalation for
`app_admin` — it already legitimately holds that attribute, and refusing it
would refuse a correctly bootstrapped cluster. It IS an escalation for
`app_user`. One policy that answered both would have to carry a per-subject
exception table, which is two policies wearing one name.

## The two gaps this closes

Both were verified against the merged code, at the line, on 2026-09-05:

1. `ROLE_CONTRACT` is `role -> (rolbypassrls, rolsuper)` — TWO attributes.
   `rolcreaterole` appears in the old escalation query only as a property of a
   *reachable target*. A runtime role with `CREATEROLE` set directly ON ITSELF
   therefore passed everything: the contract never read the column, and the
   membership walk cannot fire because a role is not a member of itself.
2. The old `ROLE_ESCALATION_SQL` ended
   `WHERE target_role.rolsuper OR target_role.rolcreaterole OR
   target_role.rolbypassrls`. `pg_read_server_files`, `pg_write_server_files`
   and `pg_execute_server_program` hold NONE of those three attributes, so
   membership in them was discarded by the scanner before any evaluator could
   see it. Naming them in a policy would have changed nothing.

:data:`ROLE_AUTHORITY_SQL` therefore filters targets not at all. Every reachable
membership edge is observed and the POLICY decides. A scanner that pre-filters
by the old policy's shape cannot be given a new policy.

## Predefined roles that are privileged without a privileged attribute

PostgreSQL documents `pg_read_server_files`, `pg_write_server_files` and
`pg_execute_server_program` as able to read or write server-side files, or to
execute programs as the server's operating-system account. Those capabilities
may yield superuser-level access while the role holds none of SUPERUSER,
CREATEROLE or BYPASSRLS. They are named individually because "privileged
predefined roles" is a category, and a category is not checkable.

## Membership is treated conservatively, on purpose

PostgreSQL 16 records `set_option` on `pg_auth_members`, and a membership
granted `WITH SET FALSE` cannot presently be adopted with `SET ROLE`. This
module does not read that column and does not care. The invariant is
**prohibited privileged membership closure** — not "`SET ROLE` is executable
right now". `set_option` is mutable cluster state that a later `GRANT ... WITH
SET TRUE` flips without any migration running, so permitting an unnecessary
privileged membership because of its present value would rest the gate on
something nothing in this repository observes again.

## Server version

The observation reads `system_user`, which PostgreSQL added in 16. ERP targets
PostgreSQL 16 (`docs/app.md`, `docs/getting_started.md`, every CI service
container). On an older server the scanner fails loudly rather than silently
omitting the authentication proof, which is the correct direction for a gate.

## Regions this does NOT monitor

Stated rather than silently exempted (ADR-0018):

* escalation through a `SECURITY DEFINER` routine owned by a privileged role;
* arbitrary object privileges (`GRANT ... ON TABLE`), which are the privilege
  manifest's question, not this one;
* any grant, `ALTER ROLE` or role creation that happens AFTER the observation
  is taken. This is a point-in-time reading, and a drift observer is a
  separate, later piece of work;
* whether a PostgreSQL superuser can mutate the cluster. It can. Nothing here
  constrains one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final

from app.migration_database_roles import MIGRATION_EXECUTOR

# ---------------------------------------------------------------------------
# Failure codes — a CLOSED set.
# ---------------------------------------------------------------------------
#: Emitted when the connection cannot prove it authenticated AS the migration
#: executor: `session_user` and `current_user` disagree (a privileged session
#: that merely ran `SET ROLE app_admin`), the authenticated identity is not the
#: executor, or a bound authentication expectation is unmet.
MIGRATION_AUTHENTICATION_IDENTITY_MISMATCH: Final[str] = (
    "MIGRATION_AUTHENTICATION_IDENTITY_MISMATCH"
)
#: The migration executor's OWN role attributes are not the required posture.
MIGRATION_EXECUTOR_DIRECT_ATTRIBUTE_FORBIDDEN: Final[str] = (
    "MIGRATION_EXECUTOR_DIRECT_ATTRIBUTE_FORBIDDEN"
)
#: The migration executor can reach a role it must not be able to reach.
MIGRATION_EXECUTOR_PRIVILEGED_MEMBERSHIP: Final[str] = (
    "MIGRATION_EXECUTOR_PRIVILEGED_MEMBERSHIP"
)
#: A runtime role's OWN role attributes are not the required posture.
RUNTIME_ROLE_DIRECT_ATTRIBUTE_FORBIDDEN: Final[str] = (
    "RUNTIME_ROLE_DIRECT_ATTRIBUTE_FORBIDDEN"
)
#: A runtime role can reach a role it must not be able to reach.
RUNTIME_ROLE_PRIVILEGED_MEMBERSHIP: Final[str] = "RUNTIME_ROLE_PRIVILEGED_MEMBERSHIP"

AUTHORITY_FAILURE_CODES: Final[frozenset[str]] = frozenset(
    {
        MIGRATION_AUTHENTICATION_IDENTITY_MISMATCH,
        MIGRATION_EXECUTOR_DIRECT_ATTRIBUTE_FORBIDDEN,
        MIGRATION_EXECUTOR_PRIVILEGED_MEMBERSHIP,
        RUNTIME_ROLE_DIRECT_ATTRIBUTE_FORBIDDEN,
        RUNTIME_ROLE_PRIVILEGED_MEMBERSHIP,
    }
)


# ---------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------
#: Role attributes this module reads, in catalogue column order. Named
#: individually because the two policies disagree about which of them are
#: permitted, and a disagreement cannot be expressed over a category.
ATTRIBUTE_NAMES: Final[tuple[str, ...]] = (
    "superuser",
    "createrole",
    "createdb",
    "replication",
    "bypassrls",
)

#: How an attribute is SPELLED to an operator, so a refusal reads like the
#: `ALTER ROLE` that repairs it.
ATTRIBUTE_SPELLING: Final[dict[str, str]] = {
    "superuser": "SUPERUSER",
    "createrole": "CREATEROLE",
    "createdb": "CREATEDB",
    "replication": "REPLICATION",
    "bypassrls": "BYPASSRLS",
}

#: Predefined roles whose membership PostgreSQL documents as reaching server
#: files or executing server-side programs. None of them holds SUPERUSER,
#: CREATEROLE or BYPASSRLS, which is exactly why the old scanner never saw
#: them.
SERVER_FILE_OR_PROGRAM_ROLES: Final[frozenset[str]] = frozenset(
    {
        "pg_read_server_files",
        "pg_write_server_files",
        "pg_execute_server_program",
    }
)

#: The roles a LONG-RUNNING ERP process connects as. Stated once, here, and
#: consumed by the policy below — a role added to this set is covered by the
#: scanner, the evaluator and the integration proofs the day it joins, with no
#: other edit.
RUNTIME_AUTHORITY_SUBJECTS: Final[frozenset[str]] = frozenset(
    {
        "app_user",
        "platform_api",
        "outbox_dispatcher",
        "platform_outbox_dispatcher",
    }
)


@dataclass(frozen=True, slots=True)
class AuthenticationExpectationV1:
    """What `system_user` must report, bound late by the operator.

    NOT a secret and never a credential: an authentication METHOD name and a
    role NAME. It is late-bound because the method is a property of the
    cluster's `pg_hba.conf`, which this repository neither owns nor can read.
    """

    method: str
    identity: str

    def as_system_user(self) -> str:
        return f"{self.method}:{self.identity}"


@dataclass(frozen=True, slots=True)
class RoleAttributesV1:
    """A role's OWN attributes. Not inherited, not reachable — its own."""

    superuser: bool
    createrole: bool
    createdb: bool
    replication: bool
    bypassrls: bool

    def held(self, attribute: str) -> bool:
        return bool(getattr(self, attribute))

    def spelled(self) -> str:
        return " ".join(
            spelling if self.held(attribute) else f"NO{spelling}"
            for attribute, spelling in ATTRIBUTE_SPELLING.items()
        )


@dataclass(frozen=True, slots=True)
class MembershipEdgeV1:
    """One (subject, reachable target) pair, flattened by the scanner.

    `direct` distinguishes `GRANT target TO subject` from a chain. It changes
    no verdict — both are refused — and exists so a refusal tells an operator
    whether there is a single grant to revoke or a chain to unpick.
    """

    subject: str
    target: str
    direct: bool
    target_attributes: RoleAttributesV1


@dataclass(frozen=True, slots=True)
class RoleAuthorityObservationV1:
    """One reading of the catalogue, shared by BOTH policies.

    Facts only. Nothing here knows what is permitted; the policies do. That is
    what lets one query answer two different questions, and what lets a third
    policy be added later without a second scanner.
    """

    session_user: str
    current_user: str
    #: `NULL` under `trust` authentication, and PostgreSQL returns exactly that
    #: — so `None` here means "the server did not authenticate this connection
    #: in a way it can name", never "not observed".
    system_user: str | None
    subject_attributes: Mapping[str, RoleAttributesV1]
    membership_edges: tuple[MembershipEdgeV1, ...]


@dataclass(frozen=True, slots=True)
class AuthorityViolationV1:
    """A closed-set code plus the operator-facing sentence.

    The message names the SUBJECT, the TARGET and the AUTHORITY CLASS. It never
    names a credential, a password, a DSN or a host: a refusal is read out of
    CI logs and deploy transcripts.
    """

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class AuthorityPolicyV1:
    """What one authority class is permitted to be, and to reach."""

    #: The words a refusal uses for this class, e.g. "runtime role".
    authority_class: str
    subjects: frozenset[str]
    #: attribute name -> the value the subject's OWN attribute must have.
    required_direct_attributes: Mapping[str, bool]
    #: Attributes that make a REACHABLE role a forbidden target.
    forbidden_target_attributes: frozenset[str]
    #: Roles that are forbidden targets by NAME, whatever attributes they hold.
    forbidden_target_roles: frozenset[str]
    direct_attribute_code: str
    membership_code: str
    #: The role this connection must have AUTHENTICATED as, or `None` when the
    #: policy makes no claim about who is connected (every runtime-role
    #: evaluation, and the elevated bootstrap's own post-repair check).
    authenticated_subject: str | None = None
    #: The late-bound `system_user` expectation, or `None` when nothing bound
    #: one. `None` is reported by the caller as UNVERIFIED, never as a pass.
    authentication: AuthenticationExpectationV1 | None = None

    def binding_authentication(
        self, expectation: AuthenticationExpectationV1 | None
    ) -> AuthorityPolicyV1:
        """The same policy with an operator's `system_user` expectation bound."""
        return replace(self, authentication=expectation)

    def without_direct_authentication(self) -> AuthorityPolicyV1:
        """The same policy, asking nothing about WHO is connected.

        The elevated bootstrap repairs the role graph with its own privileged
        identity and then re-reads it. It may inspect; it may not claim to be
        `app_admin`, because it is not. Dropping the authentication clause here
        is what keeps that honest — the direct-authentication proof comes only
        from a fresh `app_admin` connection in `--verify-only` and in Alembic.
        """
        return replace(self, authenticated_subject=None, authentication=None)


#: A runtime role may hold none of the three privileged attributes, and may
#: reach nothing that holds one.
RuntimeRoleAuthorityPolicyV1: Final[AuthorityPolicyV1] = AuthorityPolicyV1(
    authority_class="runtime role",
    subjects=RUNTIME_AUTHORITY_SUBJECTS,
    required_direct_attributes={
        "superuser": False,
        "createrole": False,
        "bypassrls": False,
    },
    forbidden_target_attributes=frozenset({"superuser", "createrole", "bypassrls"}),
    forbidden_target_roles=SERVER_FILE_OR_PROGRAM_ROLES,
    direct_attribute_code=RUNTIME_ROLE_DIRECT_ATTRIBUTE_FORBIDDEN,
    membership_code=RUNTIME_ROLE_PRIVILEGED_MEMBERSHIP,
)

#: `app_admin` MUST hold BYPASSRLS — it owns DDL and RLS must not hide objects
#: from the thing that alters them — and must hold nothing else. BYPASSRLS is
#: absent from the forbidden closure precisely because the subject legitimately
#: holds it: refusing a membership that reaches only another BYPASSRLS role
#: would refuse a correctly bootstrapped cluster.
MigrationExecutorAuthorityPolicyV1: Final[AuthorityPolicyV1] = AuthorityPolicyV1(
    authority_class="migration executor",
    subjects=frozenset({MIGRATION_EXECUTOR}),
    required_direct_attributes={
        "bypassrls": True,
        "superuser": False,
        "createrole": False,
        "createdb": False,
        "replication": False,
    },
    forbidden_target_attributes=frozenset({"superuser", "createrole"}),
    forbidden_target_roles=SERVER_FILE_OR_PROGRAM_ROLES,
    direct_attribute_code=MIGRATION_EXECUTOR_DIRECT_ATTRIBUTE_FORBIDDEN,
    membership_code=MIGRATION_EXECUTOR_PRIVILEGED_MEMBERSHIP,
    authenticated_subject=MIGRATION_EXECUTOR,
)

#: Every subject either policy asks about. The scanner takes ONE reading for
#: both, so the two policies can never disagree about what the catalogue said.
AUTHORITY_SUBJECTS: Final[frozenset[str]] = (
    RuntimeRoleAuthorityPolicyV1.subjects | MigrationExecutorAuthorityPolicyV1.subjects
)

#: The operator-supplied `system_user` expectation, e.g.
#: `scram-sha-256:app_admin`. Non-secret, and late-bound because the
#: authentication method lives in the cluster's `pg_hba.conf`.
EXPECTED_AUTHENTICATION_VAR: Final[str] = "MIGRATION_EXPECTED_AUTHENTICATION"


# ---------------------------------------------------------------------------
# The scanner — one statement, one set of bytes, both callers.
# ---------------------------------------------------------------------------
#: psycopg pyformat (`%(subjects)s`) because the deploy preflight runs it
#: through psycopg directly. The Alembic environment must therefore use
#: `exec_driver_sql`, NOT `text()`: SQLAlchemy's `text()` applies its own
#: `:name` paramstyle and would leave `%(subjects)s` a literal.
#: `exec_driver_sql` hands the string to the DBAPI unchanged, so both callers
#: execute the same bytes rather than two copies that drift.
#:
#: `pg_roles` and `pg_auth_members` are world-readable. `pg_has_role()` is the
#: shorter spelling and is deliberately NOT used: PostgreSQL restricts which
#: roles a non-superuser may interrogate with it, and this runs on a
#: NOSUPERUSER `app_admin` connection. A check that answers "no edges" because
#: it was not allowed to look is worse than no check.
#:
#: The membership walk has NO `WHERE` on the target's attributes. That absence
#: is the fix: the old scanner discarded every edge into a role holding none of
#: SUPERUSER/CREATEROLE/BYPASSRLS, which is precisely the shape of
#: `pg_execute_server_program`.
ROLE_AUTHORITY_SQL: Final[str] = """
WITH RECURSIVE subject_role(rolname, oid) AS (
    SELECT catalog_role.rolname, catalog_role.oid
    FROM pg_roles AS catalog_role
    WHERE catalog_role.rolname = ANY(%(subjects)s)
),
reachable(subject, target_oid) AS (
    SELECT subject_role.rolname, membership.roleid
    FROM subject_role
    JOIN pg_auth_members AS membership
      ON membership.member = subject_role.oid

    UNION

    SELECT reachable.subject, membership.roleid
    FROM reachable
    JOIN pg_auth_members AS membership
      ON membership.member = reachable.target_oid
),
direct_edge(subject, target_oid) AS (
    SELECT subject_role.rolname, membership.roleid
    FROM subject_role
    JOIN pg_auth_members AS membership
      ON membership.member = subject_role.oid
)
SELECT 'session'::text AS observation_kind,
       session_user::text AS subject,
       current_user::text AS target,
       NULL::boolean AS is_direct,
       NULL::boolean AS rolsuper,
       NULL::boolean AS rolcreaterole,
       NULL::boolean AS rolcreatedb,
       NULL::boolean AS rolreplication,
       NULL::boolean AS rolbypassrls

UNION ALL

SELECT 'system_user'::text,
       SYSTEM_USER::text,
       NULL::text,
       NULL::boolean,
       NULL::boolean,
       NULL::boolean,
       NULL::boolean,
       NULL::boolean,
       NULL::boolean

UNION ALL

SELECT 'subject'::text,
       catalog_role.rolname::text,
       NULL::text,
       NULL::boolean,
       catalog_role.rolsuper,
       catalog_role.rolcreaterole,
       catalog_role.rolcreatedb,
       catalog_role.rolreplication,
       catalog_role.rolbypassrls
FROM pg_roles AS catalog_role
WHERE catalog_role.rolname = ANY(%(subjects)s)

UNION ALL

SELECT 'membership'::text,
       reachable.subject::text,
       target_role.rolname::text,
       EXISTS (
           SELECT 1
           FROM direct_edge
           WHERE direct_edge.subject = reachable.subject
             AND direct_edge.target_oid = reachable.target_oid
       ),
       target_role.rolsuper,
       target_role.rolcreaterole,
       target_role.rolcreatedb,
       target_role.rolreplication,
       target_role.rolbypassrls
FROM reachable
JOIN pg_roles AS target_role ON target_role.oid = reachable.target_oid

ORDER BY 1, 2, 3
"""

#: `observation_kind, subject, target, is_direct, rolsuper, rolcreaterole,
#: rolcreatedb, rolreplication, rolbypassrls`.
AuthorityScanRow = Sequence[object]


def observation_from_rows(
    rows: Sequence[AuthorityScanRow],
) -> RoleAuthorityObservationV1:
    """Build the shared observation from :data:`ROLE_AUTHORITY_SQL`'s rows.

    Shared by both callers so neither invents its own row handling. A missing
    `session` row is a programming error, not a cluster condition, and raises.
    """
    session_user: str | None = None
    current_user: str | None = None
    system_user: str | None = None
    subject_attributes: dict[str, RoleAttributesV1] = {}
    edges: list[MembershipEdgeV1] = []

    for row in rows:
        kind = str(row[0])
        if kind == "session":
            session_user = str(row[1])
            current_user = str(row[2])
        elif kind == "system_user":
            system_user = None if row[1] is None else str(row[1])
        elif kind == "subject":
            subject_attributes[str(row[1])] = _attributes(row)
        elif kind == "membership":
            edges.append(
                MembershipEdgeV1(
                    subject=str(row[1]),
                    target=str(row[2]),
                    direct=bool(row[3]),
                    target_attributes=_attributes(row),
                )
            )

    if session_user is None or current_user is None:
        raise RuntimeError(
            "the role authority scan returned no session row; the query and "
            "this reader have drifted apart"
        )
    return RoleAuthorityObservationV1(
        session_user=session_user,
        current_user=current_user,
        system_user=system_user,
        subject_attributes=subject_attributes,
        membership_edges=tuple(edges),
    )


def _attributes(row: AuthorityScanRow) -> RoleAttributesV1:
    return RoleAttributesV1(
        superuser=bool(row[4]),
        createrole=bool(row[5]),
        createdb=bool(row[6]),
        replication=bool(row[7]),
        bypassrls=bool(row[8]),
    )


# ---------------------------------------------------------------------------
# The evaluator — pure, total, and the only place a verdict is decided.
# ---------------------------------------------------------------------------
def role_authority_violations(
    policy: AuthorityPolicyV1,
    observation: RoleAuthorityObservationV1,
) -> tuple[AuthorityViolationV1, ...]:
    """Every way `observation` fails `policy`. No I/O, no globals, no clock.

    Ordered: authentication, then direct attributes, then membership. An
    operator reads the first line and knows whether the connection was even the
    right one before reading anything about the graph.

    A subject the observation does not mention is NOT reported here. Whether a
    contracted role EXISTS is `role_contract_violations`' question and it
    already answers it; reporting it twice would make one missing role look
    like two findings.
    """
    violations: list[AuthorityViolationV1] = []
    violations.extend(_authentication_violations(policy, observation))
    violations.extend(_direct_attribute_violations(policy, observation))
    violations.extend(_membership_violations(policy, observation))
    return tuple(violations)


def _authentication_violations(
    policy: AuthorityPolicyV1,
    observation: RoleAuthorityObservationV1,
) -> list[AuthorityViolationV1]:
    expected = policy.authenticated_subject
    if expected is None:
        return []

    violations: list[AuthorityViolationV1] = []
    if observation.session_user != observation.current_user:
        violations.append(
            AuthorityViolationV1(
                code=MIGRATION_AUTHENTICATION_IDENTITY_MISMATCH,
                message=(
                    f"{policy.authority_class}: the connection authenticated as "
                    f"{observation.session_user!r} and is executing as "
                    f"{observation.current_user!r}. A privileged session that "
                    f"runs SET ROLE {expected} satisfies every check that reads "
                    "current_user alone, so authority is taken from session_user "
                    "and current_user together"
                ),
            )
        )
    elif observation.current_user != expected:
        violations.append(
            AuthorityViolationV1(
                code=MIGRATION_AUTHENTICATION_IDENTITY_MISMATCH,
                message=(
                    f"{policy.authority_class}: the connection authenticated as "
                    f"{observation.current_user!r}, and only {expected!r} may "
                    "hold this authority"
                ),
            )
        )

    expectation = policy.authentication
    if expectation is None:
        return violations
    if observation.system_user is None:
        violations.append(
            AuthorityViolationV1(
                code=MIGRATION_AUTHENTICATION_IDENTITY_MISMATCH,
                message=(
                    f"{policy.authority_class}: the server reported no "
                    "system_user, which is what PostgreSQL returns under trust "
                    f"authentication. {expectation.as_system_user()!r} was "
                    "required, and a connection nobody authenticated cannot "
                    "prove it is the one that was authorised"
                ),
            )
        )
    elif observation.system_user != expectation.as_system_user():
        violations.append(
            AuthorityViolationV1(
                code=MIGRATION_AUTHENTICATION_IDENTITY_MISMATCH,
                message=(
                    f"{policy.authority_class}: the connection authenticated as "
                    f"{observation.system_user!r}, authorised for "
                    f"{expectation.as_system_user()!r}"
                ),
            )
        )
    return violations


def _direct_attribute_violations(
    policy: AuthorityPolicyV1,
    observation: RoleAuthorityObservationV1,
) -> list[AuthorityViolationV1]:
    violations: list[AuthorityViolationV1] = []
    for subject in sorted(policy.subjects):
        attributes = observation.subject_attributes.get(subject)
        if attributes is None:
            continue
        for name in ATTRIBUTE_NAMES:
            if name not in policy.required_direct_attributes:
                continue
            required = policy.required_direct_attributes[name]
            if attributes.held(name) == required:
                continue
            spelling = ATTRIBUTE_SPELLING[name]
            wanted = spelling if required else f"NO{spelling}"
            violations.append(
                AuthorityViolationV1(
                    code=policy.direct_attribute_code,
                    message=(
                        f"{policy.authority_class} {subject!r} is "
                        f"{attributes.spelled()}; this authority class requires "
                        f"{wanted}. Repair it with a separately authorised "
                        f"ALTER ROLE {subject} {wanted} — no migration may "
                        "grant itself authority it was refused"
                    ),
                )
            )
    return violations


def _membership_violations(
    policy: AuthorityPolicyV1,
    observation: RoleAuthorityObservationV1,
) -> list[AuthorityViolationV1]:
    violations: list[AuthorityViolationV1] = []
    for edge in sorted(
        observation.membership_edges, key=lambda item: (item.subject, item.target)
    ):
        if edge.subject not in policy.subjects:
            continue
        reasons = [
            ATTRIBUTE_SPELLING[name]
            for name in ATTRIBUTE_NAMES
            if name in policy.forbidden_target_attributes
            and edge.target_attributes.held(name)
        ]
        if edge.target in policy.forbidden_target_roles:
            reasons.append("SERVER FILE/PROGRAM ACCESS")
        if not reasons:
            continue
        reach = "is a member of" if edge.direct else "can reach"
        violations.append(
            AuthorityViolationV1(
                code=policy.membership_code,
                message=(
                    f"{policy.authority_class} {edge.subject!r} {reach} "
                    f"{edge.target!r} ({'/'.join(reasons)}). Prohibited "
                    "privileged membership closure: this is not a claim that "
                    "SET ROLE is presently executable — set_option is mutable "
                    "cluster state — but that the membership must not exist. "
                    f"Repair it with a separately authorised REVOKE "
                    f"{edge.target} FROM {edge.subject}"
                ),
            )
        )
    return violations


def unverified_authentication_notice(
    policy: AuthorityPolicyV1,
    variable: str,
) -> str | None:
    """The sentence a run must print when nothing bound an expected identity.

    Returned as text rather than raised, for the same reason as the database
    identity notice: an operator who has not yet adopted the binding must not
    be blocked by it, and must not be able to read a clean run as evidence of
    something it did not check. Under trust authentication `system_user` is
    NULL, so a trust-authenticated tier can never supply this proof — it can
    only print this line.
    """
    if policy.authenticated_subject is None or policy.authentication is not None:
        return None
    return (
        f"{policy.authority_class} authentication UNVERIFIED: session_user and "
        "current_user agree, and nothing said which authentication method and "
        f"identity were authorised. Set {variable} to "
        "'<method>:<role>' (for example 'scram-sha-256:app_admin') to make this "
        "an assertion instead of an observation."
    )


def parse_authentication_expectation(
    raw: str | None,
) -> AuthenticationExpectationV1 | None:
    """Read the late-bound, NON-SECRET `<method>:<identity>` operator input.

    A malformed value raises rather than degrading to "unbound": an operator
    who set the variable asked for an assertion, and silently turning a typo
    into no check is how a gate becomes decorative.
    """
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    method, separator, identity = value.partition(":")
    if not separator or not method.strip() or not identity.strip():
        raise RuntimeError(
            "the expected authentication must be '<method>:<identity>', for "
            "example 'scram-sha-256:app_admin'; PostgreSQL's system_user uses "
            "exactly that shape"
        )
    return AuthenticationExpectationV1(method=method.strip(), identity=identity.strip())


def violation_messages(
    violations: Sequence[AuthorityViolationV1],
) -> tuple[str, ...]:
    """Render for a caller that reports strings, keeping the code visible."""
    return tuple(f"[{item.code}] {item.message}" for item in violations)


__all__ = [
    "ATTRIBUTE_NAMES",
    "ATTRIBUTE_SPELLING",
    "AUTHORITY_FAILURE_CODES",
    "AUTHORITY_SUBJECTS",
    "AuthenticationExpectationV1",
    "AuthorityPolicyV1",
    "AuthorityScanRow",
    "AuthorityViolationV1",
    "EXPECTED_AUTHENTICATION_VAR",
    "MIGRATION_AUTHENTICATION_IDENTITY_MISMATCH",
    "MIGRATION_EXECUTOR_DIRECT_ATTRIBUTE_FORBIDDEN",
    "MIGRATION_EXECUTOR_PRIVILEGED_MEMBERSHIP",
    "MembershipEdgeV1",
    "MigrationExecutorAuthorityPolicyV1",
    "ROLE_AUTHORITY_SQL",
    "RUNTIME_AUTHORITY_SUBJECTS",
    "RUNTIME_ROLE_DIRECT_ATTRIBUTE_FORBIDDEN",
    "RUNTIME_ROLE_PRIVILEGED_MEMBERSHIP",
    "RoleAttributesV1",
    "RoleAuthorityObservationV1",
    "RuntimeRoleAuthorityPolicyV1",
    "SERVER_FILE_OR_PROGRAM_ROLES",
    "observation_from_rows",
    "parse_authentication_expectation",
    "role_authority_violations",
    "unverified_authentication_notice",
    "violation_messages",
]
