"""External ticket lifecycles never acquire a second owner in ERP.

Dotmac Sub owns customer and field-operations ticket state. ERP may keep
locally-created internal tickets during the module cutover, but external
adapters may only retain opaque correlation; they may not build a ticket,
timeline, mapping, or compatibility projection here.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.schemas.sync.dotmac_sub import (
    BulkSyncRequest,
    SubExpenseClaimPayload,
    SubMaterialRequestPayload,
    SubProjectTaskPayload,
    SubWorkOrderPayload,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE = Path(__file__).with_name("ticket_status_legacy.txt")
LOCAL_OWNER = "app/services/support/ticket.py"
MODEL = "app/models/support/ticket.py"
MODEL_EXPORT = "app/models/support/__init__.py"
EXTERNAL_ADAPTER_PATHS = (
    REPO_ROOT / "app/services/crm",
    REPO_ROOT / "app/services/sync/crm",
    REPO_ROOT / "app/api/crm.py",
    REPO_ROOT / "app/api/sync/dotmac_crm.py",
    REPO_ROOT / "app/api/sync/dotmac_sub.py",
    REPO_ROOT / "app/tasks/crm.py",
)
FORBIDDEN_EXTERNAL_TICKET_NAMES = frozenset(
    {
        "CRMTicketActivityEntry",
        "CRMTicketCommentItem",
        "CRMTicketPayload",
        "CRMTicketRead",
        "TicketProjectionService",
        "TicketSyncService",
        "get_ticket",
        "get_ticket_comments",
        "get_ticket_sla_events",
        "get_tickets",
        "get_local_ticket_id",
        "link_tickets_to_project",
        "list_crm_tickets",
        "list_tickets",
        "sync_crm_tickets",
        "sync_ticket",
    }
)
RETIRED_WIRE_FIELDS = {
    BulkSyncRequest: "tickets",
    SubProjectTaskPayload: "ticket_source_id",
    SubWorkOrderPayload: "ticket_crm_id",
    SubMaterialRequestPayload: "ticket_crm_id",
    SubExpenseClaimPayload: "ticket_crm_id",
}


def _python_files() -> list[Path]:
    return sorted((REPO_ROOT / "app").rglob("*.py"))


def _ticket_status_import_count(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return sum(
        alias.name == "TicketStatus"
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "app.models.support.ticket"
        for alias in node.names
    )


def _current_legacy_imports() -> dict[str, int]:
    current: dict[str, int] = {}
    for path in _python_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in {LOCAL_OWNER, MODEL, MODEL_EXPORT}:
            continue
        count = _ticket_status_import_count(path)
        if count:
            current[relative] = count
    return current


def _read_baseline() -> dict[str, int]:
    entries: dict[str, int] = {}
    for raw_line in BASELINE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        path, count = line.rsplit(" ", 1)
        entries[path] = int(count)
    return entries


def _ticket_lifecycle_writes(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Attribute)
            and target.attr == "status"
            and isinstance(target.value, ast.Name)
            and "ticket" in target.value.id
            for target in node.targets
        ):
            hits.append(node.lineno)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "Ticket":
                if any(keyword.arg == "status" for keyword in node.keywords):
                    hits.append(node.lineno)
            if isinstance(node.func, ast.Name) and node.func.id == "setattr":
                if len(node.args) >= 2:
                    target, field = node.args[:2]
                    if (
                        isinstance(target, ast.Name)
                        and "ticket" in target.id
                        and (
                            not isinstance(field, ast.Constant)
                            or field.value == "status"
                        )
                    ):
                        hits.append(node.lineno)
    return sorted(set(hits))


def _external_ticket_symbols(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_EXTERNAL_TICKET_NAMES:
            hits.add((node.id, node.lineno))
        elif (
            isinstance(node, ast.Attribute)
            and node.attr in FORBIDDEN_EXTERNAL_TICKET_NAMES
        ):
            hits.add((node.attr, node.lineno))
        elif (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name in FORBIDDEN_EXTERNAL_TICKET_NAMES
        ):
            hits.add((node.name, node.lineno))
    return sorted(hits)


def _external_adapter_files() -> list[Path]:
    files: list[Path] = []
    for path in EXTERNAL_ADAPTER_PATHS:
        files.extend(sorted(path.rglob("*.py")) if path.is_dir() else [path])
    return files


def test_ticket_status_legacy_baseline_moves_in_both_directions() -> None:
    assert _current_legacy_imports() == _read_baseline()


def test_ticket_service_is_the_only_temporary_local_lifecycle_writer() -> None:
    offenders: list[str] = []
    for path in _python_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in {LOCAL_OWNER, MODEL}:
            continue
        offenders.extend(
            f"{relative}:{line}" for line in _ticket_lifecycle_writes(path)
        )
    assert offenders == [], "ticket lifecycle has a second writer: " + repr(offenders)


def test_external_adapters_have_no_ticket_projection_runtime() -> None:
    offenders: list[str] = []
    for path in _external_adapter_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        offenders.extend(
            f"{relative}:{line} ({name})"
            for name, line in _external_ticket_symbols(path)
        )
    assert offenders == [], "external ticket runtime returned:\n  " + "\n  ".join(
        offenders
    )


def test_retired_ticket_wire_fields_fail_closed() -> None:
    for schema, field in RETIRED_WIRE_FIELDS.items():
        assert field not in schema.model_fields
        assert schema.model_config.get("extra") == "forbid"


def test_ticket_model_has_no_external_provider_provenance() -> None:
    source = (REPO_ROOT / MODEL).read_text(encoding="utf-8")
    for forbidden in (
        "ERPNextSyncMixin",
        "erpnext_id",
        "last_synced_at",
        "observed_status",
        "source_record_id",
        "source_system",
    ):
        assert forbidden not in source


def test_external_work_references_are_opaque_and_exclusive() -> None:
    for model_path in (
        "app/models/expense/expense_claim.py",
        "app/models/people/exp/expense_claim.py",
        "app/models/finance/exp/expense_entry.py",
    ):
        source = (REPO_ROOT / model_path).read_text(encoding="utf-8")
        assert "external_work_reference" in source
        assert "one_work_reference" in source


def test_retirement_migration_archives_before_deleting_external_tickets() -> None:
    source = (
        REPO_ROOT / "alembic/versions/20260825_retire_external_tickets.py"
    ).read_text(encoding="utf-8")
    assert source.index("INSERT INTO archive.retired_external_ticket") < source.index(
        "DELETE FROM support.ticket"
    )
    assert "expense_entry" in source
    assert "external_work_reference" in source
    assert "REVOKE ALL" in source
    assert 'op.drop_column("ticket", "erpnext_id"' in source


def test_external_ticket_detector_is_sensitive(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(
        "def sync_ticket(value):\n"
        "    TicketSyncService(value)\n"
        "    list_crm_tickets(value)\n",
        encoding="utf-8",
    )
    assert _external_ticket_symbols(probe) == [
        ("TicketSyncService", 2),
        ("list_crm_tickets", 3),
        ("sync_ticket", 1),
    ]


def test_local_lifecycle_detector_is_sensitive(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(
        "def write(ticket, value):\n"
        "    ticket.status = value\n"
        "    Ticket(status=value)\n"
        "    setattr(ticket, 'status', value)\n",
        encoding="utf-8",
    )
    assert _ticket_lifecycle_writes(probe) == [2, 3, 4]
