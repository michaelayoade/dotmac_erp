"""No credential literal is committed to a tracked Python file.

Four files were, until 2026-08-11:

  - `scripts/import_uba_statements.py` carried the UBA statement-workbook
    passwords in `ACCOUNT_CONFIG`, beside the account numbers they belonged
    to, since commit `2dc8b05e`.
  - `scripts/archive/resync_purchase_invoices.py` carried `password="root"`
    for the ERPNext MariaDB dump container.
  - `scripts/capture_all_pages.py` and `scripts/retry_failed_pages.py`
    carried `USERNAME = "admin"` / `PASSWORD = "admin123"` together with the
    `BASE_URL` of the host to use them against.

The first two were bounded — the UBA passwords are derivable from the
account number printed beside them, and the MariaDB one addressed a local
throwaway container. **The screenshot scripts were not**: a working admin
login is not derivable from anything, and the target host sat on the line
above it.

The ruling was to scrub forward without rewriting history, which only holds
if nothing walks it back. That is what this test is for. The point is not
that every finding was severe; it is that three separate reviewers read
these files and none of them was looking.

## Why a literal and not an entropy heuristic

A secret scanner that guesses at entropy fires on hashes, UUIDs and base64
fixtures, gets muted, and then catches nothing. This checks something
narrower and decidable instead: a credential-named KEYWORD assigned a
literal string. `password="root"` fails; `password=args.password`,
`os.environ["X"]` and a type annotation all pass, because none of them puts
a value in the file.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Names that mean "this value is a credential". Deliberately short and
# explicit rather than a fuzzy pattern.
_CREDENTIAL_NAMES = {
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_key",
    "secret_key",
    "private_key",
    "client_secret",
}

_SCAN_ROOTS = ("app", "scripts", "alembic")

# Values that name a credential without being one. A placeholder that is
# obviously not a working secret is not what this test is about.
_NOT_A_SECRET = {"", "...", "changeme", "<redacted>", "REDACTED", "xxx"}


def _is_self_naming(name: str, value: str) -> bool:
    """`api_key = "api_key"` and `"api_key": "API Key"` name a credential
    KIND, they do not carry one. An enum member and a display label are the
    two forms this shows up in, and both are legitimate."""
    return value.strip().lower().replace(" ", "_") == name.strip().lower()


def _literal_credentials() -> list[str]:
    """Every `<credential-name> = "<literal>"` in tracked Python.

    Covers three syntactic forms, because the two real findings used two of
    them: a dict entry (`"password": "89046"`), a keyword argument
    (`password="root"`), and a plain assignment.
    """
    findings: list[str] = []
    for root in _SCAN_ROOTS:
        base = REPO_ROOT / root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            relative = path.relative_to(REPO_ROOT).as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover - not this test's job
                continue
            for node in ast.walk(tree):
                for name, value, line in _credential_assignments(node):
                    if (
                        name.lower() in _CREDENTIAL_NAMES
                        and value not in _NOT_A_SECRET
                        and not _is_self_naming(name, value)
                    ):
                        findings.append(f"{relative}:{line}  {name}=<literal>")
    return findings


def _credential_assignments(node: ast.AST):
    """Yield (name, literal_value, lineno) for the three forms that matter."""
    # `password="root"` — a keyword argument
    if isinstance(node, ast.Call):
        for keyword in node.keywords:
            if keyword.arg and isinstance(keyword.value, ast.Constant):
                if isinstance(keyword.value.value, str):
                    yield keyword.arg, keyword.value.value, keyword.value.lineno
    # `{"password": "89046"}` — a dict entry
    elif isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values, strict=False):
            if (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                yield key.value, value.value, value.lineno
    # `PASSWORD = "hunter2"` — a plain assignment
    elif isinstance(node, ast.Assign):
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    yield target.id, node.value.value, node.lineno


def test_no_credential_literal_is_committed():
    findings = _literal_credentials()
    assert findings == [], (
        "A credential literal is committed to a tracked file. Read it from the "
        "environment instead, sourced from OpenBao, with NO default — a silent "
        "fallback turns a missing credential into a confusing downstream "
        "failure:\n  " + "\n  ".join(findings)
    )


def test_the_detector_actually_fires():
    """Sensitivity proof. A clean run must mean "nothing found", not "the
    check stopped looking" — which is how the two real findings survived
    every prior review."""
    forms = [
        'pymysql.connect(user="root", password="root")',  # keyword argument
        'CONFIG = {"password": "89046"}',  # dict entry
        'SECRET_KEY = "abc123"',  # plain assignment
    ]
    for source in forms:
        tree = ast.parse(source)
        hits = [
            name
            for node in ast.walk(tree)
            for name, value, _ in _credential_assignments(node)
            if name.lower() in _CREDENTIAL_NAMES and value not in _NOT_A_SECRET
        ]
        assert hits, f"detector missed: {source}"


def test_the_detector_does_not_fire_on_a_reference():
    """The point is that no VALUE is in the file. Passing one around is fine,
    and a check that cannot tell the difference gets muted."""
    clean = [
        'AuthMethod = "api_key"',  # an enum member naming a credential KIND
        '{"api_key": "API Key"}',  # a display label
        "connect(password=os.environ['DB_PASSWORD'])",
        "connect(password=args.password)",
        "connect(password=_statement_password(account_number))",
        'connect(password="")',
    ]
    for source in clean:
        tree = ast.parse(source)
        hits = [
            name
            for node in ast.walk(tree)
            for name, value, _ in _credential_assignments(node)
            if name.lower() in _CREDENTIAL_NAMES
            and value not in _NOT_A_SECRET
            and not _is_self_naming(name, value)
        ]
        assert not hits, f"false positive on: {source}"
