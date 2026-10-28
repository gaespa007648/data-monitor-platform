"""Administrator-only operational metrics and application audit trail."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import BigInteger, String, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.dependencies import CurrentUser, Session, require_admin
from app.models import AuditLog, User


router = APIRouter(prefix="/api/v1/system", tags=["system"])


class InformationSchema(DeclarativeBase):
    pass


class GlobalStatus(InformationSchema):
    __tablename__ = "GLOBAL_STATUS"
    __table_args__ = {"schema": "information_schema"}
    VARIABLE_NAME: Mapped[str] = mapped_column(String(64), primary_key=True)
    VARIABLE_VALUE: Mapped[str | None] = mapped_column(String(1024))


class GlobalVariable(InformationSchema):
    __tablename__ = "GLOBAL_VARIABLES"
    __table_args__ = {"schema": "information_schema"}
    VARIABLE_NAME: Mapped[str] = mapped_column(String(64), primary_key=True)
    VARIABLE_VALUE: Mapped[str | None] = mapped_column(String(1024))


class DatabaseTable(InformationSchema):
    __tablename__ = "TABLES"
    __table_args__ = {"schema": "information_schema"}
    TABLE_SCHEMA: Mapped[str] = mapped_column(String(64), primary_key=True)
    TABLE_NAME: Mapped[str] = mapped_column(String(64), primary_key=True)
    DATA_LENGTH: Mapped[int | None] = mapped_column(BigInteger)
    INDEX_LENGTH: Mapped[int | None] = mapped_column(BigInteger)


STATUS_NAMES = (
    "Threads_connected", "Threads_running", "Max_used_connections", "Uptime",
    "Questions", "Slow_queries", "Innodb_buffer_pool_bytes_data",
    "Innodb_buffer_pool_read_requests", "Innodb_buffer_pool_reads", "Cpu_time",
)
VARIABLE_NAMES = ("max_connections", "innodb_buffer_pool_size")


def numeric(values: dict[str, str | None], key: str) -> int | float | None:
    value = values.get(key.lower())
    if value is None:
        return None
    try:
        return float(value) if key == "Cpu_time" else int(value)
    except ValueError:
        return None


@router.get("/database")
async def database_metrics(session: Session, user: CurrentUser):
    require_admin(user)
    status = {row.VARIABLE_NAME.lower(): row.VARIABLE_VALUE for row in
              (await session.scalars(select(GlobalStatus).where(
                  GlobalStatus.VARIABLE_NAME.in_(STATUS_NAMES)))).all()}
    variables = {row.VARIABLE_NAME.lower(): row.VARIABLE_VALUE for row in
                 (await session.scalars(select(GlobalVariable).where(
                     GlobalVariable.VARIABLE_NAME.in_(VARIABLE_NAMES)))).all()}
    database = await session.scalar(select(func.database()))
    storage = await session.execute(select(
        func.coalesce(func.sum(DatabaseTable.DATA_LENGTH), 0),
        func.coalesce(func.sum(DatabaseTable.INDEX_LENGTH), 0),
    ).where(DatabaseTable.TABLE_SCHEMA == database))
    data_bytes, index_bytes = storage.one()
    reads = numeric(status, "Innodb_buffer_pool_reads")
    requests = numeric(status, "Innodb_buffer_pool_read_requests")
    return {
        "database": database,
        "connections": numeric(status, "Threads_connected"),
        "running_queries": numeric(status, "Threads_running"),
        "peak_connections": numeric(status, "Max_used_connections"),
        "max_connections": numeric(variables, "max_connections"),
        "uptime_seconds": numeric(status, "Uptime"),
        "questions_total": numeric(status, "Questions"),
        "slow_queries_total": numeric(status, "Slow_queries"),
        "cpu_seconds_total": numeric(status, "Cpu_time"),
        "buffer_pool_used_bytes": numeric(status, "Innodb_buffer_pool_bytes_data"),
        "buffer_pool_size_bytes": numeric(variables, "innodb_buffer_pool_size"),
        "buffer_pool_hit_ratio": (1 - reads / requests) if requests and reads is not None else None,
        "data_bytes": int(data_bytes),
        "index_bytes": int(index_bytes),
    }


class AuditItem(BaseModel):
    id: int
    username: str
    action: str
    resource_id: int | None
    details: str
    created_at: str


class AuditPage(BaseModel):
    items: list[AuditItem]
    total: int
    page: int
    page_size: int


@router.get("/audit-logs", response_model=AuditPage)
async def audit_logs(session: Session, user: CurrentUser, page: int = 1, page_size: int = 20):
    require_admin(user)
    if page < 1 or not 1 <= page_size <= 100:
        raise HTTPException(422, "Invalid page or page_size")
    total = await session.scalar(select(func.count(AuditLog.id)))
    rows = (await session.execute(select(AuditLog, User.username).join(User, AuditLog.actor_id == User.id)
        .order_by(AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size))).all()
    return AuditPage(items=[AuditItem(id=entry.id, username=username, action=entry.action,
        resource_id=entry.resource_id, details=entry.details,
        created_at=entry.created_at.isoformat() + "Z") for entry, username in rows],
        total=total, page=page, page_size=page_size)
