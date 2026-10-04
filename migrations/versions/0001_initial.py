"""Create prototype records and job queue.

Revision ID: 0001
Revises:
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("suite_id", sa.String(200), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_artifacts_suite_id", "artifacts", ["suite_id"])
    op.create_table("suites", sa.Column("id", sa.String(200), primary_key=True),
                    sa.Column("payload", JSONB, nullable=False))
    op.create_table("baselines", sa.Column("suite_id", sa.String(200), primary_key=True),
                    sa.Column("artifact_id", sa.String(36), sa.ForeignKey("artifacts.id"), nullable=False))
    op.create_table("comparisons", sa.Column("id", sa.String(36), primary_key=True),
                    sa.Column("baseline_id", sa.String(36), sa.ForeignKey("artifacts.id"), nullable=False),
                    sa.Column("candidate_id", sa.String(36), sa.ForeignKey("artifacts.id"), nullable=False),
                    sa.Column("request_payload", JSONB, nullable=False),
                    sa.Column("report", JSONB, nullable=False),
                    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("jobs", sa.Column("id", sa.String(36), primary_key=True),
                    sa.Column("state", sa.String(24), nullable=False),
                    sa.Column("spec", JSONB, nullable=False),
                    sa.Column("progress", sa.Integer, nullable=False),
                    sa.Column("total", sa.Integer, nullable=False),
                    sa.Column("artifact_id", sa.String(36), sa.ForeignKey("artifacts.id")),
                    sa.Column("error", sa.Text),
                    sa.Column("lease_until", sa.DateTime(timezone=True)),
                    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_jobs_state", "jobs", ["state"])


def downgrade():
    op.drop_table("jobs")
    op.drop_table("comparisons")
    op.drop_table("baselines")
    op.drop_table("suites")
    op.drop_table("artifacts")
