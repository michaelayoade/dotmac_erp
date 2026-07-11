"""SOGo profile helpers for Mailcow offboarding."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


def _load_defaults(raw: str | bytes | bytearray | None) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if not str(raw).strip():
        return {}
    data = json.loads(str(raw))
    return data if isinstance(data, dict) else {}


def dump_defaults(defaults: dict[str, Any]) -> str:
    return json.dumps(defaults, separators=(",", ":"), ensure_ascii=False)


def remove_forward_address(
    defaults: dict[str, Any],
    email: str,
) -> tuple[dict[str, Any], bool]:
    forward = defaults.get("Forward")
    if not isinstance(forward, dict):
        return defaults, False

    addresses = forward.get("forwardAddress")
    if not isinstance(addresses, list):
        return defaults, False

    normalized = email.strip().lower()
    updated_addresses = [
        value
        for value in addresses
        if not (isinstance(value, str) and value.strip().lower() == normalized)
    ]
    if len(updated_addresses) == len(addresses):
        return defaults, False

    updated = dict(defaults)
    updated_forward = dict(forward)
    updated_forward["forwardAddress"] = updated_addresses
    if not updated_addresses:
        updated_forward["enabled"] = 0
    updated["Forward"] = updated_forward
    return updated, True


def set_forward_to_inactive(
    defaults: dict[str, Any],
    forward_to: str,
) -> tuple[dict[str, Any], bool]:
    forward = defaults.get("Forward")
    current = forward if isinstance(forward, dict) else {}
    addresses = current.get("forwardAddress")
    new_addresses = list(addresses) if isinstance(addresses, list) else []
    normalized_existing = {
        value.strip().lower() for value in new_addresses if isinstance(value, str)
    }
    if forward_to.strip().lower() not in normalized_existing:
        new_addresses.append(forward_to)

    updated_forward = dict(current)
    updated_forward.update(
        {
            "forwardAddress": new_addresses,
            "enabled": 1,
            "keepCopy": 1,
            "alwaysSend": 1,
        }
    )
    updated = dict(defaults)
    updated["Forward"] = updated_forward
    return updated, updated != defaults


@dataclass(frozen=True)
class SogoProfileRow:
    c_uid: str
    c_defaults: str | None


class SogoProfileService:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
    ) -> None:
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password

    def _connect(self):
        try:
            import pymysql  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover - environment guard
            raise RuntimeError("PyMySQL is required for SOGo profile updates") from exc
        return pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database,
            charset="utf8mb4",
            autocommit=False,
            cursorclass=pymysql.cursors.DictCursor,
        )

    def list_profiles(self) -> list[SogoProfileRow]:
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute("SELECT c_uid, c_defaults FROM sogo_user_profile")
            rows = cursor.fetchall()
        return [
            SogoProfileRow(
                c_uid=str(row["c_uid"]),
                c_defaults=row.get("c_defaults"),
            )
            for row in rows
        ]

    def update_defaults(self, c_uid: str, defaults: dict[str, Any]) -> None:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE sogo_user_profile SET c_defaults = %s WHERE c_uid = %s",
                    (dump_defaults(defaults), c_uid),
                )
            conn.commit()

    def cleanup_forwarding_references(self, email: str) -> list[str]:
        changed_profiles: list[str] = []
        for profile in self.list_profiles():
            defaults = _load_defaults(profile.c_defaults)
            updated, changed = remove_forward_address(defaults, email)
            if changed:
                self.update_defaults(profile.c_uid, updated)
                changed_profiles.append(profile.c_uid)
        return changed_profiles

    def set_inactive_forward(self, email: str, forward_to: str) -> bool:
        profiles = [row for row in self.list_profiles() if row.c_uid.lower() == email]
        if not profiles:
            return False
        defaults = _load_defaults(profiles[0].c_defaults)
        updated, changed = set_forward_to_inactive(defaults, forward_to)
        if changed:
            self.update_defaults(profiles[0].c_uid, updated)
        return True


def load_defaults(raw: str | bytes | bytearray | None) -> dict[str, Any]:
    return _load_defaults(raw)
