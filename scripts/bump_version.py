#!/usr/bin/env python3
"""Bump Dotmac ERP's app version consistently.

Usage:
    python scripts/bump_version.py fix: asset list loading
    python scripts/bump_version.py feat: add depreciation report
    python scripts/bump_version.py major: change asset numbering contract
    python scripts/bump_version.py patch
    python scripts/bump_version.py minor --dry-run
    python scripts/bump_version.py --check
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

TEXT_FILES = (
    Path("pyproject.toml"),
    Path("app/config.py"),
    Path(".env.example"),
    Path("docker-compose.yml"),
)
JSON_FILES = (
    Path("package.json"),
    Path("package-lock.json"),
)


def _read(path: Path) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _write(path: Path, content: str, *, dry_run: bool) -> None:
    if dry_run:
        return
    (ROOT / path).write_text(content, encoding="utf-8")


def _replace_once(path: Path, pattern: str, repl: str, *, dry_run: bool) -> None:
    content = _read(path)
    updated, count = re.subn(pattern, repl, content, count=1, flags=re.MULTILINE)
    if count != 1:
        raise RuntimeError(f"Expected one version match in {path}, found {count}")
    _write(path, updated, dry_run=dry_run)


def current_versions() -> dict[str, str]:
    versions: dict[str, str] = {}

    pyproject = _read(Path("pyproject.toml"))
    match = re.search(r'^version\s*=\s*"(\d+\.\d+\.\d+)"$', pyproject, re.MULTILINE)
    if not match:
        raise RuntimeError("Could not read version from pyproject.toml")
    versions["pyproject.toml"] = match.group(1)

    package = json.loads(_read(Path("package.json")))
    versions["package.json"] = package["version"]

    package_lock = json.loads(_read(Path("package-lock.json")))
    versions["package-lock.json"] = package_lock["version"]
    versions['package-lock.json packages[""]'] = package_lock["packages"][""]["version"]

    config = _read(Path("app/config.py"))
    match = re.search(
        r'app_version:\s*str\s*=\s*os\.getenv\("APP_VERSION",\s*"(\d+\.\d+\.\d+)"\)',
        config,
    )
    if not match:
        raise RuntimeError("Could not read version from app/config.py")
    versions["app/config.py"] = match.group(1)

    env_example = _read(Path(".env.example"))
    match = re.search(r"^APP_VERSION=(\d+\.\d+\.\d+)$", env_example, re.MULTILINE)
    if not match:
        raise RuntimeError("Could not read version from .env.example")
    versions[".env.example"] = match.group(1)

    compose = _read(Path("docker-compose.yml"))
    match = re.search(r"APP_VERSION:\s*\$\{APP_VERSION:-(\d+\.\d+\.\d+)\}", compose)
    if not match:
        raise RuntimeError("Could not read version from docker-compose.yml")
    versions["docker-compose.yml"] = match.group(1)

    return versions


def ensure_versions_match() -> str:
    versions = current_versions()
    unique = sorted(set(versions.values()))
    if len(unique) != 1:
        details = "\n".join(
            f"  {path}: {version}" for path, version in versions.items()
        )
        raise RuntimeError(f"Version files are out of sync:\n{details}")
    return unique[0]


def infer_bump(value: str) -> str:
    normalized = value.strip()
    lowered = normalized.lower()
    if not normalized:
        return "patch"
    if (
        "breaking change" in lowered
        or lowered.startswith("breaking:")
        or lowered.startswith("major:")
    ):
        return "major"
    if lowered.startswith("feat:") or lowered.startswith("feature:"):
        return "minor"
    if lowered.startswith(("fix:", "bug:", "hotfix:", "patch:")):
        return "patch"
    if lowered in {"patch", "minor", "major"}:
        return lowered
    return "patch"


def bump_version(version: str, bump: str) -> str:
    if not VERSION_RE.match(version):
        raise ValueError(f"Invalid version: {version}")
    major, minor, patch = (int(part) for part in version.split("."))
    if bump == "patch":
        patch += 1
    elif bump == "minor":
        minor += 1
        patch = 0
    elif bump == "major":
        major += 1
        minor = 0
        patch = 0
    else:
        raise ValueError(f"Invalid bump: {bump}")
    return f"{major}.{minor}.{patch}"


def update_versions(new_version: str, *, dry_run: bool) -> None:
    if not VERSION_RE.match(new_version):
        raise ValueError(f"Invalid version: {new_version}")

    _replace_once(
        Path("pyproject.toml"),
        r'^(version\s*=\s*")\d+\.\d+\.\d+(")$',
        rf"\g<1>{new_version}\2",
        dry_run=dry_run,
    )
    _replace_once(
        Path("app/config.py"),
        r'(app_version:\s*str\s*=\s*os\.getenv\("APP_VERSION",\s*")\d+\.\d+\.\d+("\))',
        rf"\g<1>{new_version}\2",
        dry_run=dry_run,
    )
    _replace_once(
        Path(".env.example"),
        r"^(APP_VERSION=)\d+\.\d+\.\d+$",
        rf"\g<1>{new_version}",
        dry_run=dry_run,
    )
    _replace_once(
        Path("docker-compose.yml"),
        r"(APP_VERSION:\s*\$\{APP_VERSION:-)\d+\.\d+\.\d+(\})",
        rf"\g<1>{new_version}\2",
        dry_run=dry_run,
    )

    for path in JSON_FILES:
        full_path = ROOT / path
        data = json.loads(full_path.read_text(encoding="utf-8"))
        data["version"] = new_version
        if path.name == "package-lock.json":
            data["packages"][""]["version"] = new_version
        if not dry_run:
            full_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "label",
        nargs="*",
        help="Bump type or label/message, e.g. patch, 'fix: asset import'",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only verify all version files currently match.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the proposed bump without writing files.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        current = ensure_versions_match()
        if args.check:
            print(f"Version files are in sync: {current}")
            return 0

        label = " ".join(args.label)
        bump = infer_bump(label)
        new_version = bump_version(current, bump)
        update_versions(new_version, dry_run=args.dry_run)
        action = "Would bump" if args.dry_run else "Bumped"
        print(f"{action} version: {current} -> {new_version} ({bump})")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
