import os
from collections.abc import Iterable
from uuid import UUID

from dotenv import load_dotenv

from app.db.session_context import cross_org_session
from app.models.domain_settings import DomainSetting, SettingDomain
from app.services.secrets import is_openbao_ref
from app.services.settings_spec import (
    SETTINGS_SPECS,
    SettingSpec,
    coerce_value,
    extract_db_value,
)


def _env_value(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return None
    return value


def _scope_label(organization_id: UUID | None) -> str:
    return "global" if organization_id is None else f"org={organization_id}"


def _validate_constraints(
    spec: SettingSpec, value: object | None, scope: str
) -> list[str]:
    name = f"{spec.domain.value}.{spec.key} [{scope}]"
    if spec.required and value is None:
        return [f"{name}: required value missing"]
    if value is None:
        return []

    errors: list[str] = []
    if spec.allowed:
        normalized = str(value).lower()
        if normalized not in {item.lower() for item in spec.allowed}:
            errors.append(f"{name}: value must be one of {sorted(spec.allowed)}")
    if spec.min_value is not None:
        try:
            if int(value) < spec.min_value:
                errors.append(f"{name}: value must be >= {spec.min_value}")
        except (TypeError, ValueError):
            errors.append(f"{name}: value must be an integer")
    if spec.max_value is not None:
        try:
            if int(value) > spec.max_value:
                errors.append(f"{name}: value must be <= {spec.max_value}")
        except (TypeError, ValueError):
            errors.append(f"{name}: value must be an integer")
    return errors


def validate_rows(rows: Iterable[DomainSetting]) -> list[str]:
    """Validate global and organization-specific settings without collapsing them."""
    db_map: dict[tuple[UUID | None, SettingDomain, str], DomainSetting] = {}
    organization_ids: set[UUID] = set()
    for row in rows:
        db_map[(row.organization_id, row.domain, row.key)] = row
        if row.organization_id is not None:
            organization_ids.add(row.organization_id)

    scopes: list[UUID | None] = [None, *sorted(organization_ids, key=str)]
    errors: list[str] = []

    for spec in SETTINGS_SPECS:
        name = f"{spec.domain.value}.{spec.key}"
        env_raw = _env_value(spec.env_var) if spec.env_var else None
        env_value, env_error = (
            coerce_value(spec, env_raw) if env_raw is not None else (None, None)
        )
        if env_error:
            errors.append(f"{name} [env]: {env_error}")

        db_values: dict[UUID | None, object | None] = {}
        invalid_scopes: set[UUID | None] = set()
        for scope in scopes:
            setting = db_map.get((scope, spec.domain, spec.key))
            if setting is None:
                continue
            raw = extract_db_value(setting)
            label = _scope_label(scope)
            if (
                spec.is_secret
                and raw
                and isinstance(raw, str)
                and not is_openbao_ref(raw)
            ):
                errors.append(f"{name} [{label}]: secret must be an OpenBao reference")
                invalid_scopes.add(scope)
                continue
            value, db_error = (
                coerce_value(spec, raw) if raw is not None else (None, None)
            )
            if db_error:
                errors.append(f"{name} [{label}]: db {db_error}")
                invalid_scopes.add(scope)
                continue
            db_values[scope] = value

        if env_raw is not None:
            if not env_error:
                errors.extend(_validate_constraints(spec, env_value, "env"))
            continue

        for scope in scopes:
            source_scope = scope
            if scope not in db_values and scope not in invalid_scopes:
                if scope is not None and spec.inherits:
                    source_scope = None
                else:
                    source_scope = scope
            if source_scope in invalid_scopes:
                continue
            effective = db_values.get(source_scope)
            if effective is None:
                effective = spec.default
            errors.extend(_validate_constraints(spec, effective, _scope_label(scope)))

    return errors


def main():
    load_dotenv()
    # Cross-org is intentional: this validates the global setting plus every
    # organization-specific override and labels errors with the owning scope.
    with cross_org_session() as db:
        rows = (
            db.query(DomainSetting)
            .filter(DomainSetting.is_active.is_(True))
            .order_by(DomainSetting.updated_at.asc())
            .all()
        )
        errors = validate_rows(rows)

    if errors:
        print("Settings validation failed:")
        for item in errors:
            print(f"- {item}")
        raise SystemExit(1)
    print("Settings validation passed.")


if __name__ == "__main__":
    main()
