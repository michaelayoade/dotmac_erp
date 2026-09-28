"""A ratchet on code that reaches an external effect surface directly --
constructing a provider client, hitting a raw HTTP write, using
``smtplib``, or reaching a provider through its ``.client`` accessor -- from
outside that surface's own owner.

Why this matters: ADR-0013 ("External effects are recorded by their owner
before they run") requires every irreversible external effect -- a payment
capture, a bank API call, a mailbox provisioning request, an outbound email --
to be recorded by its owner BEFORE it runs. A caller
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
        ``Call`` node, is never a hit. The symbol must resolve through an
        import of its actual defining module; a local lookalike is ignored.
        Construction at module scope is included.
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
        ``with ... as x``). An inline ``httpx.Client().post(...)`` or
        ``requests.Session().post(...)`` is also included. A client bound at
        module scope and used there or in a function is included. A
        ``self.<attr>`` opened directly or returned by a same-class factory
        and then used by another method is included. Class-local binding
        tracking keeps
        two unrelated classes that both happen to use the attribute name
        ``self._client`` from being merged into one false hit (see
        ``app/services/dotmac_sub/client.py``, where
        ``_ExactBodyHttpClient.request`` calls ``self._client.request(...)``
        on a constructor-injected client, never one THAT function opened,
        while a wholly different class binds ``self._client = httpx.Client(
        ...)`` in its own, separate method).
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

ERP-OWNED OBJECT PERSISTENCE. Raw MinIO/boto SDK client construction and
mutation are internal durable persistence and are guarded separately: only
``app/services/storage.py`` may perform them. Calls through that owner's
public ``get_storage()``/``S3StorageService`` facade remain legitimate. A new
raw SDK constructor or mutating call under ``app/``, ``scripts/`` or ``tools/``
fails the owner-bound guard; these are not external-effect family labels.

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
- The owner-bound storage scan likewise only resolves direct attribute calls;
  aliases such as ``write = client.put_object; write(...)`` and dynamic
  dispatch such as ``getattr(client, "put_object")(...)`` are not detected.
  The SDK constructor scan resolves direct imports and aliases, but not
  rebinding through variables or dynamic ``importlib`` imports. Method names
  are used as the static proxy for SDK mutation; another unrelated object
  with the same SDK-specific method name may over-match.
- Cross-method ``self`` tracking stays inside one class. It resolves direct
  constructors and same-class factories that return a directly constructed
  client, but not a factory whose result arrives through further helpers,
  inheritance, or dynamic dispatch. A constructor-injected client without
  an SDK construction in that class does not count.
- ``provider_accessor``'s ``.client.<method>`` shape matches ANY base
  expression before ``.client`` once the owning module imports a provider
  package; it does not verify the ``.client`` attribute is actually the
  provider client property. Likewise its helper-name regex matches ANY
  ``get_..._client``/``_get_..._client`` call in such a module, regardless of
  what that helper actually returns -- real over-match today:
  ``app/dependency_health.py`` imports several provider packages for its
  other health probes AND separately calls ``_get_storage_client()`` (an
  unrelated storage accessor from ``app.services.storage``),
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
from collections.abc import Sequence
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

# Checked-in package facades explicitly re-export these exact class symbols.
PROVIDER_REEXPORT_MODULES: dict[str, frozenset[str]] = {
    "app.services.dotmac_sub": frozenset({"DotmacSubClient"}),
    "app.services.finance.payments": frozenset({"PaystackClient"}),
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

#: ERP's sole owner for internal object persistence. This is intentionally a
#: separate owner-bound guard, not an external-effect family.
STORAGE_OWNER = "app/services/storage.py"
STORAGE_WRITE_METHODS: frozenset[str] = frozenset(
    {
        "put_object",
        "fput_object",
        "remove_object",
        "remove_objects",
        "copy_object",
        "compose_object",
        "restore_object",
        "delete_object",
        "delete_objects",
        "upload_file",
        "upload_fileobj",
        "make_bucket",
        "create_bucket",
        "delete_bucket",
        "remove_bucket",
        "abort_multipart_upload",
        "complete_multipart_upload",
        "create_multipart_upload",
        "upload_part",
        "upload_part_copy",
    }
)
STORAGE_WRITE_METHOD_PREFIXES: tuple[str, ...] = (
    "put_bucket_",
    "delete_bucket_",
    "put_object_",
    "delete_object_",
    "set_bucket_",
    "set_object_",
    "remove_bucket_",
    "remove_object_",
    "put_public_access_",
    "delete_public_access_",
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
REVIEWED_MODULE_COUNT = 34
REVIEWED_FAMILY_LABEL_COUNT = 44

#: Reason string used for every hit not covered by a more specific reason.
DEFAULT_REASON = (
    "grandfathered: direct external-effect caller — ADR-0013 migration debt"
)

#: Reason string used for a hand-reviewed read-only-observation module.
READ_ONLY_REASON = (
    "grandfathered: read-only provider observation — confirm under ADR-0013"
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


def _own_body_nodes(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[ast.AST, ...]:
    """Every descendant reachable from ``func`` without crossing into a
    nested function/async function/lambda -- those are separate scan
    units."""

    def _walk(node: ast.AST):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            yield child
            yield from _walk(child)

    return tuple(_walk(func))


class _ImportAliases:
    """Import aliases collected from a module: a local name resolved back to
    a canonical provider-client class name, an httpx/requests module name,
    or a smtplib name/call."""

    def __init__(self) -> None:
        #: local name -> canonical provider client class name
        self.provider: dict[str, str] = {}
        #: local module alias -> canonical provider client defining module
        self.provider_modules: dict[str, str] = {}
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

    def visit(
        self, tree: ast.AST, *, nodes: tuple[ast.AST, ...] | None = None
    ) -> None:
        for node in nodes if nodes is not None else ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "smtplib":
                        self.smtplib_module[alias.asname or "smtplib"] = "smtplib"
                    if alias.name in ("httpx", "requests"):
                        self.http_module[alias.asname or alias.name] = alias.name
                    if alias.name.startswith(PROVIDER_PACKAGE_PREFIXES):
                        self.imports_provider_package = True
                    for rel in CLIENT_DEFINING_MODULES.values():
                        module = rel.removesuffix(".py").replace("/", ".")
                        if alias.name == module and alias.asname:
                            self.provider_modules[alias.asname] = module
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
                        if HTTP_CLIENT_CTORS.get(alias.name) == module:
                            self.http_from[alias.asname or alias.name] = (
                                f"{module}.{alias.name}"
                            )
                if module.startswith(PROVIDER_PACKAGE_PREFIXES):
                    self.imports_provider_package = True
                for alias in node.names:
                    defining = CLIENT_DEFINING_MODULES.get(alias.name, "")
                    if (
                        defining
                        and module == defining.removesuffix(".py").replace("/", ".")
                    ) or alias.name in PROVIDER_REEXPORT_MODULES.get(module, ()):
                        self.provider[alias.asname or alias.name] = alias.name
                    imported_module = f"{module}.{alias.name}"
                    if imported_module in {
                        rel.removesuffix(".py").replace("/", ".")
                        for rel in CLIENT_DEFINING_MODULES.values()
                    }:
                        self.provider_modules[alias.asname or alias.name] = (
                            imported_module
                        )


def _provider_constructor_name(expr: ast.expr, aliases: _ImportAliases) -> str | None:
    if isinstance(expr, ast.Name):
        return aliases.provider.get(expr.id)
    if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
        module = aliases.provider_modules.get(expr.value.id)
        defining = CLIENT_DEFINING_MODULES.get(expr.attr, "")
        if defining and module == defining.removesuffix(".py").replace("/", "."):
            return expr.attr
    return None


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
            return HTTP_CLIENT_CTORS.get(call.func.attr) == aliases.http_module[base.id]
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
    own_nodes: Sequence[ast.AST], aliases: _ImportAliases
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
        if _provider_constructor_name(value.func, aliases):
            key = _target_key(target)
            if key:
                bound[key] = value.lineno
    return bound


def _http_client_bound_names(
    own_nodes: Sequence[ast.AST], aliases: _ImportAliases
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
    own_nodes: Sequence[ast.AST], bound: dict[str, int]
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


def _module_body_nodes(tree: ast.AST) -> list[ast.AST]:
    """Module descendants without crossing into a function or class body."""
    found: list[ast.AST] = []

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            found.append(child)
            walk(child)

    walk(tree)
    return found


def _http_write_calls(
    nodes: Sequence[ast.AST], bound: dict[str, int], aliases: _ImportAliases
) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in HTTP_WRITE_METHODS
        and (
            _rooted_in_bound_name(node.func.value, bound)
            or (
                isinstance(node.func.value, ast.Call)
                and _is_http_client_ctor_call(node.func.value, aliases)
            )
        )
        for node in nodes
    )


def _class_http_client_bindings(
    class_node: ast.ClassDef,
    aliases: _ImportAliases,
    own_nodes_by_function: dict[
        ast.FunctionDef | ast.AsyncFunctionDef, tuple[ast.AST, ...]
    ],
) -> dict[str, int]:
    """Track SDK clients held on ``self`` without merging separate classes."""
    methods = [
        node
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    factories: set[str] = set()
    for method in methods:
        own_nodes = own_nodes_by_function[method]
        local = _http_client_bound_names(own_nodes, aliases)
        if any(
            isinstance(node, ast.Return)
            and node.value is not None
            and (
                isinstance(node.value, ast.Call)
                and _is_http_client_ctor_call(node.value, aliases)
                or isinstance(node.value, ast.Name)
                and node.value.id in local
            )
            for node in own_nodes
        ):
            factories.add(method.name)

    bound: dict[str, int] = {}
    for method in methods:
        own_nodes = own_nodes_by_function[method]
        for key, line in _http_client_bound_names(own_nodes, aliases).items():
            if key.startswith("self."):
                bound[key] = line
        for node in own_nodes:
            target: ast.expr | None = None
            value: ast.expr | None = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
            elif isinstance(node, ast.AnnAssign):
                target, value = node.target, node.value
            key = _target_key(target)
            if (
                key is not None
                and key.startswith("self.")
                and isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute)
                and isinstance(value.func.value, ast.Name)
                and value.func.value.id == "self"
                and value.func.attr in factories
            ):
                bound[key] = value.lineno
    return bound


def external_effect_hits(tree: ast.AST) -> set[str]:
    """Return the set of hit family labels found in ``tree`` -- see the
    module docstring's "WHAT IS FLAGGED" section for each family's exact
    shape. Used both by the repo scan (one module at a time) and the
    sensitivity proof below."""

    hits: set[str] = set()
    all_nodes = tuple(ast.walk(tree))
    own_nodes_by_function = {
        node: _own_body_nodes(node)
        for node in all_nodes
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    aliases = _ImportAliases()
    aliases.visit(tree, nodes=all_nodes)

    # smtplib: bare imports are a module-wide hit regardless of use.
    for node in all_nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "smtplib":
                    hits.add("smtplib")
        elif isinstance(node, ast.ImportFrom) and node.module == "smtplib":
            hits.add("smtplib")

    # Module-level http_write: httpx.post(...)/requests.post(...) etc, not
    # gated on any function-local bound name.
    for node in all_nodes:
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in HTTP_WRITE_METHODS
            and isinstance(node.func.value, ast.Name)
            and aliases.http_module.get(node.func.value.id) in ("httpx", "requests")
        ):
            hits.add("http_write")

    # provider_accessor: gated on the module importing a provider package.
    if aliases.imports_provider_package:
        for node in all_nodes:
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

    # Constructor calls are scanned at every scope, including module/class
    # scope. The import resolver excludes unrelated same-named local classes.
    for node in all_nodes:
        if isinstance(node, ast.Call):
            provider = _provider_constructor_name(node.func, aliases)
            if provider:
                hits.add(f"provider:{provider}")

    module_nodes = _module_body_nodes(tree)
    module_http_bound = _http_client_bound_names(module_nodes, aliases)
    if _http_write_calls(module_nodes, module_http_bound, aliases):
        hits.add("http_write")

    # Per-function HTTP clients: local bindings plus module-level bindings.
    for node in all_nodes:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        own_nodes = own_nodes_by_function[node]

        for n in own_nodes:
            if isinstance(n, ast.Call):
                bare = _call_target_name(n.func)
                if bare is not None:
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
        if _http_write_calls(own_nodes, http_bound | module_http_bound, aliases):
            hits.add("http_write")

    for class_node in all_nodes:
        if not isinstance(class_node, ast.ClassDef):
            continue
        bound = _class_http_client_bindings(
            class_node, aliases, own_nodes_by_function
        )
        if bound and any(
            _http_write_calls(own_nodes_by_function[method], bound, aliases)
            for method in class_node.body
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
        ):
            hits.add("http_write")

    return hits


def storage_write_methods(tree: ast.AST) -> set[str]:
    """Return MinIO/boto write method names called directly in ``tree``."""

    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and (
            node.func.attr in STORAGE_WRITE_METHODS
            or node.func.attr.startswith(STORAGE_WRITE_METHOD_PREFIXES)
        )
    }


def storage_client_constructors(tree: ast.AST) -> set[str]:
    """Direct MinIO and boto3 S3 client constructors, resolved from imports."""
    minio_names: set[str] = set()
    minio_modules: set[str] = set()
    minio_api_imported = False
    boto_modules: set[str] = set()
    boto_session_modules: set[str] = set()
    boto_session_imported = False
    boto_client_names: set[str] = set()
    boto_session_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"minio", "minio.api"}:
                    minio_modules.add(alias.asname or "minio")
                    minio_api_imported |= alias.name == "minio.api" and not alias.asname
                elif alias.name == "boto3":
                    boto_modules.add(alias.asname or "boto3")
                elif alias.name == "boto3.session":
                    if alias.asname:
                        boto_session_modules.add(alias.asname)
                    else:
                        boto_modules.add("boto3")
                        boto_session_imported = True
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if node.module == "minio" and alias.name == "Minio":
                    minio_names.add(alias.asname or alias.name)
                elif node.module == "boto3" and alias.name in {"client", "resource"}:
                    boto_client_names.add(alias.asname or alias.name)
                elif node.module == "boto3.session" and alias.name == "Session":
                    boto_session_names.add(alias.asname or alias.name)

    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if (
            (isinstance(node.func, ast.Name) and node.func.id in minio_names)
            or (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "Minio"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in minio_modules
            )
            or (
                minio_api_imported
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "Minio"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "api"
                and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id == "minio"
            )
        ):
            found.add("Minio")
        elif (
            (isinstance(node.func, ast.Name) and node.func.id in boto_client_names)
            or (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in {"client", "resource"}
                and (
                    (
                        isinstance(node.func.value, ast.Name)
                        and node.func.value.id in boto_modules
                    )
                    or (
                        isinstance(node.func.value, ast.Call)
                        and (
                            isinstance(node.func.value.func, ast.Name)
                            and node.func.value.func.id in boto_session_names
                            or isinstance(node.func.value.func, ast.Attribute)
                            and node.func.value.func.attr == "Session"
                            and isinstance(node.func.value.func.value, ast.Name)
                            and node.func.value.func.value.id
                            in boto_modules | boto_session_modules
                            or boto_session_imported
                            and isinstance(node.func.value.func, ast.Attribute)
                            and node.func.value.func.attr == "Session"
                            and isinstance(node.func.value.func.value, ast.Attribute)
                            and node.func.value.func.value.attr == "session"
                            and isinstance(node.func.value.func.value.value, ast.Name)
                            and node.func.value.func.value.value.id == "boto3"
                        )
                    )
                )
            )
        ) and (
            not node.args
            or not isinstance(node.args[0], ast.Constant)
            or node.args[0].value == "s3"
        ):
            found.add("boto3.s3")
    return found


def scan_storage_writes(
    roots: tuple[Path, ...] = SCANNED_ROOTS,
) -> dict[str, set[str]]:
    """Return direct object-store write methods grouped by repository path."""

    hits: dict[str, set[str]] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            methods = storage_write_methods(tree) | storage_client_constructors(tree)
            if methods:
                hits[rel] = methods
    return hits


def _classify(rel: str, families: frozenset[str]) -> str:
    if rel in READ_ONLY_OBSERVATION_MODULES:
        return READ_ONLY_REASON
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


def _parse_baseline(lines: list[str]) -> dict[str, tuple[frozenset[str], str]]:
    baseline: dict[str, tuple[frozenset[str], str]] = {}
    previous_path = ""
    for line in lines:
        if not line.strip() or line.startswith("#"):
            continue
        path, families, reason = line.split("\t")
        assert path > previous_path, f"duplicate or unsorted baseline row: {path}"
        previous_path = path
        labels = families.split(",")
        assert labels == sorted(set(labels)), f"duplicate or unsorted families: {path}"
        baseline[path] = (frozenset(families.split(",")), reason)
    return baseline


def _load_baseline() -> dict[str, tuple[frozenset[str], str]]:
    return _parse_baseline(BASELINE_PATH.read_text(encoding="utf-8").splitlines())


def test_baseline_parser_rejects_duplicate_and_unsorted_rows() -> None:
    for lines in (
        ["a.py\thttp_write\tgrandfathered: x"] * 2,
        ["b.py\thttp_write\tgrandfathered: x", "a.py\thttp_write\tgrandfathered: x"],
        ["a.py\tprovider:X,http_write\tgrandfathered: x"],
        ["a.py\thttp_write,http_write\tgrandfathered: x"],
    ):
        try:
            _parse_baseline(lines)
        except AssertionError:
            pass
        else:
            raise AssertionError(f"malformed baseline accepted: {lines}")


def test_reviewed_external_effect_inventory_size() -> None:
    current = _scan()
    baseline = _load_baseline()
    assert current == baseline
    assert len(current) == len(baseline) == REVIEWED_MODULE_COUNT
    assert sum(len(families) for families, _ in current.values()) == (
        REVIEWED_FAMILY_LABEL_COUNT
    )


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
        module = CLIENT_DEFINING_MODULES[name].removesuffix(".py").replace("/", ".")
        lines.append(f"from {module} import {name}")
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

    assert hits == {"http_write", "provider_accessor"}

    # The real end-to-end detector (import gate OFF, as this tree has no
    # real provider-package import) must NOT report provider_accessor --
    # proves the gate is load-bearing, not vacuous.
    real_hits = external_effect_hits(tree)
    assert "provider_accessor" not in real_hits
    assert "http_write" in real_hits
    assert "provider:PaystackClient" not in real_hits
    assert "smtplib" not in real_hits


def test_internal_storage_writes_are_not_external_effect_hits() -> None:
    """Internal durable storage is outside this external-effect ratchet."""

    tree = _tree(
        """
        def persist(storage, payload):
            storage.put_object("bucket", "key", payload, len(payload))
        """
    )
    assert external_effect_hits(tree) == set()


def test_storage_writes_stay_with_the_declared_owner() -> None:
    """Existing direct writes are confined to ERP's storage owner."""

    hits = scan_storage_writes()
    assert set(hits) == {STORAGE_OWNER}
    assert hits[STORAGE_OWNER] == {
        "Minio",
        "make_bucket",
        "put_object",
        "remove_object",
    }


def test_storage_write_guard_canaries() -> None:
    positive = _tree(
        """
        def persist(storage, payload):
            storage.put_object("bucket", "key", payload, len(payload))
            storage.make_bucket("archive")
            storage.upload_fileobj(payload, "bucket", "key")
            storage.put_bucket_acl("bucket", ACL="private")
            storage.set_bucket_encryption("bucket", config)
            storage.set_bucket_tags("bucket", tags)
            storage.set_bucket_versioning("bucket", config)
            storage.set_bucket_object_lock_config("bucket", config)
            storage.put_object_retention("bucket", "key", retention)
            storage.delete_bucket_policy("bucket")
        """
    )
    negative = _tree(
        """
        def read(storage, key):
            return storage.get_object("bucket", key)
        def public_facade(storage, key, data):
            storage.upload(key, data)
            storage.delete(key)
        """
    )
    assert storage_write_methods(positive) == {
        "make_bucket",
        "put_bucket_acl",
        "set_bucket_encryption",
        "set_bucket_tags",
        "set_bucket_versioning",
        "set_bucket_object_lock_config",
        "put_object_retention",
        "delete_bucket_policy",
        "put_object",
        "upload_fileobj",
    }
    assert storage_write_methods(negative) == set()

    sdk_constructors = _tree(
        """
        from minio import Minio as M
        import minio.api as api
        import boto3 as aws
        import boto3.session as bs
        from boto3.session import Session as AwsSession
        first = M("s3.example.test")
        second = aws.client("s3")
        third = AwsSession().client("s3")
        fourth = api.Minio("s3.example.test")
        fifth = bs.Session().client("s3")
        """
    )
    assert storage_client_constructors(sdk_constructors) == {"Minio", "boto3.s3"}
    unaliased_minio = _tree(
        "import minio.api\nclient = minio.api.Minio('s3.example.test')"
    )
    unaliased_boto = _tree(
        "import boto3.session\nclient = boto3.session.Session().client('s3')"
    )
    assert storage_client_constructors(unaliased_minio) == {"Minio"}
    assert storage_client_constructors(unaliased_boto) == {"boto3.s3"}
    assert storage_client_constructors(_tree("class Minio: pass\nMinio()")) == set()


def test_http_inline_and_module_scope_client_write_canaries() -> None:
    inline = _tree(
        """
        import httpx
        def send():
            return httpx.Client().post("https://example.test")
        """
    )
    assert external_effect_hits(inline) == {"http_write"}

    module_scope = _tree(
        """
        import httpx
        client = httpx.Client()
        def send():
            return client.post("https://example.test")
        """
    )
    assert external_effect_hits(module_scope) == {"http_write"}


def test_same_class_http_client_binding_across_methods() -> None:
    factory = _tree(
        """
        import requests
        class LokiHandler:
            @staticmethod
            def _new_session():
                session = requests.Session()
                return session
            def __init__(self):
                self._session = self._new_session()
            def emit(self):
                return self._session.post("https://example.test")
        """
    )
    assert external_effect_hits(factory) == {"http_write"}

    direct = _tree(
        """
        import httpx
        class Sender:
            def __init__(self):
                self._client = httpx.Client()
            def send(self):
                return self._client.post("https://example.test")
        """
    )
    assert external_effect_hits(direct) == {"http_write"}


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


def test_provider_constructor_requires_the_provider_symbol_import() -> None:
    lookalike = _tree(
        """
        from unrelated.module import PaystackClient
        class DotmacSubClient:
            pass
        def local():
            return PaystackClient(), DotmacSubClient()
        """
    )
    assert external_effect_hits(lookalike) == set()

    module_scope = _tree(
        """
        from app.services.finance.payments.paystack_client import PaystackClient
        client = PaystackClient(config)
        """
    )
    assert external_effect_hits(module_scope) == {"provider:PaystackClient"}


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
            bound = _provider_client_bound_names(own_nodes, aliases)
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
        bound = _provider_client_bound_names(own_nodes, aliases)
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
        bound = _provider_client_bound_names(own_nodes, aliases)
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
