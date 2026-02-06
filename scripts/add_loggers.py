#!/usr/bin/env python3
"""Add missing logger setup to service files using AST for safe insertion.

Uses Python's ast module to find the exact end of the import block,
avoiding corruption of multi-line imports.
"""

import ast
import os
import re
import sys


def needs_logger(filepath: str) -> bool:
    """Check if a file needs a logger added."""
    if filepath.endswith("__init__.py"):
        return False

    with open(filepath, "r") as f:
        content = f.read()

    # Skip files that already have a logger
    if re.search(r"^logger\s*=\s*logging\.getLogger", content, re.MULTILINE):
        return False

    # Only process files that define classes (actual services)
    if not re.search(r"^class\s+\w+", content, re.MULTILINE):
        return False

    return True


def find_last_import_end_line(filepath: str) -> int | None:
    """Use AST to find the line number AFTER the last import statement."""
    with open(filepath, "r") as f:
        source = f.read()

    try:
        tree = ast.parse(source, filepath)
    except SyntaxError:
        return None

    last_import_end = None
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last_import_end = node.end_lineno
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            # Skip module docstrings
            continue
        elif isinstance(node, ast.Assign):
            # Could be __future__ or similar top-level assignment after imports
            # Only continue if before any class/function
            continue
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            break  # Stop at first class/function definition

    return last_import_end


def has_logging_import(content: str) -> bool:
    """Check if file already imports logging."""
    return bool(re.search(r"^import logging\b", content, re.MULTILINE))


def add_logger(filepath: str, dry_run: bool = False) -> bool:
    """Add logger to a file. Returns True if modified."""
    last_import_line = find_last_import_end_line(filepath)
    if last_import_line is None:
        return False

    with open(filepath, "r") as f:
        lines = f.readlines()

    content = "".join(lines)
    already_imports_logging = has_logging_import(content)

    if dry_run:
        return True

    # Build insertion: logging import (if needed) + logger line
    insert_lines = []

    if not already_imports_logging:
        # Find where to put `import logging` — ideally with stdlib imports
        # For simplicity, just add it right before the logger assignment
        insert_lines.append("import logging\n")

    insert_lines.append("\n")
    insert_lines.append("logger = logging.getLogger(__name__)\n")

    # Insert after the last import line
    insert_at = last_import_line  # 0-indexed: last_import_line is 1-based end

    # Check if there's already a blank line after imports
    if insert_at < len(lines) and lines[insert_at].strip() == "":
        # There's a blank line — insert after it
        insert_at += 1
        # Remove leading \n from our insertion since blank line exists
        if insert_lines[0] == "\n":
            insert_lines = insert_lines[1:]
        elif not already_imports_logging:
            # We have "import logging\n" first, keep the \n after it
            pass

    # Add trailing blank line if next line isn't blank
    if insert_at < len(lines) and lines[insert_at].strip() != "":
        insert_lines.append("\n")

    for j, new_line in enumerate(insert_lines):
        lines.insert(insert_at + j, new_line)

    with open(filepath, "w") as f:
        f.writelines(lines)

    return True


def main():
    dry_run = "--dry-run" in sys.argv
    services_dir = os.path.join(os.path.dirname(__file__), "..", "app", "services")
    services_dir = os.path.abspath(services_dir)

    modified = 0
    skipped = 0
    errors = []

    for root, dirs, files in os.walk(services_dir):
        for fname in sorted(files):
            if not fname.endswith(".py"):
                continue

            filepath = os.path.join(root, fname)
            rel_path = os.path.relpath(filepath, os.path.join(services_dir, ".."))

            if not needs_logger(filepath):
                skipped += 1
                continue

            try:
                if add_logger(filepath, dry_run=dry_run):
                    modified += 1
                    if not dry_run:
                        print(f"Added logger: {rel_path}")
                else:
                    skipped += 1
            except Exception as e:
                errors.append((rel_path, str(e)))
                print(f"ERROR: {rel_path}: {e}")

    print(f"\nDone: {modified} modified, {skipped} skipped, {len(errors)} errors")


if __name__ == "__main__":
    main()
