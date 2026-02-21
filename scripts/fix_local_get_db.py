"""Fix all local get_db() functions to include auto-commit."""

from __future__ import annotations

import os

# Files already handled (Phase 0 or just updated)
SKIP_FILES = {
    "app/db.py",
    "app/web/deps.py",
    "app/api/deps.py",
    "app/services/auth_dependencies.py",
    "app/api/finance/fx.py",
    "app/api/sync/dotmac_crm.py",
    "app/api/finance/rpt.py",
    "app/api/finance/banking.py",
    "app/api/finance/gl.py",
    "app/api/finance/lease.py",
    "app/api/finance/ar.py",
    "app/api/finance/ap.py",
    "app/api/finance/tax.py",
    "app/api/finance/payments.py",
    "app/api/finance/cons.py",
    "tests/conftest.py",
}

OLD_BODY = """\
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()"""

NEW_BODY = """\
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()"""

updated = []
for root, _dirs, files in os.walk("app"):
    for fname in files:
        if not fname.endswith(".py"):
            continue
        fpath = os.path.join(root, fname)
        if fpath in SKIP_FILES:
            continue

        with open(fpath) as f:
            content = f.read()

        if "def get_db(" not in content and "def _get_db(" not in content:
            continue

        if OLD_BODY not in content:
            continue

        new_content = content.replace(OLD_BODY, NEW_BODY)
        if new_content != content:
            with open(fpath, "w") as f:
                f.write(new_content)
            updated.append(fpath)

print(f"Updated {len(updated)} files:")
for f in sorted(updated):
    print(f"  {f}")
