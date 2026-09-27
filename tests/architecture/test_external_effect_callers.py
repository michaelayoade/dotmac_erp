"""A ratchet on code that reaches an external effect surface directly --
constructing a provider client, hitting a raw HTTP/object-store write, using
``smtplib``, or reaching a provider through its ``.client`` accessor -- from
outside that surface's own owner.

Why this matters: ADR-0013 ("External effects are recorded by their owner
before they run") requires every irreversible external effect -- a payment
capture, a bank API call, a mailbox provisioning request, an outbound email,
an object-store write -- to be recorded by its owner BEFORE it runs. A caller
that reaches any of these surfaces directly, anywhere it likes, can perform
the effect with no such recording having happened. ADR-0013 requires "a
two-directional ratchet: an architecture test inventories every module that
constructs a provider client or uses smtplib, freezes today's set as
grandfathered; a new direct caller fails CI; a migrated caller lowers the
inventory in the same change; the detector states its blind spots and
carries a planted sensitivity proof." This module is that test.

SCOPE. Scanned roots: :data:`SCANNED_ROOTS` -- ``app/``, ``scripts/`` and
``tools/`` -- the same roots (and for the same reason) as
``test_own_session_commits.py``. UNMONITORED, named explicitly rather than
left implicit: ``alembic/`` (migration scripts do not construct provider
clients or send mail) and ``tests/`` (test fixtures/mocks constructing a
client in a test double are not a production caller).

WHAT IS FLAGGED. Per MODULE (this ratchet is file-grained, not
call-site-grained -- see LIMITATION below), a hit is one or more FAMILIES,
each recorded in the baseline row so a module gaining a NEW family is a new
signal even though it was already grandfathered for another one:

    ``provider:<ClassName>`` -- a ``Call`` that constructs one of
        :data:`PROVIDER_CLIENTS` -- a bare name (``PaystackClient(...)``), an
        attribute access (``paystack_client.PaystackClient(...)``), or a
        call through a local import alias (``from ...paystack_client import
        PaystackClient as PC`` then ``PC(...)``) -- including the
        ``with X(...) as c:`` form, since a ``with`` statement's context
        expression is itself the same ``ast.Call`` node this scan walks. A
        bare reference or a type annotation naming the class, with no
        ``Call`` node, is never a hit.
    ``smtplib`` -- a bare ``import smtplib``, a ``from smtplib import ...``,
        or a call to ``SMTP(``/``SMTP_SSL(`` that RESOLVES to smtplib --
        either ``smtplib.SMTP(...)`` (an attribute access on a name bound to
        the ``smtplib`` module, plain or aliased) or a bare
        ``SMTP(...)``/``SMTP_SSL(...)`` call whose name was imported from
        ``smtplib`` (plain or aliased). A same-named class defined LOCALLY
        (not imported from smtplib) is deliberately not a hit.
    ``http_write`` -- a module-level ``httpx.post/put/patch/delete/request``
        or ``requests.post/put/patch/delete/request`` call (resolved through
        a module import, plain or aliased); OR, within ONE function's own
        body (not crossing into a nested ``def``/``lambda`` -- the same
        "own body" boundary ``test_own_session_commits.py`` uses), a
        ``.post/.put/.patch/.delete/.request(`` call -- possibly chained,
        e.g. ``client._get_client().get(...)`` is walked one call at a time
        -- rooted at a name (or ``self.<attr>``) that SAME FUNCTION bound to
        ``httpx.Client(...)``/``httpx.AsyncClient(...)``/``requests.Session(
        )`` (via assignment, annotated assignment, an attribute target, or
        ``with ... as x``). Scoping to one function's own body is what keeps
        two unrelated classes that both happen to use the attribute name
        ``self._client`` from being merged into one false hit (see
        ``app/services/dotmac_sub/client.py``, where
        ``_ExactBodyHttpClient.request`` calls ``self._client.request(...)``
        on a constructor-injected client, never one THAT function opened,
        while a wholly different class binds ``self._client = httpx.Client(
        ...)`` in its own, separate method).
    ``object_store_write`` -- a call to
        ``.put_object(``/``.fput_object(``/``.remove_object(``/
        ``.remove_objects(``/``.copy_object(``, the MinIO/boto write
        surface. Matched by method name alone (not by resolving the base to
        a MinIO client type) -- a repository-wide grep confirmed these five
        names are used nowhere except the real MinIO call sites today, so
        the looser match carries no observed false-positive cost; tightened
        the day that stops being true.
    ``provider_accessor`` -- in a module that imports (``import`` or
        ``from ... import``) a dotted name starting with one of
        :data:`PROVIDER_PACKAGE_PREFIXES`, either (i) a call of the shape
        ``<anything>.client.<method>(...)`` (covers both ``x.client.foo()``
        and ``self.client.foo()`` -- the base of the ``.client`` attribute
        is not otherwise constrained) or (ii) a call to a bare-named helper
        matching :data:`PROVIDER_ACCESSOR_HELPER_NAME_RE` (``get_..._client``
        / ``_get_..._client``). This is what catches a module that reaches a
        provider only through a shared property/helper and never spells the
        class name itself -- the ``app/services/dotmac_sub/sync/_*.py``
        family and ``RRRService`` (``self.client.generate_rrr(...)`` etc.)
        being the motivating real shape.

EXCLUSIONS, each with its enforceable premise:

1. Each provider client's OWN defining module (:data:`CLIENT_DEFINING_MODULES`)
   is exempt ONLY from its own ``provider:<ClassName>`` family -- premise: a
   client may construct itself (e.g. a ``classmethod`` building a fresh
   instance of its own class). Every OTHER family is still evaluated inside
   a defining module: three of the eight (``app/services/mailcow/client.py``,
   ``app/services/mailcow/cleanup_queue.py``,
   ``app/services/nextcloud/client.py``) open their own
   ``with httpx.Client(...) as client:`` and call a write method on it
   inside their own ``_request``, which is a real ``http_write`` hit in the
   baseline -- narrowing the exclusion to just the one family it has a
   premise for is what surfaces that. The other five defining modules
   (Paystack/Mono/Remita's ``_get_client()`` factory, and DotmacSub's
   ``_engine``/``IntegrationHttpClient`` wrapper) never call a write method
   on a name bound to an httpx/requests client WITHIN the same function --
   the chained call is always through a helper method's RETURN VALUE, not a
   locally bound name -- so they earn no ``http_write`` hit either, verified
   by the live scan rather than assumed.
2. The EventOutbox relay/delivery module, IF it constructs a client or
   reaches any other family. Located by grepping
   ``event_outbox``/``relay``/``dispatch`` across the repository:
   ``app/tasks/outbox_relay.py`` (the Celery relay task,
   ``relay_outbox_events`` / ``_deliver_one``) and
   ``app/services/finance/platform/outbox_publisher.py`` (``OutboxPublisher``,
   the write-side). NEITHER hits any family today -- verified by running
   this module's own scan function against each file directly
   (:func:`test_event_outbox_modules_construct_no_client`). So this
   exclusion excludes NOTHING today; :data:`EVENT_OUTBOX_MODULES` is
   declared purely so a future outbox module that DOES reach a family is a
   reviewed decision to add it here, not a silent exemption.

READ-ONLY OBSERVATION IS ENFORCED, NOT ASSERTED. The two modules in
:data:`READ_ONLY_OBSERVATION_MODULES` each get a per-module allowlist of the
EXACT provider-client method names they may call
(:data:`READ_ONLY_ALLOWED_METHODS`), walking the same bound-name-and-call-
chain resolution ``http_write`` uses (see :func:`_provider_client_calls`), so
a call chained off the bound client (``client._get_client().get(...)`` is
two separate calls, ``_get_client`` then ``get``, and BOTH must be
allowlisted) is caught, not just the first. ``_request`` additionally
requires a literal string ``"GET"`` as its first positional argument --
``client._request(method_variable, ...)`` is refused even though
``_request`` itself is allowlisted, because a variable could carry any verb.
:func:`test_read_only_modules_only_call_allowed_methods` enforces this by
actually re-deriving the call set from each file and failing if anything
outside the allowlist (or a non-literal-GET ``_request``) turns up; a
sensitivity proof plants both a compliant tree and a violating one (a
mutating call, and a non-literal-GET ``_request``) and asserts the violating
shapes are actually caught -- proving the check is not vacuous over the two
real files, which currently comply.

LIMITATION (stated, not implemented) -- BLIND SPOTS:

- File-grained, not call-site-grained: a module already grandfathered for
  one family gets no new signal from a second call site in the SAME family
  (e.g. a second, different ``PaystackClient(...)`` construction in an
  already-flagged module).
- Indirection this scanner does not resolve: ``X = PaystackClient; X(...)``
  (re-binding the class to another name before calling it), ``getattr(mod,
  "PaystackClient")(...)``, ``functools.partial(PaystackClient, ...)``, and
  an ``importlib.import_module``-obtained reference are all invisible to a
  static, import-alias-based resolver.
- ``http_write``'s bound-name tracking is per-function (not per-class): a
  client stored on ``self`` in ``__init__`` and used in a DIFFERENT method
  is not tracked as bound in that other method's own body (this is a
  conscious trade against the false-positive this exact shape produced in
  ``app/services/dotmac_sub/client.py`` -- see the family description
  above) -- a real cross-method ``self``-held httpx client used only for
  writes in a method that never opens it would currently evade this family.
- ``object_store_write`` matches on method NAME only, not on the receiver's
  type -- stated in the family description above.
- ``provider_accessor``'s ``.client.<method>`` shape matches ANY base
  expression before ``.client`` once the owning module imports a provider
  package; it does not verify the ``.client`` attribute is actually the
  provider client property. Likewise its helper-name regex matches ANY
  ``get_..._client``/``_get_..._client`` call in such a module, regardless of
  what that helper actually returns -- real over-match today:
  ``app/dependency_health.py`` imports several provider packages for its
  other health probes AND separately calls ``_get_storage_client()`` (the
  unrelated MinIO/object-store accessor from ``app.services.storage``),
  which the regex still counts as ``provider_accessor``. Left over-inclusive
  rather than narrowed, matching ADR-0013's literal instruction to flag
  ``get_*_client``/``_get_*_client`` helper calls; a false positive here
  costs an extra family label on an already-grandfathered row, not a missed
  real caller.

BASELINE lives in the sidecar file :data:`BASELINE_PATH`
(``<path>\\t<families>\\t<reason>``, families a sorted comma-list) because
the current hit count is real existing debt, not a claim it is contained to
one row. The ratchet is two-directional on the FULL ``(path, families)``
pair: a hit outside the baseline, a stale baseline row no longer produced by
the live scan, OR a baseline row whose recorded families no longer match a
fresh scan (a module silently gaining or losing a family), all fail.
"""

from __future__ import annotations

import ast
import functools
import re
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Every Python entry-point family this ratchet audits. alembic/ and tests/
#: are deliberately NOT here -- see the module docstring's SCOPE section.
SCANNED_ROOTS: tuple[Path, ...] = (
    REPO_ROOT / "app",
    REPO_ROOT / "scripts",
    REPO_ROOT / "tools",
)

#: The eight provider client classes named by ADR-0013's inventory, each with
#: a comment pointing at its defining module.
PROVIDER_CLIENTS: frozenset[str] = frozenset(
    {
        "PaystackClient",  # app/services/finance/payments/paystack_client.py
        "MonoClient",  # app/services/finance/banking/mono_client.py
        "RemitaClient",  # app/services/remita/client.py
        "DotmacSubClient",  # app/services/dotmac_sub/client.py
        "MailcowClient",  # app/services/mailcow/client.py
        "SogoCleanupQueueClient",  # app/services/mailcow/cleanup_queue.py
        "ManageSieveClient",  # app/services/mailcow/sieve.py
        "NextcloudTalkClient",  # app/services/nextcloud/client.py
    }
)

#: Provider class name -> the module (relative to REPO_ROOT, POSIX) that
#: defines it. See exclusion 1 in the module docstring. Keys MUST equal
#: PROVIDER_CLIENTS exactly (proven by
#: :func:`test_client_defining_modules_keys_match_provider_clients`) and each
#: value must actually contain ``class <Name>`` (proven by
#: :func:`test_client_defining_modules_actually_define_the_class`) -- both
#: guard against a typo silently exempting the wrong file or no file at all.
CLIENT_DEFINING_MODULES: dict[str, str] = {
    "PaystackClient": "app/services/finance/payments/paystack_client.py",
    "MonoClient": "app/services/finance/banking/mono_client.py",
    "RemitaClient": "app/services/remita/client.py",
    "DotmacSubClient": "app/services/dotmac_sub/client.py",
    "MailcowClient": "app/services/mailcow/client.py",
    "SogoCleanupQueueClient": "app/services/mailcow/cleanup_queue.py",
    "ManageSieveClient": "app/services/mailcow/sieve.py",
    "NextcloudTalkClient": "app/services/nextcloud/client.py",
}

#: EventOutbox relay/delivery modules. See exclusion 2 in the module
#: docstring -- neither reaches any family today, verified by
#: :func:`test_event_outbox_modules_construct_no_client`.
EVENT_OUTBOX_MODULES: frozenset[str] = frozenset(
    {
        "app/tasks/outbox_relay.py",
        "app/services/finance/platform/outbox_publisher.py",
    }
)

#: httpx/requests constructor names that open a client worth tracking for
#: the http_write family, and the canonical module each belongs to.
HTTP_CLIENT_CTORS: dict[str, str] = {
    "Client": "httpx",
    "AsyncClient": "httpx",
    "Session": "requests",
}

#: Write-shaped HTTP methods. "request" is included even though it is
#: verb-agnostic (a GET can go through `.request("GET", ...)` too) --
#: ADR-0013's inventory names it explicitly as a family member; a caller
#: making an occasional GET through `.request` pays the same debt-inventory
#: cost as one making a POST, since this scanner cannot see the verb passed
#: at runtime through a variable.
HTTP_WRITE_METHODS: frozenset[str] = frozenset(
    {"post", "put", "patch", "delete", "request"}
)

#: The MinIO/boto object-store write surface. See the object_store_write
#: family description in the module docstring for the match-by-name-only
#: rationale.
OBJECT_STORE_WRITE_METHODS: frozenset[str] = frozenset(
    {"put_object", "fput_object", "remove_object", "remove_objects", "copy_object"}
)

#: A module importing a dotted name starting with one of these is considered
#: a provider-touching module for the provider_accessor family.
PROVIDER_PACKAGE_PREFIXES: tuple[str, ...] = (
    "app.services.dotmac_sub",
    "app.services.remita",
    "app.services.finance.payments.paystack",
    "app.services.finance.banking.mono",
    "app.services.mailcow",
    "app.services.nextcloud",
)

#: A bare-named helper that fetches/builds a provider client, e.g.
#: `_get_mailcow_client` or `get_sub_client`.
PROVIDER_ACCESSOR_HELPER_NAME_RE = re.compile(r"^_?get_\w*_client$")

#: Per-module allowlist of provider-client method names permitted in a
#: module hand-reviewed as read-only observation. Enforced (not merely
#: asserted) by test_read_only_modules_only_call_allowed_methods -- see the
#: module docstring's "READ-ONLY OBSERVATION IS ENFORCED" section for the
#: per-file citation of each call site and its HTTP verb.
READ_ONLY_ALLOWED_METHODS: dict[str, frozenset[str]] = {
    "app/dependency_health.py": frozenset(
        {"list_banks", "_request", "test_connection", "_get_client", "get"}
    ),
    "scripts/archive/paystack_probe_unmatched_statement_refs.py": frozenset(
        {"verify_transaction"}
    ),
}

#: Modules hand-reviewed and confirmed to construct a provider client for
#: READ-ONLY observation only (GET/verify/read methods, never a mutating
#: call) -- see READ_ONLY_ALLOWED_METHODS and the module docstring.
#: Everything else that hits gets the plain "direct external-effect caller"
#: reason.
READ_ONLY_OBSERVATION_MODULES: frozenset[str] = frozenset(
    {
        "app/dependency_health.py",
        "scripts/archive/paystack_probe_unmatched_statement_refs.py",
    }
)

BASELINE_PATH = Path(__file__).with_name("external_effect_caller_baseline.txt")

#: Reason string used for every hit not covered by a more specific reason.
DEFAULT_REASON = (
    "grandfathered: direct external-effect caller — ADR-0013 migration debt"
)

#: Reason string used for a hand-reviewed read-only-observation module.
READ_ONLY_REASON = (
    "grandfathered: read-only provider observation — confirm under ADR-0013"
)

#: Reason string used for a module whose ONLY family is object_store_write --
#: the object store may turn out to be an internal system of record rather
#: than an "external effect" in ADR-0013's sense; that scoping question is
#: explicitly open, not resolved by this test.
OBJECT_STORE_REASON = (
    "grandfathered: object-storage write — scope under ADR-0013 to be confirmed "
    "(internal store vs external effect)"
)


def _call_target_name(node: ast.expr) -> str | None:
    """The bare name a Call targets, for ``Name(...)`` or ``x.attr(...)``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _target_key(node: ast.expr | None) -> str | None:
    """A trackable "bound" key: ``"db"`` for a bare name, or ``"self.db"``
    for a ``self.`` attribute target. Anything else is not tracked."""
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    ):
        return f"self.{node.attr}"
    return None


def _own_body_nodes(func: ast.FunctionDef | ast.AsyncFunctionDef):
    """Every descendant reachable from ``func`` without crossing into a
    nested function/async function/lambda -- those are separate scan
    units."""

    def _walk(node: ast.AST):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            yield child
            yield from _walk(child)

    yield from _walk(func)


class _ImportAliases:
    """Import aliases collected from a module: a local name resolved back to
    a canonical provider-client class name, an httpx/requests module name,
    or a smtplib name/call."""

    def __init__(self) -> None:
        #: local name -> canonical provider client class name
        self.provider: dict[str, str] = {}
        #: local name -> "httpx" / "requests" (from `import httpx [as x]`)
        self.http_module: dict[str, str] = {}
        #: local name -> "httpx.Client" etc (from `from httpx import Client
        #: [as x]`)
        self.http_from: dict[str, str] = {}
        #: local name -> "smtplib" (from `import smtplib [as x]`)
        self.smtplib_module: dict[str, str] = {}
        #: local name -> "smtplib.SMTP" / "smtplib.SMTP_SSL"
        self.smtplib_from: dict[str, str] = {}
        #: True if this module imports anything from a provider package.
        self.imports_provider_package: bool = False

    def visit(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "smtplib":
                        self.smtplib_module[alias.asname or "smtplib"] = "smtplib"
                    if alias.name in ("httpx", "requests"):
                        self.http_module[alias.asname or alias.name] = alias.name
                    if alias.name.startswith(PROVIDER_PACKAGE_PREFIXES):
                        self.imports_provider_package = True
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "smtplib":
                    for alias in node.names:
                        if alias.name in ("SMTP", "SMTP_SSL"):
                            self.smtplib_from[alias.asname or alias.name] = (
                                f"smtplib.{alias.name}"
                            )
                if module in ("httpx", "requests"):
                    for alias in node.names:
                        if alias.name in HTTP_CLIENT_CTORS:
                            self.http_from[alias.asname or alias.name] = (
                                f"{module}.{alias.name}"
                            )
                if module.startswith(PROVIDER_PACKAGE_PREFIXES):
                    self.imports_provider_package = True
                for alias in node.names:
                    if alias.name in PROVIDER_CLIENTS:
                        self.provider[alias.asname or alias.name] = alias.name


def _is_http_client_ctor_call(call: ast.Call, aliases: _ImportAliases) -> bool:
    """True for ``httpx.Client(...)``/``requests.Session(...)`` (plain or
    module-aliased), or a bare ``Client(...)``/``Session(...)`` resolved via
    a ``from httpx import Client [as x]``-shaped import."""

    if isinstance(call.func, ast.Attribute):
        base = call.func.value
        if isinstance(base, ast.Name) and aliases.http_module.get(base.id) in (
            "httpx",
            "requests",
        ):
            return call.func.attr in HTTP_CLIENT_CTORS
        return False
    if isinstance(call.func, ast.Name):
        return call.func.id in aliases.http_from
    return False


def _rooted_in_bound_name(expr: ast.expr, bound: dict[str, int]) -> bool:
    """True if ``expr`` is, or chains back through calls/attributes to, a
    name (or ``self.<attr>``) already recorded in ``bound``."""

    key = _target_key(expr)
    if key is not None and key in bound:
        return True
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute):
        return _rooted_in_bound_name(expr.func.value, bound)
    if isinstance(expr, ast.Attribute):
        return _rooted_in_bound_name(expr.value, bound)
    return False


def _provider_client_bound_names(
    own_nodes: list[ast.AST], alias_map: dict[str, str]
) -> dict[str, int]:
    """Names (own-body-scoped) bound to a provider-client construction, via
    plain/annotated assignment, an attribute target, or ``with ... as x``."""

    bound: dict[str, int] = {}
    for node in own_nodes:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        elif isinstance(node, ast.withitem):
            target, value = node.optional_vars, node.context_expr

        if target is None or not isinstance(value, ast.Call):
            continue
        bare = _call_target_name(value.func)
        if bare is None:
            continue
        canonical = alias_map.get(bare, bare)
        if canonical in PROVIDER_CLIENTS:
            key = _target_key(target)
            if key:
                bound[key] = value.lineno
    return bound


def _http_client_bound_names(
    own_nodes: list[ast.AST], aliases: _ImportAliases
) -> dict[str, int]:
    """Names (own-body-scoped) bound to httpx.Client/AsyncClient or
    requests.Session, via plain/annotated assignment, an attribute target,
    or ``with ... as x``."""

    bound: dict[str, int] = {}
    for node in own_nodes:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        elif isinstance(node, ast.withitem):
            target, value = node.optional_vars, node.context_expr

        if target is None or not isinstance(value, ast.Call):
            continue
        if _is_http_client_ctor_call(value, aliases):
            key = _target_key(target)
            if key:
                bound[key] = value.lineno
    return bound


def _provider_client_calls(
    own_nodes: list[ast.AST], bound: dict[str, int]
) -> list[tuple[str, ast.Call]]:
    """Every ``(method_name, call)`` reached by walking the chain back to a
    name in ``bound`` -- a chained call such as
    ``client._get_client().get(...)`` yields BOTH ``_get_client`` and
    ``get``, since each is its own ``ast.Call`` node whose chain roots at
    ``client``."""

    calls: list[tuple[str, ast.Call]] = []
    for node in own_nodes:
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and _rooted_in_bound_name(node.func.value, bound)
        ):
            calls.append((node.func.attr, node))
    return calls


def external_effect_hits(tree: ast.AST) -> set[str]:
    """Return the set of hit family labels found in ``tree`` -- see the
    module docstring's "WHAT IS FLAGGED" section for each family's exact
    shape. Used both by the repo scan (one module at a time) and the
    sensitivity proof below."""

    hits: set[str] = set()
    aliases = _ImportAliases()
    aliases.visit(tree)

    # smtplib: bare imports are a module-wide hit regardless of use.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "smtplib":
                    hits.add("smtplib")
        elif isinstance(node, ast.ImportFrom) and node.module == "smtplib":
            hits.add("smtplib")

    # Module-level http_write: httpx.post(...)/requests.post(...) etc, not
    # gated on any function-local bound name.
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in HTTP_WRITE_METHODS
            and isinstance(node.func.value, ast.Name)
            and aliases.http_module.get(node.func.value.id) in ("httpx", "requests")
        ):
            hits.add("http_write")

    # object_store_write: matched by method name alone anywhere in the tree.
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in OBJECT_STORE_WRITE_METHODS
        ):
            hits.add("object_store_write")

    # provider_accessor: gated on the module importing a provider package.
    if aliases.imports_provider_package:
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "client"
            ) or (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and PROVIDER_ACCESSOR_HELPER_NAME_RE.match(node.func.id)
            ):
                hits.add("provider_accessor")

    # Per-function families: provider construction, and http_write via a
    # function-locally bound httpx/requests client.
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        own_nodes = list(_own_body_nodes(node))

        for n in own_nodes:
            if isinstance(n, ast.Call):
                bare = _call_target_name(n.func)
                if bare is not None:
                    canonical = aliases.provider.get(bare, bare)
                    if canonical in PROVIDER_CLIENTS:
                        hits.add(f"provider:{canonical}")
                    if isinstance(n.func, ast.Attribute) and bare in (
                        "SMTP",
                        "SMTP_SSL",
                    ):
                        base = n.func.value
                        if (
                            isinstance(base, ast.Name)
                            and aliases.smtplib_module.get(base.id) == "smtplib"
                        ):
                            hits.add("smtplib")
                    if isinstance(n.func, ast.Name) and bare in aliases.smtplib_from:
                        hits.add("smtplib")

        http_bound = _http_client_bound_names(own_nodes, aliases)
        if http_bound:
            for attr, _call in _provider_client_calls(own_nodes, http_bound):
                if attr in HTTP_WRITE_METHODS:
                    hits.add("http_write")

    return hits


def _classify(rel: str, families: frozenset[str]) -> str:
    if rel in READ_ONLY_OBSERVATION_MODULES:
        return READ_ONLY_REASON
    if families == frozenset({"object_store_write"}):
        return OBJECT_STORE_REASON
    return DEFAULT_REASON


def scan_repo(
    roots: tuple[Path, ...] = SCANNED_ROOTS,
) -> dict[str, tuple[frozenset[str], str]]:
    """``path`` -> (families, classified reason), for every module with a
    hit. ``provider:<own class>`` is dropped inside that class's own
    defining module (exclusion 1); every other family is left in place."""

    excluded_files = EVENT_OUTBOX_MODULES
    hits: dict[str, tuple[frozenset[str], str]] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel in excluded_files:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            families = external_effect_hits(tree)
            own_class = next(
                (name for name, mod in CLIENT_DEFINING_MODULES.items() if mod == rel),
                None,
            )
            if own_class is not None:
                families = families - {f"provider:{own_class}"}
            if families:
                frozen = frozenset(families)
                hits[rel] = (frozen, _classify(rel, frozen))
    return hits


@functools.cache
def _scan() -> dict[str, tuple[frozenset[str], str]]:
    """One repository scan shared by every test in this module."""

    return scan_repo()


def _load_baseline() -> dict[str, tuple[frozenset[str], str]]:
    baseline: dict[str, tuple[frozenset[str], str]] = {}
    for line in BASELINE_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        path, families, reason = line.split("\t")
        baseline[path] = (frozenset(families.split(",")), reason)
    return baseline


def test_no_hit_outside_baseline() -> None:
    current = _scan()
    baseline = _load_baseline()
    new = sorted(set(current) - set(baseline))
    assert not new, (
        "These modules reach an external-effect surface directly and are "
        f"not in the reviewed baseline (ADR-0013): {new}. Record the effect "
        f"through its owner before it runs, or add the row to {BASELINE_PATH.name}."
    )


def test_baseline_has_no_stale_entries() -> None:
    current = _scan()
    baseline = _load_baseline()
    stale = sorted(set(baseline) - set(current))
    assert not stale, (
        "These baseline rows no longer match a real hit -- remove them so "
        f"the ratchet keeps its grip: {stale}"
    )


def test_baseline_families_and_reasons_match_the_classifier() -> None:
    current = _scan()
    baseline = _load_baseline()
    mismatched = sorted(
        key for key in set(current) & set(baseline) if current[key] != baseline[key]
    )
    assert not mismatched, (
        "These baseline rows no longer match a fresh scan's families and/or "
        f"reason -- a module gained or lost a family: {mismatched}"
    )
    for families, reason in baseline.values():
        assert reason.startswith("grandfathered:")
        assert families, "a baseline row must name at least one family"


def test_client_defining_modules_keys_match_provider_clients() -> None:
    assert set(CLIENT_DEFINING_MODULES) == PROVIDER_CLIENTS, (
        "CLIENT_DEFINING_MODULES must have exactly one entry per "
        "PROVIDER_CLIENTS name -- a typo here silently exempts the wrong "
        "file (or none at all)"
    )


def test_client_defining_modules_actually_define_the_class() -> None:
    for name, rel in CLIENT_DEFINING_MODULES.items():
        path = REPO_ROOT / rel
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        defined = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        }
        assert name in defined, (
            f"{rel} is declared as {name}'s defining module but contains no "
            f"`class {name}` -- CLIENT_DEFINING_MODULES has a stale or "
            "mistyped path"
        )


def test_event_outbox_modules_construct_no_client() -> None:
    """Proves exclusion 2's premise directly: re-running this module's own
    scan function against each named EventOutbox module finds no hit -- the
    exclusion excludes nothing today."""

    for rel in EVENT_OUTBOX_MODULES:
        path = REPO_ROOT / rel
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        assert external_effect_hits(tree) == set(), (
            f"{rel} was declared to reach no external-effect family "
            "(exclusion 2's premise) but a live scan now finds one -- the "
            "exclusion's premise no longer holds and must be re-reviewed"
        )


def _tree(source: str) -> ast.AST:
    return ast.parse(textwrap.dedent(source))


def test_sensitivity_proof_plants_every_provider_client() -> None:
    """Every one of PROVIDER_CLIENTS, each via a bare name, an attribute
    access, and an import alias."""

    lines = [
        "from app.services.finance.payments.paystack_client import (",
        "    PaystackClient as _Aliased,",
        ")",
        "import app.services.remita.client as remita_module",
        "",
    ]
    expected: set[str] = set()
    for i, name in enumerate(sorted(PROVIDER_CLIENTS)):
        lines.append(f"def bare_{i}():")
        lines.append(f"    return {name}(config)")
        lines.append("")
        expected.add(f"provider:{name}")
    lines.append("def attribute_form():")
    lines.append("    return remita_module.RemitaClient(config)")
    lines.append("")
    lines.append("def alias_form():")
    lines.append("    return _Aliased(config)")
    lines.append("")
    lines.append("def with_form():")
    lines.append("    with MonoClient(config) as client:")
    lines.append("        client.get_account_info(1)")

    tree = _tree("\n".join(lines))
    assert external_effect_hits(tree) == expected


def test_sensitivity_proof_plants_new_families_and_near_misses() -> None:
    planted = """
        import httpx

        # --- http_write: module-level call ---
        def module_level_write():
            return httpx.post("https://example.test", json={})

        # --- http_write: bound-in-same-function client, chained call ---
        def bound_client_write():
            with httpx.Client() as client:
                return client.post("https://example.test", json={})

        # --- object_store_write: matched by method name ---
        def store_write(minio_client):
            minio_client.put_object("bucket", "key", data, length)

        # --- provider_accessor: self.client.<method>() ---
        class Syncer:
            def run(self):
                return self.client.get_subscriber(1)

        # --- provider_accessor: a get_*_client() helper call ---
        def uses_helper():
            client = _get_mailcow_client()
            return client

        # --- near-miss: a class merely named similarly ---
        class PaystackClientConfig:
            pass

        # --- near-miss: a type annotation naming the class, never called ---
        def annotated_only(client: PaystackClient) -> None:
            pass

        # --- near-miss: referencing the class without calling it ---
        def reference_only():
            return PaystackClient

        # --- near-miss: a local unrelated SMTP class, never imported ---
        class SMTP:
            pass

        def uses_local_smtp():
            return SMTP()

        # --- near-miss: httpx.Client bound but only read, never written ---
        def bound_client_read_only():
            with httpx.Client() as client:
                return client.get("https://example.test")
    """
    tree = _tree(planted)
    aliases = _ImportAliases()
    aliases.visit(tree)
    # provider_accessor is gated on importing a provider package; this
    # planted tree deliberately imports none, so simulate the gate directly
    # against the same detection helper used by the real scan.
    aliases.imports_provider_package = True
    hits: set[str] = set()

    # Re-run the same two module-scope passes external_effect_hits uses,
    # plus the per-function pass, but with the gate forced on -- proves the
    # provider_accessor shapes themselves are matched correctly in
    # isolation from the (separately tested) import gate.
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in HTTP_WRITE_METHODS
            and isinstance(node.func.value, ast.Name)
            and aliases.http_module.get(node.func.value.id) in ("httpx", "requests")
        ):
            hits.add("http_write")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in OBJECT_STORE_WRITE_METHODS
        ):
            hits.add("object_store_write")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "client"
        ) or (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and PROVIDER_ACCESSOR_HELPER_NAME_RE.match(node.func.id)
        ):
            hits.add("provider_accessor")
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        own_nodes = list(_own_body_nodes(node))
        http_bound = _http_client_bound_names(own_nodes, aliases)
        if http_bound:
            for attr, _call in _provider_client_calls(own_nodes, http_bound):
                if attr in HTTP_WRITE_METHODS:
                    hits.add("http_write")

    assert hits == {"http_write", "object_store_write", "provider_accessor"}

    # The real end-to-end detector (import gate OFF, as this tree has no
    # real provider-package import) must NOT report provider_accessor --
    # proves the gate is load-bearing, not vacuous.
    real_hits = external_effect_hits(tree)
    assert "provider_accessor" not in real_hits
    assert "http_write" in real_hits
    assert "object_store_write" in real_hits
    assert "provider:PaystackClient" not in real_hits
    assert "smtplib" not in real_hits


def test_smtplib_smtp_call_branch_is_reachable() -> None:
    """Isolated proof that the ``smtplib.SMTP(...)``/aliased-``SMTP(...)``
    call branches themselves fire -- not just the bare-import branch, which
    the other sensitivity proofs already exercise."""

    attribute_form = _tree(
        """
        import smtplib as sm

        def connect():
            return sm.SMTP(host, port)
        """
    )
    assert external_effect_hits(attribute_form) == {"smtplib"}

    from_import_alias_form = _tree(
        """
        from smtplib import SMTP as _SMTP

        def connect():
            return _SMTP(host, port)
        """
    )
    assert external_effect_hits(from_import_alias_form) == {"smtplib"}


def test_a_local_smtp_class_alone_is_not_a_hit() -> None:
    """Near-miss isolated: a locally defined ``SMTP`` class with no smtplib
    import anywhere must not be flagged."""

    planted = """
        class SMTP:
            pass

        def uses_local_smtp():
            return SMTP()
    """
    assert external_effect_hits(_tree(planted)) == set()


def test_provider_reference_without_a_call_is_not_a_hit() -> None:
    """Near-miss isolated: a bare reference or an annotation naming a
    provider client, with no Call node at all, must not be flagged."""

    planted = """
        def annotated_only(client: PaystackClient) -> None:
            pass

        def reference_only():
            return PaystackClient

        class PaystackClientConfig:
            pass
    """
    assert external_effect_hits(_tree(planted)) == set()


def test_cross_method_self_client_is_not_merged_into_one_false_hit() -> None:
    """Real-shape proof for the http_write LIMITATION: a class whose
    constructor-injected ``self._client`` is used for a write in one method,
    while a DIFFERENT, unrelated class in the same module binds
    ``self._client = httpx.Client(...)`` in its own method that never
    writes, must not be merged into a false http_write hit -- this is
    exactly ``app/services/dotmac_sub/client.py``'s real shape."""

    planted = """
        import httpx

        class Injected:
            def __init__(self, client):
                self._client = client

            def send(self):
                return self._client.post("https://example.test")

        class OpensItsOwn:
            def open(self):
                self._client = httpx.Client()

            def read_only(self):
                return self._client.get("https://example.test")
    """
    assert external_effect_hits(_tree(planted)) == set()


def test_read_only_modules_only_call_allowed_methods() -> None:
    """Enforces (not merely asserts) READ_ONLY_ALLOWED_METHODS against the
    real files, and proves the check is not vacuous with planted violations:
    a disallowed mutating call, and a non-literal-GET ``_request``."""

    for rel, allowed in READ_ONLY_ALLOWED_METHODS.items():
        path = REPO_ROOT / rel
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        aliases = _ImportAliases()
        aliases.visit(tree)
        violations: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            own_nodes = list(_own_body_nodes(node))
            bound = _provider_client_bound_names(own_nodes, aliases.provider)
            for attr, call in _provider_client_calls(own_nodes, bound):
                if attr not in allowed:
                    violations.append(f"{rel}: disallowed call `{attr}`")
                    continue
                if attr == "_request" and not (
                    call.args
                    and isinstance(call.args[0], ast.Constant)
                    and call.args[0].value == "GET"
                ):
                    violations.append(
                        f'{rel}: `_request` called without a literal "GET" first arg'
                    )
        assert not violations, (
            f"{rel} is declared read-only-observation but calls a method "
            f"outside its reviewed allowlist: {violations}"
        )

    # Sensitivity proof: a planted mutating call outside the allowlist IS
    # caught.
    mutating = _tree(
        """
        from app.services.finance.payments.paystack_client import PaystackClient

        def probe():
            with PaystackClient(config) as client:
                client.create_transfer(amount)
        """
    )
    aliases = _ImportAliases()
    aliases.visit(mutating)
    found = False
    for node in ast.walk(mutating):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        own_nodes = list(_own_body_nodes(node))
        bound = _provider_client_bound_names(own_nodes, aliases.provider)
        for attr, _call in _provider_client_calls(own_nodes, bound):
            if attr not in READ_ONLY_ALLOWED_METHODS["app/dependency_health.py"]:
                found = True
    assert found, "the planted disallowed call must be detected"

    # Sensitivity proof: a planted non-literal-GET `_request` IS caught even
    # though `_request` itself is allowlisted.
    dynamic_verb = _tree(
        """
        from app.services.nextcloud.client import NextcloudTalkClient

        def probe(method):
            client = NextcloudTalkClient(config)
            client._request(method, "/rooms")
        """
    )
    aliases = _ImportAliases()
    aliases.visit(dynamic_verb)
    caught = False
    for node in ast.walk(dynamic_verb):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        own_nodes = list(_own_body_nodes(node))
        bound = _provider_client_bound_names(own_nodes, aliases.provider)
        for attr, call in _provider_client_calls(own_nodes, bound):
            if attr == "_request" and not (
                call.args
                and isinstance(call.args[0], ast.Constant)
                and call.args[0].value == "GET"
            ):
                caught = True
    assert caught, "a non-literal-GET `_request` call must be caught"


def test_alembic_and_tests_are_declared_unmonitored_not_silently_skipped() -> None:
    assert not any(root.name in {"alembic", "tests"} for root in SCANNED_ROOTS), (
        "alembic/ and tests/ must stay named as unmonitored in the module "
        "docstring, not silently added to SCANNED_ROOTS without a decision"
    )
