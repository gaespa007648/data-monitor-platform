"""Persist dataset monitors and their anomaly records."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("dataset_monitors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dataset_id", sa.Integer(), sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("chart_type", sa.String(10), nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("activated_at", DATETIME(fsp=6)),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False))
    op.create_index("ix_dataset_monitors_dataset_id", "dataset_monitors", ["dataset_id"])
    op.create_table("dataset_monitor_alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("monitor_id", sa.Integer(), sa.ForeignKey("dataset_monitors.id"), nullable=False),
        sa.Column("record_id", sa.Integer(), sa.ForeignKey("dataset_records.id"), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("created_at", DATETIME(fsp=6), nullable=False),
        sa.UniqueConstraint("monitor_id", "record_id", name="uq_monitor_record_alert"))
    op.create_index("ix_dataset_monitor_alerts_monitor_id", "dataset_monitor_alerts", ["monitor_id"])
    op.create_index("ix_dataset_monitor_alerts_record_id", "dataset_monitor_alerts", ["record_id"])


def downgrade():
    op.drop_table("dataset_monitor_alerts")
    op.drop_table("dataset_monitors")
