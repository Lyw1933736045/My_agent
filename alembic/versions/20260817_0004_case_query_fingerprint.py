"""Store a normalized query fingerprint for existing-case lookup."""

from alembic import op
import sqlalchemy as sa


revision = "20260817_0004"
down_revision = "20260816_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("query_fingerprint", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_events_query_fingerprint",
        "events",
        ["query_fingerprint"],
    )


def downgrade() -> None:
    op.drop_index("ix_events_query_fingerprint", table_name="events")
    op.drop_column("events", "query_fingerprint")
