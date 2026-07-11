"""ManageSieve helpers for Mailcow offboarding."""

from __future__ import annotations

import base64
import re
import socket
import ssl
from dataclasses import dataclass


class ManageSieveError(RuntimeError):
    """Raised when a ManageSieve command fails."""


_REDIRECT_LINE_RE = re.compile(
    r'^\s*redirect(?:\s+:copy)?\s+"(?P<email>[^"]+)"\s*;\s*$',
    re.IGNORECASE,
)


def quote_sieve_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def render_autoresponder_message(
    template: str,
    *,
    full_name: str,
    email: str,
) -> str:
    """Render the ERP-side autoresponder template into literal Sieve text."""
    return template.format(full_name=full_name, email=email)


def build_offboarding_sieve_script(
    *,
    full_name: str,
    email: str,
    forward_to: str,
    subject: str,
    message_template: str,
) -> str:
    message = render_autoresponder_message(
        message_template,
        full_name=full_name,
        email=email,
    )
    return (
        'require ["vacation", "copy"];\n\n'
        "vacation\n"
        "  :days 1\n"
        f'  :subject "{quote_sieve_string(subject)}"\n'
        f'"{quote_sieve_string(message)}";\n\n'
        f'redirect :copy "{quote_sieve_string(forward_to)}";\n'
        "keep;\n"
    )


def remove_redirect_from_sieve(script: str, email: str) -> tuple[str, bool]:
    """Remove redirect lines for email while preserving the rest of the script."""
    normalized = email.strip().lower()
    changed = False
    kept_lines: list[str] = []
    for line in script.splitlines():
        match = _REDIRECT_LINE_RE.match(line)
        if match and match.group("email").strip().lower() == normalized:
            changed = True
            continue
        kept_lines.append(line)
    updated = "\n".join(kept_lines).rstrip()
    if script.endswith("\n") or changed:
        updated = f"{updated}\n" if updated else ""
    return updated, changed


@dataclass(frozen=True)
class ManageSieveConfig:
    host: str
    port: int
    master_user: str
    master_password: str
    use_starttls: bool = True
    timeout: float = 20.0


class ManageSieveClient:
    """Minimal ManageSieve client for get/put/activate script operations."""

    def __init__(self, config: ManageSieveConfig) -> None:
        self.config = config

    def get_active_script(self, mailbox: str) -> tuple[str | None, str | None]:
        with self._connection(mailbox) as conn:
            scripts = self._list_scripts(conn)
            active = next((name for name, is_active in scripts if is_active), None)
            if not active:
                return None, None
            return active, self._get_script(conn, active)

    def put_and_activate_script(
        self,
        mailbox: str,
        script_name: str,
        script: str,
    ) -> None:
        with self._connection(mailbox) as conn:
            self._send_literal(conn, f'PUTSCRIPT "{script_name}"', script)
            self._expect_ok(conn)
            self._send_line(conn, f'SETACTIVE "{script_name}"')
            self._expect_ok(conn)

    def _connection(self, mailbox: str):
        return _ManageSieveConnection(self.config, mailbox)

    def _list_scripts(self, conn: _ManageSieveConnection) -> list[tuple[str, bool]]:
        self._send_line(conn, "LISTSCRIPTS")
        lines = conn.read_response_lines()
        scripts: list[tuple[str, bool]] = []
        for line in lines[:-1]:
            if not line.startswith('"'):
                continue
            name = line.split('"', 2)[1]
            scripts.append((name, "ACTIVE" in line.upper()))
        self._raise_if_not_ok(lines[-1])
        return scripts

    def _get_script(self, conn: _ManageSieveConnection, script_name: str) -> str:
        self._send_line(conn, f'GETSCRIPT "{script_name}"')
        return conn.read_literal_response()

    def _send_line(self, conn: _ManageSieveConnection, line: str) -> None:
        conn.send_line(line)

    def _send_literal(
        self, conn: _ManageSieveConnection, command: str, content: str
    ) -> None:
        data = content.encode("utf-8")
        conn.send_line(f"{command} {{{len(data)}+}}")
        conn.send_bytes(data + b"\r\n")

    def _expect_ok(self, conn: _ManageSieveConnection) -> None:
        self._raise_if_not_ok(conn.read_response_lines()[-1])

    def _raise_if_not_ok(self, line: str) -> None:
        if not line.upper().startswith("OK"):
            raise ManageSieveError(line)


class _ManageSieveConnection:
    def __init__(self, config: ManageSieveConfig, mailbox: str) -> None:
        self.config = config
        self.mailbox = mailbox
        self.sock: socket.socket | ssl.SSLSocket | None = None

    def __enter__(self) -> _ManageSieveConnection:
        raw_sock = socket.create_connection(
            (self.config.host, self.config.port),
            timeout=self.config.timeout,
        )
        self.sock = raw_sock
        self.read_response_lines()
        if self.config.use_starttls:
            self.send_line("STARTTLS")
            self._expect_ok()
            context = ssl.create_default_context()
            self.sock = context.wrap_socket(raw_sock, server_hostname=self.config.host)
        self._authenticate()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self.sock:
                self.send_line("LOGOUT")
                self.read_response_lines()
        finally:
            if self.sock:
                self.sock.close()

    def _authenticate(self) -> None:
        authcid = f"{self.mailbox}*{self.config.master_user}"
        token = base64.b64encode(
            f"\0{authcid}\0{self.config.master_password}".encode()
        ).decode("ascii")
        self.send_line(f'AUTHENTICATE "PLAIN" "{token}"')
        self._expect_ok()

    def _expect_ok(self) -> None:
        line = self.read_response_lines()[-1]
        if not line.upper().startswith("OK"):
            raise ManageSieveError(line)

    def send_line(self, line: str) -> None:
        self.send_bytes(f"{line}\r\n".encode())

    def send_bytes(self, data: bytes) -> None:
        if not self.sock:
            raise ManageSieveError("ManageSieve connection is not open")
        self.sock.sendall(data)

    def read_response_lines(self) -> list[str]:
        lines: list[str] = []
        while True:
            line = self._readline()
            lines.append(line)
            upper = line.upper()
            if upper.startswith(("OK", "NO", "BYE")):
                return lines

    def read_literal_response(self) -> str:
        first = self._readline()
        match = re.match(r"\{(\d+)\}$", first)
        if not match:
            raise ManageSieveError(f"Expected literal response, got: {first}")
        size = int(match.group(1))
        payload = self._read_exact(size).decode("utf-8")
        self._readline()
        final = self._readline()
        if not final.upper().startswith("OK"):
            raise ManageSieveError(final)
        return payload

    def _readline(self) -> str:
        data = bytearray()
        while True:
            chunk = self._read_exact(1)
            data.extend(chunk)
            if data.endswith(b"\r\n"):
                return data[:-2].decode("utf-8", errors="replace")

    def _read_exact(self, size: int) -> bytes:
        if not self.sock:
            raise ManageSieveError("ManageSieve connection is not open")
        data = bytearray()
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise ManageSieveError("ManageSieve connection closed")
            data.extend(chunk)
        return bytes(data)

