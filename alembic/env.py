from logging.config import fileConfig
import importlib
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from dotmac_kernel.prerequisites import install_prerequisite_bindings

from alembic import context
from app.config import settings
from app.db import Base
from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

# Installed BEFORE the revision map is built, so a composed module lineage can
# resolve its `depends_on` from these bindings at script-load time. ERP hosts
# `public.tenants` itself and can never run kernel `0001`, which is exactly why
# a module must declare the EFFECT it needs rather than a foreign revision —
# see `app/migration_bindings.py`.
#
# Graph commands (`alembic heads`, `history`, `show`) do NOT run this file. They
# resolve to empty edges unless `DOTMAC_MIGRATION_BINDINGS` is set, which is
# tolerated by design: ordering correctness rests on this call before every
# upgrade, on the composed gate, and on `require_prerequisites` proving the
# effects against the database before any DDL.
install_prerequisite_bindings(ASSEMBLY_PREREQUISITE_BINDINGS)

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

config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = _load_target_metadata()


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
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
