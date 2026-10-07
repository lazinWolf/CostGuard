"""Durable workload call receipts."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("checkpoint", postgresql.JSONB(), nullable=False,
                                   server_default=sa.text("'{}'::jsonb")))


def downgrade():
    op.drop_column("jobs", "checkpoint")
