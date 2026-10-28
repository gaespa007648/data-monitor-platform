"""Categories, records, import batches and audit trail."""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True))
    op.create_table("import_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_import_owner_key"))
    op.create_table("records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("value", sa.Numeric(18, 6), nullable=False),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("import_batch_id", sa.Integer(), sa.ForeignKey("import_batches.id")),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False))
    op.create_index("ix_records_time_id", "records", ["timestamp", "id"])
    op.create_index("ix_records_category_time", "records", ["category_id", "timestamp"])
    op.create_index("ix_records_owner_time", "records", ["owner_id", "timestamp"])
    op.create_table("audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("resource_id", sa.Integer()),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False))


def downgrade():
    op.drop_table("audit_logs")
    op.drop_table("records")
    op.drop_table("import_batches")
    op.drop_table("categories")
