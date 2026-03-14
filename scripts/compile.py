#!/usr/bin/env python3
"""Nuitka compilation orchestrator for DotMac ERP hardened builds.

Compiles core business logic (services, models, licensing) into .so shared
libraries so that Python source is not shipped to on-premise customers.

Usage:
    python scripts/compile.py [--output-dir /build]

This script is called from Dockerfile.hardened stage 2 (nuitka-compiler).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# Packages to compile into .so files
COMPILE_PACKAGES = [
    "app.services",
    "app.models",
    "app.licensing",
    "app.errors",
]

# Files to keep as .py stubs (importable entry points)
KEEP_INIT_STUBS = True


def run_nuitka(package: str, output_dir: Path) -> None:
    """Compile a single package with Nuitka."""
    print(f"\n{'=' * 60}")
    print(f"Compiling: {package}")
    print(f"{'=' * 60}")

    # Nuitka --module expects a filesystem path (app/services), not dotted (app.services)
    package_path = package.replace(".", "/")
    if Path(package_path).is_dir():
        target = package_path
    elif Path(f"{package_path}.py").is_file():
        target = f"{package_path}.py"
    else:
        print(
            f"ERROR: Cannot find {package_path} or {package_path}.py", file=sys.stderr
        )
        sys.exit(1)

    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--module",
        target,
        f"--output-dir={output_dir}",
        "--remove-output",
        "--no-pyi-file",
    ]
    # --include-package only applies to directories, not single .py files
    if Path(package_path).is_dir():
        cmd.insert(-2, f"--include-package={package}")

    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print(f"ERROR: Nuitka compilation failed for {package}", file=sys.stderr)
        sys.exit(1)

    print(f"OK: {package} compiled successfully")


def remove_source(package: str, app_dir: Path) -> None:
    """Remove .py source files for a compiled package, keeping __init__.py stubs."""
    parts = package.split(".")
    pkg_dir = app_dir.parent
    for part in parts:
        pkg_dir = pkg_dir / part

    if not pkg_dir.is_dir():
        print(f"  Warning: {pkg_dir} not found, skipping source removal")
        return

    removed = 0
    for py_file in pkg_dir.rglob("*.py"):
        if KEEP_INIT_STUBS and py_file.name == "__init__.py":
            # Replace with minimal stub
            py_file.write_text(
                "# Compiled module — see .so\n",
                encoding="utf-8",
            )
            continue
        py_file.unlink()
        removed += 1

    print(f"  Removed {removed} .py files from {pkg_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compile DotMac ERP with Nuitka")
    parser.add_argument(
        "--output-dir",
        default="/build",
        help="Directory for compiled .so output (default: /build)",
    )
    parser.add_argument(
        "--app-dir",
        default="app",
        help="Path to the app/ directory (default: app)",
    )
    parser.add_argument(
        "--remove-source",
        action="store_true",
        default=True,
        help="Remove .py source after compilation (default: True)",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    app_dir = Path(args.app_dir)

    print("DotMac ERP — Nuitka Compilation")
    print(f"Output directory: {output_dir}")
    print(f"Packages to compile: {COMPILE_PACKAGES}")

    # Compile each package
    for package in COMPILE_PACKAGES:
        run_nuitka(package, output_dir)

    # Copy .so files to app directory
    print(f"\n{'=' * 60}")
    print("Copying compiled .so files into app tree")
    print(f"{'=' * 60}")

    for so_file in output_dir.glob("*.so"):
        dest = app_dir.parent / so_file.name
        shutil.copy2(so_file, dest)
        print(f"  {so_file.name} -> {dest}")

    # Remove source files
    if args.remove_source:
        print(f"\n{'=' * 60}")
        print("Removing Python source for compiled packages")
        print(f"{'=' * 60}")
        for package in COMPILE_PACKAGES:
            remove_source(package, app_dir)

    print(f"\n{'=' * 60}")
    print("Compilation complete")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
