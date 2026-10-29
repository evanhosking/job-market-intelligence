"""Extraction selection provenance and bounded model usage metadata."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"


def upgrade():
    op.add_column(
        "extraction_results",
        sa.Column(
            "diagnostics",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade():
    op.drop_column("extraction_results", "diagnostics")
