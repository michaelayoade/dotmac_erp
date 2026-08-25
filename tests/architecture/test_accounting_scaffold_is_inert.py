"""The Accounting scaffold is operationally inert — proven against a booted app.

`test_accounting_composition_disabled.py` proves the MODULE is absent.  That is
not the same claim as this one.  Scaffolding added in anticipation of a cutover
can be entirely module-free and still change a deployment: an import at boot, a
registered route, a Celery task the beat scheduler picks up, a table joined to
`Base.metadata` and swept into `create_all`, or a stray environment read that
makes behaviour depend on a variable nobody set.

Each of those is a way "we only added preparation" turns out to be false, and
each is asserted false here against the real application object rather than by
reading the source.

The scaffold is three files — `app/accounting_adoption.py`,
`app/services/finance/gl/accounting_shadow.py`,
`app/services/finance/gl/accounting_backfill.py` — plus one operator script.
Everything they own must be reachable only when something deliberately calls it.
"""

from __future__ import annotations

import ast
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAFFOLD_MODULES = (
    "app.accounting_adoption",
    "app.services.finance.gl.accounting_shadow",
    "app.services.finance.gl.accounting_backfill",
)
SCAFFOLD_PATHS = (
    "app/accounting_adoption.py",
    "app/services/finance/gl/accounting_shadow.py",
    "app/services/finance/gl/accounting_backfill.py",
    "scripts/backfill_accounting.py",
)
#: The one environment variable the scaffold is allowed to read.
ALLOWED_ENV_READS = frozenset({"ACCOUNTING_COMPOSITION_ENABLED"})


def _boot_env() -> dict[str, str]:
    """The environment ERP's other subprocess-boot tests use.

    Importing `app.main` mounts ~40 routers and, left to itself, arms the
    runtime observability stack — which on a CI runner with no collector reaches
    for the network and blows the 60s `pytest-timeout`.  `test_startup.py` and
    `test_main_route_precedence.py` boot the app the same way for the same
    reason.

    Neither flag narrows what gets IMPORTED, which is the only thing this file
    asserts: `ENABLED_MODULES` is deliberately left alone so the full router set
    still mounts, and the scaffold lives under `finance/gl` — the subtree most
    likely to pull it in.
    """
    env = os.environ.copy()
    env["DOTMAC_DEV_MODE"] = "true"
    env["DOTMAC_DEFER_RUNTIME_OBSERVABILITY"] = "1"
    return env


def _scaffold_sources() -> list[tuple[str, ast.Module]]:
    return [
        (path, ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"), filename=path))
        for path in SCAFFOLD_PATHS
    ]


def test_the_scaffold_files_all_exist() -> None:
    """Everything below is a claim about these files; if one is renamed the
    claims must move with it rather than silently checking nothing."""
    missing = [path for path in SCAFFOLD_PATHS if not (REPO_ROOT / path).is_file()]
    assert not missing, f"scaffold files moved or were renamed: {missing}"


@pytest.mark.timeout(300)
def test_booting_the_app_does_not_import_the_scaffold() -> None:
    """The strongest inertness proof available: import the real application and
    see whether any scaffold module came with it.

    Run in a subprocess because the test session has already imported these
    modules — asking `sys.modules` in-process would answer a question about the
    test suite, not about the app.
    """
    probe = (
        "import sys; import app.main; "
        f"print(','.join(m for m in {SCAFFOLD_MODULES!r} if m in sys.modules))"
    )
    result = subprocess.run(  # noqa: S603 - literal in-repo probe, this interpreter
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=280,
        env=_boot_env(),
    )
    assert result.returncode == 0, result.stderr[-4000:]
    loaded = [name for name in result.stdout.strip().split(",") if name]
    assert not loaded, (
        f"booting app.main imported the Accounting scaffold: {loaded}. "
        "Preparation must not be on any startup path."
    )


def test_the_probe_would_notice_an_import() -> None:
    """Sensitivity proof for the check above (ADR-0018): it passes by finding an
    empty list, which is also what a broken probe returns.

    Deliberately the SAME harness — same interpreter, same environment, same
    `sys.modules` question — with only the thing being imported changed.  A
    proof run through a different harness proves something about that harness.
    """
    probe = (
        "import sys; import app.accounting_adoption; "
        f"print(','.join(m for m in {SCAFFOLD_MODULES!r} if m in sys.modules))"
    )
    result = subprocess.run(  # noqa: S603 - literal in-repo probe, this interpreter
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=280,
        env=_boot_env(),
    )
    assert result.returncode == 0, result.stderr[-4000:]
    assert "app.accounting_adoption" in result.stdout


def test_the_scaffold_defines_no_orm_table() -> None:
    """A model class anywhere in these files would join `Base.metadata` on
    import and be swept into every `create_all` and autogenerate diff — a
    schema change disguised as preparation."""
    for path, tree in _scaffold_sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "__tablename__"
                for target in node.targets
            ):
                raise AssertionError(f"{path} declares an ORM table")
            if isinstance(node, ast.ClassDef) and any(
                isinstance(base, ast.Name) and base.id == "Base" for base in node.bases
            ):
                raise AssertionError(f"{path} declares an ORM model")


def test_the_scaffold_registers_no_route_task_or_schedule() -> None:
    """No FastAPI router, no Celery task, no beat entry.

    A decorated function is registered by the mere act of importing its module,
    so "it is only called when we call it" stops being true the moment one of
    these decorators appears.
    """
    forbidden = {
        "shared_task",
        "task",
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "websocket",
        "on_event",
    }
    for path, tree in _scaffold_sources():
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                call = decorator.func if isinstance(decorator, ast.Call) else decorator
                name = (
                    call.attr
                    if isinstance(call, ast.Attribute)
                    else call.id
                    if isinstance(call, ast.Name)
                    else None
                )
                assert name not in forbidden, (
                    f"{path}::{node.name} is decorated with {name!r} — that "
                    "registers it at import time, which is not inert"
                )


def test_the_scaffold_creates_no_router_or_celery_object() -> None:
    """The decorator check above assumes the router exists somewhere else.
    Constructing one here would be the other half of the same mistake."""
    constructors = {"APIRouter", "Celery", "crontab"}
    for path, tree in _scaffold_sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    func.id
                    if isinstance(func, ast.Name)
                    else func.attr
                    if isinstance(func, ast.Attribute)
                    else None
                )
                assert name not in constructors, (
                    f"{path} constructs {name!r} at module scope or below"
                )


def test_the_scaffold_reads_exactly_one_environment_variable() -> None:
    """Behaviour that depends on an undocumented variable is not inert; it is
    inert on this machine.  One knob, named, with a prod-safe default."""
    reads: set[str] = set()
    for path, tree in _scaffold_sources():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            attr = func.attr if isinstance(func, ast.Attribute) else None
            if attr in {"getenv", "environ"} or (
                isinstance(func, ast.Name) and func.id == "getenv"
            ):
                if node.args and isinstance(node.args[0], ast.Constant):
                    reads.add(str(node.args[0].value))
                else:
                    raise AssertionError(f"{path} reads a non-literal env name")
    assert reads == ALLOWED_ENV_READS, (
        f"scaffold environment reads drifted: {sorted(reads)}; "
        f"expected exactly {sorted(ALLOWED_ENV_READS)}"
    )


#: Importing the scaffold with every outbound socket poisoned.  A module that
#: opened a database connection, called a secret store or reached any network at
#: import would raise `_NetworkTouched` and fail the run.  Poisoning the socket
#: rather than inspecting the engine means the check cannot pass because it
#: looked at the wrong attribute — it fails on the ACT, whatever performs it.
_NETWORK_PROBE = """
import socket, sys


class _NetworkTouched(RuntimeError):
    pass


def _forbidden(*args, **kwargs):
    raise _NetworkTouched("import-time network access")


socket.socket.connect = _forbidden
socket.socket.connect_ex = _forbidden
socket.create_connection = _forbidden
{body}
print("clean")
"""


def _run_probe(body: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - literal in-repo probe, this interpreter
        [sys.executable, "-c", _NETWORK_PROBE.format(body=body)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_importing_the_scaffold_reaches_no_network_or_database() -> None:
    """Import-time side effects are the classic way "just a declaration" turns
    into a connection attempt at boot."""
    result = _run_probe(
        "import app.accounting_adoption\n"
        "import app.services.finance.gl.accounting_shadow\n"
        "import app.services.finance.gl.accounting_backfill\n"
    )
    assert result.returncode == 0, (
        "importing the Accounting scaffold reached the network:\n"
        + result.stderr[-4000:]
    )
    assert result.stdout.strip().endswith("clean")


def test_the_network_probe_actually_bites() -> None:
    """Sensitivity proof (ADR-0018): the check above passes by NOT raising,
    which is also what a probe with a typo'd patch target does.  Prove the same
    harness fails when something really does connect."""
    result = _run_probe("import socket\nsocket.create_connection(('127.0.0.1', 9))\n")
    assert result.returncode != 0
    assert "_NetworkTouched" in result.stderr


def test_the_declaration_is_pure_data_and_functions() -> None:
    """`app/accounting_adoption.py` is the file most likely to grow behaviour.

    Its module body must stay imports, constants, one exception class and
    function definitions — no calls at module scope beyond building the
    constants themselves, because a call at module scope runs at import.
    """
    tree = ast.parse(
        (REPO_ROOT / "app/accounting_adoption.py").read_text(encoding="utf-8")
    )
    allowed = (
        ast.Import,
        ast.ImportFrom,
        ast.Assign,
        ast.AnnAssign,
        ast.ClassDef,
        ast.FunctionDef,
        ast.Expr,  # the module docstring
    )
    offenders = [
        type(node).__name__
        for node in tree.body
        if not isinstance(node, allowed)
        or (isinstance(node, ast.Expr) and not isinstance(node.value, ast.Constant))
    ]
    assert not offenders, (
        f"app/accounting_adoption.py has non-declarative statements: {offenders}"
    )


def test_the_flag_is_off_by_default_in_this_process() -> None:
    """The default is what every deployment gets until someone sets it."""
    module = importlib.import_module("app.accounting_adoption")
    assert module.COMPOSITION_ENABLED is False
    assert module.composition_state()["ready"] is False
