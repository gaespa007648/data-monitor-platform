"""Persist versioned import schemas and batch rule snapshots."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("import_schema_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("visibility", sa.String(10), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("builtin_key", sa.String(30), unique=True),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", DATETIME(fsp=6), nullable=False),
        sa.UniqueConstraint("owner_id", "name", name="uq_schema_owner_name"))
    op.create_table("import_schema_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("template_id", sa.Integer(), sa.ForeignKey("import_schema_templates.id"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("file_format", sa.String(5), nullable=False),
        sa.Column("extra_policy", sa.String(10), nullable=False),
        sa.Column("trim_whitespace", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("rules_hash", sa.String(64), nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.UniqueConstraint("template_id", "number", name="uq_schema_version_number"))
    op.create_table("import_schema_fields",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version_id", sa.Integer(), sa.ForeignKey("import_schema_versions.id"), nullable=False),
        sa.Column("source_field", sa.String(128, collation="utf8mb4_bin"), nullable=False),
        sa.Column("target_field", sa.String(30), nullable=False),
        sa.Column("data_type", sa.String(15)),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("allow_numeric_string", sa.Boolean()),
        sa.Column("max_length", sa.Integer()),
        sa.Column("max_digits", sa.Integer()),
        sa.Column("decimal_places", sa.Integer()),
        sa.UniqueConstraint("version_id", "source_field", name="uq_schema_source_field"),
        sa.UniqueConstraint("version_id", "target_field", name="uq_schema_target_field"))
    op.add_column("import_batches", sa.Column("schema_version_id", sa.Integer(), nullable=True))
    op.add_column("import_batches", sa.Column("rules_hash", sa.String(64), nullable=True))
    op.add_column("import_batches", sa.Column("schema_snapshot", sa.JSON(), nullable=True))
    op.create_foreign_key("fk_batch_schema_version", "import_batches", "import_schema_versions", ["schema_version_id"], ["id"])


def downgrade():
    op.drop_constraint("fk_batch_schema_version", "import_batches", type_="foreignkey")
    for column in ["schema_snapshot", "rules_hash", "schema_version_id"]:
        op.drop_column("import_batches", column)
    op.drop_table("import_schema_fields")
    op.drop_table("import_schema_versions")
    op.drop_table("import_schema_templates")
