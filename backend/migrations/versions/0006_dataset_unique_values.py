"""Track unique values for dynamic dataset fields."""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("dataset_unique_values",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dataset_id", sa.Integer(), sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column("record_id", sa.Integer(), sa.ForeignKey("dataset_records.id"), nullable=False),
        sa.Column("field_name", sa.String(64, collation="utf8mb4_bin"), nullable=False),
        sa.Column("value_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("dataset_id", "field_name", "value_hash",
                            name="uq_dataset_field_value"))


def downgrade():
    op.drop_table("dataset_unique_values")
