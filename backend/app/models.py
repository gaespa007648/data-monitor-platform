from datetime import datetime, timezone

from decimal import Decimal
from sqlalchemy import DateTime, String, ForeignKey, Numeric, Text, UniqueConstraint, Index, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.dialects.mysql import DATETIME

TimestampType = DateTime().with_variant(DATETIME(fsp=6), "mysql")


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        TimestampType, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)


class ImportBatch(Base):
    __tablename__ = "import_batches"
    __table_args__ = (UniqueConstraint("owner_id", "idempotency_key", name="uq_import_owner_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    idempotency_key: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int]
    schema_version_id: Mapped[int | None] = mapped_column(ForeignKey("import_schema_versions.id"))
    rules_hash: Mapped[str | None] = mapped_column(String(64))
    schema_snapshot: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(TimestampType, default=utcnow)


class Record(Base):
    __tablename__ = "records"
    __table_args__ = (
        Index("ix_records_time_id", "timestamp", "id"),
        Index("ix_records_category_time", "category_id", "timestamp"),
        Index("ix_records_owner_time", "owner_id", "timestamp"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    value: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    timestamp: Mapped[datetime] = mapped_column(TimestampType, nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="manual")
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"))
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(TimestampType, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TimestampType, default=utcnow, onupdate=utcnow)
    __mapper_args__ = {"version_id_col": version}


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[int | None]
    details: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(TimestampType, default=utcnow)


class ImportSchemaTemplate(Base):
    __tablename__ = "import_schema_templates"
    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_schema_owner_name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(500), default="")
    visibility: Mapped[str] = mapped_column(String(10), default="private")
    is_active: Mapped[bool] = mapped_column(default=True)
    builtin_key: Mapped[str | None] = mapped_column(String(30), unique=True)
    created_at: Mapped[datetime] = mapped_column(TimestampType, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TimestampType, default=utcnow, onupdate=utcnow)


class ImportSchemaVersion(Base):
    __tablename__ = "import_schema_versions"
    __table_args__ = (UniqueConstraint("template_id", "number", name="uq_schema_version_number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("import_schema_templates.id"))
    number: Mapped[int]
    status: Mapped[str] = mapped_column(String(12), default="draft")
    file_format: Mapped[str] = mapped_column(String(5))
    extra_policy: Mapped[str] = mapped_column(String(10), default="reject")
    trim_whitespace: Mapped[bool] = mapped_column(default=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    rules_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TimestampType, default=utcnow)


class ImportSchemaField(Base):
    __tablename__ = "import_schema_fields"
    __table_args__ = (
        UniqueConstraint("version_id", "source_field", name="uq_schema_source_field"),
        UniqueConstraint("version_id", "target_field", name="uq_schema_target_field"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("import_schema_versions.id"))
    source_field: Mapped[str] = mapped_column(String(128, collation="utf8mb4_bin"))
    target_field: Mapped[str] = mapped_column(String(30))
    data_type: Mapped[str | None] = mapped_column(String(15))
    position: Mapped[int]
    allow_numeric_string: Mapped[bool | None]
    max_length: Mapped[int | None]
    max_digits: Mapped[int | None]
    decimal_places: Mapped[int | None]


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_dataset_owner_name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(500), default="")
    schema_version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(TimestampType, default=utcnow)


class DatasetSchemaVersion(Base):
    __tablename__ = "dataset_schema_versions"
    __table_args__ = (UniqueConstraint("dataset_id", "number", name="uq_dataset_schema_number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"))
    number: Mapped[int]
    definition: Mapped[dict] = mapped_column(JSON)
    rules_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TimestampType, default=utcnow)


class DatasetImportBatch(Base):
    __tablename__ = "dataset_import_batches"
    __table_args__ = (UniqueConstraint("owner_id", "idempotency_key", name="uq_dataset_import_owner_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"))
    schema_version_id: Mapped[int] = mapped_column(ForeignKey("dataset_schema_versions.id"))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    idempotency_key: Mapped[str] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int]
    schema_snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(TimestampType, default=utcnow)


class DatasetRecord(Base):
    __tablename__ = "dataset_records"
    __table_args__ = (Index("ix_dataset_records_dataset_id", "dataset_id", "id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"))
    schema_version_id: Mapped[int] = mapped_column(ForeignKey("dataset_schema_versions.id"))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("dataset_import_batches.id"))
    source: Mapped[str] = mapped_column(String(10))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(TimestampType, default=utcnow)
