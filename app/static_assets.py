"""Content-versioned URLs for ERP-owned browser assets."""

from hashlib import sha256
from pathlib import Path

_TYPEAHEAD_SCRIPT = (
    Path(__file__).resolve().parent.parent / "static" / "js" / "typeahead.js"
)


def typeahead_script_url() -> str:
    """Invalidate browser caches whenever the shipped typeahead script changes."""
    digest = sha256(_TYPEAHEAD_SCRIPT.read_bytes()).hexdigest()[:12]
    return f"/static/js/typeahead.js?v={digest}"
