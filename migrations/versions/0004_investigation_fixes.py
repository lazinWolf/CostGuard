"""Record each fix an investigation tests: {name, job_id, report_id}."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("investigations", sa.Column("fixes", JSONB, nullable=False, server_default="[]"))


def downgrade():
    op.drop_column("investigations", "fixes")
