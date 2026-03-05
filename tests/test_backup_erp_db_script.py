from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts/backup_erp_db.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_backup_script_prunes_old_remote_backups(tmp_path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    local_dir = tmp_path / "local"
    remote_root = tmp_path / "remote"
    remote_dir = remote_root / "db.backup" / "dotmac_erp"
    remote_dir.mkdir(parents=True)

    for idx in range(1, 7):
        (remote_dir / f"dotmac_erp_2024010{idx}_010101.sql.gz").write_text(
            f"old-{idx}",
            encoding="utf-8",
        )

    docker_script = """#!/usr/bin/env python3
import sys

sys.stdout.write("-- fake pg_dump output\\n")
"""
    _write_executable(fake_bin / "docker", docker_script)

    rclone_script = f"""#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

REMOTE_ROOT = Path({str(remote_root)!r})


def resolve_remote(remote_path: str) -> Path:
    _, _, relative = remote_path.partition(":")
    relative = relative.lstrip("/")
    return REMOTE_ROOT / relative


def main() -> int:
    args = sys.argv[1:]
    if not args:
        return 1

    command = args[0]
    if command == "copy":
        src = Path(args[1])
        dest = resolve_remote(args[2])
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest / src.name)
        return 0

    if command == "lsf":
        dest = resolve_remote(args[1])
        entries = sorted(
            p.name for p in dest.iterdir() if p.is_file()
        )
        for name in entries:
            print(f"2024-01-01 00:00:00|{{name}}")
        return 0

    if command == "deletefile":
        target = resolve_remote(args[1])
        target.unlink()
        return 0

    raise SystemExit(f"unsupported rclone invocation: {{args}}")


raise SystemExit(main())
"""
    _write_executable(fake_bin / "rclone", rclone_script)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["LOCAL_DIR"] = str(local_dir)
    env["REMOTE"] = "Backup:db.backup"
    env["REMOTE_DIR"] = "Backup:db.backup/dotmac_erp"
    env["KEEP_LAST"] = "5"
    env["PGPASSWORD"] = "test-password"

    subprocess.run(  # noqa: S603
        [str(SCRIPT_PATH)],
        check=True,
        cwd=str(REPO_ROOT),
        env=env,
    )

    remote_files = sorted(path.name for path in remote_dir.iterdir())

    assert len(remote_files) == 5
    assert "dotmac_erp_20240101_010101.sql.gz" not in remote_files
    assert any(name.startswith("dotmac_erp_") for name in remote_files)


def test_backup_script_loads_postgres_password_from_env_file(tmp_path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    local_dir = tmp_path / "local"
    remote_root = tmp_path / "remote"
    remote_dir = remote_root / "db.backup" / "dotmac_erp"
    remote_dir.mkdir(parents=True)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "POSTGRES_PASSWORD=env-file-password\n",
        encoding="utf-8",
    )

    docker_script = """#!/usr/bin/env python3
import sys

if "-e" not in sys.argv:
    raise SystemExit("docker exec did not receive -e")

env_arg = sys.argv[sys.argv.index("-e") + 1]
if env_arg != "PGPASSWORD=env-file-password":
    raise SystemExit(f"unexpected docker env argument: {env_arg}")

sys.stdout.write("-- fake pg_dump output\\n")
"""
    _write_executable(fake_bin / "docker", docker_script)

    rclone_script = f"""#!/usr/bin/env python3
import shutil
import sys
from pathlib import Path

REMOTE_ROOT = Path({str(remote_root)!r})


def resolve_remote(remote_path: str) -> Path:
    _, _, relative = remote_path.partition(":")
    relative = relative.lstrip("/")
    return REMOTE_ROOT / relative


def main() -> int:
    args = sys.argv[1:]
    if args[0] == "copy":
        src = Path(args[1])
        dest = resolve_remote(args[2])
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest / src.name)
        return 0
    if args[0] == "lsf":
        return 0
    raise SystemExit(f"unsupported rclone invocation: {{args}}")


raise SystemExit(main())
"""
    _write_executable(fake_bin / "rclone", rclone_script)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["LOCAL_DIR"] = str(local_dir)
    env["REMOTE"] = "Backup:db.backup"
    env["REMOTE_DIR"] = "Backup:db.backup/dotmac_erp"
    env["KEEP_LAST"] = "5"
    env["ENV_FILE"] = str(env_file)
    env.pop("PGPASSWORD", None)
    env.pop("POSTGRES_PASSWORD", None)

    subprocess.run(  # noqa: S603
        [str(SCRIPT_PATH)],
        check=True,
        cwd=str(REPO_ROOT),
        env=env,
    )
