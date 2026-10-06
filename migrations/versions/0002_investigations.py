"""Persist analyst settings and bounded economic investigations."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("settings", sa.Column("id", sa.String(50), primary_key=True),
                    sa.Column("payload", JSONB, nullable=False))
    op.create_table("investigations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("report_id", sa.String(36), sa.ForeignKey("comparisons.id"), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("request", JSONB, nullable=False),
        sa.Column("result", JSONB, nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("proposal_job_id", sa.String(36), sa.ForeignKey("jobs.id")),
        sa.Column("proposal_report_id", sa.String(36), sa.ForeignKey("comparisons.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_investigations_state", "investigations", ["state"])


def downgrade():
    op.drop_table("investigations")
    op.drop_table("settings")
