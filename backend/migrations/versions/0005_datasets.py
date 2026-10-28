"""Datasets with append-only schemas and dynamic records."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("datasets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.UniqueConstraint("owner_id", "name", name="uq_dataset_owner_name"))
    op.create_table("dataset_schema_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dataset_id", sa.Integer(), sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("rules_hash", sa.String(64), nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.UniqueConstraint("dataset_id", "number", name="uq_dataset_schema_number"))
    op.create_table("dataset_import_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dataset_id", sa.Integer(), sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column("schema_version_id", sa.Integer(), sa.ForeignKey("dataset_schema_versions.id"), nullable=False),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("schema_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_dataset_import_owner_key"))
    op.create_table("dataset_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dataset_id", sa.Integer(), sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column("schema_version_id", sa.Integer(), sa.ForeignKey("dataset_schema_versions.id"), nullable=False),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("import_batch_id", sa.Integer(), sa.ForeignKey("dataset_import_batches.id")),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False))
    op.create_index("ix_dataset_records_dataset_id", "dataset_records", ["dataset_id", "id"])


def downgrade():
    op.drop_table("dataset_records")
    op.drop_table("dataset_import_batches")
    op.drop_table("dataset_schema_versions")
    op.drop_table("datasets")
