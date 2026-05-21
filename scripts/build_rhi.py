#!/usr/bin/env python3
"""RHI Nuitka build orchestrator.

Compiles protected Python modules file-by-file so package directories stay
importable in the hardened image. The default profile only compiles the small
licensing/error surface, which keeps the GitHub RHI build within the Actions
timeout while preserving ``app.services`` and ``app.models`` as source packages
for normal nested imports.

Nuitka cannot compile ``__init__.py`` directly in module mode. Package
``__init__.py`` files are therefore kept as package entry points, while regular
modules inside those packages are compiled and their source files are removed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEFAULT_PACKAGE_TREES = (
    "app.licensing",
)
FULL_PACKAGE_TREES = (
    "app.services",
    "app.models",
    "app.licensing",
)
SINGLE_MODULES = (
    "app.errors",
)
CONFLICTS = (
    ("app/services/audit.py", "app/services/audit"),
)


def dotted_to_path(root: Path, dotted_name: str) -> Path:
    return root.joinpath(*dotted_name.split("."))


def extension_outputs(py_file: Path) -> list[Path]:
    return sorted(py_file.parent.glob(f"{py_file.stem}*.so"))


def resolve_conflicts(project_root: Path) -> None:
    """Remove legacy files that collide with package directories."""
    for file_name, dir_name in CONFLICTS:
        legacy_file = project_root / file_name
        package_dir = project_root / dir_name
        if legacy_file.is_file() and package_dir.is_dir():
            print(
                f"Removing namespace conflict: {legacy_file.relative_to(project_root)} "
                f"because {package_dir.relative_to(project_root)}/ exists"
            )
            legacy_file.unlink()


def discover_python_files(
    project_root: Path,
    app_dir: Path,
    package_trees: tuple[str, ...],
) -> list[Path]:
    files: list[Path] = []

    for package in package_trees:
        package_dir = dotted_to_path(project_root, package)
        if not package_dir.is_dir():
            raise FileNotFoundError(f"Package directory not found: {package_dir}")
        files.extend(
            py_file
            for py_file in sorted(package_dir.rglob("*.py"))
            if py_file.name != "__init__.py"
        )

    for module in SINGLE_MODULES:
        module_file = dotted_to_path(project_root, module).with_suffix(".py")
        if not module_file.is_file():
            raise FileNotFoundError(f"Module file not found: {module_file}")
        files.append(module_file)

    app_root = app_dir.resolve()
    return sorted(
        {
            file.resolve()
            for file in files
            if file.resolve().is_relative_to(app_root)
        }
    )


def compile_file(py_file: Path, project_root: Path) -> Path:
    """Compile one source file in place and return the generated extension."""
    before = set(extension_outputs(py_file))
    rel_file = py_file.relative_to(project_root)
    print(f"\nCompiling {rel_file}")

    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--module",
        str(rel_file),
        f"--output-dir={py_file.parent}",
        "--remove-output",
        "--no-pyi-file",
    ]
    result = subprocess.run(cmd, cwd=project_root, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Nuitka failed for {rel_file}")

    after = set(extension_outputs(py_file))
    created = sorted(after - before)
    if created:
        so_file = created[-1]
    else:
        matches = extension_outputs(py_file)
        if not matches:
            raise RuntimeError(f"Nuitka reported success but no .so exists for {rel_file}")
        so_file = matches[-1]

    print(f"  -> {so_file.relative_to(project_root)}")
    return so_file


def remove_source(py_file: Path, so_file: Path, project_root: Path) -> None:
    if not so_file.is_file():
        raise RuntimeError(
            f"Refusing to remove {py_file.relative_to(project_root)}; "
            f"missing {so_file.relative_to(project_root)}"
        )
    py_file.unlink()
    print(f"  removed source {py_file.relative_to(project_root)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build RHI extension modules in place")
    parser.add_argument(
        "--project-root",
        default=".",
        help="Repository root containing app/ (default: current directory)",
    )
    parser.add_argument(
        "--app-dir",
        default="app",
        help="App directory relative to project root (default: app)",
    )
    parser.add_argument(
        "--keep-source",
        action="store_true",
        help="Compile in place but keep original .py files",
    )
    parser.add_argument(
        "--full-services-models",
        action="store_true",
        help=(
            "Compile app.services and app.models as well. This is much slower "
            "and can exceed the GitHub RHI timeout."
        ),
    )
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    app_dir = (project_root / args.app_dir).resolve()
    package_trees = FULL_PACKAGE_TREES if args.full_services_models else DEFAULT_PACKAGE_TREES

    print("DotMac ERP RHI Nuitka build")
    print(f"Project root: {project_root}")
    print("Mode: file-by-file in-place compilation")
    print(f"Package trees: {', '.join(package_trees)}")

    resolve_conflicts(project_root)
    py_files = discover_python_files(project_root, app_dir, package_trees)
    print(f"Python files selected: {len(py_files)}")

    compiled: list[tuple[Path, Path]] = []
    for py_file in py_files:
        compiled.append((py_file, compile_file(py_file, project_root)))

    if args.keep_source:
        print("\nKeeping source files because --keep-source was set")
    else:
        print("\nRemoving compiled source files")
        for py_file, so_file in compiled:
            remove_source(py_file, so_file, project_root)

    print("\nRHI Nuitka build complete")


if __name__ == "__main__":
    main()
