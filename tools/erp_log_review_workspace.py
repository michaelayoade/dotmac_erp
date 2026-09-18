"""Temporary, branch-scoped implementation driver; never included in fix PRs.

Uses immutable reviewed source, exact-match edits, the repository's Poetry
installation and ordinary non-force pushes. It does not merge or deploy.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import textwrap

BASE = "25b319a04cf1b1909eae83c87988fe196e805349"
ROOT = Path.cwd().resolve()
RECIPES = Path(__file__).resolve().parent / "erp_log_review_fixes"


def run(*args, cwd=ROOT, check=True, env=None):
    result = subprocess.run(
        args, cwd=cwd, check=check, text=True, capture_output=True, env=env
    )
    if result.stdout:
        print(result.stdout, end="", flush=True)
    if result.stderr:
        print(result.stderr, end="", flush=True)
    return result


def inspect_owners():
    for path in sorted((ROOT / "app").rglob("*.py")):
        source = path.read_text()
        if "EmailProfile" not in source:
            continue
        lines = source.splitlines()
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = "\n".join(lines[node.lineno - 1:node.end_lineno])
            if ".is_active =" in body or ("delete(" in body and "profile" in body):
                print(f"EMAIL_MUTATION_OWNER {path.relative_to(ROOT)}:{node.lineno}\n{body}", flush=True)
    for path in sorted((ROOT / "scripts").glob("*deploy*")):
        if path.is_file() and path.suffix in (".sh", ".py"):
            print(f"DEPLOY_OWNER {path.relative_to(ROOT)}", flush=True)
    for path in sorted(ROOT.glob("*deploy*.sh")):
        print(f"DEPLOY_OWNER {path.relative_to(ROOT)}", flush=True)
    revisions = set()
    parents = set()
    for path in (ROOT / "alembic/versions").glob("*.py"):
        for node in ast.parse(path.read_text()).body:
            name = None
            value = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name, value = node.targets[0].id, node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                name, value = node.target.id, node.value
            if name not in ("revision", "down_revision") or value is None:
                continue
            item = ast.literal_eval(value)
            values = item if isinstance(item, (tuple, list)) else [item]
            (revisions if name == "revision" else parents).update(v for v in values if v)
    print("ERP_MIGRATION_HEADS " + json.dumps(sorted(revisions - parents)), flush=True)


def main():
    assert run("git", "rev-parse", "HEAD").stdout.strip() == BASE
    inspect_owners()
    active_python = run("poetry", "env", "info", "--executable").stdout.strip()
    env = os.environ.copy()
    env["VIRTUAL_ENV"] = str(Path(active_python).parent.parent)
    env["PATH"] = str(Path(active_python).parent) + os.pathsep + env["PATH"]
    failures = []
    for recipe_path in sorted(RECIPES.glob("*.py")):
        recipe = runpy.run_path(str(recipe_path))
        branch = recipe["BRANCH"]
        assert branch.startswith("fix/erp-")
        existing = run("git", "ls-remote", "--heads", "origin", f"refs/heads/{branch}").stdout.strip()
        if existing:
            print("EXISTING_BRANCH " + branch, flush=True)
            continue
        work = ROOT.parent / ("review-" + branch.removeprefix("fix/erp-"))
        run("git", "worktree", "add", "--detach", str(work), BASE)
        changed = set()

        def change(name, old, new):
            path = work / name
            source = path.read_text()
            if source.count(old) != 1:
                raise RuntimeError(f"Expected exactly one edit anchor in {name}: {old[:100]!r}")
            path.write_text(source.replace(old, new))
            changed.add(name)

        def write(name, content):
            path = work / name
            if path.exists():
                raise RuntimeError(f"Refusing to replace an existing file through write(): {name}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(content).strip() + "\n")
            changed.add(name)

        def rewrite(name, function, transform):
            path = work / name
            source = path.read_text()
            lines = source.splitlines(keepends=True)
            matches = [n for n in ast.walk(ast.parse(source)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == function]
            if len(matches) != 1:
                raise RuntimeError(f"Expected one function {name}:{function}")
            node = matches[0]
            before = "".join(lines[node.lineno - 1:node.end_lineno])
            after = transform(before).rstrip() + "\n"
            if before == after:
                raise RuntimeError(f"No change to {name}:{function}")
            path.write_text("".join(lines[:node.lineno - 1]) + after + "".join(lines[node.end_lineno:]))
            changed.add(name)

        try:
            recipe["apply"](change, write, rewrite)
            files = sorted(changed)
            for name in files:
                if name.endswith(".py"):
                    ast.parse((work / name).read_text(), filename=name)
            python_files = [p for p in files if p.endswith(".py")]
            if python_files:
                run("poetry", "run", "ruff", "format", *python_files, cwd=work, env=env)
                run("poetry", "run", "ruff", "check", *python_files, cwd=work, env=env)
            run("git", "diff", "--check", cwd=work)
            tests = run("poetry", "run", "pytest", *recipe["TESTS"], "-q", "--tb=short", "--timeout=60", cwd=work, env=env, check=False)
            run("git", "switch", "-c", branch, cwd=work)
            run("git", "config", "user.name", "github-actions[bot]", cwd=work)
            run("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com", cwd=work)
            run("git", "add", "--", *files, cwd=work)
            run("git", "commit", "-m", recipe["TITLE"], cwd=work)
            run("git", "push", "origin", f"HEAD:refs/heads/{branch}", cwd=work)
            sha = run("git", "rev-parse", "HEAD", cwd=work).stdout.strip()
            print("FIX_RESULT " + json.dumps({"branch": branch, "sha": sha, "files": files, "focused_test_exit": tests.returncode}), flush=True)
            if tests.returncode:
                failures.append(branch)
        except Exception as exc:
            print("FIX_FAILED " + json.dumps({"branch": branch, "error": str(exc)}), flush=True)
            failures.append(branch)
    if failures:
        print("VERIFICATION_FAILURES " + json.dumps(failures), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
