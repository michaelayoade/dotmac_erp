"""Join automation security and entity configuration migrations."""

from collections.abc import Sequence

revision: str = "20260912_merge_automation"
down_revision: tuple[str, str] = (
    "20260911_automation_security_lifecycle",
    "20260911_automation_entity_configuration",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
