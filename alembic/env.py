from logging.config import fileConfig
import importlib
import os
from pathlib import Path

from sqlalchemy import engine_from_config, pool, text
from sqlalchemy.engine import Connection, make_url

from dotmac_kernel.planes import install_module_plane_selections
from dotmac_kernel.prerequisites import install_prerequisite_bindings

from alembic import context
from app.db import Base
from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS
from app.migration_database_roles import (
    MIGRATION_EXECUTOR,
    MIGRATION_OWNERSHIP_SQL,
    ROLE_CONTRACT,
    migration_executor_violations,
    migration_ownership_violations,
)
from app.migration_planes import ASSEMBLY_MODULE_PLANES

# Installed BEFORE the revision map is built, so a composed module lineage can
# resolve both its `depends_on` effects and its selected storage plane at
# script-load time. ERP hosts `public.tenants` itself and can never run kernel
# `0001`, which is exactly why a module must declare the EFFECT it needs rather
# than a foreign revision — see `app/migration_bindings.py`. Plane selection is
# the independent assembly decision recorded in `app/migration_planes.py`.
#
# This is independent of the executor contract below: bindings decide WHICH
# revision supplies an effect, `verify_migration_connection` decides whether the
# identity running the migration may alter anything at all. Both must hold.
#
# Graph commands (`alembic heads`, `history`, `show`) do NOT run this file. They
# need the kernel's graph-command environment variables when invoked outside a
# process that already installed these declarations. Deploy upgrades always run
# this file; the composed gate receives both declarations explicitly.
install_prerequisite_bindings(ASSEMBLY_PREREQUISITE_BINDINGS)
install_module_plane_selections(ASSEMBLY_MODULE_PLANES)

MODEL_MODULES = (
    "app.models.audit",
    "app.models.auth",
    "app.models.domain_settings",
    "app.models.finance",
    "app.models.finance.ipsas",
    "app.models.infrastructure_health",
    "app.models.person",
    "app.models.procurement",
    "app.models.rbac",
    "app.models.scheduler",
)


def _model_sources_removed() -> bool:
    """Detect hardened images where Nuitka removed model source files."""
    repo_root = Path(__file__).resolve().parents[1]
    return not (repo_root / "app" / "models" / "audit.py").exists()


def _load_target_metadata():
    for module_name in MODEL_MODULES:
        try:
            importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if _model_sources_removed() and (exc.name or "").startswith("app.models"):
                return None
            raise
    return Base.metadata


config = context.config


def _migration_url() -> str:
    value = os.environ.get("MIGRATION_DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError(
            "MIGRATION_DATABASE_URL is required. Alembic never falls back to "
            "the application's DATABASE_URL."
        )
    return value


def verify_migration_connection(connection: Connection) -> None:
    if connection.dialect.name != "postgresql":
        return
    current_user = str(connection.scalar(text("SELECT current_user")))
    rows = connection.execute(
        text(
            "SELECT rolname, rolbypassrls, rolsuper FROM pg_roles "
            "WHERE rolname = ANY(:names)"
        ),
        {"names": list(ROLE_CONTRACT)},
    ).all()
    observed = {str(row[0]): (bool(row[1]), bool(row[2])) for row in rows}
    ownership_rows = connection.execute(text(MIGRATION_OWNERSHIP_SQL)).all()
    non_owned_counts = {str(row[0]): int(row[1]) for row in ownership_rows}
    violations = (
        *migration_executor_violations(current_user, observed),
        *migration_ownership_violations(non_owned_counts),
    )
    if violations:
        raise RuntimeError(
            "migration executor contract failed: " + "; ".join(violations)
        )


migration_url = _migration_url()
config.set_main_option("sqlalchemy.url", migration_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = _load_target_metadata()


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    parsed = make_url(url)
    if (
        parsed.get_backend_name() == "postgresql"
        and parsed.username != MIGRATION_EXECUTOR
    ):
        raise RuntimeError(
            f"offline migration URL user is {parsed.username!r}, required "
            f"{MIGRATION_EXECUTOR!r}"
        )
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        version_table_schema="public",
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # Catalogue reads autobegin a SQLAlchemy transaction. Finish that
        # read-only preflight before Alembic takes transaction authority;
        # legacy revisions use ``autocommit_block()``, which requires the
        # migration context to own its outer transaction.
        with connection.begin():
            verify_migration_connection(connection)
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            version_table_schema="public",
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
