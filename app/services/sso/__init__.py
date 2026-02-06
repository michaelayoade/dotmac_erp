"""
SSO Service Module.

Provides Single Sign-On functionality across multiple DotMac apps
deployed under the same parent domain.

Architecture:
- SSO Provider (App #1): Hosts auth database, handles login
- SSO Clients (App #2, #3): Validate tokens against shared DB

Usage:
- Set SSO_ENABLED=true on all apps
- Set SSO_PROVIDER_MODE=true on App #1 only
- Set AUTH_DATABASE_URL on App #2 and #3 to connect to App #1's database
- Set SSO_COOKIE_DOMAIN=.company.com on all apps
- Set SSO_JWT_SECRET to same value on all apps

Example configuration for SSO Provider (App #1):
    SSO_ENABLED=true
    SSO_PROVIDER_MODE=true
    SSO_COOKIE_DOMAIN=.company.com
    SSO_JWT_SECRET=your-256-bit-secret

Example configuration for SSO Client (App #2, #3):
    SSO_ENABLED=true
    SSO_PROVIDER_MODE=false
    SSO_COOKIE_DOMAIN=.company.com
    SSO_JWT_SECRET=your-256-bit-secret  # Same as App #1
    AUTH_DATABASE_URL=postgresql://readonly:pass@app1.company.com:5432/dotmac_erp
    SSO_PROVIDER_URL=https://sso.company.com
"""

from .session_sync import SSOSessionSync
from .token_validator import SSOTokenValidator

__all__ = ["SSOTokenValidator", "SSOSessionSync"]
