"""Add explicit case parents for independent source runs."""

from alembic import op
import sqlalchemy as sa


revision = "20260816_0003"
down_revision = "20260816_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column(
            "event_type",
            sa.String(16),
            nullable=False,
            server_default="run",
        ),
    )
    op.add_column("events", sa.Column("case_key", sa.String(128), nullable=True))
    op.create_index("ix_events_event_type", "events", ["event_type"])
    op.create_index("ix_events_parent_event_id", "events", ["parent_event_id"])
    op.create_unique_constraint("uq_events_case_key", "events", ["case_key"])


def downgrade() -> None:
    op.drop_constraint("uq_events_case_key", "events", type_="unique")
    op.drop_index("ix_events_parent_event_id", table_name="events")
    op.drop_index("ix_events_event_type", table_name="events")
    op.drop_column("events", "case_key")
    op.drop_column("events", "event_type")
