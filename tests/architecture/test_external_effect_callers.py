"""A ratchet on code that constructs an external-effect provider client (or
uses ``smtplib`` directly) outside that client's own defining module.

Why this matters: ADR-0013 ("External effects are recorded by their owner
before they run") requires every irreversible external effect -- a payment
capture, a bank API call, a mailbox provisioning request, an outbound email
-- to be recorded by its owner BEFORE it runs. A caller that constructs a
provider client (or opens an SMTP connection) directly, anywhere it likes,
can perform that effect with no such recording having happened. ADR-0013
requires "a two-directional ratchet: an architecture test inventories every
module that constructs a provider client or uses smtplib, freezes today's
set as grandfathered; a new direct caller fails CI; a migrated caller lowers
the inventory in the same change; the detector states its blind spots and
carries a planted sensitivity proof." This module is that test.

SCOPE. Scanned roots: :data:`SCANNED_ROOTS` -- ``app/``, ``scripts/`` and
``tools/`` -- the same roots (and for the same reason) as
``test_own_session_commits.py``. UNMONITORED, named explicitly rather than
left implicit: ``alembic/`` (migration scripts do not construct provider
clients or send mail) and ``tests/`` (test fixtures/mocks constructing a
client in a test double are not a production caller).

WHAT IS FLAGGED. Per MODULE (this ratchet is file-grained, not
function-grained -- see LIMITATION below), a hit is:

    (a) a ``Call`` that constructs one of :data:`PROVIDER_CLIENTS` -- a bare
        name (``PaystackClient(...)``), an attribute access
        (``paystack_client.PaystackClient(...)``), or a call through a local
        import alias (``from ...paystack_client import PaystackClient as
        PC`` then ``PC(...)``) -- including the ``with X(...) as c:`` form,
        since a ``with`` statement's context expression is itself the same
        ``ast.Call`` node this scan already walks.
    (b) ``smtplib`` use: a bare ``import smtplib``, a
        ``from smtplib import ...``, or a call to ``SMTP(``/``SMTP_SSL(``
        that RESOLVES to smtplib -- either ``smtplib.SMTP(...)`` (an
        attribute access on a name bound to the ``smtplib`` module, plain or
        aliased) or a bare ``SMTP(...)``/``SMTP_SSL(...)`` call whose name
        was imported from ``smtplib`` (plain or aliased). A same-named class
        defined LOCALLY (not imported from smtplib) is deliberately not a
        hit -- see the sensitivity proof's ``local_smtp_class`` near-miss.

PROVIDER_CLIENTS is deliberately just the eight class names ADR-0013's
inventory names -- a call resolved to any OTHER name is never a hit, so a
bare reference to, or an annotation naming, one of these classes
(``client: PaystackClient``) is never flagged: only an actual ``Call`` node
whose target resolves to one of these names counts.

EXCLUSIONS, each with its enforceable premise:

1. Each client's OWN defining module (:data:`CLIENT_DEFINING_MODULES`) --
   premise: a client may construct itself (e.g. a ``classmethod`` building a
   fresh instance of its own class). Verified empty in practice today (grep
   over all eight defining modules for a literal ``ClassName(`` call other
   than the ``class`` statement itself found none -- every observed factory
   uses ``cls(...)``, which this scanner does not resolve back to the
   literal class name and so was never going to flag this module anyway) --
   the exclusion is stated because the premise holds, not because it changes
   today's result.
2. The EventOutbox relay/delivery module, IF it constructs a client.
   Located by grepping ``event_outbox``/``relay``/``dispatch`` across the
   repository: ``app/tasks/outbox_relay.py`` (the Celery relay task,
   ``relay_outbox_events`` / ``_deliver_one``) and
   ``app/services/finance/platform/outbox_publisher.py`` (``OutboxPublisher``,
   the write-side). NEITHER constructs any of :data:`PROVIDER_CLIENTS` nor
   uses ``smtplib`` -- verified by running this module's own scan function
   against each file: both come back with no hit. So this exclusion excludes
   NOTHING today; :data:`EVENT_OUTBOX_MODULES` is declared (and asserted
   hit-free by :func:`test_event_outbox_modules_construct_no_client`) purely
   so a future outbox module that DOES construct a client is a reviewed
   decision to add it here, not a silent exemption.

LIMITATION (stated, not implemented): this ratchet is FILE-grained, not
call-site-grained -- a module with ten call sites across three provider
clients gets exactly one baseline row. A module already in the baseline for
one client can grow a second, different direct-caller shape with no new
signal from this test. Per-call-site granularity was rejected as
disproportionate to a debt inventory whose job is "does this module still
touch a provider client directly at all", the same choice
``test_own_session_commits.py`` makes explicit for its own per-function
grain in the other direction.

BASELINE lives in the sidecar file :data:`BASELINE_PATH`
(``<path>\\t<reason>``) because the current hit count is real existing debt,
not a claim it is contained to one row. The ratchet is two-directional: a
hit outside the baseline, or a stale baseline row no longer produced by the
live scan, both fail.
"""

from __future__ import annotations

import ast
import functools
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
#: defines it. See exclusion 1 in the module docstring.
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
#: docstring -- neither constructs a provider client or uses smtplib today,
#: verified by :func:`test_event_outbox_modules_construct_no_client`.
EVENT_OUTBOX_MODULES: frozenset[str] = frozenset(
    {
        "app/tasks/outbox_relay.py",
        "app/services/finance/platform/outbox_publisher.py",
    }
)

#: Modules hand-reviewed and confirmed to construct a provider client for
#: READ-ONLY observation only (GET/verify/read methods, never a mutating
#: call) -- see the module-by-module citation below. Everything else that
#: hits gets the plain "direct external-effect caller" reason.
#:
#: - app/dependency_health.py: every construction site is a bounded health
#!   probe that calls a read method and discards the result --
#:   ``PaystackClient(...).list_banks()`` (GET /bank, line ~292),
#:   ``NextcloudTalkClient(...)._request("GET", ...)`` (line ~317),
#:   ``DotmacSubClient(...).test_connection()`` (GET /subscribers/sync,
#:   confirmed at app/services/dotmac_sub/client.py:2571), and
#:   ``RemitaClient(...)._get_client().get("/", ...)`` (line ~398). None
#:   mutates provider state.
#: - scripts/archive/paystack_probe_unmatched_statement_refs.py: the script's
#:   own docstring states "This script is read-only (it does not write to
#:   DB)", and its one call site is ``client.verify_transaction(c)`` (GET
#:   /transaction/verify/..., confirmed at
#:   app/services/finance/payments/paystack_client.py:395-396).
READ_ONLY_OBSERVATION_MODULES: frozenset[str] = frozenset(
    {
        "app/dependency_health.py",
        "scripts/archive/paystack_probe_unmatched_statement_refs.py",
    }
)

BASELINE_PATH = Path(__file__).with_name("external_effect_caller_baseline.txt")

#: Reason string used for every hit not in READ_ONLY_OBSERVATION_MODULES.
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


def _attribute_base_name(node: ast.expr) -> str | None:
    """The bare name at the root of a (possibly dotted) attribute access,
    e.g. ``smtplib`` in ``smtplib.SMTP`` or ``sm`` in ``sm.SMTP``."""
    if isinstance(node, ast.Name):
        return node.id
    return None


class _ImportAliases:
    """Import aliases collected from a module, resolving a local name back
    to a canonical provider-client class name or to ``"smtplib"`` /
    ``"smtplib.SMTP"`` / ``"smtplib.SMTP_SSL"``."""

    def __init__(self) -> None:
        #: local name -> canonical provider client class name
        self.provider: dict[str, str] = {}
        #: local name -> "smtplib" (from `import smtplib [as x]`)
        self.smtplib_module: dict[str, str] = {}
        #: local name -> "smtplib.SMTP" / "smtplib.SMTP_SSL"
        #: (from `from smtplib import SMTP [as x]`)
        self.smtplib_from: dict[str, str] = {}

    def visit(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "smtplib":
                        self.smtplib_module[alias.asname or "smtplib"] = "smtplib"
            elif isinstance(node, ast.ImportFrom):
                if node.module == "smtplib":
                    for alias in node.names:
                        if alias.name in ("SMTP", "SMTP_SSL"):
                            self.smtplib_from[alias.asname or alias.name] = (
                                f"smtplib.{alias.name}"
                            )
                for alias in node.names:
                    if alias.name in PROVIDER_CLIENTS:
                        self.provider[alias.asname or alias.name] = alias.name


def external_effect_hits(tree: ast.AST) -> set[str]:
    """Return the set of hit labels found in ``tree``:
    ``"provider:<ClassName>"`` for a provider-client construction, or
    ``"smtplib"`` for any smtplib use (an import, or a resolved
    ``SMTP(``/``SMTP_SSL(`` call). Used both by the repo scan (one module at
    a time) and the sensitivity proof below."""

    hits: set[str] = set()
    aliases = _ImportAliases()
    aliases.visit(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "smtplib":
                    hits.add("smtplib")
        elif isinstance(node, ast.ImportFrom):
            if node.module == "smtplib":
                hits.add("smtplib")
        elif isinstance(node, ast.Call):
            bare = _call_target_name(node.func)
            if bare is None:
                continue
            # (a) provider-client construction: bare name, attribute, or a
            # local import alias resolved back to the canonical class name.
            canonical = aliases.provider.get(bare, bare)
            if canonical in PROVIDER_CLIENTS:
                hits.add(f"provider:{canonical}")
            # (b) a smtplib.SMTP(...)/smtplib.SMTP_SSL(...) attribute call.
            if isinstance(node.func, ast.Attribute) and bare in ("SMTP", "SMTP_SSL"):
                base = _attribute_base_name(node.func.value)
                if base is not None and aliases.smtplib_module.get(base) == "smtplib":
                    hits.add("smtplib")
            # (b) a bare SMTP(...)/SMTP_SSL(...) call resolved via a
            # `from smtplib import SMTP [as x]` alias.
            if isinstance(node.func, ast.Name) and bare in aliases.smtplib_from:
                hits.add("smtplib")

    return hits


def _classify(rel: str) -> str:
    if rel in READ_ONLY_OBSERVATION_MODULES:
        return READ_ONLY_REASON
    return DEFAULT_REASON


def scan_repo(roots: tuple[Path, ...] = SCANNED_ROOTS) -> dict[str, str]:
    """``path`` -> classified reason, for every module with a hit."""

    excluded = set(CLIENT_DEFINING_MODULES.values()) | EVENT_OUTBOX_MODULES
    hits: dict[str, str] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel in excluded:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            if external_effect_hits(tree):
                hits[rel] = _classify(rel)
    return hits


@functools.cache
def _scan() -> dict[str, str]:
    """One repository scan shared by every test in this module."""

    return scan_repo()


def _load_baseline() -> dict[str, str]:
    baseline: dict[str, str] = {}
    for line in BASELINE_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        key, _, reason = line.partition("\t")
        baseline[key] = reason
    return baseline


def test_no_hit_outside_baseline() -> None:
    current = _scan()
    baseline = _load_baseline()
    new = sorted(set(current) - set(baseline))
    assert not new, (
        "These modules construct an external-effect provider client (or use "
        "smtplib) directly and are not in the reviewed baseline (ADR-0013): "
        f"{new}. Record the effect through its owner before it runs, or add "
        f"the row to {BASELINE_PATH.name}."
    )


def test_baseline_has_no_stale_entries() -> None:
    current = _scan()
    baseline = _load_baseline()
    stale = sorted(set(baseline) - set(current))
    assert not stale, (
        "These baseline rows no longer match a real hit -- remove them so "
        f"the ratchet keeps its grip: {stale}"
    )


def test_baseline_reasons_match_the_classifier() -> None:
    current = _scan()
    baseline = _load_baseline()
    mismatched = sorted(
        key for key in set(current) & set(baseline) if current[key] != baseline[key]
    )
    assert not mismatched, (
        f"These baseline reasons no longer match the classifier: {mismatched}"
    )
    for reason in baseline.values():
        assert reason.startswith("grandfathered:")


def test_client_defining_modules_are_excluded_from_the_repo_scan() -> None:
    hits = _scan()
    for module in CLIENT_DEFINING_MODULES.values():
        assert module not in hits, (
            f"{module} is a provider client's own defining module and must "
            "be excluded wholesale (exclusion 1)"
        )


def test_event_outbox_modules_construct_no_client() -> None:
    """Proves exclusion 2's premise directly: re-running this module's own
    scan function against each named EventOutbox module (bypassing the
    exclusion) finds no hit -- the exclusion excludes nothing today."""

    for rel in EVENT_OUTBOX_MODULES:
        path = REPO_ROOT / rel
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        assert external_effect_hits(tree) == set(), (
            f"{rel} was declared to construct no provider client and use no "
            "smtplib (exclusion 2's premise) but a live scan now finds one -- "
            "the exclusion's premise no longer holds and must be re-reviewed"
        )


def _tree(source: str) -> ast.AST:
    return ast.parse(textwrap.dedent(source))


def test_sensitivity_proof_plants_and_near_misses() -> None:
    """Plant every flagged shape (each of the eight clients via a bare name,
    an attribute access, and an import alias; the ``with`` form; both
    smtplib import shapes) and every near-miss side by side; the detected
    set must equal exactly the planted provider-client/smtplib shapes."""

    planted = """
        import smtplib
        from smtplib import SMTP
        from app.services.finance.payments.paystack_client import (
            PaystackClient as PC,
        )
        import app.services.remita.client as remita_client

        # --- provider (a): bare name construction ---
        def build_paystack():
            return PaystackClient(config)

        # --- provider (a): attribute access construction ---
        def build_remita():
            return remita_client.RemitaClient(config)

        # --- provider (a): local import alias ---
        def build_paystack_via_alias():
            return PC(config)

        # --- provider (a): the `with X(...) as c:` form ---
        def build_mono_with():
            with MonoClient(config) as client:
                client.get_account_info(1)

        # --- smtplib (b): plain import already present at module scope ---
        # (covered by the top-level `import smtplib` above)

        # --- smtplib (b): attribute call resolved via the module import ---
        def send_via_attribute():
            server = smtplib.SMTP(host, port)
            return server

        # --- smtplib (b): bare call resolved via the from-import alias ---
        def send_via_from_import():
            server = SMTP(host, port)
            return server

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
    """

    tree = _tree(planted)
    hits = external_effect_hits(tree)

    assert hits == {
        "provider:PaystackClient",
        "provider:RemitaClient",
        "provider:MonoClient",
        "smtplib",
    }


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


def test_alembic_and_tests_are_declared_unmonitored_not_silently_skipped() -> None:
    assert not any(root.name in {"alembic", "tests"} for root in SCANNED_ROOTS), (
        "alembic/ and tests/ must stay named as unmonitored in the module "
        "docstring, not silently added to SCANNED_ROOTS without a decision"
    )
