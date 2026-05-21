from logging.config import fileConfig
import importlib
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.config import settings
from app.db import Base

MODEL_MODULES = (
    "app.models.audit",
    "app.models.auth",
    "app.models.domain_settings",
    "app.models.finance",
    "app.models.finance.ipsas",
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
