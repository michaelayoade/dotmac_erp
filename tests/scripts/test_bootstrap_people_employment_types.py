from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from app.services.people.hr.employment_type_bootstrap import (
    BootstrapMode,
    EmploymentTypeBootstrapResult,
)
from scripts import bootstrap_people_employment_types as cli

ORG_ID = UUID("00000000-0000-0000-0000-000000000101")


class _Db:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _result(mode: BootstrapMode) -> EmploymentTypeBootstrapResult:
    return EmploymentTypeBootstrapResult(
        organization_id=ORG_ID,
        tenant_id=ORG_ID,
        mode=mode,
        source_count=1,
        target_before_count=0,
        target_after_count=1,
        created=1,
        updated=0,
        unchanged=0,
        source_fingerprint_set_digest="sha256:" + "a" * 64,
        target_before_fingerprint_set_digest="sha256:" + "b" * 64,
        target_after_fingerprint_set_digest="sha256:" + "c" * 64,
    )


def _run(monkeypatch: pytest.MonkeyPatch, mode_flag: str) -> _Db:
    db = _Db()

    def organizations(**kwargs):
        assert kwargs == {"include_inactive": True, "only": ORG_ID}
        yield ORG_ID, db

    class Service:
        def __init__(self, observed_db, *, organization_id):
            assert observed_db is db
            assert organization_id == ORG_ID

        def execute(self, *, mode, page_size):
            assert page_size == 200
            return _result(mode)

    monkeypatch.setattr(cli, "for_each_organization", organizations)
    monkeypatch.setattr(cli, "EmploymentTypeBootstrapService", Service)
    assert cli.main(["--organization-id", str(ORG_ID), mode_flag]) == 0
    return db


def test_cli_requires_exactly_one_mode() -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--organization-id", str(ORG_ID)])
    with pytest.raises(SystemExit):
        parser.parse_args(["--organization-id", str(ORG_ID), "--dry-run", "--commit"])


def test_dry_run_rolls_back_and_never_commits(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _run(monkeypatch, "--dry-run")
    assert (db.commits, db.rollbacks) == (0, 1)
    assert '"committed":false' in capsys.readouterr().out


@pytest.mark.parametrize("mode_flag", ["--commit", "--replay"])
def test_write_modes_commit_without_rollback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode_flag: str,
) -> None:
    db = _run(monkeypatch, mode_flag)
    assert (db.commits, db.rollbacks) == (1, 0)
    assert '"committed":true' in capsys.readouterr().out


def test_failure_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _Db()

    def organizations(**kwargs):
        yield ORG_ID, db

    service = SimpleNamespace(
        execute=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("changed"))
    )
    monkeypatch.setattr(cli, "for_each_organization", organizations)
    monkeypatch.setattr(
        cli, "EmploymentTypeBootstrapService", lambda *args, **kwargs: service
    )
    with pytest.raises(RuntimeError, match="changed"):
        cli.main(["--organization-id", str(ORG_ID), "--commit"])
    assert (db.commits, db.rollbacks) == (0, 1)
