"""Index dataset write times for monitor interval queries."""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_dataset_records_dataset_created", "dataset_records",
                    ["dataset_id", "created_at"])


def downgrade():
    op.drop_index("ix_dataset_records_dataset_created", table_name="dataset_records")
