from datetime import datetime, timezone

from decimal import Decimal
from sqlalchemy import DateTime, String, ForeignKey, Numeric, Text, UniqueConstraint, Index
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
