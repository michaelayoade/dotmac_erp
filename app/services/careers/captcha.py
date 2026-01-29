"""
CAPTCHA verification service using Cloudflare Turnstile.

Verifies CAPTCHA tokens to protect public forms from automated submissions.
"""

import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


async def verify_captcha(token: str, remote_ip: Optional[str] = None) -> bool:
    """
    Verify a Cloudflare Turnstile CAPTCHA token.

    Args:
        token: The CAPTCHA response token from the client
        remote_ip: Optional client IP address for additional verification

    Returns:
        True if verification succeeds, False otherwise
    """
    secret_key = settings.captcha_secret_key
    if not secret_key:
        # CAPTCHA not configured - allow all (for development)
        logger.warning("CAPTCHA secret key not configured, skipping verification")
        return True

    if not token:
        logger.warning("Empty CAPTCHA token provided")
        return False

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            payload = {
                "secret": secret_key,
                "response": token,
            }
            if remote_ip:
                payload["remoteip"] = remote_ip

            response = await client.post(TURNSTILE_VERIFY_URL, data=payload)
            response.raise_for_status()
            result = response.json()

            if result.get("success"):
                logger.debug("CAPTCHA verification successful")
                return True
            else:
                error_codes = result.get("error-codes", [])
                logger.warning("CAPTCHA verification failed: %s", error_codes)
                return False

    except httpx.TimeoutException:
        logger.error("CAPTCHA verification timed out")
        return False
    except httpx.HTTPStatusError as e:
        logger.error("CAPTCHA verification HTTP error: %s", e)
        return False
    except Exception as e:
        logger.exception("CAPTCHA verification unexpected error: %s", e)
        return False


def is_captcha_enabled() -> bool:
    """Check if CAPTCHA is configured and enabled."""
    return bool(settings.captcha_site_key and settings.captcha_secret_key)


def get_captcha_site_key() -> Optional[str]:
    """Get the CAPTCHA site key for client-side rendering."""
    return settings.captcha_site_key
