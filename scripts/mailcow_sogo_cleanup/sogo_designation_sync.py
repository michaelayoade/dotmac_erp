#!/usr/bin/env python3
"""Project Mailcow mailbox designations into managed SOGo signature blocks."""

from __future__ import annotations

import argparse
import html
import logging
import os
import re
from typing import Any

try:
    from .sogo_cleanup_queue import load_config_file
    from .sogo_forward_cleanup import (
        MailcowSogoDatabase,
        SogoProfile,
        restart_sogo_services,
    )
except ImportError:  # pragma: no cover - direct script execution
    from sogo_cleanup_queue import load_config_file
    from sogo_forward_cleanup import (
        MailcowSogoDatabase,
        SogoProfile,
        restart_sogo_services,
    )


logger = logging.getLogger("sogo_designation_sync")

IDENTITIES_KEY = "SOGoMailIdentities"
DESIGNATION_ATTRIBUTE = "designation"
MANAGED_BLOCK_RE = re.compile(
    r"(?:<br\s*/?>\s*)?"
    r'<span\s+data-dotmac-erp-designation=["\']true["\'][^>]*>'
    r".*?</span>",
    flags=re.IGNORECASE | re.DOTALL,
)


def merge_managed_designation(
    signature: str | None,
    designation: str | None,
) -> str:
    """Replace only the ERP-managed designation block in a signature."""
    current = signature if isinstance(signature, str) else ""
    preserved = MANAGED_BLOCK_RE.sub("", current).rstrip()
    normalized = (designation or "").strip()
    if not normalized:
        return preserved
    block = f'<span data-dotmac-erp-designation="true">{html.escape(normalized)}</span>'
    return f"{preserved}<br>{block}" if preserved else block


def _identity_index(identities: list[Any], email: str) -> int | None:
    normalized_email = email.strip().lower()
    for index, identity in enumerate(identities):
        if (
            isinstance(identity, dict)
            and str(identity.get("email", "")).strip().lower() == normalized_email
        ):
            return index
    for index, identity in enumerate(identities):
        if isinstance(identity, dict) and identity.get("isDefault"):
            return index
    for index, identity in enumerate(identities):
        if isinstance(identity, dict):
            return index
    return None


def project_designation_to_defaults(
    defaults: dict[str, Any],
    *,
    email: str,
    full_name: str,
    designation: str | None,
) -> tuple[dict[str, Any], bool]:
    """Return SOGo defaults with the managed designation signature reconciled."""
    raw_identities = defaults.get(IDENTITIES_KEY)
    identities = list(raw_identities) if isinstance(raw_identities, list) else []
    index = _identity_index(identities, email)
    normalized_designation = (designation or "").strip() or None

    if index is None:
        if normalized_designation is None:
            return defaults, False
        identity: dict[str, Any] = {
            "email": email,
            "signature": merge_managed_designation("", normalized_designation),
            "isDefault": 1,
        }
        if full_name.strip():
            identity["fullName"] = full_name.strip()
        identities.append(identity)
    else:
        identity = dict(identities[index])
        current_signature = identity.get("signature")
        updated_signature = merge_managed_designation(
            current_signature if isinstance(current_signature, str) else "",
            normalized_designation,
        )
        if updated_signature:
            identity["signature"] = updated_signature
        else:
            identity.pop("signature", None)
        identities[index] = identity

    updated = dict(defaults)
    updated[IDENTITIES_KEY] = identities
    if normalized_designation is not None:
        updated.update(
            {
                "SOGoMailUseSignatureOnNew": 1,
                "SOGoMailUseSignatureOnReply": 1,
                "SOGoMailUseSignatureOnForward": 1,
            }
        )
    return updated, updated != defaults


def reconcile_designations(
    database: MailcowSogoDatabase,
    *,
    apply: bool,
) -> int:
    mailboxes = {
        mailbox.username.strip().lower(): mailbox
        for mailbox in database.list_mailboxes()
    }
    changed_profiles: list[SogoProfile] = []
    for profile in database.list_profiles():
        mailbox = mailboxes.get(profile.uid.strip().lower())
        if mailbox is None:
            continue
        raw_designation = mailbox.custom_attributes.get(DESIGNATION_ATTRIBUTE)
        designation = str(raw_designation) if raw_designation is not None else None
        updated, changed = project_designation_to_defaults(
            profile.defaults,
            email=mailbox.username,
            full_name=mailbox.full_name,
            designation=designation,
        )
        if changed:
            changed_profiles.append(SogoProfile(uid=profile.uid, defaults=updated))
    if apply:
        database.update_profiles(changed_profiles)
    return len(changed_profiles)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="Optional environment-style config file")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Apply SOGo changes")
    mode.add_argument("--dry-run", action="store_true", help="Preview changes only")
    mode.add_argument(
        "--no-restart",
        action="store_true",
        help="Do not restart SOGo and memcached after applying changes",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_config_file(args.config)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    mailcow_dir = os.getenv("MAILCOW_DIR", "/opt/mailcow-dockerized")
    docker_bin = os.getenv("DOCKER_BIN", "docker")
    mysql_service = os.getenv("MYSQL_SERVICE", "mysql-mailcow")
    restart_services = [
        value.strip()
        for value in os.getenv(
            "SOGO_RESTART_SERVICES",
            "sogo-mailcow,memcached-mailcow",
        ).split(",")
        if value.strip()
    ]
    database = MailcowSogoDatabase(
        mailcow_dir=mailcow_dir,
        docker_bin=docker_bin,
        mysql_service=mysql_service,
    )
    apply = bool(args.apply)
    changed_count = reconcile_designations(database, apply=apply)
    logger.info(
        "Designation projection matched %s SOGo profile(s)%s",
        changed_count,
        "" if apply else " (dry run)",
    )
    if apply and changed_count and not args.no_restart:
        restart_sogo_services(
            mailcow_dir=mailcow_dir,
            docker_bin=docker_bin,
            services=restart_services,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
