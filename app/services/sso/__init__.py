"""Protocol-based external authentication adapters.

ERP consumes OpenID Connect identity assertions and owns all local session and
authorization state. No external application database is imported or queried.
"""

from .oidc import OIDCClient, oidc_client

__all__ = ["OIDCClient", "oidc_client"]
