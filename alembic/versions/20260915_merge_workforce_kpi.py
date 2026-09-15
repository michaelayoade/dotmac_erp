"""Join workforce provisioning and KPI measurement direction migrations.

Revision ID: 20260915_merge_workforce_kpi
Revises: 20260915_workforce_monitor, 20260915_kpi_direction

Both independently authored migrations must run. This merge does not rewrite
an already-published migration or drop either feature's database state.
"""

revision = "20260915_merge_workforce_kpi"
down_revision = ("20260915_workforce_monitor", "20260915_kpi_direction")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
