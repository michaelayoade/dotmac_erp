#!/usr/bin/env python3
"""Validate release PR version and changelog discipline.

This script is intentionally dependency-free so it can run in GitHub Actions
without installing the app.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


RELEASE_FILES = {
    "pyproject.toml",
    "package.json",
    "package-lock.json",
    "CHANGELOG.md",
}
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
PYPROJECT_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


def fail(message: str) -> None:
    print(f"release-check: {message}", file=sys.stderr)
    raise SystemExit(1)


def run_git(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout


def changed_files(base_ref: str, head_ref: str) -> set[str]:
    if head_ref == "WORKTREE":
        output = run_git(["diff", "--name-only", base_ref])
    else:
        output = run_git(["diff", "--name-only", f"{base_ref}...{head_ref}"])
    return {line.strip() for line in output.splitlines() if line.strip()}


def read_file_at(ref: str, path: str) -> str:
    return run_git(["show", f"{ref}:{path}"])


def read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def pyproject_version(text: str) -> str:
    match = PYPROJECT_VERSION_RE.search(text)
    if not match:
        fail("could not find [tool.poetry] version in pyproject.toml")
    return match.group(1)


def package_version(path: str) -> str:
    data = json.loads(read_text(path))
    version = data.get("version")
    if not isinstance(version, str):
        fail(f"{path} does not contain a string top-level version")
    return version


def package_lock_versions(path: str) -> tuple[str, str]:
    data = json.loads(read_text(path))
    root_version = data.get("version")
    package_version_value = data.get("packages", {}).get("", {}).get("version")
    if not isinstance(root_version, str):
        fail(f"{path} does not contain a string top-level version")
    if not isinstance(package_version_value, str):
        fail(f"{path} does not contain packages[''].version")
    return root_version, package_version_value


def semver_tuple(version: str) -> tuple[int, int, int]:
    if not SEMVER_RE.fullmatch(version):
        fail(f"version must use MAJOR.MINOR.PATCH semver, got {version!r}")
    return tuple(int(part) for part in version.split("."))  # type: ignore[return-value]


def ensure_release_branch_matches(head_ref: str, version: str) -> None:
    if not head_ref.startswith("release/"):
        return
    branch_version = head_ref.removeprefix("release/").removeprefix("v")
    if (
        branch_version
        and SEMVER_RE.fullmatch(branch_version)
        and branch_version != version
    ):
        fail(
            f"release branch version {branch_version!r} does not match "
            f"metadata version {version!r}"
        )


def changelog_has_release(version: str) -> bool:
    changelog = read_text("CHANGELOG.md")
    pattern = re.compile(
        rf"^## \[{re.escape(version)}\](?: - \d{{4}}-\d{{2}}-\d{{2}})?\s*$",
        re.MULTILINE,
    )
    return bool(pattern.search(changelog))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--head-ref", default="")
    parser.add_argument("--head-sha", default="HEAD")
    args = parser.parse_args()

    changed = changed_files(args.base_ref, args.head_sha)
    missing = sorted(RELEASE_FILES - changed)
    if missing:
        fail(
            "release PRs must update every release file; missing changes in: "
            + ", ".join(missing)
        )

    py_version = pyproject_version(read_text("pyproject.toml"))
    package_json_version = package_version("package.json")
    lock_root_version, lock_package_version = package_lock_versions("package-lock.json")

    versions = {
        "pyproject.toml": py_version,
        "package.json": package_json_version,
        "package-lock.json": lock_root_version,
        "package-lock.json packages['']": lock_package_version,
    }
    unique_versions = set(versions.values())
    if len(unique_versions) != 1:
        details = ", ".join(f"{name}={version}" for name, version in versions.items())
        fail(f"release versions must match across metadata files: {details}")

    version = py_version
    current_tuple = semver_tuple(version)
    base_version = pyproject_version(read_file_at(args.base_ref, "pyproject.toml"))
    base_tuple = semver_tuple(base_version)
    if current_tuple <= base_tuple:
        fail(
            f"release version {version} must be greater than base version "
            f"{base_version}"
        )

    ensure_release_branch_matches(args.head_ref, version)

    if not changelog_has_release(version):
        fail(f"CHANGELOG.md must include a release heading for [{version}]")

    print(f"release-check: release PR metadata is valid for v{version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
