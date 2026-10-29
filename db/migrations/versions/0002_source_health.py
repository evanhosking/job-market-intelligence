"""Durable guest-source health and per-job enrichment state."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"


def upgrade():
    op.create_table(
        "source_health",
        sa.Column("source", sa.String(), primary_key=True),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("retry_at", sa.String(), nullable=True),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.create_table(
        "detail_tasks",
        sa.Column("source_job_id", sa.String(), primary_key=True),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.String(), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column(
            "payload", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column("fetched_at", sa.String(), nullable=True),
    )
    op.create_index("ix_detail_tasks_state", "detail_tasks", ["state"])


def downgrade():
    op.drop_index("ix_detail_tasks_state", "detail_tasks")
    op.drop_table("detail_tasks")
    op.drop_table("source_health")
