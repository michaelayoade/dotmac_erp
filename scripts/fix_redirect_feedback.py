#!/usr/bin/env python3
"""Add feedback query params to RedirectResponse calls missing them.

For success-path POST redirects:
- Detail page URLs (contain an entity ID): append ?saved=1
- List page URLs (no entity ID): append ?success=Record+saved+successfully

Skips:
- Redirects already having ?success=, ?saved=, ?error=, ?created=, ?updated=, ?deleted=
- Auth/login redirects
- Error-path redirects (inside except blocks)
- Import lines and type hints
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Feedback params already present
FEEDBACK_PARAMS = re.compile(r"[?&](success|saved|error|created|updated|deleted)=")
# Auth redirect URLs to skip
AUTH_URLS = re.compile(r"(/login|/auth|/logout|/register|/forgot|/reset-password)")
# URL patterns indicating a detail page (contains a variable like {id})
DETAIL_URL_PATTERNS = [
    r"\{[a-z_]+_?id\}",  # f-string with {entity_id}
    r"_url\(",  # helper function like _project_url(project)
    r"str\([a-z_]+\.",  # str(entity.id) concatenation
    r'f"[^"]*\{',  # any f-string with variables
    r"f'[^']*\{",  # any f-string with variables (single quotes)
]

# Files to process
WEB_ROUTE_DIRS = [
    Path("/root/dotmac/app/web"),
    Path("/root/dotmac/app/services"),
]


def find_python_files(dirs: list[Path]) -> list[Path]:
    """Find all Python files in given directories."""
    files = []
    for d in dirs:
        files.extend(sorted(d.rglob("*.py")))
    return files


def has_redirect_response(content: str) -> bool:
    """Check if file contains RedirectResponse calls."""
    return "RedirectResponse(" in content


def is_detail_url(url_text: str) -> bool:
    """Check if a URL redirects to a detail page (contains entity ID)."""
    return any(re.search(pattern, url_text) for pattern in DETAIL_URL_PATTERNS)


def already_has_feedback(url_text: str) -> bool:
    """Check if URL already has feedback query params."""
    return bool(FEEDBACK_PARAMS.search(url_text))


def is_auth_url(url_text: str) -> bool:
    """Check if URL is an auth/login redirect."""
    return bool(AUTH_URLS.search(url_text))


def is_inside_except_block(lines: list[str], line_idx: int) -> bool:
    """Rough check: is this line inside an except block?

    Looks backward for 'except' with less or equal indentation.
    Returns True if we find 'except' before finding 'try' at the same indent level.
    """
    target_indent = len(lines[line_idx]) - len(lines[line_idx].lstrip())

    for i in range(line_idx - 1, max(line_idx - 30, -1), -1):
        line = lines[i]
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(stripped)
        if indent < target_indent:
            if stripped.startswith("except"):
                return True
            if stripped.startswith(("try:", "def ", "async def ", "class ")):
                return False
    return False


def add_saved_param(url_text: str) -> str:
    """Add ?saved=1 to a URL string.

    Handles:
    - Simple string: "/path" -> "/path?saved=1"
    - f-string with vars: f"/path/{id}" -> f"/path/{id}?saved=1"
    - Helper call: _project_url(p) -> _project_url(p) + "?saved=1"
    """
    # URL is a function call like _project_url(project)
    if re.search(r"[a-z_]+\(", url_text) and not url_text.strip().startswith(
        ('f"', "f'", '"', "'")
    ):
        return url_text.rstrip() + ' + "?saved=1"'

    # URL is a string literal or f-string
    # Find the closing quote
    for q in ('"""', "'''", '"', "'"):
        if url_text.rstrip().endswith(q):
            base = url_text.rstrip()[: -len(q)]
            # Check if URL already has ? in it
            if "?" in base:
                return base + "&saved=1" + q
            return base + "?saved=1" + q

    return url_text


def add_success_param(url_text: str, message: str = "Record+saved+successfully") -> str:
    """Add ?success=... to a URL string (for list page redirects)."""
    for q in ('"""', "'''", '"', "'"):
        if url_text.rstrip().endswith(q):
            base = url_text.rstrip()[: -len(q)]
            if "?" in base:
                return base + f"&success={message}" + q
            return base + f"?success={message}" + q

    return url_text


def extract_redirect_block(lines: list[str], start: int) -> tuple[int, int, str]:
    """Extract a full RedirectResponse(...) call that may span multiple lines.

    Returns (start_line, end_line, full_text).
    """
    text = lines[start]
    if "RedirectResponse(" in text:
        # Count parens to find the complete call
        paren_count = 0
        end = start
        for i in range(start, min(start + 10, len(lines))):
            for ch in lines[i]:
                if ch == "(":
                    paren_count += 1
                elif ch == ")":
                    paren_count -= 1
            end = i
            if paren_count <= 0:
                break
        full_text = "\n".join(lines[start : end + 1])
        return start, end, full_text
    return start, start, text


def get_action_from_context(lines: list[str], line_idx: int) -> str:
    """Try to determine the action (create/update/delete) from surrounding context."""
    # Look for the function name
    for i in range(line_idx - 1, max(line_idx - 50, -1), -1):
        line = lines[i].strip()
        if line.startswith(("def ", "async def ")):
            func_name = line.split("(")[0].replace("def ", "").replace("async ", "")
            if "delete" in func_name.lower():
                return "deleted"
            if "create" in func_name.lower() or "new" in func_name.lower():
                return "created"
            if "update" in func_name.lower() or "edit" in func_name.lower():
                return "updated"
            return "saved"
        if "@router.post" in line:
            if "/delete" in line:
                return "deleted"
    return "saved"


def process_file(filepath: Path, dry_run: bool = True) -> list[str]:
    """Process a single file, adding feedback params to RedirectResponse calls.

    Returns list of changes made.
    """
    content = filepath.read_text()
    if not has_redirect_response(content):
        return []

    lines = content.split("\n")
    changes = []
    modified = False
    i = 0

    while i < len(lines):
        line = lines[i]

        # Skip import lines and type hints
        stripped = line.strip()
        if stripped.startswith(("from ", "import ", "#", ")")) or "-> " in stripped:
            i += 1
            continue

        # Skip response_class= declarations
        if "response_class=RedirectResponse" in stripped:
            i += 1
            continue

        if "RedirectResponse(" not in stripped:
            i += 1
            continue

        # Found a RedirectResponse call
        start, end, block_text = extract_redirect_block(lines, i)

        # Skip if not a 303 redirect (success redirect)
        if "303" not in block_text:
            i = end + 1
            continue

        # Skip if already has feedback
        if already_has_feedback(block_text):
            i = end + 1
            continue

        # Skip auth URLs
        if is_auth_url(block_text):
            i = end + 1
            continue

        # Skip if inside except block (error-path redirect)
        if is_inside_except_block(lines, start):
            i = end + 1
            continue

        # Extract the URL value
        url_match = re.search(r"url\s*=\s*(.+?)(?:,|\))", block_text, re.DOTALL)
        if not url_match:
            i = end + 1
            continue

        url_text = url_match.group(1).strip()

        # Determine if detail or list page
        is_detail = is_detail_url(url_text)
        action = get_action_from_context(lines, start)

        # Now modify the URL in the lines
        # Find the line with url= and modify it
        for j in range(start, end + 1):
            if "url=" in lines[j] or (j == start and "url=" not in block_text):
                # Simple single-line case: url="..." or url=f"..."
                url_line = lines[j]

                if is_detail and re.search(r"[a-z_]+_url\(", url_line):
                    # Helper function: _project_url(p) -> _project_url(p) + "?saved=1"
                    # Find the closing paren of the helper call
                    m = re.search(r"([a-z_]+_url\([^)]+\))", url_line)
                    if m:
                        old = m.group(1)
                        new = old + ' + "?saved=1"'
                        lines[j] = url_line.replace(old, new)
                        changes.append(f"  L{j + 1}: {old} -> {new}")
                        modified = True
                        break
                elif is_detail and '+ "?saved=1"' not in url_line:
                    # f-string or plain string with detail URL
                    # Find the closing quote before , or )
                    for q in ('"', "'"):
                        # Look for patterns like: url=f"/path/{id}"  or url="/path"
                        # We need to insert ?saved=1 before the closing quote
                        pattern = rf"(url\s*=\s*f?{q}[^{q}]*?)({q})"
                        m = re.search(pattern, url_line)
                        if m:
                            url_content = m.group(1)
                            if "?" in url_content:
                                new_url = url_content + "&saved=1" + q
                            else:
                                new_url = url_content + "?saved=1" + q
                            lines[j] = (
                                url_line[: m.start()] + new_url + url_line[m.end() :]
                            )
                            changes.append(f"  L{j + 1}: added ?saved=1")
                            modified = True
                            break
                    else:
                        continue
                    break
                elif not is_detail:
                    # List page URL: add ?success= message
                    for q in ('"', "'"):
                        pattern = rf"(url\s*=\s*f?{q}[^{q}]*?)({q})"
                        m = re.search(pattern, url_line)
                        if m:
                            url_content = m.group(1)
                            msg_map = {
                                "deleted": "Record+deleted+successfully",
                                "created": "Record+created+successfully",
                                "updated": "Record+updated+successfully",
                                "saved": "Record+saved+successfully",
                            }
                            msg = msg_map.get(action, "Record+saved+successfully")
                            if "?" in url_content:
                                new_url = url_content + f"&success={msg}" + q
                            else:
                                new_url = url_content + f"?success={msg}" + q
                            lines[j] = (
                                url_line[: m.start()] + new_url + url_line[m.end() :]
                            )
                            changes.append(f"  L{j + 1}: added ?success={msg}")
                            modified = True
                            break
                    else:
                        continue
                    break

        i = end + 1

    if modified and not dry_run:
        filepath.write_text("\n".join(lines))

    return changes


def main() -> None:
    dry_run = "--execute" not in sys.argv
    verbose = "--verbose" in sys.argv or "-v" in sys.argv

    if dry_run:
        print("DRY RUN — pass --execute to apply changes\n")

    files = find_python_files(WEB_ROUTE_DIRS)
    total_changes = 0
    files_changed = 0

    for filepath in files:
        # Skip test files, __pycache__, etc.
        if "__pycache__" in str(filepath) or "/tests/" in str(filepath):
            continue

        changes = process_file(filepath, dry_run=dry_run)
        if changes:
            files_changed += 1
            total_changes += len(changes)
            print(f"\n{filepath.relative_to(Path('/root/dotmac'))}:")
            for change in changes:
                print(change)
        elif verbose:
            print(f"  (no changes) {filepath.relative_to(Path('/root/dotmac'))}")

    print(
        f"\n{'Would modify' if dry_run else 'Modified'}: {total_changes} redirects across {files_changed} files"
    )


if __name__ == "__main__":
    main()
