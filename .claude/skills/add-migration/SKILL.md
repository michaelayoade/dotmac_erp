---
name: add-migration
description: Create an idempotent Alembic migration
arguments:
  - name: description
    description: "Short description of the migration (e.g. 'add training_record table')"
---

# Add Database Migration

Create a safe, idempotent Alembic migration for the DotMac ERP.

## Steps

### 1. Generate the migration
```bash
poetry run alembic revision --autogenerate -m "$ARGUMENTS"
```

### 2. Review the generated file
Read the newly created migration file in `alembic/versions/`.

### 3. Make it idempotent
**CRITICAL**: All migrations MUST be safe to run multiple times. Wrap every operation:

```python
def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Tables: check before creating
    if not inspector.has_table("my_table", schema="my_schema"):
        op.create_table(...)

    # Columns: check before adding
    if inspector.has_table("my_table", schema="my_schema"):
        columns = {col["name"] for col in inspector.get_columns("my_table", schema="my_schema")}
        if "new_column" not in columns:
            op.add_column(...)

    # Enums: check before creating
    existing_enums = [e["name"] for e in inspector.get_enums(schema="my_schema")]
    if "my_enum" not in existing_enums:
        my_enum.create(bind)

    # Indexes: check before creating
    indexes = {idx["name"] for idx in inspector.get_indexes("my_table", schema="my_schema")}
    if "ix_my_index" not in indexes:
        op.create_index(...)
```

### 4. PostgreSQL enum gotcha
If adding values to an existing Python enum, you need `ALTER TYPE ... ADD VALUE`:
```python
# Python-side change alone causes InvalidTextRepresentation error
op.execute("ALTER TYPE my_schema.my_enum ADD VALUE IF NOT EXISTS 'NEW_VALUE'")
```
Use `create_type=False` in `postgresql.ENUM()` column definitions to prevent DDL auto-creation.

### 5. Test the migration
```bash
# Apply
poetry run alembic upgrade head

# Verify it's idempotent (run again — should be no-op)
poetry run alembic upgrade head

# If needed inside Docker:
docker exec dotmac_erp_app poetry run alembic upgrade head
```

### 6. For deployed environments
If `alembic/` is not bind-mounted in Docker:
```bash
docker cp alembic/versions/NEW_FILE.py dotmac_erp_app:/app/alembic/versions/
docker exec dotmac_erp_app poetry run alembic upgrade head
```
