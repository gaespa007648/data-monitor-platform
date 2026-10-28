"""Preserve microseconds consistently when records are reloaded."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

columns = {"users": ["created_at"], "records": ["timestamp", "created_at", "updated_at"],
           "import_batches": ["created_at"], "audit_logs": ["created_at"]}


def upgrade():
    for table, names in columns.items():
        for name in names:
            op.alter_column(table, name, existing_type=sa.DateTime(), type_=DATETIME(fsp=6), existing_nullable=False)


def downgrade():
    for table, names in columns.items():
        for name in names:
            op.alter_column(table, name, existing_type=DATETIME(fsp=6), type_=sa.DateTime(), existing_nullable=False)
