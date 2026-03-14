#!/usr/bin/env python3
"""DotMac ERP License Generation CLI.

Internal-only tool — NOT shipped to customers.

Usage:
    python tools/license_gen.py generate-keypair --out-dir ./keys
    python tools/license_gen.py create-license --spec license_spec.json --key ./keys/private.pem --out dotmac.lic
    python tools/license_gen.py validate-license --lic dotmac.lic --pub ./keys/public.pem
    python tools/license_gen.py show-fingerprint
"""

from __future__ import annotations

import base64
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

try:
    import click
except ImportError:
    print("Error: 'click' package required. Install with: pip install click")
    sys.exit(1)

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

SEPARATOR = "\n---\n"


@click.group()
def cli() -> None:
    """DotMac ERP License Generation Tool."""


@cli.command()
@click.option(
    "--out-dir",
    default="./keys",
    help="Directory to write keypair files",
    type=click.Path(),
)
def generate_keypair(out_dir: str) -> None:
    """Generate a new Ed25519 keypair for license signing."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    # Write private key (PEM)
    priv_path = out / "private.pem"
    priv_bytes = private_key.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    )
    priv_path.write_bytes(priv_bytes)
    click.echo(f"Private key written to: {priv_path}")

    # Write public key (PEM)
    pub_path = out / "public.pem"
    pub_bytes = public_key.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    pub_path.write_bytes(pub_bytes)
    click.echo(f"Public key written to: {pub_path}")

    # Write raw public key as base64 (for embedding in validator.py)
    raw_pub = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    b64_pub = base64.b64encode(raw_pub).decode()
    b64_path = out / "public_key_b64.txt"
    b64_path.write_text(b64_pub)
    click.echo(f"Base64 public key: {b64_pub}")
    click.echo(f"  (also saved to: {b64_path})")
    click.echo()
    click.echo("Copy the base64 value into app/licensing/validator.py _PUBLIC_KEY_B64")


@cli.command()
@click.option(
    "--spec",
    required=True,
    help="JSON spec file for the license",
    type=click.Path(exists=True),
)
@click.option(
    "--key", required=True, help="Path to private key PEM", type=click.Path(exists=True)
)
@click.option("--out", required=True, help="Output .lic file path", type=click.Path())
def create_license(spec: str, key: str, out: str) -> None:
    """Create a signed license file from a JSON spec."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    spec_data = json.loads(Path(spec).read_text())

    # Ensure required fields
    if "license_id" not in spec_data:
        click.echo("Error: spec must contain 'license_id'", err=True)
        sys.exit(1)
    if "customer_name" not in spec_data:
        click.echo("Error: spec must contain 'customer_name'", err=True)
        sys.exit(1)
    if "customer_id" not in spec_data:
        click.echo("Error: spec must contain 'customer_id'", err=True)
        sys.exit(1)

    # Set defaults
    spec_data.setdefault("version", 1)
    spec_data.setdefault("issued_at", datetime.now(tz=UTC).isoformat())
    spec_data.setdefault("grace_period_days", 30)
    spec_data.setdefault("max_organizations", 5)
    spec_data.setdefault("max_users", 100)
    spec_data.setdefault("modules", [])
    spec_data.setdefault("features", {})

    if "expires_at" not in spec_data:
        click.echo("Error: spec must contain 'expires_at'", err=True)
        sys.exit(1)

    # Serialize payload
    payload_json = json.dumps(spec_data, indent=2, default=str)
    payload_bytes = payload_json.encode("utf-8")

    # Sign
    priv_key_data = Path(key).read_bytes()
    private_key = load_pem_private_key(priv_key_data, password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        click.echo("Error: key must be an Ed25519 private key", err=True)
        sys.exit(1)

    signature = private_key.sign(payload_bytes)

    # Write license file
    payload_b64 = base64.b64encode(payload_bytes).decode()
    sig_b64 = base64.b64encode(signature).decode()
    lic_content = f"{payload_b64}{SEPARATOR}{sig_b64}"

    Path(out).write_text(lic_content, encoding="utf-8")
    click.echo(f"License written to: {out}")
    click.echo(f"  License ID: {spec_data['license_id']}")
    click.echo(f"  Customer: {spec_data['customer_name']}")
    click.echo(f"  Expires: {spec_data['expires_at']}")
    click.echo(f"  Modules: {spec_data['modules']}")


@cli.command()
@click.option(
    "--lic", required=True, help="Path to .lic file", type=click.Path(exists=True)
)
@click.option(
    "--pub", required=True, help="Path to public key PEM", type=click.Path(exists=True)
)
def validate_license(lic: str, pub: str) -> None:
    """Validate an existing license file against a public key."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    text = Path(lic).read_text(encoding="utf-8").strip()
    if SEPARATOR not in text:
        click.echo("INVALID: Missing separator (---)", err=True)
        sys.exit(1)

    payload_b64, sig_b64 = text.split(SEPARATOR, maxsplit=1)
    payload_bytes = base64.b64decode(payload_b64)
    signature = base64.b64decode(sig_b64)

    pub_key_data = Path(pub).read_bytes()
    public_key = load_pem_public_key(pub_key_data)
    if not isinstance(public_key, Ed25519PublicKey):
        click.echo("Error: key must be an Ed25519 public key", err=True)
        sys.exit(1)

    try:
        public_key.verify(signature, payload_bytes)
    except InvalidSignature:
        click.echo("INVALID: Signature verification failed", err=True)
        sys.exit(1)

    payload = json.loads(payload_bytes)
    click.echo("VALID: Signature verified successfully")
    click.echo(f"  License ID: {payload.get('license_id')}")
    click.echo(f"  Customer: {payload.get('customer_name')}")
    click.echo(f"  Issued: {payload.get('issued_at')}")
    click.echo(f"  Expires: {payload.get('expires_at')}")
    click.echo(f"  Modules: {payload.get('modules')}")
    click.echo(f"  Max Users: {payload.get('max_users')}")
    click.echo(f"  Max Orgs: {payload.get('max_organizations')}")

    # Check expiry
    expires = datetime.fromisoformat(payload["expires_at"])
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    now = datetime.now(tz=UTC)
    if now > expires:
        grace = payload.get("grace_period_days", 30)
        from datetime import timedelta

        grace_end = expires + timedelta(days=grace)
        if now > grace_end:
            click.echo("  WARNING: License EXPIRED (past grace period)")
        else:
            days_left = (grace_end - now).days
            click.echo(
                f"  WARNING: License expired, {days_left} days of grace remaining"
            )
    else:
        days_left = (expires - now).days
        click.echo(f"  Status: {days_left} days remaining")


@cli.command()
def show_fingerprint() -> None:
    """Display this machine's hardware fingerprint."""
    # Add project root to path so we can import app modules
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))

    from app.licensing.fingerprint import get_machine_fingerprint

    fp = get_machine_fingerprint()
    click.echo(f"Machine fingerprint: {fp}")
    click.echo("Use this value in the license spec's 'hardware_fingerprint' field.")


if __name__ == "__main__":
    cli()
