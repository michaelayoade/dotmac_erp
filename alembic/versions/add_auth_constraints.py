"""Add auth constraints and domain setting checks.

Revision ID: add_auth_constraints
Revises: add_rls_policies
Create Date: 2025-01-09
"""

from alembic import op

revision = "add_auth_constraints"
down_revision = "add_rls_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            UPDATE domain_settings
            SET value_text = COALESCE(value_text, value_json #>> '{}'),
                value_json = NULL
            WHERE value_type IN ('string', 'integer')
            """
        )
        op.execute(
            """
            UPDATE domain_settings
            SET value_json = COALESCE(value_json, to_json(value_text)),
                value_text = NULL
            WHERE value_type = 'json' AND value_text IS NOT NULL
            """
        )
    op.create_check_constraint(
        "ck_domain_settings_value_storage",
        "domain_settings",
        "(value_type = 'json' AND value_text IS NULL) "
        "OR (value_type IN ('string', 'integer') AND value_json IS NULL) "
        "OR (value_type = 'boolean')",
    )
    op.create_unique_constraint(
        "uq_user_credentials_person_provider",
        "user_credentials",
        ["person_id", "provider"],
    )
    op.create_unique_constraint(
        "uq_user_credentials_provider_username",
        "user_credentials",
        ["provider", "username"],
    )
    op.create_check_constraint(
        "ck_user_credentials_local_requirements",
        "user_credentials",
        "(provider != 'local') OR (username IS NOT NULL AND password_hash IS NOT NULL)",
    )
    op.drop_index("ix_sessions_token_hash", table_name="sessions")
    op.create_index("ix_sessions_token_hash", "sessions", ["token_hash"], unique=True)
    op.create_unique_constraint("uq_api_keys_key_hash", "api_keys", ["key_hash"])


def downgrade() -> None:
    op.drop_constraint(
        "uq_api_keys_key_hash",
        "api_keys",
        type_="unique",
    )
    op.drop_index("ix_sessions_token_hash", table_name="sessions")
    op.create_index("ix_sessions_token_hash", "sessions", ["token_hash"])
    op.drop_constraint(
        "ck_user_credentials_local_requirements",
        "user_credentials",
        type_="check",
    )
    op.drop_constraint(
        "uq_user_credentials_provider_username",
        "user_credentials",
        type_="unique",
    )
    op.drop_constraint(
        "uq_user_credentials_person_provider",
        "user_credentials",
        type_="unique",
    )
    op.drop_constraint(
        "ck_domain_settings_value_storage",
        "domain_settings",
        type_="check",
    )
