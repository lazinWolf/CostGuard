"""Store per-case summaries while a job runs, so progress is visible case by case."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("cases", JSONB, nullable=True))


def downgrade():
    op.drop_column("jobs", "cases")
